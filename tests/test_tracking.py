from uuid import uuid4

import pytest

from app.db.database import get_connection
from app.repositories.tracking import TrackingRepository
from app.services.tracking import TrackingService
from app.tracking import TrackingBatch
from tests.test_auth import OTHER, PHONE, _register


def payload(**sample):
    return {
        "device": PHONE,
        "session": str(uuid4()),
        "samples": [{"seq": 1, "ageMs": 100, "visible": True, "x": 0.4, "y": -0.2} | sample],
    }


def test_auth_and_phone_isolation(client, anon):
    token = _register(anon, client, PHONE)
    _register(anon, client, OTHER)
    data = payload()
    assert anon.post("/api/device/tracking", json=data).status_code == 401
    headers = {"Authorization": f"Bearer {token}"}
    assert (
        anon.post(
            "/api/device/tracking", json=data | {"device": OTHER}, headers=headers
        ).status_code
        == 403
    )
    assert anon.post("/api/device/tracking", json=data, headers=headers).status_code == 200
    assert anon.get(f"/devices/{PHONE}/tracking.json", follow_redirects=False).status_code == 303
    response = client.get(f"/devices/{PHONE}/tracking.json")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json()["points"][0]["x"] == 0.4


@pytest.mark.parametrize(
    "change", [{"x": 1.1}, {"x": None}, {"ageMs": -1}, {"ageMs": 60001}, {"visible": False}]
)
def test_bad_samples_are_rejected(client, change):
    assert client.post("/api/device/tracking", json=payload(**change)).status_code == 422


def test_lost_target_and_retries_and_clock_translation(client, anon):
    _register(anon, client, PHONE)
    repo = TrackingRepository()
    data = payload()
    data["samples"].append({"seq": 2, "ageMs": 0, "visible": False, "x": None, "y": None})
    batch = TrackingBatch.model_validate(data)
    repo.add(batch, 1000)
    repo.add(batch, 1001)
    points = repo.recent(PHONE, 990)
    assert len(points) == 2
    assert points[0]["at"] == 999900
    assert points[1]["x"] is None and points[1]["y"] is None
    assert repo.recent(OTHER, 990) == []


def test_history_boundaries_and_retention(client, anon):
    _register(anon, client, PHONE)
    service = TrackingService(clock=lambda: 1000)
    assert service.record(TrackingBatch.model_validate(payload()))
    service.clock = lambda: 2000
    assert service.record(TrackingBatch.model_validate(payload()))
    with get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM tracking_observations").fetchone()[0] == 1
    assert len(service.history(PHONE)["points"]) == 1
    assert client.get(f"/devices/{PHONE}/tracking.json?seconds=100000").status_code == 422
    assert client.get("/devices/missing/tracking.json").status_code == 404


def test_graphs_have_explicit_camera_source_and_separate_axes(client, anon):
    _register(anon, client, PHONE)
    page = client.get("/devices").text
    assert "Данные камеры телефона" in page
    assert page.count('class="tracking-chart"') == 1
    assert 'data-axis="x"' in page and 'data-axis="y"' in page
