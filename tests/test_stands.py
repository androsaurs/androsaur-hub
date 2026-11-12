import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.clients.stand import StandTransport
from app.dependencies import get_stand_service
from app.repositories.stands import StandConflict, StandRepository
from app.services.stands import StandService
from app.stands import PhoneStandReport, StandEnrollment
from tests.test_auth import OTHER, PHONE, _register

ID = "stand-112233445566"
KEY = "a" * 64
PIN = "b" * 64


def enrollment(device: str = PHONE, id: str = ID, ip: str = "192.168.1.50") -> StandEnrollment:
    return StandEnrollment(device=device, id=id, ip=ip, key=KEY, fingerprint=PIN)


class Transport(StandTransport):
    failed = False
    wrong_identity = False

    def discover(self, stand):
        return None

    def request(self, stand, quiet=None):
        if self.failed:
            raise ConnectionError("unavailable")
        return {
            "id": "stand-ffffffffffff" if self.wrong_identity else stand.id,
            "firmware": "test",
            "phoneId": stand.device,
            "protocol": 1,
            "capabilities": ["status", "policy"],
            "quiet": quiet or False,
        }


@pytest.fixture
def setup(client: TestClient, anon: TestClient):
    token = _register(anon, client, PHONE)
    _register(anon, client, OTHER)
    transport = Transport()
    service = StandService(transport=transport, clock=lambda: 1000)
    client.app.dependency_overrides[get_stand_service] = lambda: service
    return service, transport, {"Authorization": f"Bearer {token}"}


def test_only_owner_phone_can_enroll(client, anon, setup):
    service, _, headers = setup
    payload = enrollment().model_dump(mode="json") | {"key": KEY}
    assert anon.post("/api/device/stand", json=payload).status_code == 401
    assert (
        anon.post(
            "/api/device/stand", json=payload | {"device": OTHER}, headers=headers
        ).status_code
        == 403
    )
    response = anon.post("/api/device/stand", json=payload, headers=headers)
    assert response.status_code == 200 and KEY not in response.text
    assert service.views()[PHONE].online


def test_stand_is_nested_and_secrets_never_reach_page(client, anon, setup):
    service, _, _ = setup
    service.enroll(enrollment())
    page = client.get("/devices")
    assert page.status_code == 200 and "Подставка" in page.text
    assert ID in page.text and KEY not in page.text and PIN not in page.text
    assert KEY not in service.views()[PHONE].model_dump_json()


def test_phone_reports_its_own_link_independently_of_hub_link(client, anon, setup):
    service, _, headers = setup
    service.enroll(enrollment())
    response = anon.post(
        "/api/device/sync",
        json={"device": PHONE, "standLink": {"id": ID, "state": "offline"}},
        headers=headers,
    )
    assert response.status_code == 200
    assert service.repo.get(PHONE)["phone_state"] == "offline"
    page = client.get("/devices").text
    assert "Телефон ↔ подставка" in page and "Нет связи" in page
    assert KEY not in page and PIN not in page
    assert anon.post("/api/device/sync", json={"device": PHONE}, headers=headers).status_code == 200
    assert service.views()[PHONE].phone_link == "unknown"


def test_hub_failure_does_not_override_phone_success(client, setup):
    service, transport, _ = setup
    service.enroll(enrollment())
    service.repo.phone_report(PHONE, PhoneStandReport(id=ID, state="online", seenAgoMs=5000), 1000)
    transport.failed = True
    service.refresh_all()
    view = service.views()[PHONE]
    assert not view.online
    assert view.phone_link == "online"


@pytest.mark.parametrize("state,seen", [("online", 0), ("offline", None)])
def test_phone_link_report_expires_without_new_reports(client, setup, state, seen):
    service, _, _ = setup
    service.enroll(enrollment())
    service.repo.phone_report(PHONE, PhoneStandReport(id=ID, state=state, seenAgoMs=seen), 1000)
    assert service.views()[PHONE].phone_link == state
    service.clock = lambda: 1091
    assert service.views()[PHONE].phone_link == "stale"


def test_old_success_is_not_green_even_with_recent_report(client, setup):
    service, _, _ = setup
    service.enroll(enrollment())
    service.repo.phone_report(PHONE, PhoneStandReport(id=ID, state="online", seenAgoMs=44000), 1000)
    assert service.views()[PHONE].phone_link == "online"
    service.clock = lambda: 1002
    assert service.views()[PHONE].phone_link == "stale"


def test_missing_report_and_different_stand_cannot_reuse_previous_success(client, setup):
    service, _, _ = setup
    service.enroll(enrollment())
    assert service.views()[PHONE].phone_link == "unknown"
    for report in (None, PhoneStandReport(id="stand-ffffffffffff", state="online", seenAgoMs=0)):
        service.repo.phone_report(PHONE, PhoneStandReport(id=ID, state="online", seenAgoMs=0), 1000)
        service.repo.phone_report(PHONE, report, 1001)
        assert service.views()[PHONE].phone_link == "unknown"


def test_phone_cannot_report_another_phones_link(client, anon, setup):
    service, _, headers = setup
    service.enroll(enrollment())
    response = anon.post(
        "/api/device/sync",
        json={"device": OTHER, "standLink": {"id": ID, "state": "offline"}},
        headers=headers,
    )
    assert response.status_code == 403
    assert service.views()[PHONE].phone_link == "unknown"


