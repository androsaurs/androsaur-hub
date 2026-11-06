from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from app.clients.europaplus import SIGN_CODES, EuropaPlusProvider, parse_forecasts
from app.db.database import get_connection, init_db
from app.dependencies import get_horoscope_service
from app.horoscope import SIGNS, DailyHoroscopes, HoroscopeError, HoroscopeSource
from app.services.horoscope import HoroscopeService
from tests.test_people import DEVICE, KATYA, OTHER, PAPA, join, person


class Source:
    calls = 0
    broken = False

    def today(self, day):
        self.calls += 1
        if self.broken:
            raise HoroscopeError("offline")
        return DailyHoroscopes(date=day, texts={s: f"Прогноз {s}" for s in SIGNS})


def setup(client):
    join(client, DEVICE, "Дом", [KATYA, PAPA])
    now = [datetime(2026, 11, 6, 10, tzinfo=UTC).timestamp()]
    source = Source()
    service = HoroscopeService(
        providers={
            "europaplus": HoroscopeSource("Europa Plus", lambda: source, "радио Европа Плюс")
        },
        clock=lambda: now[0],
    )
    client.app.dependency_overrides[get_horoscope_service] = lambda: service
    return service, source, now


def request(client, person_id=KATYA["id"]):
    return client.post("/api/horoscope", json={"person": person_id})


def test_profile_selection_and_forecast_are_personal_and_immediate(client):
    service, source, _ = setup(client)
    assert request(client).json()["status"] == "sign_missing"
    page = client.post("/people/zodiac", data={"person": KATYA["id"], "sign": "capricorn"})
    assert '<option value="capricorn" selected>' in page.text
    service.set_sign(PAPA["id"], "leo")
    result = request(client)
    assert result.headers["cache-control"] == "no-store"
    body = result.json()
    assert body["sign"] == "capricorn" and body["sign_name"] == "Козерог"
    assert body["text"] == "Прогноз capricorn"
    assert body["date"] == "2026-11-06"
    assert body["source"] == "europaplus" and body["source_date_verified"] is False
    assert body["source_name"] == "Europa Plus"
    assert body["source_speech_name"] == "радио Европа Плюс"
    assert request(client, PAPA["id"]).json()["text"] == "Прогноз leo"
    assert source.calls == 1  # one bundle for every user and phone
    service.set_sign(KATYA["id"], "aries")
    assert request(client).json()["text"] == "Прогноз aries"
    assert source.calls == 1
    client.post("/people/zodiac", data={"person": KATYA["id"], "sign": ""})
    assert request(client).json()["status"] == "sign_missing"


def test_invalid_profile_inputs_do_not_change_existing_sign(client):
    service, _, _ = setup(client)
    service.set_sign(KATYA["id"], "aries")
    assert (
        "Выберите знак"
        in client.post("/people/zodiac", data={"person": KATYA["id"], "sign": "rozeyrog"}).text
    )
    assert request(client).json()["sign"] == "aries"
    assert (
        "Выберите человека"
        in client.post("/people/zodiac", data={"person": "missing", "sign": "aries"}).text
    )
    assert request(client, "missing").json()["status"] == "unknown_person"
    assert client.post("/api/horoscope", json={"person": ""}).status_code == 422


def test_can_replace_or_disable_provider_without_changing_profiles(client):
    service, source, now = setup(client)
    alternative = Source()
    service = HoroscopeService(
        providers={
            "europaplus": HoroscopeSource("Europa Plus", lambda: source, "радио Европа Плюс"),
            "other": HoroscopeSource("Другой источник", lambda: alternative),
        },
        clock=lambda: now[0],
    )
    client.app.dependency_overrides[get_horoscope_service] = lambda: service
    service.set_sign(KATYA["id"], "aries")
    assert request(client).json()["source"] == "europaplus"
    page = client.post("/integrations/horoscope/provider", data={"provider": "other"})
    assert '<option value="other" selected>' in page.text
    assert request(client).json()["source_name"] == "Другой источник"
    assert request(client).json()["source_speech_name"] == "Другой источник"
    assert source.calls == 1 and alternative.calls == 1
    assert (
        "Такого источника"
        in client.post("/integrations/horoscope/provider", data={"provider": "missing"}).text
    )
    client.post("/integrations/horoscope/provider", data={"provider": ""})
    assert request(client).json()["status"] == "disabled"
    assert source.calls == 1 and alternative.calls == 1


