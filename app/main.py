import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.db.database import init_db
from app.dependencies import get_weather_service
from app.routers import api, pages

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Инициализация ресурсов на старте. Сюда же добавляйте пулы соединений,
    # клиентов внешних API, планировщики и т.п.
    init_db()
    task = asyncio.create_task(_weather_loop(app)) if get_settings().weather_enabled else None
    yield
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


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
    app.include_router(pages.router)
    app.include_router(api.router)
    return app


app = create_app()
