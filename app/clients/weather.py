"""Внешние сервисы погоды.

- Поиск места по названию — Nominatim (OpenStreetMap, без ключа, не чаще раза в секунду,
  нужен User-Agent; данные © участники OpenStreetMap, ODbL). Поиск Open-Meteo не находит
  многие русские названия («Мурманск»), поэтому не он.
- Прогноз — MET Norway Locationforecast 2.0 (без ключа, нужен User-Agent с контактом, данные
  CC BY 4.0). Прогноз Open-Meteo из домашней сети владельца недоступен, поэтому не он.
"""

import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

import httpx

from app.schemas import WeatherPlace, WeatherReading

USER_AGENT = "androsaur-hub/0.1 (+https://github.com/androsaurs/androsaur-hub)"
GEOCODE_URL = "https://nominatim.openstreetmap.org/search"
FORECAST_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"


class WeatherError(Exception):
    """Сервис погоды не ответил или ответил не то."""


class WeatherClient(Protocol):
    def find(self, name: str) -> WeatherPlace | None: ...

    def forecast(self, place: WeatherPlace) -> tuple[WeatherReading, float]:
        """Погода и момент (Unix, с), раньше которого MET просит не спрашивать снова."""
        ...


def place_from_nominatim(r: dict[str, Any]) -> WeatherPlace:
    """Место из ответа Nominatim: «Мурманск, Мурманская область, Россия»."""
    address = r.get("address") or {}
    parts = [r.get("name") or r.get("display_name", "").split(",")[0]]
    parts += [address.get("state"), address.get("country")]
    names: list[str] = []
    for part in parts:
        if part and part not in names:
            names.append(part)
    return WeatherPlace(name=", ".join(names), latitude=float(r["lat"]), longitude=float(r["lon"]))


def reading_from_met(place: str, data: dict[str, Any], now: float) -> WeatherReading:
    """Погода из ответа MET: температура, ветер и значок ближайшего часа,
    наименьшая и наибольшая температура за 12 часов."""
    series = data["properties"]["timeseries"]
    if not series:
        raise WeatherError("пустой прогноз")
    first = series[0]
    details = first["data"]["instant"]["details"]
    later = first["data"].get("next_1_hours") or first["data"].get("next_6_hours") or {}
    symbol = later.get("summary", {}).get("symbol_code", "")
    temps = [s["data"]["instant"]["details"].get("air_temperature") for s in series[:12]]
    temps = [t for t in temps if t is not None]
    when = datetime.fromisoformat(first["time"].replace("Z", "+00:00")).timestamp()
    return WeatherReading(
        place=place,
        temperature=details["air_temperature"],
        symbol=symbol,
        wind=details.get("wind_speed"),
        min=min(temps) if temps else None,
        max=max(temps) if temps else None,
        time_ms=int(when * 1000),
        fetched_at_ms=int(now * 1000),
    )


class HttpWeatherClient:
    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout

    def find(self, name: str) -> WeatherPlace | None:
        try:
            reply = httpx.get(
                GEOCODE_URL,
                params={
                    "q": name,
                    "format": "jsonv2",
                    "limit": 1,
                    "addressdetails": 1,
                    "accept-language": "ru",
                },
                headers={"User-Agent": USER_AGENT},
                timeout=self._timeout,
            )
            reply.raise_for_status()
        except httpx.HTTPError as e:
            raise WeatherError(f"поиск места не ответил: {type(e).__name__}") from None
        results = reply.json()
        return place_from_nominatim(results[0]) if results else None

    def forecast(self, place: WeatherPlace) -> tuple[WeatherReading, float]:
        try:
            reply = httpx.get(
                FORECAST_URL,
                # MET просит не больше 4 знаков после запятой.
                params={"lat": round(place.latitude, 4), "lon": round(place.longitude, 4)},
                headers={"User-Agent": USER_AGENT},
                timeout=self._timeout,
            )
            reply.raise_for_status()
        except httpx.HTTPError as e:
            raise WeatherError(f"прогноз не ответил: {type(e).__name__}") from None
        now = time.time()
        return reading_from_met(place.name.split(",")[0], reply.json(), now), _expires(reply, now)


def _expires(reply: httpx.Response, now: float) -> float:
    """До какого момента MET просит не спрашивать снова (заголовок ``Expires``)."""
    try:
        return parsedate_to_datetime(reply.headers["expires"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return now
