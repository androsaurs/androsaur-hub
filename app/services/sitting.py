import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.repositories.devices import DeviceRepository
from app.repositories.people import PeopleRepository
from app.repositories.sitting import DEFAULTS, SittingRepository
from app.schemas import SittingBucket, SittingPlan

HISTORY_DAYS = 7
CELL_MIN = 15
CELLS_PER_DAY = 24 * 60 // CELL_MIN
MAX_MIN = 600


class SittingError(Exception):
    """Понятная причина отказа; текст показывается в админке."""


@dataclass
class Cell:
    style: str  # CSS-цвет ячейки
    title: str


@dataclass
class DayRow:
    label: str
    cells: list[Cell]


@dataclass
class PersonHealth:
    id: str
    name: str
    enabled: bool
    first_min: int
    repeat_min: int
    break_min: int
    today_streak_min: int
    today_present_min: int
    days: list[DayRow]


_ABSENT = "#e5e7eb"
_FUTURE = "#f3f4f6"


def cell_color(streak_min: int, scale_min: int) -> str:
    """От зелёного (серия короткая) до красного (серия дошла до конца шкалы)."""
    ratio = min(1.0, streak_min / max(1, scale_min))
    return f"hsl({round(120 * (1 - ratio))} 70% 48%)"


class SittingService:
    """«Не засиживаться»: настройки людей и тепловая карта непрерывного сидения."""

    def __init__(
        self,
        repository: SittingRepository | None = None,
        people: PeopleRepository | None = None,
        devices: DeviceRepository | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repository = repository or SittingRepository()
        self._people = people or PeopleRepository()
        self._devices = devices or DeviceRepository()
        self._clock = clock

    def _active(self) -> dict[str, str]:
        return {p.id: p.name for p in self._people.house().values() if p.status == "active"}

    def plans(self) -> list[SittingPlan]:
        """Для телефона: настройки тех, у кого включено."""
        names = self._active()
        saved = self._repository.settings()
        return [
            SittingPlan(
                person=person,
                name=names[person],
                first_min=s["first_min"],
                repeat_min=s["repeat_min"],
                break_min=s["break_min"],
            )
            for person, s in sorted(saved.items(), key=lambda kv: names.get(kv[0], ""))
            if person in names and s["enabled"]
        ]

    def save(self, person: str, enabled: bool, first: int, repeat: int, rest: int) -> None:
        if person not in self._active():
            raise SittingError("Такого человека нет в доме")
        for label, value in (("Первое напоминание", first), ("Повтор", repeat), ("Перерыв", rest)):
            if not 1 <= value <= MAX_MIN:
                raise SittingError(f"{label}: от 1 до {MAX_MIN} минут")
        self._repository.save(person, enabled, first, repeat, rest)

    def record(self, device: str, buckets: list[SittingBucket]) -> bool:
        """Принимает ячейки телефона. False — устройство не зарегистрировано."""
        if not self._devices.exists(device):
            return False
        known = set(self._active())
        for b in buckets:
            if b.person in known:
                self._repository.put_bucket(b.person, b.bucket, b.present_s, b.streak_min, b.level)
        return True

    def view(self) -> list[PersonHealth]:
        now = datetime.fromtimestamp(self._clock())
        today = now.date()
        first_day = today - timedelta(days=HISTORY_DAYS - 1)
        data = self._repository.buckets_since(first_day.strftime("%Y-%m-%dT00:00"))
        saved = self._repository.settings()
        result = []
        for person, name in sorted(self._active().items(), key=lambda kv: kv[1].casefold()):
            s = saved.get(person, DEFAULTS)
            scale = s["first_min"] + s["repeat_min"]
            mine = data.get(person, {})
            rows = []
            for offset in range(HISTORY_DAYS):
                day = today - timedelta(days=offset)
                rows.append(DayRow(day.strftime("%d.%m"), self._cells(day, now, mine, scale)))
            todays = [v for k, v in mine.items() if k.startswith(today.isoformat())]
            result.append(
                PersonHealth(
                    person,
                    name,
                    bool(s["enabled"]),
                    s["first_min"],
                    s["repeat_min"],
                    s["break_min"],
                    max((v[1] for v in todays), default=0),
                    round(sum(v[0] for v in todays) / 60),
                    rows,
                )
            )
        return result

    @staticmethod
    def _cells(
        day: date, now: datetime, mine: dict[str, tuple[int, int, int]], scale: int
    ) -> list[Cell]:
        cells = []
        for n in range(CELLS_PER_DAY):
            start = datetime.combine(day, datetime.min.time()) + timedelta(minutes=CELL_MIN * n)
            span = f"{start:%d.%m %H:%M}–{start + timedelta(minutes=CELL_MIN):%H:%M}"
            got = mine.get(start.strftime("%Y-%m-%dT%H:%M"))
            if start > now:
                cells.append(Cell(_FUTURE, span))
            elif got is None or got[0] <= 0:
                cells.append(Cell(_ABSENT, f"{span}: вас не было"))
            else:
                present, streak, _level = got
                text = (
                    f"{span}: на месте {max(1, round(present / 60))} мин, без перерыва {streak} мин"
                )
                cells.append(Cell(cell_color(streak, scale), text))
        return cells
