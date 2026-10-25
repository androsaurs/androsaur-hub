import json
import sqlite3

from app.db.database import get_connection
from app.schemas import SettingField


def _field(row: sqlite3.Row) -> SettingField:
    return SettingField(
        module=row["module"],
        key=row["key"],
        type=row["type"],
        title=row["title"],
        description=row["description"],
        unit=row["unit"],
        min=None if row["min_value"] is None else int(row["min_value"]),
        max=None if row["max_value"] is None else int(row["max_value"]),
        default=json.loads(row["default_value"]),
    )


class SettingsRepository:
    """Доступ к описаниям настроек, значениям дома и номеру ревизии."""

    def upsert_fields(self, fields: list[SettingField]) -> None:
        with get_connection() as conn:
            conn.executemany(
                """
                INSERT INTO setting_fields
                    (module, key, type, title, description, unit,
                     min_value, max_value, default_value)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (module, key) DO UPDATE SET
                    type = excluded.type, title = excluded.title,
                    description = excluded.description, unit = excluded.unit,
                    min_value = excluded.min_value, max_value = excluded.max_value,
                    default_value = excluded.default_value
                """,
                [
                    (
                        f.module,
                        f.key,
                        f.type,
                        f.title,
                        f.description,
                        f.unit,
                        f.min,
                        f.max,
                        json.dumps(f.default),
                    )
                    for f in fields
                ],
            )

    def fields(self) -> list[SettingField]:
        with get_connection() as conn:
            rows = conn.execute("SELECT * FROM setting_fields ORDER BY module, key").fetchall()
        return [_field(row) for row in rows]

    def field(self, module: str, key: str) -> SettingField | None:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM setting_fields WHERE module = ? AND key = ?", (module, key)
            ).fetchone()
        return None if row is None else _field(row)

    def values(self) -> dict[tuple[str, str], int]:
        with get_connection() as conn:
            rows = conn.execute("SELECT module, key, value FROM setting_values").fetchall()
        return {(row["module"], row["key"]): json.loads(row["value"]) for row in rows}

    def set_value(self, module: str, key: str, value: int, now: float) -> int:
        """Задать значение дома; возвращает новую ревизию."""
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO setting_values (module, key, value, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT (module, key) DO UPDATE SET
                    value = excluded.value, updated_at = excluded.updated_at
                """,
                (module, key, json.dumps(value), now),
            )
            return _bump(conn)

    def clear_value(self, module: str, key: str) -> int:
        """Убрать значение дома (действует умолчание модуля); возвращает новую ревизию."""
        with get_connection() as conn:
            conn.execute("DELETE FROM setting_values WHERE module = ? AND key = ?", (module, key))
            return _bump(conn)

    def revision(self) -> int:
        with get_connection() as conn:
            row = conn.execute("SELECT rev FROM revision WHERE id = 1").fetchone()
        return int(row["rev"])


def _bump(conn: sqlite3.Connection) -> int:
    conn.execute("UPDATE revision SET rev = rev + 1 WHERE id = 1")
    return int(conn.execute("SELECT rev FROM revision WHERE id = 1").fetchone()["rev"])
