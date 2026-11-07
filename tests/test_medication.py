from datetime import datetime

import pytest

from app.db.database import get_connection
from app.dependencies import get_medication_service
from app.medication import interval_at, normalize_time, slot_key
from app.services.medication import MedicationService
from tests.test_people import PAPA, join, phone_sync
from tests.test_settings import DEVICE, register

KATYA_ID = "0123456789ab"


def at(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M")


def test_time_is_normalised_or_rejected():
    assert normalize_time("8:05") == "08:05"
    assert normalize_time(" 18:00 ") == "18:00"
    for bad in ("", "24:00", "8", "08:60", "8:5", "ab:cd"):
        assert normalize_time(bad) is None


def start_of(times, now):
    interval = interval_at(times, at(now))
    return None if interval is None else (slot_key(interval[0]), slot_key(interval[1]))


def test_an_interval_runs_to_the_next_time_and_through_midnight():
    times = ["08:00", "18:00"]
    assert start_of(times, "2026-11-07 12:00") == ("2026-11-07T08:00", "2026-11-07T18:00")
    assert start_of(times, "2026-11-07 18:00") == ("2026-11-07T18:00", "2026-11-08T08:00")
    assert start_of(times, "2026-11-07 23:59") == ("2026-11-07T18:00", "2026-11-08T08:00")
    # после полуночи это всё тот же вечерний интервал, а не новый
    assert start_of(times, "2026-11-08 03:00") == ("2026-11-07T18:00", "2026-11-08T08:00")
    assert start_of(times, "2026-11-08 08:00") == ("2026-11-08T08:00", "2026-11-08T18:00")


def test_three_times_and_one_time_and_none():
    three = ["20:00", "08:00", "14:00"]
    assert start_of(three, "2026-11-07 15:00") == ("2026-11-07T14:00", "2026-11-07T20:00")
    assert start_of(three, "2026-11-07 07:00") == ("2026-11-06T20:00", "2026-11-07T08:00")
    assert start_of(["09:00"], "2026-11-07 10:00") == ("2026-11-07T09:00", "2026-11-08T09:00")
    assert start_of(["09:00"], "2026-11-07 08:00") == ("2026-11-06T09:00", "2026-11-07T09:00")
    assert interval_at([], at("2026-11-07 10:00")) is None


@pytest.fixture
def hub(client):
    register(client)
    join(client, DEVICE, "Redmi 12", [PAPA])
    return client


def plans(client):
    return phone_sync(client)["medications"]


def add(client, person, time):
    return client.post(f"/medication/{person}/times", data={"at": time}, follow_redirects=False)


def test_each_person_has_a_schedule_of_any_length_and_the_phone_gets_it(hub):
    assert plans(hub) == []
    for t in ("20:00", "08:00", "14:00"):
        assert add(hub, PAPA["id"], t).status_code == 303
    (plan,) = plans(hub)
    assert plan == {"person": PAPA["id"], "name": "Папа", "times": ["08:00", "14:00", "20:00"]}


def test_a_bad_duplicate_or_unknown_time_is_refused_with_a_reason(hub):
    add(hub, PAPA["id"], "08:00")
    for person, text in ((PAPA["id"], "25:00"), (PAPA["id"], "8:00"), ("nobody", "09:00")):
        reply = add(hub, person, text)
        assert reply.status_code == 303 and "error=" in reply.headers["location"], (person, text)
    assert plans(hub)[0]["times"] == ["08:00"]


def test_a_time_can_be_removed(hub):
    add(hub, PAPA["id"], "08:00")
    add(hub, PAPA["id"], "20:00")
    hub.post(f"/medication/{PAPA['id']}/times/delete", data={"at": "08:00"})
    assert plans(hub)[0]["times"] == ["20:00"]
    hub.post(f"/medication/{PAPA['id']}/times/delete", data={"at": "20:00"})
    assert plans(hub) == []


def events(hub, *items):
    body = {"device": DEVICE, "events": list(items)}
    return hub.post("/api/device/medication", json=body)


def test_phone_events_are_kept_once_per_interval(hub):
    add(hub, PAPA["id"], "08:00")
    e = {"person": PAPA["id"], "slot": "2026-11-07T08:00", "kind": "reminded", "at": 1_000}
    assert events(hub, e).status_code == 204
    assert events(hub, e, {**e, "kind": "taken", "at": 2_000}).status_code == 204
    log = MedicationService().plans()  # сервис читает ту же базу
    assert log and log[0].person == PAPA["id"]
    from app.repositories.medication import MedicationRepository

    got = MedicationRepository().log_since("2026-11-07T00:00")
    assert got == {(PAPA["id"], "2026-11-07T08:00"): {"reminded": 1_000, "taken": 2_000}}


def test_events_from_an_unknown_device_or_person_are_not_taken(hub):
    from app.repositories.medication import MedicationRepository

    body = {"device": "unknown-device-1", "events": []}
    assert hub.post("/api/device/medication", json=body).status_code == 404
    events(hub, {"person": "nobody", "slot": "2026-11-07T08:00", "kind": "taken", "at": 1})
    assert MedicationRepository().log_since("2000-01-01T00:00") == {}


def test_history_shows_taken_reminded_and_unseen(hub):
    from app.repositories.medication import MedicationRepository

    add(hub, PAPA["id"], "08:00")
    add(hub, PAPA["id"], "18:00")
    now = datetime(2026, 11, 7, 12, 0)
    service = MedicationService(clock=lambda: now.timestamp())
    # график появился давно: в историю попадают все интервалы недели
    with get_connection() as conn:
        conn.execute(
            "UPDATE medication_times SET added_at = ?", (datetime(2026, 10, 1).timestamp(),)
        )
    repo = MedicationRepository()
    repo.log_add(PAPA["id"], "2026-11-06T08:00", "reminded", 1, DEVICE)
    repo.log_add(PAPA["id"], "2026-11-06T08:00", "taken", 2, DEVICE)
    repo.log_add(PAPA["id"], "2026-11-06T18:00", "reminded", 3, DEVICE)
    (person,) = service.view()
    status = {s.start.strftime("%m-%d %H:%M"): s.status for s in person.slots}
    assert status["11-07 08:00"] == "now"  # идёт: напоминания пока не было
    assert status["11-06 18:00"] == "reminded"
    assert status["11-06 08:00"] == "taken"
    assert status["11-05 18:00"] == "unseen"
    assert "11-07 18:00" not in status  # будущие интервалы не показываем


def test_the_page_lists_people_with_their_times(hub):
    add(hub, PAPA["id"], "08:00")
    page = hub.get("/medication")
    assert page.status_code == 200
    assert "Папа" in page.text and "08:00" in page.text and 'aria-current="page"' in page.text


def test_two_people_keep_separate_schedules(hub):
    from tests.test_people import KATYA

    join(hub, DEVICE, "Redmi 12", [PAPA, KATYA])
    add(hub, PAPA["id"], "08:00")
    add(hub, KATYA["id"], "09:30")
    by_name = {p["name"]: p["times"] for p in plans(hub)}
    assert by_name == {"Папа": ["08:00"], "Катя": ["09:30"]}


def test_dependency_is_available():
    assert isinstance(get_medication_service(), MedicationService)


def test_history_starts_when_the_schedule_appeared(hub):
    for time in ("06:00", "14:00", "20:00"):
        add(hub, PAPA["id"], time)
    now = datetime(2026, 11, 7, 18, 30)
    with get_connection() as conn:  # график добавили сегодня в 18:20
        conn.execute(
            "UPDATE medication_times SET added_at = ?", (datetime(2026, 11, 7, 18, 20).timestamp(),)
        )
    (person,) = MedicationService(clock=lambda: now.timestamp()).view()
    # идёт интервал с 14:00; раньше графика не было, так что «не видели» за вчера и утро выдумка
    assert [s.start.strftime("%m-%d %H:%M") for s in person.slots] == ["11-07 14:00"]
