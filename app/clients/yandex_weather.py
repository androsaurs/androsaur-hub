"""Яндекс Погода (тариф «для умного дома»: бесплатно, только некоммерчески, сегодня и завтра).

GraphQL на ``api.weather.yandex.ru/graphql/query``, ключ в заголовке ``X-Yandex-Weather-Key``.
Ключ — учётные данные: не печатать и не логировать. Лимиты тарифа маленькие (по соглашению около
30 запросов в сутки), поэтому хаб спрашивает редко и раздаёт результат телефонам.
"""

import time
from datetime import datetime, timedelta, tzinfo
from typing import Any

import httpx

from app.clients.weather import WeatherError
from app.schemas import HourPoint, WeatherPlace, WeatherReading

URL = "https://api.weather.yandex.ru/graphql/query"

QUERY = """
query ($lat: Float!, $lon: Float!) {
  weatherByPoint(request: {lat: $lat, lon: $lon}) {
    now { temperature condition windSpeed }
    forecast { days(limit: 2) { time hours {
      time temperature feelsLike condition windSpeed windGust precProbability
    } } }
  }
}
"""

# Явления Яндекса — в коды MET, которые уже понимают хаб и телефон («hail» и «thunder» — свои).
CONDITIONS = {
    "CLEAR": "clearsky",
    "PARTLY_CLOUDY": "partlycloudy",
    "CLOUDY": "cloudy",
    "OVERCAST": "cloudy",
    "LIGHT_RAIN": "lightrain",
    "RAIN": "rain",
    "HEAVY_RAIN": "heavyrain",
    "SHOWERS": "rainshowers",
    "SLEET": "sleet",
    "LIGHT_SNOW": "lightsnow",
    "SNOW": "snow",
    "SNOWFALL": "heavysnow",
    "HAIL": "hail",
    "THUNDERSTORM": "thunder",
    "THUNDERSTORM_WITH_RAIN": "rainandthunder",
    "THUNDERSTORM_WITH_HAIL": "hailandthunder",
}


def symbol(condition: str | None) -> str:
    return CONDITIONS.get(condition or "", "cloudy")


def _wet(code: str) -> bool:
    return any(w in code for w in ("rain", "sleet", "snow", "hail"))


def reading_from_yandex(
    place: str, data: dict[str, Any], now: float, zone: tzinfo | None = None
) -> WeatherReading:
    """Погода из ответа Яндекса: сейчас, мин. и макс. за 12 часов, ближайшие осадки, завтра."""
    if data.get("errors"):
        raise WeatherError(str(data["errors"][0].get("message", "ошибка запроса"))[:200])
    weather = (data.get("data") or {}).get("weatherByPoint")
    if not weather or not weather.get("now"):
        raise WeatherError("в ответе нет погоды")
    current = weather["now"]
    hours = []
    points = []
    for day in (weather.get("forecast") or {}).get("days") or []:
        for hour in day.get("hours") or []:
            at = datetime.fromisoformat(hour["time"])
            hours.append((at, hour.get("temperature"), symbol(hour.get("condition"))))
            if hour.get("temperature") is not None:
                points.append(
                    HourPoint(
                        time_ms=int(at.timestamp() * 1000),
                        temperature=hour["temperature"],
                        feels_like=hour.get("feelsLike"),
                        symbol=symbol(hour.get("condition")),
                        wind=hour.get("windSpeed"),
                        gust=hour.get("windGust"),
                        precip_prob=hour.get("precProbability"),
                    )
                )
    hours.sort(key=lambda h: h[0])
    moment = datetime.fromtimestamp(now).astimezone(zone)
    # Для графика — от начала текущего часа до конца завтра (Яндекс отдаёт сегодня и завтра).
    start = (now - now % 3600) * 1000
    points = sorted(
        (p for p in points if start <= p.time_ms < start + 48 * 3600 * 1000),
        key=lambda p: p.time_ms,
    )
    next12 = [
        h for h in hours if moment - timedelta(hours=1) < h[0] <= moment + timedelta(hours=12)
    ]
    temps = [h[1] for h in next12 if h[1] is not None]
    now_symbol = symbol(current.get("condition"))
    later = None
    if not _wet(now_symbol):
        later = next((h for h in next12 if h[0] > moment and _wet(h[2])), None)
    tomorrow = (moment + timedelta(days=1)).date()
    tomorrow_hours = [h for h in hours if h[0].astimezone(moment.tzinfo).date() == tomorrow]
    tomorrow_temps = [h[1] for h in tomorrow_hours if h[1] is not None]
    noon = min(
        tomorrow_hours,
        key=lambda h: abs(h[0].astimezone(moment.tzinfo).hour - 12),
        default=None,
    )
    return WeatherReading(
        place=place,
        temperature=float(current["temperature"]),
        symbol=now_symbol,
        wind=current.get("windSpeed"),
        min=min(temps) if temps else None,
        max=max(temps) if temps else None,
        time_ms=int(now * 1000),
        fetched_at_ms=int(now * 1000),
        later_symbol=later[2] if later else None,
        later_time_ms=int(later[0].timestamp() * 1000) if later else None,
        tomorrow_min=min(tomorrow_temps) if tomorrow_temps else None,
        tomorrow_max=max(tomorrow_temps) if tomorrow_temps else None,
        tomorrow_symbol=noon[2] if noon else None,
        source="Яндекс Погода",
        hours=points,
    )


class YandexWeatherClient:
    def __init__(self, key: str, timeout: float = 10.0) -> None:
        self._key = key
        self._timeout = timeout

    def forecast(self, place: WeatherPlace) -> WeatherReading:
        try:
            reply = httpx.post(
                URL,
                json={
                    "query": QUERY,
                    "variables": {
                        "lat": round(place.latitude, 4),
                        "lon": round(place.longitude, 4),
                    },
                },
                headers={"X-Yandex-Weather-Key": self._key},
                timeout=self._timeout,
            )
        except httpx.HTTPError as e:
            raise WeatherError(f"Яндекс не ответил: {type(e).__name__}") from None
        if reply.status_code in (401, 403):
            raise WeatherError(f"ключ не принят или исчерпан лимит запросов ({reply.status_code})")
        if reply.status_code != 200:
            raise WeatherError(f"Яндекс ответил {reply.status_code}")
        try:
            data = reply.json()
        except ValueError:
            raise WeatherError("Яндекс ответил не JSON") from None
        return reading_from_yandex(place.name.split(",")[0], data, time.time())
