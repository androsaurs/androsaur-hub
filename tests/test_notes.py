import io
import wave

import pytest

from app.clients.groq import Transcript, transcript_from
from app.clients.weather import WeatherError
from app.dependencies import get_device_service, get_integrations_service, get_notes_service
from app.services.devices import DeviceService
from app.services.integrations import IntegrationsService
from app.services.notes import NotesService
from tests.test_settings import DEVICE, register

KEY = "gsk_test_key_0123456789ABCD"
NOW = 1_792_951_200.0


def wav(seconds: float = 1.0) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))
    return out.getvalue()


class FakeGroq:
    result = Transcript("Купи хлеба по дороге", 82.0, 97.0)
    fail: str | None = None
    calls = 0

    def __init__(self, key: str) -> None:
        self.key = key

    def check(self) -> str:
        if FakeGroq.fail:
            raise WeatherError(FakeGroq.fail)
        return "ключ принят, whisper-large-v3-turbo доступна"

    def transcribe(self, data: bytes) -> Transcript:
        FakeGroq.calls += 1
        if FakeGroq.fail:
            raise WeatherError(FakeGroq.fail)
        assert data.startswith(b"RIFF")
        return FakeGroq.result


@pytest.fixture
def hub(client, tmp_path, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("HUB_NOTES_DIR", str(tmp_path / "notes"))
    get_settings.cache_clear()
    FakeGroq.result, FakeGroq.fail, FakeGroq.calls = (
        Transcript("Купи хлеба по дороге", 82.0, 97.0),
        None,
        0,
    )
    clock = lambda: NOW  # noqa: E731

    def integrations() -> IntegrationsService:
        return IntegrationsService(groq=FakeGroq, clock=clock)

    def notes() -> NotesService:
        return NotesService(integrations=integrations(), clock=clock)

    client.app.dependency_overrides[get_integrations_service] = integrations
    client.app.dependency_overrides[get_notes_service] = notes
    client.app.dependency_overrides[get_device_service] = lambda: DeviceService(clock=clock)
    client.notes = notes
    return client


def upload(client, note_id="n1", data=None, created=None):
    return client.post(
        f"/api/device/notes/{note_id}",
        params={
            "device": DEVICE,
            "to": "катя",
            "from": "Папа",
            "createdMs": int((created or NOW) * 1000),
            "durationMs": 1000,
        },
        content=data if data is not None else wav(),
        headers={"Content-Type": "audio/wav"},
    )


def sync(client):
    return client.post("/api/device/sync", json={"device": DEVICE}).json()


def enable_groq(client, **config):
    client.post("/integrations/groq/key", data={"key": KEY})
    for key, value in config.items():
        client.post("/integrations/groq/config", data={"key": key, "value": value})


def test_without_groq_notes_wait_and_no_text_goes_to_the_phone(hub):
    register(hub)
    assert upload(hub).status_code == 201
    assert upload(hub).json() == {"added": False}, "sent twice is kept once"
    assert hub.notes().transcribe_pending() == 0
    assert sync(hub)["transcripts"] == []
    assert "Groq выключен" in hub.get("/notes").text


def test_a_reliable_transcript_reaches_the_phone(hub):
    register(hub)
    enable_groq(hub)
    upload(hub)
    assert hub.notes().transcribe_pending() == 1
    assert sync(hub)["transcripts"] == [{"id": "n1", "text": "Купи хлеба по дороге"}]
    page = hub.get("/notes").text
    assert "«Купи хлеба по дороге»" in page and "Петя читает текстом" in page
    assert 'src="/notes/n1.wav"' in page, "debug: the sound is kept"
    assert hub.get("/notes/n1.wav").content.startswith(b"RIFF")


def test_a_doubtful_transcript_is_not_read_out(hub):
    register(hub)
    enable_groq(hub)
    FakeGroq.result = Transcript("купи хлеб а по то", 41.0, 90.0)
    upload(hub)
    hub.notes().transcribe_pending()
    assert sync(hub)["transcripts"] == []
    assert "сомнительно — играет запись" in hub.get("/notes").text


def test_the_threshold_is_the_owners(hub):
    register(hub)
    enable_groq(hub, min_confidence=40)
    FakeGroq.result = Transcript("купи хлеб", 41.0, 90.0)
    upload(hub)
    hub.notes().transcribe_pending()
    assert [t["id"] for t in sync(hub)["transcripts"]] == ["n1"]


def test_without_debug_the_sound_goes_after_transcription(hub):
    register(hub)
    enable_groq(hub, keep_audio=0)
    upload(hub)
    hub.notes().transcribe_pending()
    assert hub.get("/notes/n1.wav").status_code == 404
    assert "звук удалён после расшифровки" in hub.get("/notes").text


def test_failures_are_retried_then_given_up(hub):
    register(hub)
    enable_groq(hub)
    upload(hub)
    FakeGroq.fail = "лимит запросов Groq, повторю позже"
    service = hub.notes()
    for _ in range(3):
        service.transcribe_pending()
    assert "не расшифровалась: лимит запросов Groq" in hub.get("/notes").text
    assert service.transcribe_pending() == 0


def test_not_a_wav_is_refused(hub):
    register(hub)
    assert upload(hub, data=b"hello").status_code == 422


def test_old_notes_are_forgotten(hub):
    register(hub)
    upload(hub, "old", created=NOW - 15 * 86400)
    upload(hub, "new")
    assert hub.notes().cleanup() == 1
    assert hub.get("/notes/old.wav").status_code == 404


def test_check_connection_of_groq(hub):
    enable_groq(hub)
    assert "Работает: ключ принят" in hub.post("/integrations/groq/check").text
    FakeGroq.fail = "ключ не принят (401)"
    assert "Не вышло: ключ не принят (401)" in hub.post("/integrations/groq/check").text
    assert KEY not in hub.get("/integrations").text


def test_whisper_answer_is_read():
    t = transcript_from(
        {
            "text": " Купи  хлеба ",
            "segments": [
                {"start": 0, "end": 1, "avg_logprob": -0.2, "no_speech_prob": 0.02},
                {"start": 1, "end": 3, "avg_logprob": -0.5, "no_speech_prob": 0.1},
            ],
        }
    )
    assert t.text == "Купи хлеба"
    assert t.confidence == 67.0 and t.speech == 92.7