def test_same_day_cache_survives_outage_and_restart_but_never_crosses_midnight(client):
    service, source, now = setup(client)
    service.set_sign(KATYA["id"], "aries")
    first = request(client).json()
    init_db()
    assert request(client).json() == first
    source.broken = True
    now[0] += 3601
    assert request(client).json()["status"] == "ok"
    assert source.calls == 2
    request(client)
    assert source.calls == 2  # backoff after failed refresh
    now[0] = first["valid_until_ms"] / 1000
    failed = request(client).json()
    assert failed["date"] == "2026-11-07" and failed["status"] == "unavailable"
    assert failed["text"] is None
    assert source.calls == 3
    now[0] += 61
    source.broken = False
    assert request(client).json()["status"] == "ok"


def test_day_uses_hub_timezone_instead_of_utc(client):
    service, _, now = setup(client)
    service.set_sign(KATYA["id"], "aries")
    now[0] = datetime(2026, 11, 6, 22, tzinfo=UTC).timestamp()
    body = request(client).json()
    assert body["date"] == "2026-11-07" and body["timezone"] == "Europe/Moscow"
    assert body["valid_until_ms"] == int(datetime(2026, 11, 7, 21, tzinfo=UTC).timestamp() * 1000)


def test_concurrent_requests_fetch_a_single_bundle(client):
    service, source, _ = setup(client)
    service.set_sign(KATYA["id"], "aries")
    with ThreadPoolExecutor(max_workers=5) as pool:
        replies = list(pool.map(lambda _: service.today(KATYA["id"]), range(5)))
    assert all(r.status == "ok" for r in replies)
    assert source.calls == 1


def test_migration_preserves_existing_people_and_profile_survives_rename_and_merge(client):
    service, _, _ = setup(client)
    # Reproduce the pre-feature database and run the real startup migration twice.
    with get_connection() as conn:
        conn.execute("ALTER TABLE house_people DROP COLUMN zodiac_sign")
    init_db()
    init_db()
    assert request(client).json()["status"] == "sign_missing"
    service.set_sign(KATYA["id"], "virgo")
    client.post("/people/rename", data={"person": KATYA["id"], "to": "Катюша"})
    incoming = person("ffffffffffff", "Катя", range(200, 210))
    join(client, OTHER, "Другой телефон", [incoming])
    assert request(client, incoming["id"]).json()["status"] == "unknown_person"
    client.post("/people/merge", data={"person": incoming["id"], "into": KATYA["id"]})
    assert request(client).json()["sign"] == "virgo"


def payload():
    return {
        "data": {
            "type": {
                str(i): {"code": code, "today": f"Прогноз для {code}"}
                for i, code in enumerate(SIGN_CODES.values(), 1)
            }
        }
    }


def test_europaplus_maps_its_own_codes_to_canonical_signs(monkeypatch):
    def get(url, **kwargs):
        assert kwargs["params"] == {"region": 85}
        return httpx.Response(200, json=payload(), request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)
    bundle = EuropaPlusProvider().today(date(2026, 11, 6))
    assert bundle.texts["capricorn"] == "Прогноз для rozeyrog"
    assert len(bundle.texts) == 12 and not bundle.source_date_verified


@pytest.mark.parametrize("bad", [{}, {"data": {"type": []}}, {"data": {"type": {}}}])
def test_broken_provider_shape_is_a_controlled_error(bad):
    with pytest.raises(HoroscopeError):
        parse_forecasts(bad, date(2026, 11, 6))


def test_empty_html_and_duplicate_signs_are_rejected():
    for text in ("", "<b>Прогноз</b>", None, "x" * 3001):
        value = payload()
        value["data"]["type"]["1"]["today"] = text
        with pytest.raises(HoroscopeError):
            parse_forecasts(value, date(2026, 11, 6))
    value = payload()
    value["data"]["type"]["1"]["code"] = "telec"
    with pytest.raises(HoroscopeError):
        parse_forecasts(value, date(2026, 11, 6))


def test_provider_wrong_day_is_not_cached(client):
    service, source, _ = setup(client)
    service.set_sign(KATYA["id"], "aries")
    source.today = lambda day: DailyHoroscopes(
        date=day - timedelta(days=1), texts=dict.fromkeys(SIGNS, "Вчерашний прогноз")
    )
    assert request(client).json()["status"] == "unavailable"
