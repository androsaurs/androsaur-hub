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


templates.env.filters["ago"] = _ago
templates.env.globals["app_name"] = get_settings().app_name
