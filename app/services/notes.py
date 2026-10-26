import logging
import time
from collections.abc import Callable

from app.clients.weather import WeatherError
from app.config import get_settings
from app.repositories.notes import NotesRepository
from app.schemas import HubNote, NoteText
from app.services.integrations import IntegrationsService

log = logging.getLogger(__name__)

# Столько раз пробовать расшифровать записку, прежде чем сдаться (лимит, сбой сети).
_ATTEMPTS = 3


class NotesService:
    """Голосовые записки в хабе: приём звука с телефона, расшифровка Groq и тексты обратно.

    Телефон автономен: записка у него всегда есть звуком; текст из хаба — дополнение, с ним
    Петя читает записку голосом синтезатора. Звук и текст — слова семьи: не в логах.
    """

    def __init__(
        self,
        repository: NotesRepository | None = None,
        integrations: IntegrationsService | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repository = repository or NotesRepository()
        self._integrations = integrations or IntegrationsService(clock=clock)
        self._clock = clock
        self._attempts: dict[str, int] = {}

    def receive(self, note: HubNote, wav: bytes) -> bool:
        if not wav.startswith(b"RIFF") or len(wav) < 100:
            raise ValueError("не WAV")
        return self._repository.add(note, wav, self._clock())

    def transcribe_pending(self) -> int:
        """Расшифровать ждущие записки, если интеграция Groq включена. Возвращает, сколько."""
        groq = self._integrations.groq()
        if groq is None:
            return 0
        key, min_confidence, keep_audio = groq
        client = self._integrations.groq_client(key)
        done = 0
        for note in self._repository.pending():
            path = self._repository.audio_path(note.id)
            if not path.exists():
                self._repository.fail(note.id, "нет звука", final=True)
                continue
            try:
                result = client.transcribe(path.read_bytes())
            except WeatherError as e:
                tries = self._attempts.get(note.id, 0) + 1
                self._attempts[note.id] = tries
                self._repository.fail(note.id, str(e), final=tries >= _ATTEMPTS)
                log.warning("note transcription failed (try %d): %s", tries, e)
                continue
            usable = (
                bool(result.text) and result.confidence >= min_confidence and result.speech >= 50
            )
            self._repository.finish(
                note.id, result.text, result.confidence, result.speech, usable, self._clock()
            )
            if not keep_audio:
                self._repository.drop_audio(note.id)
            done += 1
        return done

    def texts_for(self, device_id: str) -> list[NoteText]:
        since = int((self._clock() - get_settings().notes_keep_days * 86400) * 1000)
        return self._repository.texts_for(device_id, since)

    def recent(self) -> list[HubNote]:
        return self._repository.recent()

    def audio(self, note_id: str) -> bytes | None:
        path = self._repository.audio_path(note_id)
        return path.read_bytes() if path.exists() else None

    def cleanup(self) -> int:
        before = int((self._clock() - get_settings().notes_keep_days * 86400) * 1000)
        return self._repository.forget_older(before)
