import pytest

from app.clients.groq import Transcript
from app.clients.home_assistant import HomeError, Speaker
from app.dependencies import get_home_service, get_integrations_service
from app.services.home import HomeService
from app.services.integrations import IntegrationsService
from tests.test_notes import KEY, NOW, FakeGroq, wav
from tests.test_people import PAPA, join, phone_sync
from tests.test_settings import DEVICE, register

HA_URL = "http://192.168.1.71:8123"
HA_TOKEN = "t" * 40
KIDS = "media_player.yandex_station_kids"
LIVING = "media_player.yandex_station_living"


class FakeHome:
    spoken: list[tuple[str, str]] = []
    fail: str | None = None

    def __init__(self, url: str, token: str) -> None:
        assert url == HA_URL and token == HA_TOKEN

    def check(self) -> str:
        if FakeHome.fail:
            raise HomeError(FakeHome.fail)
        return "Работает: колонок Яндекса — 2"

    def speakers(self) -> list[Speaker]:
        if FakeHome.fail:
            raise HomeError(FakeHome.fail)
        return [Speaker(KIDS, "Колонка детская"), Speaker(LIVING, "Колонка гостиная")]

    def speak(self, entity_id: str, text: str) -> None:
        if FakeHome.fail:
            raise HomeError(FakeHome.fail)
        FakeHome.spoken.append((entity_id, text))


