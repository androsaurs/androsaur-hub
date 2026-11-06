"""Derived profile facts. A birthday gives age, not school or retirement status."""

from datetime import date

from app.horoscope import SIGNS


def age_on(birth_date: date, today: date) -> int | None:
    if birth_date > today:
        return None
    # For Feb 29, the anniversary in a non-leap year is March 1.
    return (
        today.year
        - birth_date.year
        - ((today.month, today.day) < (birth_date.month, birth_date.day))
    )


def zodiac_for_birth_date(birth_date: date) -> str:
    """Conventional calendar boundaries; a manual sign can override them."""
    day = (birth_date.month, birth_date.day)
    for boundary, sign in (
        ((1, 20), "capricorn"),
        ((2, 19), "aquarius"),
        ((3, 20), "pisces"),
        ((4, 20), "aries"),
        ((5, 20), "taurus"),
        ((6, 21), "gemini"),
        ((7, 22), "cancer"),
        ((8, 23), "leo"),
        ((9, 23), "virgo"),
        ((10, 23), "libra"),
        ((11, 22), "scorpio"),
        ((12, 21), "sagittarius"),
    ):
        if day <= boundary:
            return sign
    return "capricorn"


def effective_zodiac_sign(manual: str, birth_date: date | None) -> str:
    if manual in SIGNS:
        return manual
    return zodiac_for_birth_date(birth_date) if birth_date else ""


def age_label(years: int) -> str:
    suffix = "лет"
    if years % 100 not in (11, 12, 13, 14):
        if years % 10 == 1:
            suffix = "год"
        elif years % 10 in (2, 3, 4):
            suffix = "года"
    return f"{years} {suffix}"
