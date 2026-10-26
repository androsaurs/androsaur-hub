from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.config import get_settings

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _ago(seconds: int) -> str:
    """Jinja-фильтр: давность в словах («12 с назад», «5 мин назад»)."""
    if seconds < 60:
        return f"{max(seconds, 0)} с назад"
    if seconds < 3600:
        return f"{seconds // 60} мин назад"
    if seconds < 86400:
        return f"{seconds // 3600} ч назад"
    return f"{seconds // 86400} дн назад"


def _temp(value: float) -> str:
    """Jinja-фильтр: температура со знаком («+11°», «−3°», «0°»)."""
    t = int(value + 0.5) if value >= 0 else -int(-value + 0.5)
    return "0°" if t == 0 else (f"+{t}°" if t > 0 else f"−{-t}°")


def _datetimeformat(value: float) -> str:
    """Jinja-фильтр: момент Unix (с) по местному времени хаба — «26.10 08:51»."""
    from datetime import datetime

    return datetime.fromtimestamp(value).strftime("%d.%m %H:%M")


templates.env.filters["ago"] = _ago
templates.env.filters["datetimeformat"] = _datetimeformat
templates.env.filters["temp"] = _temp
templates.env.globals["app_name"] = get_settings().app_name
