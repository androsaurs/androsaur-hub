import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.config import get_settings

# Схема БД. Для одного небольшого портала простых миграций «CREATE TABLE IF NOT
# EXISTS» достаточно; при росте проекта замените на полноценный мигратор.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""


def _open() -> sqlite3.Connection:
    """Открыть соединение по пути из настроек.

    Поддерживает два режима (выбираются значением ``PORTAL_DATABASE_PATH``):
    - обычный файл — для прод/локального запуска (durability сохраняется);
    - in-memory (``:memory:`` или URI ``file:...?mode=memory&cache=shared``) —
      для тестов: без диска и без fsync, отдельные соединения видят одну БД,
      пока живо хотя бы одно из них (anchor держит фикстура).
    """
    raw = str(get_settings().database_path)
    if raw == ":memory:" or raw.startswith("file:"):
        dsn = "file:portal?mode=memory&cache=shared" if raw == ":memory:" else raw
        conn = sqlite3.connect(dsn, uri=True)
    else:
        Path(raw).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(raw)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    """Открыть соединение с БД. Коммитит при успешном выходе, всегда закрывает.

    Путь к БД читается из настроек на каждый вызов — это позволяет тестам
    подменять базу через переменную окружения.
    """
    conn = _open()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Создать таблицы, если их ещё нет. Вызывается на старте приложения."""
    with get_connection() as conn:
        conn.executescript(_SCHEMA)
