from datetime import datetime

import pytest

from app.db.database import get_connection
from app.repositories.sitting import SittingRepository
from app.services.sitting import SittingService, cell_color
from tests.test_people import PAPA, join, phone_sync
from tests.test_settings import DEVICE, register


@pytest.fixture
def hub(client):
    register(client)
    join(client, DEVICE, "Redmi 12", [PAPA])
    return client


def sync_sitting(client):
    return phone_sync(client)["sitting"]


def save(client, person, enabled=True, first=40, repeat=20, rest=5):
    data = {"first_min": first, "repeat_min": repeat, "break_min": rest}
    if enabled:
        data["enabled"] = "on"
    return client.post(f"/health/{person}", data=data, follow_redirects=False)


def test_nothing_is_sent_to_phones_until_it_is_switched_on(hub):
    assert sync_sitting(hub) == []
    save(hub, PAPA["id"], enabled=False)
    assert sync_sitting(hub) == []


def test_settings_go_to_the_phone_with_their_own_values(hub):
    assert save(hub, PAPA["id"], first=1, repeat=2, rest=1).status_code == 303
    (plan,) = sync_sitting(hub)
    assert plan == {
        "person": PAPA["id"],
        "name": "Папа",
        "firstMin": 1,
        "repeatMin": 2,
        "breakMin": 1,
    }


def test_bad_settings_are_refused_with_a_reason(hub):
    for first, repeat, rest in ((0, 20, 5), (40, 0, 5), (40, 20, 0), (601, 20, 5)):
        reply = save(hub, PAPA["id"], first=first, repeat=repeat, rest=rest)
        assert "error=" in reply.headers["location"], (first, repeat, rest)
    assert "error=" in save(hub, "nobody").headers["location"]
    assert sync_sitting(hub) == []


def cells(*items, device=DEVICE):
    return {"device": device, "buckets": [
        {"person": PAPA["id"], "bucket": b, "presentS": p, "streakMin": s, "level": lv}
        for b, p, s, lv in items
    ]}  # fmt: skip


def test_cells_are_kept_and_only_grow(hub):
    first = cells(("2026-11-07T14:15", 300, 20, 0))
    assert hub.post("/api/device/sitting", json=first).status_code == 204
    again = cells(("2026-11-07T14:15", 200, 35, 1), ("2026-11-07T14:15", 500, 10, 0))
    assert hub.post("/api/device/sitting", json=again).status_code == 204
    got = SittingRepository().buckets_since("2026-11-07T00:00")
    assert got == {PAPA["id"]: {"2026-11-07T14:15": (500, 35, 1)}}


def test_bad_cells_and_unknown_devices_are_refused(hub):
    bad = cells(("2026-11-07T14:10", 300, 20, 0))  # не на границе 15 минут
    assert hub.post("/api/device/sitting", json=bad).status_code == 422
    unknown = cells(("2026-11-07T14:15", 300, 20, 0), device="unknown-device-1")
    assert hub.post("/api/device/sitting", json=unknown).status_code == 404
    stranger = cells(("2026-11-07T14:15", 300, 20, 0))
    stranger["buckets"][0]["person"] = "nobody"
    assert hub.post("/api/device/sitting", json=stranger).status_code == 204
    assert SittingRepository().buckets_since("2000-01-01T00:00") == {}


def test_colour_runs_from_green_to_red():
    assert cell_color(0, 60) == "hsl(120 70% 48%)"
    assert cell_color(30, 60) == "hsl(60 70% 48%)"
    assert cell_color(60, 60) == "hsl(0 70% 48%)"
    assert cell_color(500, 60) == "hsl(0 70% 48%)"


def test_the_heat_map_has_a_cell_per_quarter_hour_for_a_week(hub):
    save(hub, PAPA["id"], first=40, repeat=20)
    hub.post("/api/device/sitting", json=cells(("2026-11-07T14:15", 840, 50, 1)))
    now = datetime(2026, 11, 7, 18, 0)
    (person,) = SittingService(clock=lambda: now.timestamp()).view()
    assert len(person.days) == 7 and all(len(d.cells) == 96 for d in person.days)
    today = person.days[0]
    cell = today.cells[14 * 4 + 1]  # 14:15
    assert cell.style == "hsl(20 70% 48%)"  # серия 50 из шкалы 60
    assert "14:15–14:30" in cell.title and "50 мин" in cell.title
    assert today.cells[0].title.endswith("вас не было")
    assert today.cells[95].style == "#f3f4f6"  # 23:45 ещё не наступило
    assert (person.today_streak_min, person.today_present_min) == (50, 14)


def test_the_page_shows_people_settings_and_the_map(hub):
    save(hub, PAPA["id"], first=40, repeat=20)
    page = hub.get("/health")
    assert page.status_code == 200
    assert "Папа" in page.text and 'name="first_min"' in page.text and "heat-cell" in page.text
    assert 'aria-current="page"' in page.text


def test_history_survives_a_connection_per_test(hub):
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sitting_buckets").fetchone()[0] == 0
