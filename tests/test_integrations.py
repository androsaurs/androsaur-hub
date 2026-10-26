from datetime import UTC

import pytest

from app.clients.weather import WeatherError
from app.clients.yandex_weather import reading_from_yandex
from app.dependencies import get_device_service, get_integrations_service, get_weather_service
from app.schemas import WeatherPlace, WeatherReading
from app.services.devices import DeviceService
from app.services.integrations import IntegrationsService
from app.services.weather import WeatherService
from tests.test_settings import DEVICE, register

KEY = "secret-yandex-key-1234ABCD"
SPB = WeatherPlace(name="Санкт-Петербург, Россия", latitude=59.94, longitude=30.31)
NOW = 1_792_951_200.0  # 2026-10-25 20:00 UTC


def yandex_answer(condition: str = "CLOUDY") -> dict:
    def hour(t: str, temp: int, cond: str) -> dict:
        return {"time": t, "temperature": temp, "condition": cond}

    return {
        "data": {
            "weatherByPoint": {
                "now": {"temperature": 11, "condition": condition, "windSpeed": 4.9},
                "forecast": {
                    "days": [
                        {
                            "time": "2026-10-25T00:00:00+00:00",
                            "hours": [
                                hour("2026-10-25T21:00:00+00:00", 10, "CLOUDY"),
                                hour("2026-10-25T23:00:00+00:00", 9, "LIGHT_RAIN"),
                            ],
                        },
                        {
                            "time": "2026-10-26T00:00:00+00:00",
                            "hours": [
                                hour("2026-10-26T03:00:00+00:00", 6, "CLEAR"),
                                hour("2026-10-26T12:00:00+00:00", 13, "PARTLY_CLOUDY"),
                                hour("2026-10-26T18:00:00+00:00", 8, "RAIN"),
                            ],
                        },
                    ]
                },
            }
        }
    }


class FakeYandex:
    calls = 0
    fail: str | None = None
    keys: list[str] = []

    def __init__(self, key: str) -> None:
        FakeYandex.keys.append(key)

    def forecast(self, place: WeatherPlace) -> WeatherReading:
        FakeYandex.calls += 1
        if FakeYandex.fail:
            raise WeatherError(FakeYandex.fail)
        return reading_from_yandex(place.name.split(",")[0], yandex_answer(), NOW, UTC)


class FakeMet:
    def find(self, name: str) -> WeatherPlace | None:
        return SPB

    def forecast(self, place: WeatherPlace) -> tuple[WeatherReading, float]:
        reading = WeatherReading(
            place="СПб",
            temperature=5.0,
            symbol="fog",
            time_ms=0,
            fetched_at_ms=0,
            source="MET Norway",
        )
        return reading, NOW + 600


@pytest.fixture
def hub(client):
    FakeYandex.calls, FakeYandex.fail, FakeYandex.keys = 0, None, []
    clock = lambda: NOW  # noqa: E731

    def integrations() -> IntegrationsService:
        return IntegrationsService(yandex=FakeYandex, clock=clock)

    def weather() -> WeatherService:
        return WeatherService(client=FakeMet(), clock=clock, integrations=integrations())

    client.app.dependency_overrides[get_integrations_service] = integrations
    client.app.dependency_overrides[get_weather_service] = weather
    client.app.dependency_overrides[get_device_service] = lambda: DeviceService(weather=weather())
    return client


def sync(client):
    return client.post("/api/device/sync", json={"device": DEVICE}).json()


def test_yandex_answer_is_read_like_the_met_one():
    reading = reading_from_yandex("СПб", yandex_answer(), NOW, UTC)
    assert (reading.temperature, reading.symbol, reading.wind) == (11.0, "cloudy", 4.9)
    assert (reading.min, reading.max) == (6, 10), "the next 12 hours: 21:00, 23:00, 03:00"
    assert reading.later_symbol == "lightrain"
    assert reading.later_time_ms == 1792969200000
    assert (reading.tomorrow_min, reading.tomorrow_max, reading.tomorrow_symbol) == (
        6,
        13,
        "partlycloudy",
    )
    assert reading.source == "Яндекс Погода"


def test_graphql_errors_are_reported():
    with pytest.raises(WeatherError, match="Field not allowed"):
        reading_from_yandex("СПб", {"errors": [{"message": "Field not allowed"}]}, NOW)


