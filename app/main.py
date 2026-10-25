from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.db.database import init_db
from app.routers import api, pages

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Инициализация ресурсов на старте. Сюда же добавляйте пулы соединений,
    # клиентов внешних API, планировщики и т.п.
    init_db()
    yield
    # Очистка ресурсов на остановке (если потребуется).


def create_app() -> FastAPI:
    """Фабрика приложения. Удобна для тестов и нескольких конфигураций."""
    settings = get_settings()
    app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(pages.router)
    app.include_router(api.router)
    return app


app = create_app()
