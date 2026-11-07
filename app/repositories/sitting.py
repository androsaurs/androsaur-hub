from app.db.database import get_connection

DEFAULTS = {"enabled": 0, "first_min": 40, "repeat_min": 20, "break_min": 5}


class SittingRepository:
    """Настройки «не засиживаться» по людям дома и ячейки для тепловой карты."""

    def settings(self) -> dict[str, dict[str, int]]:
        with get_connection() as conn:
            rows = conn.execute("SELECT * FROM sitting_settings").fetchall()
        return {r["person"]: {k: int(r[k]) for k in DEFAULTS} for r in rows}

    def save(self, person: str, enabled: bool, first: int, repeat: int, rest: int) -> None:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO sitting_settings (person, enabled, first_min, repeat_min, break_min) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(person) DO UPDATE SET "
                "enabled = excluded.enabled, first_min = excluded.first_min, "
                "repeat_min = excluded.repeat_min, break_min = excluded.break_min",
                (person, int(enabled), first, repeat, rest),
            )

    def put_bucket(
        self, person: str, bucket: str, present_s: int, streak_min: int, level: int
    ) -> None:
        """Ячейка не уменьшается: телефон может прислать её повторно, а позже дополнить."""
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO sitting_buckets (person, bucket, present_s, streak_min, level) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(person, bucket) DO UPDATE SET "
                "present_s = MAX(present_s, excluded.present_s), "
                "streak_min = MAX(streak_min, excluded.streak_min), "
                "level = MAX(level, excluded.level)",
                (person, bucket, present_s, streak_min, level),
            )

    def buckets_since(self, bucket_from: str) -> dict[str, dict[str, tuple[int, int, int]]]:
        """person → bucket → (секунд на месте, самая долгая серия в минутах, уровень)."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT person, bucket, present_s, streak_min, level FROM sitting_buckets "
                "WHERE bucket >= ?",
                (bucket_from,),
            ).fetchall()
        result: dict[str, dict[str, tuple[int, int, int]]] = {}
        for r in rows:
            result.setdefault(r["person"], {})[r["bucket"]] = (
                r["present_s"],
                r["streak_min"],
                r["level"],
            )
        return result
