from app.db.database import get_connection
from app.schemas import Device, DeviceRegistration

_COLUMNS = "id, name, model, app_version, last_seen_at, applied_rev"


class DeviceRepository:
    """Доступ к таблице ``devices``."""

    def upsert(self, reg: DeviceRegistration, now: float) -> None:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO devices (id, name, model, app_version, registered_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (id) DO UPDATE SET
                    name = excluded.name, model = excluded.model,
                    app_version = excluded.app_version, last_seen_at = excluded.last_seen_at
                """,
                (reg.id, reg.name, reg.model, reg.app_version, now, now),
            )

    def seen(self, device_id: str, applied_rev: int | None, now: float) -> bool:
        """Отметить обращение устройства. False — такого устройства нет."""
        with get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE devices SET last_seen_at = ?,
                    applied_rev = COALESCE(?, applied_rev)
                WHERE id = ?
                """,
                (now, applied_rev, device_id),
            )
        return cursor.rowcount > 0

    def list(self) -> list[Device]:
        with get_connection() as conn:
            rows = conn.execute(f"SELECT {_COLUMNS} FROM devices ORDER BY name, id").fetchall()
        return [Device(**dict(row)) for row in rows]

    def people_state(self, device_id: str) -> tuple[bool, str, int] | None:
        """Принят ли телефон в дом (его исходные люди разобраны), модель лиц его камеры и
        версия людей дома, которую он уже загрузил. None — такого телефона нет."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT joined, face_model, people_rev FROM devices WHERE id = ?", (device_id,)
            ).fetchone()
        return None if row is None else (bool(row["joined"]), row["face_model"], row["people_rev"])

    def set_people_state(self, device_id: str, face_model: str) -> None:
        with get_connection() as conn:
            conn.execute(
                "UPDATE devices SET joined = 1, face_model = ? WHERE id = ?",
                (face_model, device_id),
            )

    def set_people_rev(self, device_id: str, rev: int) -> None:
        with get_connection() as conn:
            conn.execute("UPDATE devices SET people_rev = ? WHERE id = ?", (rev, device_id))
