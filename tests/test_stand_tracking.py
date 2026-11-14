import pytest

from app.dependencies import get_stand_service
from app.services.stands import StandService
from app.stands import TrackingSettings
from tests.test_auth import PHONE, _register
from tests.test_stands import KEY, PIN, Transport, enrollment

SETTINGS = {
    "enabled": True, "panRangeDeg": 4.5, "tiltRangeDeg": 4.5,
    "speedDegS": 2.25, "stepDeg": 0.45, "smoothingMs": 200,
    "deadZonePercent": 4, "panReverse": True, "tiltReverse": True,
}
BASE = f"/devices/{PHONE}/stand/tracking"


class TrackingTransport(Transport):
    def __init__(self):
        self.settings = SETTINGS.copy()
        self.calls = []
        self.ignore_write = False
        self.old_firmware = False

    def request(self, stand, quiet=None):
        data = super().request(stand, quiet)
        if not self.old_firmware:
            data["capabilities"].append("trackingSettings")
            data["tracking"] = {
                "outputEnabled": self.settings["enabled"], "state": "tracking",
                "settings": self.settings.copy(), "panDeg": 2, "tiltDeg": -1,
            }
        return data

    def tracking(self, stand, change):
        self.calls.append(change)
        if not self.ignore_write:
            if isinstance(change, bool):
                self.settings["enabled"] = change
            else:
                self.settings = change.model_dump(by_alias=True)
        return self.request(stand)


@pytest.fixture
def tracking(client, anon):
    _register(anon, client, PHONE)
    transport = TrackingTransport()
    service = StandService(transport=transport)
    service.enroll(enrollment())
    client.app.dependency_overrides[get_stand_service] = lambda: service
    return service, transport


def test_page_exposes_human_units_without_credentials(client, tracking):
    page = client.get("/devices").text
    for label in ("Движение и слежение", "Размах поворота", "Скорость движения", "Плавность",
                  "Максимальный шаг", "Зона покоя", "° в секунду"):
        assert label in page
    assert KEY not in page and PIN not in page
    data = client.get(BASE + ".json")
    assert data.headers["cache-control"] == "no-store"
    assert data.json()["tracking"]["settings"] == SETTINGS


def test_live_save_and_stop_resume_are_confirmed_and_cached(client, tracking):
    service, transport = tracking
    changed = SETTINGS | {"panRangeDeg": 30, "tiltRangeDeg": 15, "speedDegS": 20}
    response = client.post(BASE + "/settings", json=changed)
    assert response.status_code == 200
    assert response.json()["tracking"]["settings"] == changed
    assert service.views()[PHONE].status.tracking.settings.speed_deg_s == 20
    for enabled in (False, True):
        response = client.post(BASE + "/control", json={"enabled": enabled})
        assert response.status_code == 200
        assert response.json()["tracking"]["outputEnabled"] is enabled
    assert len(transport.calls) == 3


@pytest.mark.parametrize("patch", [
    {"panRangeDeg": 91}, {"tiltRangeDeg": 46}, {"speedDegS": 0},
    {"stepDeg": -1}, {"smoothingMs": 1001}, {"deadZonePercent": 0},
    {"enabled": "yes"}, {"panReverse": 1}, {"unknown": 10},
])
def test_invalid_settings_do_not_reach_controller(client, tracking, patch):
    _, transport = tracking
    assert client.post(BASE + "/settings", json=SETTINGS | patch).status_code == 422
    assert not transport.calls


def test_owner_auth_and_cross_site_protection(client, anon, tracking):
    _, transport = tracking
    assert anon.get(BASE + ".json", follow_redirects=False).status_code == 303
    for suffix, payload in (("settings", SETTINGS), ("control", {"enabled": False})):
        assert anon.post(BASE + "/" + suffix, json=payload).status_code == 401
        assert client.post(
            BASE + "/" + suffix, json=payload, headers={"Origin": "https://evil.example"}
        ).status_code == 403
    assert not transport.calls


def test_no_false_success_on_offline_ignored_write_or_wrong_identity(client, tracking):
    _, transport = tracking
    transport.ignore_write = True
    assert client.post(BASE + "/control", json={"enabled": False}).status_code == 503
    transport.failed = True
    assert client.get(BASE + ".json").status_code == 503
    transport.failed = False
    transport.wrong_identity = True
    assert client.get(BASE + ".json").status_code == 503


def test_older_firmware_and_detached_stand_have_explanations(client, tracking):
    service, transport = tracking
    transport.old_firmware = True
    assert client.get(BASE + ".json").status_code == 409
    service.repo.detach(PHONE)
    assert client.get(BASE + ".json").status_code == 404


def test_transport_uses_fixed_paths_and_human_units(monkeypatch):
    from app.clients.stand import StandTransport

    sent = []
    transport = StandTransport()
    monkeypatch.setattr(transport, "_request", lambda *args: sent.append(args) or {})
    stand = enrollment()
    transport.tracking(stand, TrackingSettings.model_validate(SETTINGS))
    transport.tracking(stand, False)
    assert sent == [
        (stand, "/v1/tracking/settings", SETTINGS),
        (stand, "/v1/tracking/control", {"enabled": False}),
    ]
