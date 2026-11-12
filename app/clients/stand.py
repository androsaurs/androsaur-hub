import hashlib
import hmac
import http.client
import json
import ssl
from typing import Any

from zeroconf import Zeroconf

from app.stands import StandEnrollment


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
            body = None if quiet is None else json.dumps({"quiet": quiet})
            conn.request(
                "GET" if quiet is None else "POST",
                "/v1/status" if quiet is None else "/v1/policy",
                body=body,
                headers={
                    "Authorization": f"Bearer {stand.key.get_secret_value()}",
                    "Content-Type": "application/json",
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
