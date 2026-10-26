import time
from collections.abc import Callable
from typing import Protocol

from app.clients.groq import GroqClient, Transcript
from app.clients.weather import WeatherError
from app.clients.yandex_weather import YandexWeatherClient
from app.repositories.integrations import IntegrationsRepository
from app.repositories.weather import WeatherRepository
from app.schemas import ConfigFieldView, IntegrationView, WeatherPlace, WeatherReading

YANDEX_WEATHER = "yandex_weather"
GROQ = "groq"

# Где проверять соединение, если место погоды в доме ещё не задано.
_MOSCOW = WeatherPlace(name="Москва", latitude=55.7558, longitude=37.6173)


class YandexClient(Protocol):
    def forecast(self, place: WeatherPlace) -> WeatherReading: ...


class GroqClientLike(Protocol):
    def check(self) -> str: ...

    def transcribe(self, wav: bytes) -> Transcript: ...


class _Field:
    """Настройка карточки: ключ, подпись, единица и пределы (целое число)."""

    def __init__(self, key: str, label: str, unit: str, low: int, high: int, default: int) -> None:
        self.key, self.label, self.unit = key, label, unit
        self.low, self.high, self.default = low, high, default


class _Card:
    def __init__(
        self, id: str, title: str, description: str, builtin: bool, fields: list[_Field]
    ) -> None:
        self.id, self.title, self.description = id, title, description
        self.builtin, self.fields = builtin, fields
        self.config = {f.key: f.default for f in fields}


CATALOG = [
    _Card(
        YANDEX_WEATHER,
        "Яндекс Погода",
        "Погода для дома от Яндекса: сейчас, по часам и на завтра. Бесплатный тариф «для умного "
        "дома» — только некоммерческое использование, около 30 запросов в сутки, поэтому хаб "
        "спрашивает редко и раздаёт погоду телефонам. Ключ — в кабинете на "
        "yandex.ru/pogoda/b2b/smarthome. Пока интеграция включена, погода берётся у Яндекса; "
        "если он не ответил — у MET Norway. Телефоны без хаба по-прежнему берут погоду сами.",
        builtin=False,
        fields=[_Field("refresh_min", "Спрашивать раз в", "мин", 30, 1440, 120)],
    ),
    _Card(
        GROQ,
        "Groq — расшифровка записок",
        "Голосовые записки расшифровываются в текст (Whisper large-v3 turbo, русский), и Петя "
        "доставляет их голосом синтезатора: «Тебе сообщение от мамы: …». Сомнительная "
        "расшифровка (уверенность ниже порога) не читается — играет сама запись. «Повтори "
        "записку» всегда играет оригинал. Звук записок уходит в Groq; текст хранится в хабе и "
        "на телефоне. Без хаба или без интеграции записки, как раньше, играются записью. Ключ — "
        "console.groq.com/keys.",
        builtin=False,
        fields=[
            _Field("min_confidence", "Читать текстом при уверенности от", "%", 0, 100, 55),
            _Field(
                "keep_audio",
                "Отладка: хранить звук после расшифровки (1 — да, 0 — нет)",
                "",
                0,
                1,
                1,
            ),
        ],
    ),
    _Card(
        "met_norway",
        "MET Norway",
        "Прогноз норвежского метеоинститута (api.met.no): без ключа, данные CC BY 4.0. Источник "
        "по умолчанию и запасной, если другая погода не ответила. Им же пользуются телефоны "
        "без хаба.",
        builtin=True,
        fields=[],
    ),
    _Card(
        "osm_nominatim",
        "Поиск места — OpenStreetMap",
        "Находит город для погоды по названию (Nominatim, © участники OpenStreetMap). Без ключа.",
        builtin=True,
        fields=[],
    ),
]


class IntegrationError(Exception):
    """Действие с интеграцией нельзя принять; текст показывается в админке."""


