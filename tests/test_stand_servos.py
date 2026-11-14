import pytest

from app.clients.stand import StandTransport
from tests.test_stands import enrollment


@pytest.mark.parametrize(
    "kwargs",
    [
        {"pan_us": 1449},
        {"pan_us": 1551},
        {"tilt_us": 500},
        {"duration_ms": 0},
        {"duration_ms": 5001},
        {"pan_us": True},
    ],
)
def test_invalid_servo_command_never_reaches_network(monkeypatch, kwargs):
    sent = []
    transport = StandTransport()
    monkeypatch.setattr(transport, "_request", lambda *args: sent.append(args))
    with pytest.raises(ValueError):
        transport.servos(enrollment(), "move", **kwargs)
    assert sent == []


def test_servo_command_checks_controller_identity(monkeypatch):
    transport = StandTransport()
    monkeypatch.setattr(transport, "_request", lambda *args: {"id": "other", "phoneId": "other"})
    with pytest.raises(ValueError, match="identity"):
        transport.servos(enrollment(), "center")


def test_stop_needs_no_motion_parameters(monkeypatch):
    sent = []
    stand = enrollment()

    def request(target, path, payload):
        sent.append((path, payload))
        return {"id": stand.id, "phoneId": stand.device}

    transport = StandTransport()
    monkeypatch.setattr(transport, "_request", request)
    transport.servos(stand, "stop")
    assert sent == [("/v1/servos/stop", {})]
