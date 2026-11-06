import logging
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from app.clients.home_assistant import HomeAssistantClient, HomeError, Speaker
from app.clients.weather import WeatherError
from app.repositories.home import HOME_ASSISTANT, HomeRepository, RoomRow
from app.repositories.integrations import IntegrationsRepository
from app.repositories.people import PeopleRepository
from app.schemas import AnnounceReply, RoomWords
from app.services.integrations import IntegrationsService

log = logging.getLogger(__name__)

_MAX_TEXT = 400
_FORM = re.compile(r"^[а-яa-z0-9-]+( [а-яa-z0-9-]+){0,2}$")


class HomeClient(Protocol):
    def check(self) -> str: ...

    def speakers(self) -> list[Speaker]: ...

    def speak(self, entity_id: str, text: str) -> None: ...


class HomeSettingError(Exception):
    """Настройку нельзя принять; текст показывается в админке."""


def normalize(raw: str) -> str:
    return " ".join(raw.lower().replace("ё", "е").split())


def parse_forms(raw: str, title: str) -> list[str]:
    """Как называют комнату голосом: через запятую; пусто — по названию."""
    forms = [normalize(p) for p in raw.split(",")] if raw.strip() else [normalize(title)]
    forms = list(dict.fromkeys(f for f in forms if f))
    if not forms or len(forms) > 8:
        raise HomeSettingError("Голосовых названий должно быть от одного до восьми")
    for form in forms:
        if not _FORM.match(form):
            raise HomeSettingError(
                f"«{form}»: только русские буквы, до трёх слов, названия через запятую"
            )
    return forms


EVERY_ROOM = "all"


