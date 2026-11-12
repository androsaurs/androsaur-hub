import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.db.database import init_db
from app.dependencies import get_notes_service, get_stand_service, get_weather_service
from app.repositories.settings import SettingsRepository
from app.routers import api, auth, pages
from app.schemas import WAKE_PHRASE_FIELD
from app.security import LoginRequired, require_owner, set_phone_cookie
from app.services.auth import AuthService

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Инициализация ресурсов на старте. Сюда же добавляйте пулы соединений,
    # клиентов внешних API, планировщики и т.п.
    init_db()
    SettingsRepository().upsert_fields([WAKE_PHRASE_FIELD])
    setup_token = AuthService().ensure_setup_token()
    if setup_token:
        print(
            "Androsaur Hub: владелец ещё не создан. Откройте в браузере "
            f"http://<адрес хаба>:<порт>/setup?token={setup_token} (ссылка действует сутки; "
            "новую выдаёт команда python -m app.cli setup-link)",
            flush=True,
        )
    tasks = (
        [asyncio.create_task(_weather_loop(app)), asyncio.create_task(_notes_loop(app))]
        if get_settings().weather_enabled
        else []
    )
    if get_settings().stand_poll_enabled:
        tasks.append(asyncio.create_task(_stand_loop(app)))
    yield
    for task in tasks:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _notes_loop(app: FastAPI) -> None:
    """Раз в 15 секунд расшифровывает новые записки (если включён Groq) и убирает старые."""
    while True:
        service = app.dependency_overrides.get(get_notes_service, get_notes_service)()
        with contextlib.suppress(Exception):
            await asyncio.to_thread(service.transcribe_pending)
            await asyncio.to_thread(service.cleanup)
        await asyncio.sleep(15)


async def _stand_loop(app: FastAPI) -> None:
    """Poll stands directly; no phone, weather integration or owner session required."""
    while True:
        service = app.dependency_overrides.get(get_stand_service, get_stand_service)()
        with contextlib.suppress(Exception):
            await asyncio.to_thread(service.refresh_all)
        await asyncio.sleep(15)


async def _weather_loop(app: FastAPI) -> None:
    """Раз в минуту проверяет, не пора ли обновить погоду.

    Сам запрос — не чаще настройки и срока, который просит MET."""
    while True:
        service = app.dependency_overrides.get(get_weather_service, get_weather_service)()
        with contextlib.suppress(Exception):
            await asyncio.to_thread(service.refresh)
        await asyncio.sleep(60)


def create_app() -> FastAPI:
    """Фабрика приложения. Удобна для тестов и нескольких конфигураций."""
    settings = get_settings()
    app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(auth.router)
    app.include_router(pages.router, dependencies=[Depends(require_owner)])
    app.include_router(api.public_router)
    app.include_router(api.router)

    @app.exception_handler(LoginRequired)
    async def login_required(request: Request, _: LoginRequired) -> Response:
        if request.method == "GET":
            target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse(
                f"/login?next={quote(target)}", status_code=status.HTTP_303_SEE_OTHER
            )
        return PlainTextResponse("login required", status_code=status.HTTP_401_UNAUTHORIZED)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response: Response = await call_next(request)
        phone_session = getattr(request.state, "new_phone_session", None)
        if phone_session:
            set_phone_cookie(request, response, phone_session)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
        return response

    return app


app = create_app()
