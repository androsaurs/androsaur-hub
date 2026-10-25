from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация хаба. Значения берутся из переменных окружения
    (префикс ``HUB_``) или из файла ``.env``."""

    model_config = SettingsConfigDict(
        env_prefix="HUB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Androsaur Hub"
    debug: bool = False
    database_path: Path = Path("data/hub.sqlite3")
    host: str = "127.0.0.1"
    port: int = 8000
    # Устройство считается на связи, если обращалось к хабу не раньше этого срока.
    online_window_s: int = 90
    # Погода: обновлять в фоне (в тестах выключено) и не чаще, чем раз в столько секунд.
    weather_enabled: bool = True
    weather_refresh_s: int = 1800


@lru_cache
def get_settings() -> Settings:
    """Кэшированный синглтон настроек.

    Используется как FastAPI-зависимость и при прямом доступе из слоёв.
    В тестах кэш сбрасывается через ``get_settings.cache_clear()``.
    """
    return Settings()
