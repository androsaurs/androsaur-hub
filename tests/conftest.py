import sqlite3
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings


@pytest.fixture
def client(monkeypatch) -> Iterator[TestClient]:
    """TestClient с изолированной in-memory БД на каждый тест.

    Используется shared-cache in-memory SQLite: база живёт, пока открыт
    ``anchor``-соединение, поэтому отдельные соединения репозитория видят
    одну и ту же БД. Уникальное имя на тест → полная изоляция. Диска и fsync
    нет — setup на порядок(и) быстрее файловой БД (см. CLAUDE.md → «Тесты»).
    """
    dsn = f"file:test_{uuid.uuid4().hex}?mode=memory&cache=shared"
    monkeypatch.setenv("PORTAL_DATABASE_PATH", dsn)
    get_settings.cache_clear()

    anchor = sqlite3.connect(dsn, uri=True)  # держит in-memory БД живой весь тест
    try:
        from app.main import create_app

        app = create_app()
        with TestClient(app) as test_client:  # триггерит lifespan → init_db()
            yield test_client
    finally:
        anchor.close()
        get_settings.cache_clear()
