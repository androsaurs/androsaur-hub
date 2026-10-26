from pathlib import Path
from typing import Any

from app.config import get_settings
from app.db.database import get_connection
from app.schemas import HubNote, NoteText


def _note(row: Any) -> HubNote:
    return HubNote(
        id=row["id"],
        device_id=row["device_id"],
        to_label=row["to_label"],
        from_label=row["from_label"],
        created_ms=row["created_ms"],
        duration_ms=row["duration_ms"],
        status=row["status"],
        text=row["text"],
        confidence=row["confidence"],
        speech=row["speech"],
        usable=None if row["usable"] is None else bool(row["usable"]),
        error=row["error"],
        has_audio=bool(row["has_audio"]),
    )


class NotesRepository:
    """Записки в хабе: строка в базе и звук файлом в ``notes_dir`` (вне git)."""

    def audio_path(self, note_id: str) -> Path:
        return Path(get_settings().notes_dir) / f"{note_id}.wav"

    def add(self, note: HubNote, wav: bytes, now: float) -> bool:
        """Принять записку; False — она уже есть (телефон прислал повторно)."""
        with get_connection() as conn:
            if conn.execute("SELECT 1 FROM hub_notes WHERE id = ?", (note.id,)).fetchone():
                return False
            path = self.audio_path(note.id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(wav)
            conn.execute(
                """
                INSERT INTO hub_notes (id, device_id, to_label, from_label, created_ms,
                    duration_ms, received_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    note.id,
                    note.device_id,
                    note.to_label,
                    note.from_label,
                    note.created_ms,
                    note.duration_ms,
                    now,
                ),
            )
        return True

    def pending(self, limit: int = 5) -> list[HubNote]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM hub_notes WHERE status = 'pending' AND has_audio = 1 "
                "ORDER BY received_at LIMIT ?",
                (limit,),
            ).fetchall()
        return [_note(r) for r in rows]

    def finish(
        self, note_id: str, text: str, confidence: float, speech: float, usable: bool, now: float
    ) -> None:
        with get_connection() as conn:
            conn.execute(
                "UPDATE hub_notes SET status = 'done', text = ?, confidence = ?, speech = ?, "
                "usable = ?, error = NULL, transcribed_at = ? WHERE id = ?",
                (text, confidence, speech, int(usable), now, note_id),
            )

    def fail(self, note_id: str, error: str, final: bool) -> None:
        with get_connection() as conn:
            conn.execute(
                "UPDATE hub_notes SET status = ?, error = ? WHERE id = ?",
                ("failed" if final else "pending", error[:200], note_id),
            )

    def drop_audio(self, note_id: str) -> None:
        self.audio_path(note_id).unlink(missing_ok=True)
        with get_connection() as conn:
            conn.execute("UPDATE hub_notes SET has_audio = 0 WHERE id = ?", (note_id,))

    def texts_for(self, device_id: str, since_ms: int) -> list[NoteText]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, text FROM hub_notes WHERE device_id = ? AND usable = 1 "
                "AND created_ms >= ? ORDER BY created_ms DESC LIMIT 50",
                (device_id, since_ms),
            ).fetchall()
        return [NoteText(id=r["id"], text=r["text"]) for r in rows]

    def recent(self, limit: int = 50) -> list[HubNote]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM hub_notes ORDER BY created_ms DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_note(r) for r in rows]

    def get(self, note_id: str) -> HubNote | None:
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM hub_notes WHERE id = ?", (note_id,)).fetchone()
        return None if row is None else _note(row)

    def forget_older(self, before_ms: int) -> int:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id FROM hub_notes WHERE created_ms < ?", (before_ms,)
            ).fetchall()
            for r in rows:
                self.audio_path(r["id"]).unlink(missing_ok=True)
            conn.execute("DELETE FROM hub_notes WHERE created_ms < ?", (before_ms,))
        return len(rows)