class IntegrationsService:
    """Раздел «Интеграции»: внешние сервисы, которые хаб использует для всего дома."""

    def __init__(
        self,
        repository: IntegrationsRepository | None = None,
        weather: WeatherRepository | None = None,
        yandex: Callable[[str], YandexClient] = YandexWeatherClient,
        clock: Callable[[], float] = time.time,
        groq: Callable[[str], GroqClientLike] = GroqClient,
    ) -> None:
        self._repository = repository or IntegrationsRepository()
        self._weather = weather or WeatherRepository()
        self._yandex = yandex
        self._groq = groq
        self._clock = clock

    def all(self) -> list[IntegrationView]:
        views = []
        for card in CATALOG:
            row = self._repository.get(card.id)
            secret = row.secret if row else None
            views.append(
                IntegrationView(
                    id=card.id,
                    title=card.title,
                    description=card.description,
                    builtin=card.builtin,
                    enabled=card.builtin or bool(row and row.enabled),
                    key_hint=f"…{secret[-4:]}" if secret else None,
                    config={**card.config, **(row.config if row else {})},
                    fields=[
                        ConfigFieldView(
                            key=f.key, label=f.label, unit=f.unit, low=f.low, high=f.high
                        )
                        for f in card.fields
                    ],
                    checked_at=row.checked_at if row else None,
                    check_ok=row.check_ok if row else None,
                    check_message=row.check_message if row else None,
                )
            )
        return views

    def _card(self, integration_id: str) -> _Card:
        card = next((c for c in CATALOG if c.id == integration_id), None)
        if card is None or card.builtin:
            raise IntegrationError("Такой интеграции нет")
        return card

    def save_key(self, integration_id: str, key: str) -> None:
        self._card(integration_id)
        key = key.strip()
        if not 8 <= len(key) <= 200 or any(c.isspace() for c in key):
            raise IntegrationError("Ключ выглядит не так: вставьте его целиком, без пробелов")
        self._repository.set_secret(integration_id, key)
        self._repository.set_enabled(integration_id, True)

    def clear_key(self, integration_id: str) -> None:
        self._card(integration_id)
        self._repository.set_secret(integration_id, None)
        self._repository.set_enabled(integration_id, False)

    def set_enabled(self, integration_id: str, enabled: bool) -> None:
        self._card(integration_id)
        row = self._repository.get(integration_id)
        if enabled and not (row and row.secret):
            raise IntegrationError("Сначала введите ключ")
        self._repository.set_enabled(integration_id, enabled)

    def set_refresh(self, integration_id: str, minutes: int) -> None:
        self.set_config(integration_id, "refresh_min", minutes)

    def set_config(self, integration_id: str, key: str, value: int) -> None:
        card = self._card(integration_id)
        field = next((f for f in card.fields if f.key == key), None)
        if field is None:
            raise IntegrationError("Такой настройки нет")
        if not field.low <= value <= field.high:
            raise IntegrationError(f"«{field.label}»: от {field.low} до {field.high}")
        row = self._repository.get(integration_id)
        self._repository.set_config(
            integration_id, {**card.config, **(row.config if row else {}), key: value}
        )

    def groq(self) -> tuple[str, int, bool] | None:
        """Ключ Groq, порог уверенности (%) и хранить ли звук, если интеграция включена."""
        row = self._repository.get(GROQ)
        if not (row and row.enabled and row.secret):
            return None
        card = self._card(GROQ)
        config = {**card.config, **row.config}
        return row.secret, int(config["min_confidence"]), bool(config["keep_audio"])

    def groq_client(self, key: str) -> GroqClientLike:
        return self._groq(key)

    def check(self, integration_id: str) -> tuple[bool, str]:
        """Проверить соединение настоящим запросом. Погода из ответа сразу становится погодой
        дома (если место задано), чтобы не тратить лишний запрос из маленького лимита."""
        self._card(integration_id)
        row = self._repository.get(integration_id)
        if not (row and row.secret):
            raise IntegrationError("Сначала введите ключ")
        if integration_id == GROQ:
            try:
                message = f"Работает: {self._groq(row.secret).check()}"
                ok = True
            except WeatherError as e:
                message, ok = f"Не вышло: {e}", False
            self._repository.set_check(integration_id, ok, message, self._clock())
            return ok, message
        stored = self._weather.get()
        place = stored[0] if stored else _MOSCOW
        now = self._clock()
        try:
            reading = self._yandex(row.secret).forecast(place)
        except WeatherError as e:
            message = f"Не вышло: {e}"
            self._repository.set_check(integration_id, False, message, now)
            return False, message
        if stored:
            settings = self.yandex()
            refresh_s = settings[1] if settings else 7200
            self._weather.set_reading(
                reading.model_copy(update={"valid_until_ms": int((now + refresh_s + 900) * 1000)}),
                now,
                now + refresh_s,
            )
        from app.services.weather import describe

        message = (
            f"Работает: {place.name.split(',')[0]} — {round(reading.temperature):+d}°, "
            f"{describe(reading.symbol)}"
        )
        self._repository.set_check(integration_id, True, message, now)
        return True, message

    def yandex(self) -> tuple[str, int] | None:
        """Ключ Яндекс Погоды и как часто её спрашивать (с), если интеграция включена."""
        row = self._repository.get(YANDEX_WEATHER)
        if not (row and row.enabled and row.secret):
            return None
        minutes = row.config.get("refresh_min", 120)
        return row.secret, int(minutes) * 60

    def yandex_client(self, key: str) -> YandexClient:
        return self._yandex(key)
