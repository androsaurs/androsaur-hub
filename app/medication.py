"""Приём лекарств: интервалы между временами приёма (чистая логика, без БД).

Сутки делятся временами приёма на интервалы, интервал бесшовно переходит в новые сутки: при
временах 08:00 и 18:00 интервалы «с 08:00 до 18:00» и «с 18:00 до 08:00 следующего дня». Один
интервал — одно напоминание. Ключ интервала — время его начала «ГГГГ-ММ-ДДTЧЧ:ММ» по местным часам
(их же использует телефон). Те же правила в приложении ``androsaur`` (``MedicationSchedule``).
"""

import re
from datetime import date, datetime, timedelta

SLOT_FORMAT = "%Y-%m-%dT%H:%M"
MAX_TIMES = 12  # приёмов в сутки у одного человека
_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def normalize_time(text: str) -> str | None:
    """«8:5» → None, «8:05» → «08:05»; None, если это не время ЧЧ:ММ."""
    match = _TIME.match(text.strip())
    return None if match is None else f"{int(match.group(1)):02d}:{match.group(2)}"


def _at(day: date, hhmm: str) -> datetime:
    return datetime.combine(day, datetime.strptime(hhmm, "%H:%M").time())


def slot_key(start: datetime) -> str:
    return start.strftime(SLOT_FORMAT)


def starts_on(times: list[str], day: date) -> list[datetime]:
    """Начала интервалов в этот день по возрастанию."""
    return [_at(day, t) for t in sorted(set(times))]


def interval_at(times: list[str], now: datetime) -> tuple[datetime, datetime] | None:
    """Интервал, идущий в момент ``now``: (начало, конец); None, если времён нет.

    Начало — последнее время приёма не позже ``now`` (вчерашнее, пока сегодняшнее не наступило),
    конец — следующее время приёма (у единственного времени — через сутки)."""
    if not times:
        return None
    today = now.date()
    starts = starts_on(times, today - timedelta(days=1)) + starts_on(times, today)
    starts += starts_on(times, today + timedelta(days=1))
    current = max(s for s in starts if s <= now)
    following = min(s for s in starts if s > current)
    return current, following