@pytest.fixture
def hub(client, tmp_path, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("HUB_NOTES_DIR", str(tmp_path / "notes"))
    get_settings.cache_clear()
    FakeGroq.result, FakeGroq.fail, FakeGroq.calls = (
        Transcript("Приходите есть", 82.0, 97.0),
        None,
        0,
    )
    FakeHome.spoken, FakeHome.fail = [], None

    def integrations() -> IntegrationsService:
        return IntegrationsService(groq=FakeGroq, clock=lambda: NOW)

    def home() -> HomeService:
        return HomeService(integrations=integrations(), client=FakeHome, clock=lambda: NOW)

    client.app.dependency_overrides[get_integrations_service] = integrations
    client.app.dependency_overrides[get_home_service] = home
    return client


def connect(client) -> None:
    client.post("/integrations/groq/key", data={"key": KEY})
    client.post("/home/connection", data={"url": HA_URL, "token": HA_TOKEN})


def add_room(client, title="Детская", forms="", entity=KIDS):
    return client.post("/home/rooms", data={"title": title, "forms": forms, "entity_id": entity})


def _rooms(client) -> list[dict]:
    register(client)
    return client.post("/api/device/sync", json={"device": DEVICE}).json()["rooms"]


def announce(client, rid, data=None, **params):
    return client.post(
        f"/api/device/announce/{rid}",
        params={"from": "Женя", **params},
        content=data if data is not None else wav(),
        headers={"Content-Type": "audio/wav"},
    )


def test_rooms_reach_the_phone_with_their_voice_names(hub):
    connect(hub)
    add_room(hub, "Детская", "детская, детскую, Детской")
    add_room(hub, "Гостиная", "", LIVING)
    rooms = {r["name"]: r["words"] for r in _rooms(hub)}
    assert rooms == {
        "Детская": ["детская", "детскую", "детской"],
        "Гостиная": ["гостиная"],
    }


def test_the_message_is_spoken_on_the_rooms_speaker_as_the_role(hub):
    register(hub)
    join(hub, DEVICE, "Redmi 12", [PAPA])
    hub.post("/people/rename", data={"person": PAPA["id"], "to": "Женя"})
    hub.post("/people/roles", data={"person": PAPA["id"], "roles": "папа"})
    connect(hub)
    add_room(hub)
    rid = phone_sync(hub, DEVICE, [PAPA])["rooms"][0]["id"]
    reply = announce(hub, rid, fromId=PAPA["id"])
    assert reply.json() == {"status": "spoken", "text": "Приходите есть"}
    assert FakeHome.spoken == [(KIDS, "Папа говорит: Приходите есть")]


def test_all_speakers_say_it_when_the_room_is_all(hub):
    register(hub)
    connect(hub)
    add_room(hub)
    add_room(hub, "Гостиная", entity=LIVING)
    reply = announce(hub, "all")
    assert reply.json()["status"] == "spoken"
    assert sorted(e for e, _ in FakeHome.spoken) == sorted([KIDS, LIVING])
    assert len({t for _, t in FakeHome.spoken}) == 1


def test_without_a_role_the_name_the_phone_gave_is_used_and_nobody_gives_a_plain_message(hub):
    connect(hub)
    add_room(hub)
    rid = _rooms(hub)[0]["id"]
    announce(hub, rid)
    announce(hub, rid, **{"from": ""})
    assert [t for _, t in FakeHome.spoken] == [
        "Женя говорит: Приходите есть",
        "Сообщение: Приходите есть",
    ]


def test_an_unclear_message_is_not_spoken(hub):
    connect(hub)
    add_room(hub)
    rid = _rooms(hub)[0]["id"]
    FakeGroq.result = Transcript("мм", 20.0, 97.0)
    assert announce(hub, rid).json() == {"status": "unclear", "text": "мм"}
    FakeGroq.result = Transcript("", 0.0, 0.0)
    assert announce(hub, rid).json()["status"] == "unclear"
    assert FakeHome.spoken == []


def test_the_phone_is_told_why_nothing_was_spoken(hub):
    add_room_without_ha = hub.post("/home/rooms", data={"title": "Х", "entity_id": KIDS})
    assert "Выберите колонку" not in add_room_without_ha.text
    hub.post("/home/connection", data={"url": HA_URL, "token": HA_TOKEN})
    add_room(hub)
    rid = _rooms(hub)[0]["id"]
    assert announce(hub, rid).json()["status"] == "no_groq"
    hub.post("/integrations/groq/key", data={"key": KEY})
    assert announce(hub, "nope").json()["status"] == "unknown_room"
    FakeHome.fail = "Home Assistant недоступен"
    assert announce(hub, rid).json() == {"status": "error", "text": "Приходите есть"}
    FakeHome.fail = None
    FakeGroq.fail = "лимит"
    assert announce(hub, rid).json()["status"] == "error"
    hub.post("/home/token/clear")
    FakeGroq.fail = None
    assert announce(hub, rid).json()["status"] == "no_home"


def test_a_sound_that_is_not_a_wav_is_refused(hub):
    connect(hub)
    add_room(hub)
    rid = _rooms(hub)[0]["id"]
    assert announce(hub, rid, data=b"x" * 500).status_code == 422
    assert FakeHome.spoken == []


def test_room_names_are_checked(hub):
    connect(hub)
    assert "Выберите колонку" in add_room(hub, entity="light.x").text
    assert "только русские буквы" in add_room(hub, forms="детская!").text
    add_room(hub, "Детская", "детская")
    assert "уже занято комнатой «Детская»" in add_room(hub, "Дети", "детская", LIVING).text
    assert len(_rooms(hub)) == 1


def test_a_room_can_be_changed_and_removed(hub):
    connect(hub)
    add_room(hub, "Детская", "детская")
    rid = _rooms(hub)[0]["id"]
    hub.post(
        f"/home/rooms/{rid}", data={"title": "Игровая", "forms": "игровая", "entity_id": LIVING}
    )
    assert _rooms(hub)[0]["name"] == "Игровая"
    announce(hub, rid)
    assert FakeHome.spoken[0][0] == LIVING
    hub.post(f"/home/rooms/{rid}/delete")
    assert _rooms(hub) == []


def test_the_connection_is_checked_and_the_token_is_never_shown(hub):
    assert "не подключён" in hub.get("/home").text
    connect(hub)
    page = hub.get("/home").text
    assert HA_TOKEN not in page and "…" + HA_TOKEN[-4:] in page and "Колонка детская" in page
    assert "Работает: колонок Яндекса — 2" in hub.post("/home/check", follow_redirects=True).text
    FakeHome.fail = "Home Assistant не принял токен"
    assert (
        "Не вышло: Home Assistant не принял токен"
        in hub.post("/home/check", follow_redirects=True).text
    )
    assert "Адрес вида" in hub.post("/home/connection", data={"url": "ftp://x", "token": ""}).text
    hub.post("/home/token/clear")
    assert "не подключён" in hub.get("/home").text
