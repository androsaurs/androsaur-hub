import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

OWNER, OWNER_PASSWORD = "owner", "correct horse battery"
_PUBLIC_API = {"/api/health", "/api/device/pair"}
DEFAULT_DEVICE = "test-device-0000"


class HubTestClient(TestClient):
    """Клиент тестов: вошёл как владелец и сам подставляет токен телефона в запросы к API.

    Токен выдаётся на тот ID устройства, от имени которого идёт запрос (поле ``device``
    или ``id`` в JSON, параметр ``device``), как если бы этот телефон был привязан."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._tokens: dict[str, str] = {}

    def device_token(self, device_id: str = DEFAULT_DEVICE) -> str:
        if device_id not in self._tokens:
            from app.services.auth import AuthService

            auth = AuthService()
            self._tokens[device_id] = auth.pair(auth.new_pairing_code(), device_id, "test")
        return self._tokens[device_id]

    @staticmethod
    def _device_of(url: str, kwargs: dict[str, Any]) -> str:
        body = kwargs.get("json")
        if isinstance(body, dict):
            for key in ("device", "id"):
                if isinstance(body.get(key), str) and body[key]:
                    return body[key]
        params = kwargs.get("params")
        if isinstance(params, dict) and params.get("device"):
            return str(params["device"])
        query = parse_qs(urlsplit(url).query).get("device")
        return query[0] if query else DEFAULT_DEVICE

    def request(self, method: str, url: Any, **kwargs: Any):  # type: ignore[override]
        path = urlsplit(str(url)).path
        headers = dict(kwargs.get("headers") or {})
        if (
            path.startswith("/api/")
            and path not in _PUBLIC_API
            and not any(k.lower() == "authorization" for k in headers)
        ):
            headers["Authorization"] = (
                f"Bearer {self.device_token(self._device_of(str(url), kwargs))}"
            )
            kwargs["headers"] = headers
        return super().request(method, url, **kwargs)


@contextmanager
def _hub(monkeypatch, with_owner: bool) -> Iterator[HubTestClient]:
    """Хаб с изолированной in-memory БД на каждый тест.

    Используется shared-cache in-memory SQLite: база живёт, пока открыт
    ``anchor``-соединение, поэтому отдельные соединения репозитория видят
    одну и ту же БД. Уникальное имя на тест → полная изоляция. Диска и fsync
    нет — setup на порядок(и) быстрее файловой БД (см. CLAUDE.md → «Тесты»).
    """
    dsn = f"file:test_{uuid.uuid4().hex}?mode=memory&cache=shared"
    monkeypatch.setenv("HUB_DATABASE_PATH", dsn)
    monkeypatch.setenv("HUB_WEATHER_ENABLED", "false")  # без фоновых запросов в интернет
    monkeypatch.setenv("HUB_STAND_POLL_ENABLED", "false")
    get_settings.cache_clear()
    monkeypatch.setattr("app.services.auth._N", 2**10)  # быстрый scrypt только в тестах

    anchor = sqlite3.connect(dsn, uri=True)  # держит in-memory БД живой весь тест
    try:
        from app.main import create_app
        from app.services.auth import LOGIN_THROTTLE, PAIRING_THROTTLE, AuthService

        LOGIN_THROTTLE._fails.clear()
        PAIRING_THROTTLE._fails.clear()
        app = create_app()
        with HubTestClient(app) as test_client:  # триггерит lifespan → init_db()
            if with_owner:
                AuthService().add_user(OWNER, OWNER_PASSWORD, OWNER_PASSWORD)
                test_client.post("/login", data={"username": OWNER, "password": OWNER_PASSWORD})
            yield test_client
    finally:
        anchor.close()
        get_settings.cache_clear()


@pytest.fixture
def client(monkeypatch) -> Iterator[TestClient]:
    """Хаб, в котором владелец уже вошёл; запросы к API идут с токеном телефона."""
    with _hub(monkeypatch, with_owner=True) as test_client:
        yield test_client


@pytest.fixture
def fresh(monkeypatch) -> Iterator[TestClient]:
    """Только что установленный хаб: владельца ещё нет."""
    with _hub(monkeypatch, with_owner=False) as test_client:
        yield test_client


@pytest.fixture
def anon(client: TestClient) -> TestClient:
    """Посторонний в той же сети: та же база, но без входа и без токена."""
    return TestClient(client.app)
