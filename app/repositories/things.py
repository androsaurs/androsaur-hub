from app.db.database import get_connection

MEMORY_SERVICE = "memory_service"
DEFAULT_STORE = "androsaur-home"


class ThingsRepository:
    """Адрес сервера памяти и стор для «где что лежит». Ключ хранится в ``integrations``."""

    def settings(self) -> tuple[str, str] | None:
        with get_connection() as conn:
            row = conn.execute("SELECT url, store FROM memory_settings WHERE id = 1").fetchone()
        return None if row is None else (str(row["url"]), str(row["store"]))

    def set_settings(self, url: str, store: str) -> None:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO memory_settings (id, url, store) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET url = excluded.url, store = excluded.store",
                (url, store),
            )
