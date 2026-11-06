from datetime import date
from typing import Any

import httpx

from app.horoscope import SIGNS, DailyHoroscopes, HoroscopeError

URL = "https://admin.europaplus.ru/api/horoscope"
SIGN_CODES = dict(
    zip(
        SIGNS,
        (
            "oven",
            "telec",
            "bliznecy",
            "rak",
            "lev",
            "deva",
            "vesy",
            "scorpio",
            "strelec",
            "rozeyrog",
            "vodoley",
            "ryby",
        ),
        strict=True,
    )
)


def parse_forecasts(payload: Any, day: date) -> DailyHoroscopes:
    try:
        rows = payload["data"]["type"]
        if not isinstance(rows, dict) or len(rows) != 12:
            raise ValueError("Expected twelve signs")
        by_code = {row["code"]: row["today"] for row in rows.values()}
        texts = {}
        for sign, code in SIGN_CODES.items():
            text = by_code[code]
            if not isinstance(text, str) or not 5 <= len(text.strip()) <= 3000:
                raise ValueError("Invalid forecast text")
            if "<" in text or ">" in text:
                raise ValueError("HTML is not a forecast")
            texts[sign] = " ".join(text.split())
        return DailyHoroscopes(date=day, texts=texts)
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise HoroscopeError("Europa Plus вернула неполный или неизвестный формат") from e


class EuropaPlusProvider:
    def today(self, day: date) -> DailyHoroscopes:
        try:
            response = httpx.get(
                URL,
                params={"region": 85},
                timeout=5,
                headers={"User-Agent": "androsaur-hub (+https://github.com/androsaurs)"},
            )
            response.raise_for_status()
            return parse_forecasts(response.json(), day)
        except (httpx.HTTPError, ValueError) as e:
            raise HoroscopeError("Europa Plus сейчас недоступна") from e
