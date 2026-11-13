import time
from collections.abc import Callable
from typing import Any

from app.repositories.devices import DeviceRepository
from app.repositories.tracking import TrackingRepository
from app.tracking import TrackingBatch


class TrackingService:
    def __init__(
        self, repository: TrackingRepository | None = None, clock: Callable[[], float] = time.time
    ) -> None:
        self.repository = repository or TrackingRepository()
        self.clock = clock

    def record(self, batch: TrackingBatch) -> bool:
        if not DeviceRepository().exists(batch.device):
            return False
        self.repository.add(batch, self.clock())
        return True

    def history(self, device: str, seconds: int = 60) -> dict[str, Any]:
        if not DeviceRepository().exists(device):
            raise LookupError("unknown phone")
        now = self.clock()
        return {
            "now": round(now * 1000),
            "seconds": seconds,
            "points": self.repository.recent(device, now - seconds),
        }
