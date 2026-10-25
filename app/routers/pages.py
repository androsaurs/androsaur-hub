import time
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.clients.weather import WeatherError
from app.dependencies import (
    get_device_service,
    get_people_service,
    get_settings_service,
    get_weather_service,
)
from app.services.devices import DeviceService
from app.services.people import ERRORS, PeopleService, PersonError
from app.services.settings import SettingError, SettingsService
from app.services.weather import WeatherService, describe
from app.templating import templates

router = APIRouter(tags=["pages"])


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    error: str | None = None,
    settings: SettingsService = Depends(get_settings_service),
    devices: DeviceService = Depends(get_device_service),
    weather: WeatherService = Depends(get_weather_service),
) -> HTMLResponse:
    place, reading = weather.current()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "page": "settings",
            "weather_place": place,
            "weather": reading,
            "weather_text": describe(reading.symbol) if reading else "",
            "now_ms": int(time.time() * 1000),
            "settings": settings.list(),
            "revision": settings.revision(),
            "devices": devices.all(),
            "error": error,
        },
    )


@router.post("/settings/{module}/{key}")
def set_setting(
    module: str,
    key: str,
    value: str = Form(...),
    settings: SettingsService = Depends(get_settings_service),
) -> RedirectResponse:
    try:
        settings.set(module, key, value)
    except SettingError as e:
        return _back(str(e))
    return _back()


@router.post("/settings/{module}/{key}/reset")
def reset_setting(
    module: str,
    key: str,
    settings: SettingsService = Depends(get_settings_service),
) -> RedirectResponse:
    try:
        settings.reset(module, key)
    except SettingError as e:
        return _back(str(e))
    return _back()


@router.post("/weather/place")
def set_weather_place(
    name: str = Form(...),
    weather: WeatherService = Depends(get_weather_service),
) -> RedirectResponse:
    try:
        weather.set_place(name)
    except WeatherError as e:
        return _back(str(e))
    return _back()


@router.post("/weather/place/clear")
def clear_weather_place(weather: WeatherService = Depends(get_weather_service)) -> RedirectResponse:
    weather.clear()
    return _back()


@router.get("/people", response_class=HTMLResponse)
def people_page(
    request: Request,
    error: str | None = None,
    people: PeopleService = Depends(get_people_service),
    devices: DeviceService = Depends(get_device_service),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "people.html",
        {
            "page": "people",
            "device_count": len(devices.all()),
            "people": people.all(),
            "incoming": people.incoming(),
            "commands": people.commands(),
            "errors": ERRORS,
            "error": error,
        },
    )


@router.post("/people/rename")
def rename_person(
    person: str = Form(...),
    to: str = Form(...),
    people: PeopleService = Depends(get_people_service),
) -> RedirectResponse:
    try:
        people.rename(person, to)
    except PersonError as e:
        return _back(str(e), "/people")
    return _back(path="/people")


@router.post("/people/merge")
def merge_person(
    person: str = Form(...),
    into: str = Form(...),
    people: PeopleService = Depends(get_people_service),
) -> RedirectResponse:
    """Человек с телефона из другой системы: ``into`` — ID человека дома или ``new``."""
    try:
        if into == "new":
            people.add_new(person)
        else:
            people.merge(person, into)
    except PersonError as e:
        return _back(str(e), "/people")
    return _back(path="/people")


def _back(error: str | None = None, path: str = "/") -> RedirectResponse:
    # 303 → браузер делает GET, защита от повторной отправки формы.
    url = path if error is None else f"{path}?error={quote(error)}"
    return RedirectResponse(url=url, status_code=303)
