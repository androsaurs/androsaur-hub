import pytest

from app.clients.stand import StandTransport
from tests.test_stands import enrollment


@pytest.mark.parametrize(
    "enabled,duration",
    [
        (True, 999),
        (True, 120001),
        (1, 1000),
        (True, True),
        (True, 1000.5),
    ],
)
def test_invalid_override_never_reaches_controller(monkeypatch, enabled, duration):
    sent = []
    client = StandTransport()
    monkeypatch.setattr(client, "_request", lambda *args: sent.append(args))
    with pytest.raises(ValueError):
        client.charger(enrollment(), enabled, duration)
    assert sent == []


def test_auto_clears_override_without_faking_battery_data(monkeypatch):
    stand = enrollment()
    sent = []

    def request(target, path, payload):
        sent.append((path, payload))
        return {"id": stand.id, "phoneId": stand.device}

    client = StandTransport()
    monkeypatch.setattr(client, "_request", request)
    client.charger(stand, None)
    assert sent == [("/v1/charger", {"mode": "auto"})]


def test_override_verifies_reply_identity(monkeypatch):
    client = StandTransport()
    monkeypatch.setattr(client, "_request", lambda *args: {"id": "wrong", "phoneId": "wrong"})
    with pytest.raises(ValueError, match="identity"):
        client.charger(enrollment(), True)
