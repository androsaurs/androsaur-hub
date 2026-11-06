from datetime import date

from app.db.database import get_connection
from app.horoscope import DailyHoroscopes


class HoroscopeRepository:
    def provider(self) -> str:
        with get_connection() as conn:
            row = conn.execute("SELECT provider FROM horoscope_settings WHERE id = 1").fetchone()
        return str(row["provider"]) if row else "europaplus"

    def set_provider(self, provider: str) -> None:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO horoscope_settings(id, provider) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET provider = excluded.provider",
                (provider,),
            )

    def cached(self, provider: str, day: str) -> tuple[DailyHoroscopes | None, float, float]:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM horoscope_cache WHERE provider = ? AND day = ?",
                (provider, day),
            ).fetchone()
        if row is None:
            return None, 0, 0
        forecasts = (
            DailyHoroscopes.model_validate_json(row["forecasts"]) if row["forecasts"] else None
        )
        return forecasts, row["fetched_at"], row["retry_after"]

    def save(self, provider: str, forecasts: DailyHoroscopes, now: float) -> None:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO horoscope_cache(provider, day, forecasts, fetched_at, retry_after) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(provider, day) DO UPDATE SET "
                "forecasts=excluded.forecasts, fetched_at=excluded.fetched_at, "
                "retry_after=excluded.retry_after",
                (
                    provider,
                    forecasts.date.isoformat(),
                    forecasts.model_dump_json(),
                    now,
                    now + 3600,
                ),
            )
            conn.execute("DELETE FROM horoscope_cache WHERE day < ?", (forecasts.date.isoformat(),))

    def failed(self, provider: str, day: str, now: float) -> None:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO horoscope_cache(provider, day, fetched_at, retry_after) "
                "VALUES (?, ?, 0, ?) ON CONFLICT(provider, day) DO UPDATE SET "
                "retry_after=excluded.retry_after",
                (provider, day, now + 60),
            )

    def person(self, person: str) -> tuple[str, str, date | None] | None:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT status, zodiac_sign, birth_date FROM house_people WHERE id = ?",
                (person,),
            ).fetchone()
        return (
            (
                row["status"],
                row["zodiac_sign"],
                date.fromisoformat(row["birth_date"]) if row["birth_date"] else None,
            )
            if row
            else None
        )

    def set_sign(self, person: str, sign: str) -> bool:
        with get_connection() as conn:
            return (
                conn.execute(
                    "UPDATE house_people SET zodiac_sign = ? WHERE id = ? AND status = 'active'",
                    (sign, person),
                ).rowcount
                == 1
            )