def test_the_page_lists_the_cards_and_never_shows_the_key(hub):
    page = hub.get("/integrations").text
    assert "Яндекс Погода" in page and "MET Norway" in page and "OpenStreetMap" in page
    assert hub.post("/integrations/yandex_weather/key", data={"key": KEY}).status_code == 200
    page = hub.get("/integrations").text
    assert "…ABCD" in page and "включено" in page
    assert KEY not in page and "1234ABCD" not in page


def test_check_connection_tells_the_result(hub):
    hub.post("/integrations/yandex_weather/key", data={"key": KEY})
    page = hub.post("/integrations/yandex_weather/check").text
    assert "Работает: Москва — +11°, облачно" in page
    assert FakeYandex.keys[-1] == KEY

    FakeYandex.fail = "ключ не принят или исчерпан лимит запросов (403)"
    page = hub.post("/integrations/yandex_weather/check").text
    assert "Не вышло: ключ не принят" in page


def test_a_check_with_a_place_becomes_the_weather_of_the_house(hub):
    register(hub)
    hub.post("/weather/place", data={"name": "Петербург"})
    hub.post("/integrations/yandex_weather/key", data={"key": KEY})
    hub.post("/integrations/yandex_weather/check")
    weather = sync(hub)["weather"]
    assert weather["source"] == "Яндекс Погода" and weather["temperature"] == 11.0
    assert weather["validUntilMs"] == int((NOW + 7200 + 900) * 1000)


def test_the_house_takes_yandex_and_falls_back_to_met(hub):
    register(hub)
    hub.post("/integrations/yandex_weather/key", data={"key": KEY})
    hub.post("/weather/place", data={"name": "Петербург"})
    assert sync(hub)["weather"]["source"] == "Яндекс Погода"

    FakeYandex.fail = "Яндекс не ответил"
    hub.post("/weather/place", data={"name": "Петербург"})
    assert sync(hub)["weather"]["source"] == "MET Norway"

    hub.post("/integrations/yandex_weather/enabled", data={"enabled": 0})
    FakeYandex.fail = None
    calls = FakeYandex.calls
    hub.post("/weather/place", data={"name": "Петербург"})
    assert sync(hub)["weather"]["source"] == "MET Norway" and FakeYandex.calls == calls


def test_bad_input_is_refused(hub):
    assert (
        "Ключ выглядит не так"
        in hub.post("/integrations/yandex_weather/key", data={"key": "x"}).text
    )
    assert (
        "Сначала введите ключ"
        in hub.post("/integrations/yandex_weather/enabled", data={"enabled": 1}).text
    )
    assert "Сначала введите ключ" in hub.post("/integrations/yandex_weather/check").text
    hub.post("/integrations/yandex_weather/key", data={"key": KEY})
    assert "30–1440" in hub.post("/integrations/yandex_weather/refresh", data={"minutes": 5}).text
    assert (
        "Такой интеграции нет" in hub.post("/integrations/met_norway/key", data={"key": KEY}).text
    )


def test_the_refresh_interval_is_kept(hub):
    hub.post("/integrations/yandex_weather/key", data={"key": KEY})
    hub.post("/integrations/yandex_weather/refresh", data={"minutes": 180})
    assert 'value="180"' in hub.get("/integrations").text


def test_removing_the_key_switches_it_off(hub):
    hub.post("/integrations/yandex_weather/key", data={"key": KEY})
    hub.post("/integrations/yandex_weather/key/clear")
    page = hub.get("/integrations").text
    assert "…ABCD" not in page and "выключено" in page


def test_the_weather_page_has_the_hours_for_the_chart(hub):
    register(hub)
    hub.post("/integrations/yandex_weather/key", data={"key": KEY})
    hub.post("/weather/place", data={"name": "Петербург"})
    hours = sync(hub)["weather"]["hours"]
    assert 0 < len(hours) <= 25, "the phone gets the next day only"
    assert set(hours[0]) >= {"timeMs", "temperature", "symbol", "wind", "precipProb"}
    page = hub.get("/weather").text
    assert 'id="chart-data"' in page and "Яндекс Погода" in page
    assert "Таблица по часам" in page


def test_the_weather_page_without_a_place(hub):
    assert "Прогноза по часам пока нет" in hub.get("/weather").text


def test_the_weather_page_for_the_phone_has_no_menu(hub):
    register(hub)
    hub.post("/weather/place", data={"name": "Петербург"})
    page = hub.get("/weather", params={"embed": 1}).text
    assert 'class="topbar"' not in page and "container embed" in page
    assert '<body class="embed">' in page and "Таблица по часам" not in page
    assert 'class="topbar"' in hub.get("/weather").text
