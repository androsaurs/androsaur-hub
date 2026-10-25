from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация приложения. Значения берутся из переменных окружения
    (префикс ``PORTAL_``) или из файла ``.env``."""

    model_config = SettingsConfigDict(
        env_prefix="PORTAL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "FastAPI Jinja Portal"
    debug: bool = False
    database_path: Path = Path("data/app.sqlite3")
    host: str = "127.0.0.1"
    port: int = 8000


@lru_cache
def get_settings() -> Settings:
    """Кэшированный синглтон настроек.

    Используется как FastAPI-зависимость и при прямом доступе из слоёв.
    В тестах кэш сбрасывается через ``get_settings.cache_clear()``.
    """
    return Settings()