@pytest.mark.parametrize("report", [
    {"id": ID, "state": "online"},
    {"state": "offline"},
    {"id": ID, "state": "online", "seenAgoMs": -1},
    {"id": ID, "state": "invented"},
])
def test_phone_link_rejects_ambiguous_reports(client, anon, setup, report):
    _, _, headers = setup
    response = anon.post(
        "/api/device/sync", json={"device": PHONE, "standLink": report}, headers=headers
    )
    assert response.status_code == 422


def test_existing_stand_mapping_survives_report_columns_migration(client, setup):
    from app.db.database import get_connection, init_db

    service, _, _ = setup
    service.enroll(enrollment())
    with get_connection() as conn:
        for column in ("phone_state", "phone_reported_at", "phone_seen_at"):
            conn.execute(f"ALTER TABLE stands DROP COLUMN {column}")
    init_db()
    assert service.repo.get(PHONE)["credential"] == KEY
    assert service.views()[PHONE].phone_link == "unknown"
    assert service.views()[PHONE].online


def test_same_phone_retry_and_ip_change_keep_binding(client, setup):
    service, _, _ = setup
    service.enroll(enrollment())
    service.enroll(enrollment(ip="192.168.1.51"))
    assert len(service.views()) == 1
    assert service.views()[PHONE].ip == "192.168.1.51"


def test_one_stand_per_phone_and_one_phone_per_stand(client, setup):
    service, _, _ = setup
    service.enroll(enrollment())
    with pytest.raises(StandConflict):
        service.enroll(enrollment(device=OTHER))
    with pytest.raises(StandConflict):
        service.enroll(enrollment(id="stand-112233445567"))
    assert len(service.views()) == 1


def test_identity_change_requires_explicit_detach(client, setup):
    service, _, _ = setup
    service.enroll(enrollment())
    changed = enrollment().model_copy(update={"fingerprint": "c" * 64})
    with pytest.raises(StandConflict):
        service.enroll(changed)


def test_live_identity_must_be_verified_before_registration(client, setup):
    service, transport, _ = setup
    transport.wrong_identity = True
    with pytest.raises(ValueError):
        service.enroll(enrollment())
    assert not service.views()


def test_live_owner_must_match_the_enrolling_phone(client, setup):
    service, transport, _ = setup
    original = transport.request
    transport.request = lambda stand, quiet=None: original(stand, quiet) | {"phoneId": OTHER}
    with pytest.raises(ValueError):
        service.enroll(enrollment())
    assert not service.views()


def test_offline_is_independent_of_phone_and_cached_status_is_not_online(client, setup):
    service, transport, _ = setup
    service.enroll(enrollment())
    transport.failed = True
    service.refresh_all()
    assert not service.views()[PHONE].online
    assert service.views()[PHONE].status is not None
    assert KEY not in service.views()[PHONE].model_dump_json()


def test_stale_status_is_not_online(client, setup):
    service, _, _ = setup
    service.enroll(enrollment())
    assert not StandService(clock=lambda: 1046).views()[PHONE].online


def test_ip_change_is_discovered_without_the_phone(client, setup):
    service, transport, _ = setup
    service.enroll(enrollment())
    original = transport.request

    def moved(stand, quiet=None):
        if stand.ip == "192.168.1.50":
            raise ConnectionError("address changed")
        return original(stand, quiet)

    transport.request = moved
    transport.discover = lambda stand: stand.model_copy(update={"ip": "192.168.1.51"})
    service.refresh_all()
    assert service.views()[PHONE].online
    assert service.views()[PHONE].ip == "192.168.1.51"


def test_owner_can_clear_a_stale_mapping_after_physical_reset(client, anon, setup):
    service, _, _ = setup
    service.enroll(enrollment())
    path = f"/devices/{PHONE}/stand/forget"
    assert anon.post(path).status_code == 401
    assert client.post(path, follow_redirects=False).status_code == 303
    service.enroll(enrollment(device=OTHER))
    assert OTHER in service.views() and PHONE not in service.views()


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "169.254.169.254",
        "8.8.8.8",
        "224.1.1.1",
        "::1",
        "example.com",
        "192.168.1.2:8000",
    ],
)
def test_cannot_enroll_external_or_special_endpoints(ip):
    with pytest.raises(ValidationError):
        enrollment(ip=ip)


def test_hub_policy_is_owner_only_and_does_not_need_phone(client, anon, setup):
    service, _, _ = setup
    service.enroll(enrollment())
    path = f"/devices/{PHONE}/stand/policy"
    assert anon.post(path, data={"quiet": "true"}).status_code == 401
    assert (
        client.post(
            path, data={"quiet": "true"}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert client.post(path, data={"quiet": "true"}, follow_redirects=False).status_code == 303
    assert service.views()[PHONE].status.quiet


def test_certificate_checked_before_credential_sent(monkeypatch):
    sent = []

    class Socket:
        def getpeercert(self, binary_form):
            return b"not the enrolled certificate"

    class Connection:
        sock = Socket()

        def __init__(self, *args, **kwargs):
            pass

        def connect(self):
            pass

        def close(self):
            pass

        def request(self, *args, **kwargs):
            sent.append(kwargs)

    monkeypatch.setattr("app.clients.stand.http.client.HTTPSConnection", Connection)
    with pytest.raises(ConnectionError):
        StandTransport().request(enrollment())
    assert not sent


def test_detach_only_affects_own_agent(client, anon, setup):
    service, _, headers = setup
    service.enroll(enrollment())
    assert (
        anon.post("/api/device/stand/detach", json={"device": OTHER}, headers=headers).status_code
        == 403
    )
    assert (
        anon.post("/api/device/stand/detach", json={"device": PHONE}, headers=headers).status_code
        == 200
    )
    assert StandRepository().get(PHONE) is None
