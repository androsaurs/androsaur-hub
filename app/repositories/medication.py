from app.db.database import get_connection


class MedicationRepository:
    """Времена приёма лекарств по людям дома и журнал напоминаний."""

    def times(self) -> dict[str, list[str]]:
        with get_connection() as conn:
            rows = conn.execute("SELECT person, at FROM medication_times ORDER BY at").fetchall()
        result: dict[str, list[str]] = {}
        for row in rows:
            result.setdefault(row["person"], []).append(row["at"])
        return result

    def add_time(self, person: str, at: str, now: float) -> bool:
        with get_connection() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO medication_times (person, at, added_at) VALUES (?, ?, ?)",
                (person, at, now),
            )
        return cursor.rowcount > 0

    def since(self) -> dict[str, float]:
        """Когда у человека появился график (самое раннее из его времён)."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT person, MIN(added_at) AS since FROM medication_times GROUP BY person"
            ).fetchall()
        return {row["person"]: row["since"] for row in rows}

    def delete_time(self, person: str, at: str) -> None:
        with get_connection() as conn:
            conn.execute("DELETE FROM medication_times WHERE person = ? AND at = ?", (person, at))

    def log_add(self, person: str, slot: str, kind: str, at_ms: int, device: str) -> None:
        """Одно событие вида на интервал: повтор от телефона (после обрыва связи) не дублируется."""
        with get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO medication_log (person, slot, kind, at_ms, device) "
                "VALUES (?, ?, ?, ?, ?)",
                (person, slot, kind, at_ms, device),
            )

    def log_since(self, slot_from: str) -> dict[tuple[str, str], dict[str, int]]:
        """Журнал с интервалов не раньше ``slot_from``: (человек, интервал) → {вид: время, мс}."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT person, slot, kind, at_ms FROM medication_log WHERE slot >= ?", (slot_from,)
            ).fetchall()
        result: dict[tuple[str, str], dict[str, int]] = {}
        for row in rows:
            result.setdefault((row["person"], row["slot"]), {})[row["kind"]] = row["at_ms"]
        return result
