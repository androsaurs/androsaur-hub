import pytest

from app.db.database import get_connection, init_db
from app.repositories.devices import DeviceRepository
from app.services.devices import DeviceService
from app.stands import StandBatteryStatus
from app.templating import templates
from tests.test_settings import DEVICE, register, sync


def test_phone_battery_without_stand_is_visible_and_updates(client):
    register(client)
    assert sync(client, battery={"percent": 73, "charging": True, "ageMs": 1200}).status_code == 200
    page = client.get("/devices").text
    assert "73%" in page and "Идёт зарядка" in page
    assert sync(client, battery={"percent": 72, "charging": False, "ageMs": 0}).status_code == 200
    live = client.get("/devices/battery.json")
    assert live.status_code == 200 and live.headers["cache-control"] == "no-store"
    assert "72%" in live.json()["phones"][DEVICE]
    assert "Не идёт зарядка" in live.json()["phones"][DEVICE]
    assert live.json()["stands"] == {}


def test_missing_battery_does_not_fabricate_fresh_values(client):
    register(client)
    assert "ещё не передал состояние батареи" in client.get("/devices").text
    sync(client, battery={"percent": 0, "charging": False, "ageMs": 1000})
    saved = DeviceRepository().list()[0]
    sync(client, battery=None)
    assert DeviceRepository().list()[0].battery_observed_at == saved.battery_observed_at
    view = DeviceService(clock=lambda: saved.battery_observed_at + 91).all()[0]
    assert view.battery.percent == 0 and not view.battery.fresh
    html = templates.env.get_template("_phone_battery.html").render(battery=view.battery)
    assert "Данные устарели" in html and "последнее состояние" in html


@pytest.mark.parametrize("battery", [
    {"percent": -1, "charging": True, "ageMs": 0},
    {"percent": 101, "charging": True, "ageMs": 0},
    {"percent": 50.5, "charging": True, "ageMs": 0},
    {"percent": 50, "charging": "false", "ageMs": 0},
    {"percent": 50, "charging": False, "ageMs": 60001},
    {"percent": 50, "ageMs": 0},
])
def test_invalid_battery_rejected(client, battery):
    register(client)
    assert sync(client, battery=battery).status_code == 422
    assert DeviceRepository().list()[0].battery_percent is None


def test_other_phone_cannot_write_battery_and_pages_require_owner(client, anon):
    register(client)
    token = client.device_token("other-device-0000")
    payload = {"device": DEVICE, "battery": {"percent": 1, "charging": False, "ageMs": 0}}
    assert anon.post("/api/device/sync", json=payload).status_code == 401
    assert anon.post("/api/device/sync", json=payload,
                     headers={"Authorization": f"Bearer {token}"}).status_code == 403
    assert anon.get("/devices/battery.json", follow_redirects=False).status_code == 303
    assert DeviceRepository().list()[0].battery_percent is None


def test_battery_migration_preserves_device_and_is_idempotent(client):
    register(client)
    with get_connection() as conn:
        for field in ("battery_percent", "battery_charging", "battery_observed_at"):
            conn.execute(f"ALTER TABLE devices DROP COLUMN {field}")
    init_db()
    init_db()
    device = DeviceRepository().list()[0]
    assert device.id == DEVICE and device.battery_percent is None
    assert sync(client, battery={"percent": 65, "charging": False, "ageMs": 0}).status_code == 200


def test_out_of_order_measurement_does_not_overwrite_newer(client):
    register(client)
    repo = DeviceRepository()
    from app.battery import PhoneBatteryReport
    repo.battery(DEVICE, PhoneBatteryReport(percent=75, charging=True, ageMs=0), 1000)
    repo.battery(DEVICE, PhoneBatteryReport(percent=1, charging=False, ageMs=60000), 1001)
    assert repo.list()[0].battery_percent == 75


def test_disabled_switch_is_not_presented_as_physical_pause(client):
    from app.stands import StandStatus, StandView
    power = StandBatteryStatus(outputEnabled=False, chargeAllowed=False, startPercent=40,
                               stopPercent=80, state="paused", percent=100, charging=True, ageMs=10)
    stand = StandView(id="stand-123456789abc", device=DEVICE, ip="192.168.1.57", online=True,
                      last_seen_at=1000, error=None, battery_ago_s=0, battery_fresh=True,
                      status=StandStatus(id="stand-123456789abc", phoneId=DEVICE, firmware="test",
                                         protocol=1, capabilities=["battery"], quiet=False,
                                         battery=power))
    html = templates.env.get_template("_stand_battery.html").render(stand=stand)
    assert "Аппаратное управление не включено" in html and "Решение подставки" in html
    assert "Подставка приостановила зарядку" not in html
