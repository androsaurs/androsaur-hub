import json
import sqlite3
from typing import Any

from app.db.database import get_connection
from app.stands import PhoneStandReport, StandEnrollment


class StandConflict(Exception):
    pass


class StandRepository:
    def phone_report(self, device: str, report: PhoneStandReport | None, now: float) -> None:
        with get_connection() as conn:
            row = conn.execute("SELECT id FROM stands WHERE device=?", (device,)).fetchone()
            if row is None:
                return
            if report is not None and report.state != "unpaired" and report.id != row["id"]:
                report = None
            conn.execute(
                "UPDATE stands SET phone_state=?, phone_reported_at=?, phone_seen_at=? "
                "WHERE device=?",
                (
                    report.state if report else None,
                    now if report else None,
                    now - report.seen_ago_ms / 1000
                    if report is not None and report.seen_ago_ms is not None
                    else None,
                    device,
                ),
            )

    def enroll(self, stand: StandEnrollment) -> None:
        with get_connection() as conn:
            try:
                conn.execute(
                    "INSERT INTO stands (id, device, ip, fingerprint, credential) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                    "ip=excluded.ip, credential=excluded.credential "
                    "WHERE stands.device=excluded.device "
                    "AND stands.fingerprint=excluded.fingerprint",
                    (
                        stand.id,
                        stand.device,
                        stand.ip,
                        stand.fingerprint,
                        stand.key.get_secret_value(),
                    ),
                )
                row = conn.execute(
                    "SELECT device, fingerprint FROM stands WHERE id=?", (stand.id,)
                ).fetchone()
                if (
                    row is None
                    or row["device"] != stand.device
                    or row["fingerprint"] != stand.fingerprint
                ):
                    raise StandConflict("stand already bound")
            except sqlite3.IntegrityError:
                raise StandConflict("phone already has a stand") from None

    def rows(self) -> list[dict[str, Any]]:
        with get_connection() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM stands").fetchall()]

    def get(self, device: str) -> dict[str, Any] | None:
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM stands WHERE device=?", (device,)).fetchone()
        return dict(row) if row else None

    def status(self, stand: StandEnrollment, payload: dict[str, Any], now: float) -> None:
        with get_connection() as conn:
            conn.execute(
                "UPDATE stands SET status=?, last_seen_at=?, ip=?, error=NULL "
                "WHERE id=? AND fingerprint=? AND credential=?",
                (
                    json.dumps(payload),
                    now,
                    stand.ip,
                    stand.id,
                    stand.fingerprint,
                    stand.key.get_secret_value(),
                ),
            )

    def failed(self, stand: StandEnrollment) -> None:
        with get_connection() as conn:
            conn.execute(
                "UPDATE stands SET error='unavailable' WHERE id=? AND credential=?",
                (stand.id, stand.key.get_secret_value()),
            )

    def detach(self, device: str) -> None:
        with get_connection() as conn:
            conn.execute("DELETE FROM stands WHERE device=?", (device,))
