import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.medication import (
    MAX_TIMES,
    interval_at,
    normalize_time,
    slot_key,
    starts_on,
)
from app.repositories.devices import DeviceRepository
from app.repositories.medication import MedicationRepository
from app.repositories.people import PeopleRepository
from app.schemas import MedicationEvent, MedicationPlan

HISTORY_DAYS = 7


class MedicationError(Exception):
    """Понятная причина отказа; текст показывается в админке."""


@dataclass
class SlotView:
    """Один интервал в истории: когда начался и чем закончился."""

    start: datetime
    status: str  # taken | reminded | unseen | now | later
    text: str


@dataclass
class PersonView:
    id: str
    name: str
    times: list[str]
    slots: list[SlotView]  # новые первыми


_STATUS_TEXT = {
    "taken": "принял",
    "reminded": "напомнили, подтверждения не было",
    "unseen": "не видели: напоминания не было",
    "now": "идёт сейчас",
}


class MedicationService:
    """Графики приёма лекарств людей дома и журнал напоминаний от телефонов."""

    def __init__(
        self,
        repository: MedicationRepository | None = None,
        people: PeopleRepository | None = None,
        devices: DeviceRepository | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repository = repository or MedicationRepository()
        self._people = people or PeopleRepository()
        self._devices = devices or DeviceRepository()
        self._clock = clock

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self._clock())

    def _active(self) -> dict[str, str]:
        return {p.id: p.name for p in self._people.house().values() if p.status == "active"}

    def plans(self) -> list[MedicationPlan]:
        """Для телефона: график каждого, у кого заданы времена."""
        names = self._active()
        times = self._repository.times()
        return [
            MedicationPlan(person=person, name=names[person], times=at)
            for person, at in sorted(times.items(), key=lambda kv: names.get(kv[0], ""))
            if person in names and at
        ]

    def add_time(self, person: str, text: str) -> None:
        at = normalize_time(text)
        if at is None:
            raise MedicationError("Время приёма — часы и минуты, например 08:30")
        if person not in self._active():
            raise MedicationError("Такого человека нет в доме")
        current = self._repository.times().get(person, [])
        if at in current:
            raise MedicationError(f"Время {at} уже есть в графике")
        if len(current) >= MAX_TIMES:
            raise MedicationError(f"Не больше {MAX_TIMES} приёмов в сутки")
        self._repository.add_time(person, at, self._clock())

    def remove_time(self, person: str, text: str) -> None:
        at = normalize_time(text)
        if at is not None:
            self._repository.delete_time(person, at)

    def record(self, device: str, events: list[MedicationEvent]) -> bool:
        """Принимает события телефона. False — устройство не зарегистрировано."""
        if not self._devices.exists(device):
            return False
        known = set(self._active())
        for e in events:
            if e.person in known:
                self._repository.log_add(e.person, e.slot, e.kind, e.at, device)
        return True

    def view(self) -> list[PersonView]:
        """Люди дома с их графиком и историей за последние дни."""
        now = self._now()
        today = now.date()
        first_day = today - timedelta(days=HISTORY_DAYS - 1)
        log = self._repository.log_since(slot_key(datetime.combine(first_day, datetime.min.time())))
        times = self._repository.times()
        since = self._repository.since()
        result = []
        for person, name in sorted(self._active().items(), key=lambda kv: kv[1].casefold()):
            at = times.get(person, [])
            current = interval_at(at, now)
            slots = []
            # История с интервала, шедшего при появлении графика: раньше «не видели» не бывает.
            began = (
                interval_at(at, datetime.fromtimestamp(since[person])) if person in since else None
            )
            for offset in range(HISTORY_DAYS):
                day: date = today - timedelta(days=offset)
                for start in reversed(starts_on(at, day)):
                    if start > now or (began is not None and start < began[0]):
                        continue
                    events = log.get((person, slot_key(start)), {})
                    if "taken" in events:
                        status = "taken"
                    elif current is not None and start == current[0]:
                        status = "reminded" if "reminded" in events else "now"
                    elif "reminded" in events:
                        status = "reminded"
                    else:
                        status = "unseen"
                    slots.append(SlotView(start, status, _STATUS_TEXT[status]))
            result.append(PersonView(person, name, at, slots))
        return result
