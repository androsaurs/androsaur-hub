from datetime import UTC

import pytest

from app.clients.weather import (
    WeatherError,
    place_from_nominatim,
    reading_from_met,
    tomorrow_from_met,
)
from app.dependencies import get_device_service, get_weather_service
from app.schemas import WeatherPlace, WeatherReading
from app.services.devices import DeviceService
from app.services.weather import WeatherService, describe
from tests.test_settings import DEVICE, register

SPB = WeatherPlace(name="Санкт-Петербург, Россия", latitude=59.93863, longitude=30.31413)


class FakeClient:
    def __init__(self) -> None:
        self.forecasts = 0
        self.fail = False

    def find(self, name: str) -> WeatherPlace | None:
        return SPB if "петербург" in name.lower() else None

    def forecast(self, place: WeatherPlace) -> tuple[WeatherReading, float]:
        if self.fail:
            raise WeatherError("нет связи")
        self.forecasts += 1
        reading = WeatherReading(
            place=place.name.split(",")[0],
            temperature=11.2,
            symbol="cloudy",
            wind=4.9,
            min=9.8,
            max=12.0,
            time_ms=1_000,
            fetched_at_ms=int(self.now * 1000),
        )
        return reading, self.now + 600

    now = 1_000_000.0


@pytest.fixture
def fake(client):
    fake = FakeClient()
    clock = lambda: fake.now  # noqa: E731
    client.app.dependency_overrides[get_weather_service] = lambda: WeatherService(
        client=fake, clock=clock
    )
    client.app.dependency_overrides[get_device_service] = lambda: DeviceService(
        weather=WeatherService(client=fake, clock=clock)
    )
    return fake


def sync(client):
    return client.post("/api/device/sync", json={"device": DEVICE}).json()


def test_without_a_place_phones_use_their_own(client, fake):
    register(client)
    reply = sync(client)
    assert reply["weatherPlace"] is None and reply["weather"] is None
    assert "Место не задано" in client.get("/").text


def test_a_city_set_in_the_hub_reaches_the_phones_with_its_weather(client, fake):
    register(client)
    assert client.post("/weather/place", data={"name": "Санкт-Петербург"}).status_code == 200
    reply = sync(client)
    assert reply["weatherPlace"] == {
        "name": "Санкт-Петербург, Россия",
        "latitude": 59.93863,
        "longitude": 30.31413,
    }
    assert reply["weather"]["temperature"] == 11.2 and reply["weather"]["symbol"] == "cloudy"
    assert reply["weather"]["fetchedAtMs"] == 1_000_000_000
    page = client.get("/").text
    assert "+11° · облачно · ветер 5 м/с · за 12 ч от +10° до +12°" in page


def test_an_unknown_city_is_refused(client, fake):
    reply = client.post("/weather/place", data={"name": "Нигдебург"})
    assert "Не нашёл «Нигдебург»" in reply.text


def test_the_forecast_is_not_asked_too_often(client, fake):
    client.post("/weather/place", data={"name": "Петербург"})
    service = WeatherService(client=fake, clock=lambda: fake.now)
    assert fake.forecasts == 1
    fake.now += 300
    assert service.refresh() is False, "MET asked to wait and it is not time yet"
    fake.now += 1800
    assert service.refresh() is True and fake.forecasts == 2


def test_a_failed_refresh_keeps_the_last_weather(client, fake):
    register(client)
    client.post("/weather/place", data={"name": "Петербург"})
    fake.fail = True
    fake.now += 3600
    assert WeatherService(client=fake, clock=lambda: fake.now).refresh() is False
    assert sync(client)["weather"]["temperature"] == 11.2


def test_clearing_the_place_hands_it_back_to_the_phones(client, fake):
    register(client)
    client.post("/weather/place", data={"name": "Петербург"})
    client.post("/weather/place/clear")
    assert sync(client)["weatherPlace"] is None


def test_met_answer_is_read():
    point = {
        "time": "2026-10-25T15:00:00Z",
        "data": {
            "instant": {"details": {"air_temperature": 11.2, "wind_speed": 4.9}},
            "next_1_hours": {"summary": {"symbol_code": "lightrain"}},
        },
    }
    later = [
        {"time": "x", "data": {"instant": {"details": {"air_temperature": t}}}} for t in (8.0, 13.5)
    ]
    reading = reading_from_met("СПб", {"properties": {"timeseries": [point, *later]}}, 2.0)
    assert (reading.temperature, reading.symbol, reading.min, reading.max) == (
        11.2,
        "lightrain",
        8.0,
        13.5,
    )
    assert reading.time_ms == 1792940400000 and reading.fetched_at_ms == 2000


@pytest.mark.parametrize(
    ("symbol", "text"),
    [
        ("clearsky_night", "ясно"),
        ("partlycloudy_day", "переменная облачность"),
        ("lightrain", "небольшой дождь"),
        ("heavyrainshowers_day", "сильный кратковременный дождь"),
        ("sleet", "мокрый снег"),
        ("lightssnowshowersandthunder_day", "небольшой кратковременный снег, гроза"),
        ("snowandthunder", "снег, гроза"),
    ],
)
def test_symbols_in_words(symbol, text):
    assert describe(symbol) == text


def test_nominatim_answer_is_read():
    place = place_from_nominatim(
        {
            "name": "Мурманск",
            "lat": "68.9706650",
            "lon": "33.0749700",
            "address": {"state": "Мурманская область", "country": "Россия"},
        }
    )
    assert place == WeatherPlace(
        name="Мурманск, Мурманская область, Россия", latitude=68.970665, longitude=33.07497
    )
    moscow = {"name": "Москва", "lat": "55.75", "lon": "37.62", "address": {"state": "Москва"}}
    assert place_from_nominatim(moscow).name == "Москва"


def test_rain_later_is_found_and_not_when_it_already_rains():
    def hour(t: str, symbol: str) -> dict:
        return {
            "time": t,
            "data": {
                "instant": {"details": {"air_temperature": 10.0}},
                "next_1_hours": {"summary": {"symbol_code": symbol}},
            },
        }

    dry = [hour("2026-10-25T06:00:00Z", "cloudy"), hour("2026-10-25T07:00:00Z", "fair_day")]
    wet = [hour("2026-10-25T15:00:00Z", "lightrain"), hour("2026-10-25T16:00:00Z", "rain")]
    reading = reading_from_met("СПб", {"properties": {"timeseries": dry + wet}}, 0)
    assert (reading.later_symbol, reading.later_time_ms) == ("lightrain", 1792940400000)
    raining = reading_from_met("СПб", {"properties": {"timeseries": wet}}, 0)
    assert raining.later_symbol is None


def test_tomorrow_is_read_in_the_local_day():
    def point(t: str, temp: float, symbol: str) -> dict:
        return {
            "time": t,
            "data": {
                "instant": {"details": {"air_temperature": temp}},
                "next_6_hours": {"summary": {"symbol_code": symbol}},
            },
        }

    series = [
        point("2026-10-25T20:00:00Z", 9.0, "cloudy"),
        point("2026-10-26T00:00:00Z", 4.0, "clearsky_night"),
        point("2026-10-26T12:00:00Z", 9.5, "lightrain"),
        point("2026-10-26T18:00:00Z", 6.0, "cloudy"),
        point("2026-10-27T06:00:00Z", 2.0, "fog"),
    ]
    now = 1792951200.0  # 2026-10-25 20:00 UTC
    assert tomorrow_from_met(series, now, UTC) == (4.0, 9.5, "lightrain")
