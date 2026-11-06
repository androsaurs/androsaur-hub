"""«Зюзя, запомни» и «Зюзя, вспомни»: семейная память в сервере памяти (вещи и любые факты).

Голос расшифровывает Groq (звук нигде не хранится). Запись и поиск идут в отдельный стор,
чтобы поиск не видел других записей сервера. Без сервера памяти или Groq телефон отвечает,
что память недоступна.
"""

import logging
import re
import time
from collections.abc import Callable
from typing import Protocol

from app.clients.memory_service import MemoryServiceClient, MemoryServiceError
from app.clients.weather import WeatherError
from app.repositories.integrations import IntegrationsRepository
from app.repositories.things import DEFAULT_STORE, MEMORY_SERVICE, ThingsRepository
from app.schemas import ThingReply
from app.services.integrations import IntegrationsService
from app.things_text import Record, clean, kind_and_item, pick, same_item, words

log = logging.getLogger(__name__)

_STORE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


class ThingsSettingError(Exception):
    """Настройка не принята; текст показывается в админке."""


class MemoryLike(Protocol):
    def records(self) -> list[Record]: ...
    def scores(self, query: str, limit: int = 8) -> dict[str, float]: ...
    def store(self, content: str, item: str, kind: str, author: str, author_id: str) -> str: ...
    def delete(self, content_hash: str) -> None: ...
    def check(self) -> str: ...


class ThingsService:
    def __init__(
        self,
        repository: ThingsRepository | None = None,
        integrations: IntegrationsService | None = None,
        secrets: IntegrationsRepository | None = None,
        client: Callable[[str, str, str], MemoryLike] = MemoryServiceClient,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repository = repository or ThingsRepository()
        self._secrets = secrets or IntegrationsRepository()
        self._integrations = integrations or IntegrationsService(
            repository=self._secrets, clock=clock
        )
        self._client = client
        self._clock = clock

    def settings(self) -> tuple[str, str]:
        return self._repository.settings() or ("", DEFAULT_STORE)

    def key_hint(self) -> str | None:
        row = self._secrets.get(MEMORY_SERVICE)
        return f"…{row.secret[-4:]}" if row and row.secret else None

    def check_state(self) -> tuple[float, bool, str] | None:
        row = self._secrets.get(MEMORY_SERVICE)
        if row is None or row.checked_at is None:
            return None
        return row.checked_at, bool(row.check_ok), row.check_message or ""

    def save_connection(self, url: str, store: str, key: str) -> None:
        url = url.strip().rstrip("/")
        if not re.match(r"^https?://[^\s/]+(:\d+)?(/\S*)?$", url):
            raise ThingsSettingError("Адрес вида http://192.168.1.72:8000")
        store = store.strip() or DEFAULT_STORE
        if not _STORE.match(store):
            raise ThingsSettingError("Стор: латиница, цифры, - и _, до 40 символов")
        key = key.strip()
        if key and (len(key) < 16 or len(key) > 300 or any(c.isspace() for c in key)):
            raise ThingsSettingError("Ключ выглядит не так: вставьте его целиком, без пробелов")
        self._repository.set_settings(url, store)
        if key:
            self._secrets.set_secret(MEMORY_SERVICE, key)
            self._secrets.set_enabled(MEMORY_SERVICE, True)

    def clear_key(self) -> None:
        self._secrets.set_secret(MEMORY_SERVICE, None)
        self._secrets.set_enabled(MEMORY_SERVICE, False)

    def _connected(self) -> MemoryLike | None:
        saved = self._repository.settings()
        row = self._secrets.get(MEMORY_SERVICE)
        if not (saved and row and row.enabled and row.secret):
            return None
        return self._client(saved[0], row.secret, saved[1])

    def check(self) -> tuple[bool, str]:
        client = self._connected()
        if client is None:
            raise ThingsSettingError("Сначала укажите адрес и ключ")
        try:
            message, ok = client.check(), True
        except MemoryServiceError as e:
            message, ok = f"Не вышло: {e}", False
        self._secrets.set_check(MEMORY_SERVICE, ok, message, self._clock())
        return ok, message

    def records(self) -> tuple[list[Record], str | None]:
        """Записи для админки (новые первыми) и причина, если их не получили."""
        client = self._connected()
        if client is None:
            return [], None
        try:
            return sorted(client.records(), key=lambda r: -r.created), None
        except MemoryServiceError as e:
            return [], str(e)

    def delete(self, content_hash: str) -> None:
        client = self._connected()
        if client is None:
            raise ThingsSettingError("Сначала укажите адрес и ключ")
        try:
            client.delete(content_hash)
        except MemoryServiceError as e:
            raise ThingsSettingError(str(e)) from None

    def _heard(self, wav: bytes) -> tuple[str | None, ThingReply | None]:
        """Расшифровка голоса или готовый ответ с причиной, почему её нет."""
        groq = self._integrations.groq()
        if groq is None:
            return None, ThingReply(status="no_groq")
        key, min_confidence, _ = groq
        try:
            result = self._integrations.groq_client(key).transcribe(wav)
        except WeatherError as e:
            log.warning("things transcription failed: %s", e)
            return None, ThingReply(status="error")
        text = " ".join(result.text.split())
        if not text or result.confidence < min_confidence or result.speech < 50:
            log.info(
                "things unclear: chars=%d confidence=%.2f speech=%s",
                len(text),
                result.confidence,
                result.speech,
            )
            return None, ThingReply(status="unclear", text=text or None)
        return text, None

    def remember(self, wav: bytes, person_id: str, label: str) -> ThingReply:
        client = self._connected()
        if client is None:
            return ThingReply(status="no_memory")
        heard, problem = self._heard(wav)
        if problem is not None or heard is None:
            return problem or ThingReply(status="error")
        text = clean(heard)
        kind, item = kind_and_item(text)
        if len(words(text)) < 2:
            # Одно слово («батарейки») нечего хранить: потом не ответить, что с ним.
            return ThingReply(status="unclear", text=heard)
        try:
            existing = client.records()
            if any(words(r.content) == words(text) for r in existing):
                return ThingReply(status="saved", text=text)
            # Предмет с новым местом заменяет старую запись о нём; факты только копятся.
            same = [r for r in existing if kind == "location" and same_item(item, r.item)]
            client.store(text, " ".join(item), kind, label.strip(), person_id.strip())
            for old in same:
                client.delete(old.content_hash)
        except MemoryServiceError as e:
            log.warning("things remember failed: %s", e)
            return ThingReply(status="error")
        log.info("things remembered: kind=%s chars=%d replaced=%d", kind, len(text), len(same))
        if same:
            return ThingReply(status="updated", text=text, previous=same[0].content)
        return ThingReply(status="saved", text=text)

    def find(self, wav: bytes) -> ThingReply:
        client = self._connected()
        if client is None:
            return ThingReply(status="no_memory")
        heard, problem = self._heard(wav)
        if problem is not None or heard is None:
            return problem or ThingReply(status="error")
        query = clean(heard)
        try:
            records = client.records()
            scores = client.scores(query)
        except MemoryServiceError as e:
            log.warning("things find failed: %s", e)
            return ThingReply(status="error")
        for record in records:
            record.score = scores.get(record.content_hash, 0.0)
        chosen, more = pick(query, records)
        log.info("things found: records=%d chosen=%d more=%d", len(records), len(chosen), more)
        if not chosen:
            return ThingReply(status="not_found", text=query)
        answer = ". ".join(r.content for r in chosen)
        if more:
            answer += f". Есть ещё записи: {more}"
        return ThingReply(status="found", text=answer)
