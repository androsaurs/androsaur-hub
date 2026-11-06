"""Provider-independent zodiac IDs and the daily forecast contract."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from pydantic import BaseModel, Field

SIGNS = {
    "aries": "Овен",
    "taurus": "Телец",
    "gemini": "Близнецы",
    "cancer": "Рак",
    "leo": "Лев",
    "virgo": "Дева",
    "libra": "Весы",
    "scorpio": "Скорпион",
    "sagittarius": "Стрелец",
    "capricorn": "Козерог",
    "aquarius": "Водолей",
    "pisces": "Рыбы",
}


class HoroscopeError(Exception):
    """The provider could not supply a complete, valid daily forecast."""


class DailyHoroscopes(BaseModel):
    date: date
    texts: dict[str, str]
    # Some sources expose only 'today', without a publication date.
    source_date_verified: bool = False


class HoroscopeProvider(Protocol):
    def today(self, day: date) -> DailyHoroscopes: ...


@dataclass(frozen=True)
class HoroscopeSource:
    name: str
    factory: Callable[[], HoroscopeProvider]
    speech_name: str | None = None


class HoroscopeRequest(BaseModel):
    person: str = Field(min_length=1, max_length=100)
    for_greeting: bool = False


class HoroscopeReply(BaseModel):
    status: str
    date: date
    timezone: str
    valid_until_ms: int
    person: str
    greeting_enabled: bool = False
    sign: str | None = None
    sign_name: str | None = None
    text: str | None = None
    source: str | None = None
    source_name: str | None = None
    source_speech_name: str | None = None
    fetched_at_ms: int | None = None
    source_date_verified: bool = False
