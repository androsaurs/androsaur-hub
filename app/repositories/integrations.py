import json
from typing import Any

from app.db.database import get_connection


class IntegrationRow:
    def __init__(self, row: Any) -> None:
        self.id: str = row["id"]
        self.enabled: bool = bool(row["enabled"])
        self.secret: str | None = row["secret"]
        self.config: dict[str, int] = json.loads(row["config"] or "{}")
        self.checked_at: float | None = row["checked_at"]
        self.check_ok: bool | None = None if row["check_ok"] is None else bool(row["check_ok"])
        self.check_message: str | None = row["check_message"]


class IntegrationsRepository:
    """Состояние интеграций: включена ли, ключ, настройки и последняя проверка."""

    def get(self, integration_id: str) -> IntegrationRow | None:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM integrations WHERE id = ?", (integration_id,)
            ).fetchone()
        return None if row is None else IntegrationRow(row)

    def _ensure(self, conn: Any, integration_id: str) -> None:
        conn.execute("INSERT OR IGNORE INTO integrations (id) VALUES (?)", (integration_id,))

    def set_secret(self, integration_id: str, secret: str | None) -> None:
        with get_connection() as conn:
            self._ensure(conn, integration_id)
            conn.execute(
                "UPDATE integrations SET secret = ?, checked_at = NULL, check_ok = NULL, "
                "check_message = NULL WHERE id = ?",
                (secret, integration_id),
            )

    def set_enabled(self, integration_id: str, enabled: bool) -> None:
        with get_connection() as conn:
            self._ensure(conn, integration_id)
            conn.execute(
                "UPDATE integrations SET enabled = ? WHERE id = ?", (int(enabled), integration_id)
            )

    def set_config(self, integration_id: str, config: dict[str, int]) -> None:
        with get_connection() as conn:
            self._ensure(conn, integration_id)
            conn.execute(
                "UPDATE integrations SET config = ? WHERE id = ?",
                (json.dumps(config), integration_id),
            )

    def set_check(self, integration_id: str, ok: bool, message: str, now: float) -> None:
        with get_connection() as conn:
            self._ensure(conn, integration_id)
            conn.execute(
                "UPDATE integrations SET checked_at = ?, check_ok = ?, check_message = ? "
                "WHERE id = ?",
                (now, int(ok), message, integration_id),
            )