class HomeService:
    """Умный дом: Home Assistant и комнаты, куда можно «передать» голосом.

    Звук сообщения не сохраняется: он расшифровывается и сразу произносится на колонке.
    """

    def __init__(
        self,
        repository: HomeRepository | None = None,
        integrations: IntegrationsService | None = None,
        secrets: IntegrationsRepository | None = None,
        people: PeopleRepository | None = None,
        client: Callable[[str, str], HomeClient] = HomeAssistantClient,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repository = repository or HomeRepository()
        self._secrets = secrets or IntegrationsRepository()
        self._integrations = integrations or IntegrationsService(
            repository=self._secrets, clock=clock
        )
        self._people = people or PeopleRepository()
        self._client = client
        self._clock = clock

    def url(self) -> str | None:
        return self._repository.url()

    def key_hint(self) -> str | None:
        row = self._secrets.get(HOME_ASSISTANT)
        return f"…{row.secret[-4:]}" if row and row.secret else None

    def check_state(self) -> tuple[float, bool, str] | None:
        row = self._secrets.get(HOME_ASSISTANT)
        if row is None or row.checked_at is None:
            return None
        return row.checked_at, bool(row.check_ok), row.check_message or ""

    def save_connection(self, url: str, token: str) -> None:
        url = url.strip().rstrip("/")
        if not re.match(r"^https?://[^\s/]+(:\d+)?(/\S*)?$", url):
            raise HomeSettingError("Адрес вида http://192.168.1.71:8123")
        token = token.strip()
        if token and (len(token) < 20 or len(token) > 600 or any(c.isspace() for c in token)):
            raise HomeSettingError("Токен выглядит не так: вставьте его целиком, без пробелов")
        self._repository.set_url(url)
        if token:
            self._secrets.set_secret(HOME_ASSISTANT, token)
            self._secrets.set_enabled(HOME_ASSISTANT, True)

    def clear_token(self) -> None:
        self._secrets.set_secret(HOME_ASSISTANT, None)
        self._secrets.set_enabled(HOME_ASSISTANT, False)

    def _connected(self) -> HomeClient | None:
        url = self._repository.url()
        row = self._secrets.get(HOME_ASSISTANT)
        if not (url and row and row.enabled and row.secret):
            return None
        return self._client(url, row.secret)

    def check(self) -> tuple[bool, str]:
        client = self._connected()
        if client is None:
            raise HomeSettingError("Сначала укажите адрес и токен")
        try:
            message, ok = client.check(), True
        except HomeError as e:
            message, ok = f"Не вышло: {e}", False
        self._secrets.set_check(HOME_ASSISTANT, ok, message, self._clock())
        return ok, message

    def speakers(self) -> tuple[list[Speaker], str | None]:
        """Колонки из Home Assistant для выбора в админке и причина, если списка нет."""
        client = self._connected()
        if client is None:
            return [], None
        try:
            return client.speakers(), None
        except HomeError as e:
            return [], str(e)

    def rooms(self) -> list[RoomRow]:
        return self._repository.rooms()

    def _checked(
        self, title: str, forms: str, entity_id: str, room_id: str | None
    ) -> tuple[str, list[str], str]:
        title = " ".join(title.split())
        if not 1 <= len(title) <= 40:
            raise HomeSettingError("Название комнаты — от 1 до 40 символов")
        entity_id = entity_id.strip()
        if not entity_id.startswith("media_player."):
            raise HomeSettingError("Выберите колонку")
        parsed = parse_forms(forms, title)
        for other in self._repository.rooms():
            if other.id != room_id and set(other.forms) & set(parsed):
                raise HomeSettingError(f"Название уже занято комнатой «{other.title}»")
        return title, parsed, entity_id

    def add_room(self, title: str, forms: str, entity_id: str) -> None:
        title, parsed, entity_id = self._checked(title, forms, entity_id, None)
        self._repository.add_room(title, parsed, entity_id)

    def update_room(self, room_id: str, title: str, forms: str, entity_id: str) -> None:
        title, parsed, entity_id = self._checked(title, forms, entity_id, room_id)
        if not self._repository.update_room(room_id, title, parsed, entity_id):
            raise HomeSettingError("Такой комнаты нет")

    def delete_room(self, room_id: str) -> None:
        self._repository.delete_room(room_id)

    def rooms_for_phone(self) -> list[RoomWords]:
        return [RoomWords(id=r.id, name=r.title, words=r.forms) for r in self._repository.rooms()]

    def _sender(self, person_id: str, label: str) -> str:
        """«Папа» (первая роль), если человека знают по ID; иначе имя, как его назвал телефон."""
        if person_id:
            for roles in self._people.roles():
                if roles.person == person_id and roles.words:
                    return roles.words[0].strip().capitalize()
        return label.strip()

    def announce(self, room_id: str, wav: bytes, person_id: str, label: str) -> AnnounceReply:
        """Расшифровать сказанное и произнести на колонке комнаты («Папа говорит: …»)."""
        everywhere = room_id == EVERY_ROOM
        room = None if everywhere else self._repository.room(room_id)
        if room is None and not everywhere:
            return AnnounceReply(status="unknown_room")
        client = self._connected()
        if client is None:
            return AnnounceReply(status="no_home")
        groq = self._integrations.groq()
        if groq is None:
            return AnnounceReply(status="no_groq")
        key, min_confidence, _ = groq
        try:
            result = self._integrations.groq_client(key).transcribe(wav)
        except WeatherError as e:
            log.warning("announce transcription failed: %s", e)
            return AnnounceReply(status="error")
        text = " ".join(result.text.split())[:_MAX_TEXT]
        if not text or result.confidence < min_confidence or result.speech < 50:
            log.info(
                "announce unclear: chars=%d confidence=%.2f speech=%s",
                len(text),
                result.confidence,
                result.speech,
            )
            return AnnounceReply(status="unclear", text=text or None)
        sender = self._sender(person_id, label)
        phrase = f"{sender} говорит: {text}" if sender else f"Сообщение: {text}"
        targets = (
            [r.entity_id for r in self._repository.rooms()] if room is None else [room.entity_id]
        )
        if not targets:
            return AnnounceReply(status="unknown_room")
        # All at once: a speaker that does not answer must not make the others wait out its timeout.

        def speak(entity_id: str) -> bool:
            try:
                client.speak(entity_id, phrase)
            except HomeError as e:
                log.warning("announce speak failed: %s", e)
                return False
            return True

        with ThreadPoolExecutor(max_workers=len(targets)) as pool:
            failed = list(pool.map(speak, targets)).count(False)
        if failed == len(targets):
            return AnnounceReply(status="error", text=text)
        log.info(
            "announce spoken: room=%s speakers=%d failed=%d chars=%d",
            room_id,
            len(targets),
            failed,
            len(text),
        )
        return AnnounceReply(status="spoken", text=text)
