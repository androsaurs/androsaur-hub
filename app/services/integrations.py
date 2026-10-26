import time
from collections.abc import Callable
from typing import Protocol

from app.clients.weather import WeatherError
from app.clients.yandex_weather import YandexWeatherClient
from app.repositories.integrations import IntegrationsRepository
from app.repositories.weather import WeatherRepository
from app.schemas import IntegrationView, WeatherPlace, WeatherReading

YANDEX_WEATHER = "yandex_weather"

# Где проверять соединение, если место погоды в доме ещё не задано.
_MOSCOW = WeatherPlace(name="Москва", latitude=55.7558, longitude=37.6173)


class YandexClient(Protocol):
    def forecast(self, place: WeatherPlace) -> WeatherReading: ...


class _Card:
    def __init__(
        self, id: str, title: str, description: str, builtin: bool, config: dict[str, int]
    ) -> None:
        self.id, self.title, self.description = id, title, description
        self.builtin, self.config = builtin, config


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
        config={"refresh_min": 120},
    ),
    _Card(
        "met_norway",
        "MET Norway",
        "Прогноз норвежского метеоинститута (api.met.no): без ключа, данные CC BY 4.0. Источник "
        "по умолчанию и запасной, если другая погода не ответила. Им же пользуются телефоны "
        "без хаба.",
        builtin=True,
        config={},
    ),
    _Card(
        "osm_nominatim",
        "Поиск места — OpenStreetMap",
        "Находит город для погоды по названию (Nominatim, © участники OpenStreetMap). Без ключа.",
        builtin=True,
        config={},
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
    ) -> None:
        self._repository = repository or IntegrationsRepository()
        self._weather = weather or WeatherRepository()
        self._yandex = yandex
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
        card = self._card(integration_id)
        if not 30 <= minutes <= 1440:
            raise IntegrationError("Обновлять можно раз в 30–1440 минут")
        row = self._repository.get(integration_id)
        self._repository.set_config(
            integration_id, {**card.config, **(row.config if row else {}), "refresh_min": minutes}
        )

    def check(self, integration_id: str) -> tuple[bool, str]:
        """Проверить соединение настоящим запросом. Погода из ответа сразу становится погодой
        дома (если место задано), чтобы не тратить лишний запрос из маленького лимита."""
        self._card(integration_id)
        row = self._repository.get(integration_id)
        if not (row and row.secret):
            raise IntegrationError("Сначала введите ключ")
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
