import json
import time
from collections.abc import Callable
from typing import Any, cast

from app.clients.stand import StandTransport
from app.repositories.devices import DeviceRepository
from app.repositories.stands import StandRepository
from app.stands import PhoneStandLinkState, StandEnrollment, StandStatus, StandView


class StandService:
    def __init__(
        self,
        repo: StandRepository | None = None,
        transport: StandTransport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.repo = repo or StandRepository()
        self.transport = transport or StandTransport()
        self.clock = clock

    @staticmethod
    def enrollment(row: dict[str, Any]) -> StandEnrollment:
        return StandEnrollment(
            device=row["device"],
            id=row["id"],
            fingerprint=row["fingerprint"],
            key=row["credential"],
            ip=row["ip"],
        )

    def read(self, stand: StandEnrollment, quiet: bool | None = None) -> StandStatus:
        status = StandStatus.model_validate(self.transport.request(stand, quiet))
        if status.id != stand.id or status.phone_id != stand.device:
            raise ValueError("stand identity mismatch")
        return status

    def enroll(self, stand: StandEnrollment) -> None:
        if not DeviceRepository().exists(stand.device):
            raise LookupError("unknown phone")
        status = self.read(stand)
        self.repo.enroll(stand)
        self.repo.status(stand, status.model_dump(), self.clock())

    def refresh_all(self) -> None:
        for row in self.repo.rows():
            stand = self.enrollment(row)
            try:
                status = self.read(stand)
                self.repo.status(stand, status.model_dump(), self.clock())
            except (OSError, ValueError):
                try:
                    discovered = self.transport.discover(stand)
                    if discovered is None:
                        raise ConnectionError("stand not discovered")
                    status = self.read(discovered)
                    self.repo.status(discovered, status.model_dump(), self.clock())
                except (OSError, ValueError):
                    self.repo.failed(stand)

    def policy(self, device: str, quiet: bool) -> None:
        row = self.repo.get(device)
        if row is None:
            raise LookupError("stand not attached")
        stand = self.enrollment(row)
        try:
            status = self.read(stand, quiet)
            if status.quiet != quiet:
                raise ValueError("policy was not applied")
            self.repo.status(stand, status.model_dump(), self.clock())
        except (OSError, ValueError):
            self.repo.failed(stand)
            raise

    def views(self) -> dict[str, StandView]:
        now = self.clock()
        def phone_link(row: dict[str, Any]) -> PhoneStandLinkState:
            if row["phone_state"] is None or row["phone_reported_at"] is None:
                return "unknown"
            if not 0 <= now - row["phone_reported_at"] <= 90:
                return "stale"
            if row["phone_state"] == "online" and (
                row["phone_seen_at"] is None or not 0 <= now - row["phone_seen_at"] <= 45
            ):
                return "stale"
            return cast(PhoneStandLinkState, row["phone_state"])

        return {
            row["device"]: StandView(
                id=row["id"],
                device=row["device"],
                ip=row["ip"],
                online=row["last_seen_at"] is not None
                and 0 <= now - row["last_seen_at"] <= 45
                and not row["error"],
                last_seen_at=row["last_seen_at"],
                status=StandStatus.model_validate(json.loads(row["status"]))
                if row["status"]
                else None,
                error=row["error"],
                phone_link=phone_link(row),
                phone_reported_ago_s=max(0, int(now - row["phone_reported_at"]))
                if row["phone_reported_at"] is not None
                else None,
            )
            for row in self.repo.rows()
        }
