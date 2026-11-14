import hashlib
import hmac
import http.client
import json
import ssl
from typing import Any

from zeroconf import Zeroconf

from app.stands import StandEnrollment, TrackingSettings


class StandTransport:
    """Verify the BLE-enrolled certificate before sending the delegated credential.

    IP is validated, port/paths are fixed, redirects are never followed.
    The explicit pin replaces the public CA/hostname checks for this self-signed device.
    """

    def discover(self, stand: StandEnrollment) -> StandEnrollment | None:
        service_type = "_androsaur-stand._tcp.local."
        with Zeroconf() as mdns:
            info = mdns.get_service_info(service_type, stand.id + "." + service_type, timeout=1500)
            if info is None or info.port != 8443 or info.properties.get(b"id") != stand.id.encode():
                return None
            for address in info.parsed_addresses():
                try:
                    return StandEnrollment(
                        device=stand.device,
                        id=stand.id,
                        fingerprint=stand.fingerprint,
                        key=stand.key,
                        ip=address,
                    )
                except ValueError:
                    continue
        return None

    def request(self, stand: StandEnrollment, quiet: bool | None = None) -> dict[str, Any]:
        return self._request(
            stand,
            "/v1/status" if quiet is None else "/v1/policy",
            None if quiet is None else {"quiet": quiet},
        )

    def servos(
        self,
        stand: StandEnrollment,
        action: str,
        *,
        pan_us: int = 1500,
        tilt_us: int = 1500,
        duration_ms: int = 1000,
    ) -> dict[str, Any]:
        if action not in {"center", "stop", "move"}:
            raise ValueError("unsupported servo action")
        if action == "move" and (
            any(type(v) is not int for v in (pan_us, tilt_us, duration_ms))
            or not 1450 <= pan_us <= 1550
            or not 1450 <= tilt_us <= 1550
            or not 20 <= duration_ms <= 5000
        ):
            raise ValueError("servo command outside uncalibrated limits")
        path = "/v1/servos" if action == "move" else f"/v1/servos/{action}"
        payload = (
            {"panUs": pan_us, "tiltUs": tilt_us, "durationMs": duration_ms}
            if action == "move"
            else {}
        )
        data = self._request(stand, path, payload)
        if data.get("id") != stand.id or data.get("phoneId") != stand.device:
            raise ValueError("stand identity mismatch")
        return data

    def tracking(
        self, stand: StandEnrollment, settings: TrackingSettings | bool
    ) -> dict[str, Any]:
        payload = (
            {"enabled": settings}
            if isinstance(settings, bool)
            else settings.model_dump(by_alias=True)
        )
        path = "/v1/tracking/control" if isinstance(settings, bool) else "/v1/tracking/settings"
        return self._request(stand, path, payload)

    def charger(
        self,
        stand: StandEnrollment,
        enabled: bool | None,
        duration_ms: int = 60000,
    ) -> dict[str, Any]:
        if enabled is not None and (
            type(enabled) is not bool
            or type(duration_ms) is not int
            or not 1000 <= duration_ms <= 120000
        ):
            raise ValueError("invalid charger override")
        payload = (
            {"mode": "auto"}
            if enabled is None
            else {
                "enabled": enabled,
                "durationMs": duration_ms,
            }
        )
        data = self._request(stand, "/v1/charger", payload)
        if data.get("id") != stand.id or data.get("phoneId") != stand.device:
            raise ValueError("stand identity mismatch")
        return data

    def _request(
        self,
        stand: StandEnrollment,
        path: str,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        conn = http.client.HTTPSConnection(stand.ip, 8443, timeout=3, context=context)
        try:
            conn.connect()
            if conn.sock is None:
                raise ConnectionError("no TLS connection")
            certificate = conn.sock.getpeercert(binary_form=True)
            if not certificate or not hmac.compare_digest(
                hashlib.sha256(certificate).hexdigest(), stand.fingerprint
            ):
                raise ConnectionError("certificate mismatch")
            body = None if payload is None else json.dumps(payload)
            conn.request(
                "GET" if payload is None else "POST",
                path,
                body=body,
                headers={
                    "Authorization": f"Bearer {stand.key.get_secret_value()}",
                    "Content-Type": "application/json",
                    "Connection": "close",
                },
            )
            reply = conn.getresponse()
            if reply.status != 200:
                raise ConnectionError("stand rejected request")
            raw = reply.read(8193)
            if len(raw) > 8192:
                raise ValueError("stand response too large")
            data: dict[str, Any] = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("invalid stand response")
            return data
        finally:
            conn.close()
