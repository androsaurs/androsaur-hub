import json
from typing import Any

from app.db.database import get_connection
from app.trace import Event

KEEP_DAYS = 14


class TraceRepository:
    """События профилирования. Хранятся ``KEEP_DAYS`` суток, потом стираются при записи."""

    def add(self, events: list[Event], device: str, now_ms: int) -> None:
        rows = [
            (
                str(e["run"])[:40],
                int(e["ts"]),
                str(e["src"])[:10],
                str(e["stage"])[:40],
                json.dumps(e.get("data") or {}, ensure_ascii=False),
                device,
            )
            for e in events
        ]
        with get_connection() as conn:
            conn.executemany(
                "INSERT INTO trace_events (run, ts, src, stage, data, device) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
            # Старые стираются по времени получения хабом: у телефона и хаба часы могут расходиться.
            conn.execute(
                "DELETE FROM trace_events WHERE run IN "
                "(SELECT run FROM trace_events GROUP BY run HAVING MAX(ts) < ?)",
                (now_ms - KEEP_DAYS * 86_400_000,),
            )

    def since(self, since_ms: int) -> list[Event]:
        """События прогонов, начавшихся не раньше ``since_ms``."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT run, ts, src, stage, data FROM trace_events WHERE run IN "
                "(SELECT run FROM trace_events WHERE src != 'hub' "
                "GROUP BY run HAVING MIN(ts) >= ?) "
                "ORDER BY id",
                (since_ms,),
            ).fetchall()
        return [_event(r) for r in rows]

    def clear(self) -> None:
        with get_connection() as conn:
            conn.execute("DELETE FROM trace_events")


def _event(row: Any) -> Event:
    return {
        "run": row["run"],
        "ts": row["ts"],
        "src": row["src"],
        "stage": row["stage"],
        "data": json.loads(row["data"]),
    }
