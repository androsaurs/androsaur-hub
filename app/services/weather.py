import logging
import time
from collections.abc import Callable

from app.clients.weather import HttpWeatherClient, WeatherClient, WeatherError
from app.config import get_settings
from app.repositories.weather import WeatherRepository
from app.schemas import WeatherPlace, WeatherReading
from app.services.integrations import IntegrationsService

log = logging.getLogger(__name__)

# Описание кода погоды MET Norway словами — то же, что говорит телефон.
_BASE = {
    "clearsky": "ясно",
    "fair": "малооблачно",
    "partlycloudy": "переменная облачность",
    "cloudy": "облачно",
    "fog": "туман",
    "thunder": "гроза",
}
_PRECIP = {"rain": "дождь", "sleet": "мокрый снег", "snow": "снег", "hail": "град"}


def describe(symbol: str) -> str:
    code = symbol.split("_")[0]
    if code in _BASE:
        return _BASE[code]
    thunder = "thunder" in code
    code = code.replace("andthunder", "")
    intensity = ""
    for prefix, word in (("lights", "небольшой "), ("light", "небольшой "), ("heavy", "сильный ")):
        if code.startswith(prefix):
            intensity, code = word, code[len(prefix) :]
            break
    showers = code.endswith("showers")
    base = _PRECIP.get(code.removesuffix("showers"), "осадки")
    text = intensity + ("кратковременный " if showers and base != "мокрый снег" else "") + base
    return text + (", гроза" if thunder else "")


class WeatherService:
    """Погода дома: место задаётся в админке, прогноз обновляется в фоне и раздаётся телефонам.

    Телефон автономен: без хаба он берёт погоду сам. Хаб лишь избавляет телефоны от лишних
    запросов и задаёт место для всего дома.
    """

    def __init__(
        self,
        repository: WeatherRepository | None = None,
        client: WeatherClient | None = None,
        clock: Callable[[], float] = time.time,
        integrations: IntegrationsService | None = None,
    ) -> None:
        self._repository = repository or WeatherRepository()
        self._client = client or HttpWeatherClient()
        self._clock = clock
        self._integrations = integrations or IntegrationsService(clock=clock)

    def current(self) -> tuple[WeatherPlace | None, WeatherReading | None]:
        stored = self._repository.get()
        if stored is None:
            return None, None
        place, reading, _ = stored
        return place, reading

    def set_place(self, name: str) -> WeatherPlace:
        name = " ".join(name.split())
        if not 1 <= len(name) <= 100:
            raise WeatherError("Напишите город")
        place = self._client.find(name)
        if place is None:
            raise WeatherError(f"Не нашёл «{name}»")
        self._repository.set_place(place)
        self.refresh(force=True)
        return place

    def clear(self) -> None:
        self._repository.clear()

    def refresh(self, force: bool = False) -> bool:
        """Обновить прогноз, если пора: не раньше срока MET и не чаще настройки.

        True — обновлён."""
        stored = self._repository.get()
        if stored is None:
            return False
        place, reading, expires_at = stored
        now = self._clock()
        yandex = self._integrations.yandex()
        interval = yandex[1] if yandex else get_settings().weather_refresh_s
        due = reading is None or now - reading.fetched_at_ms / 1000 >= interval
        if not force and (not due or now < expires_at):
            return False
        fresh: WeatherReading | None = None
        expires = now
        if yandex:
            try:
                fresh = self._integrations.yandex_client(yandex[0]).forecast(place)
            except WeatherError as e:
                # Ключ и ответ Яндекса в лог не пишем — только что случилось.
                log.warning("yandex weather failed, falling back to MET: %s", e)
        if fresh is None:
            try:
                fresh, expires = self._client.forecast(place)
            except WeatherError as e:
                log.warning("weather refresh failed: %s", e)
                return False
        # Телефоны считают погоду хаба свежей до следующего обновления (с запасом), а не час.
        fresh = fresh.model_copy(update={"valid_until_ms": int((now + interval + 900) * 1000)})
        self._repository.set_reading(fresh, now, max(expires, now + min(interval, 1800)))
        return True
