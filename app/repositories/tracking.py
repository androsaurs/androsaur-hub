from typing import Any

from app.db.database import get_connection
from app.tracking import TrackingBatch


class TrackingRepository:
    def add(self, batch: TrackingBatch, now: float) -> None:
        with get_connection() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO tracking_observations "
                "(device, session, seq, observed_at, received_at, x, y) VALUES (?,?,?,?,?,?,?)",
                [
                    (batch.device, batch.session, s.seq, now - s.age_ms / 1000, now, s.x, s.y)
                    for s in batch.samples
                ],
            )
            conn.execute("DELETE FROM tracking_observations WHERE received_at < ?", (now - 900,))
            conn.execute(
                "DELETE FROM tracking_observations WHERE device=? AND rowid NOT IN "
                "(SELECT rowid FROM tracking_observations WHERE device=? "
                "ORDER BY observed_at DESC, seq DESC LIMIT 5000)",
                (batch.device, batch.device),
            )

    def recent(self, device: str, since: float) -> list[dict[str, Any]]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT observed_at, x, y, session FROM tracking_observations "
                "WHERE device=? AND observed_at>=? ORDER BY observed_at, seq LIMIT 2500",
                (device, since),
            ).fetchall()
        return [
            {
                "at": round(r["observed_at"] * 1000),
                "x": r["x"],
                "y": r["y"],
                "session": r["session"],
            }
            for r in rows
        ]
