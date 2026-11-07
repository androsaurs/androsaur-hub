import json
import time
from collections.abc import Callable
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.clients.weather import WeatherError
from app.dependencies import (
    get_character_service,
    get_device_service,
    get_home_service,
    get_horoscope_service,
    get_integrations_service,
    get_notes_service,
    get_people_service,
    get_settings_service,
    get_things_service,
    get_trace_service,
    get_weather_service,
)
from app.horoscope import SIGNS, HoroscopeError
from app.services.characters import CharacterError, CharacterService
from app.services.devices import DeviceService
from app.services.home import HomeService, HomeSettingError
from app.services.horoscope import HoroscopeService
from app.services.integrations import IntegrationError, IntegrationsService
from app.services.notes import NotesService
from app.services.people import ERRORS, PeopleService, PersonError
from app.services.settings import SettingError, SettingsService
from app.services.things import ThingsService, ThingsSettingError
from app.services.trace import TraceService
from app.services.weather import WeatherService, describe
from app.templating import templates
from app.trace import STAGES

router = APIRouter(tags=["pages"])


@router.get("/characters", response_class=HTMLResponse)
def characters_page(
    request: Request,
    person: str = "",
    error: str | None = None,
    saved: bool = False,
    characters: CharacterService = Depends(get_character_service),
    people: PeopleService = Depends(get_people_service),
) -> HTMLResponse:
    household = people.all()
    selected = next((p for p in household if p.id == person), None)
    return templates.TemplateResponse(
        request,
        "characters.html",
        {
            "page": "characters",
            "catalog": characters.catalog(),
            "people": household,
            "person": selected,
            "assignments": characters.assignments(),
            "error": error,
            "saved": saved and selected is not None,
        },
    )


@router.post("/characters/select")
def select_character(
    person: str = Form(...),
    character: str = Form(...),
    characters: CharacterService = Depends(get_character_service),
) -> RedirectResponse:
    try:
        characters.select(person, character)
    except CharacterError as e:
        return RedirectResponse(
            f"/characters?person={quote(person)}&error={quote(str(e))}", status_code=303
        )
    return RedirectResponse(f"/characters?person={quote(person)}&saved=1", status_code=303)


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
            "zodiac_signs": SIGNS,
            "profile_today": people.today(),
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


@router.post("/people/zodiac")
def set_zodiac_sign(
    person: str = Form(...),
    sign: str = Form(""),
    horoscope: HoroscopeService = Depends(get_horoscope_service),
) -> RedirectResponse:
    try:
        horoscope.set_sign(person, sign)
    except HoroscopeError as e:
        return _back(str(e), "/people")
    return _back(path="/people")


@router.post("/people/birth-date")
def set_person_birth_date(
    person: str = Form(...),
    birth_date: str = Form(""),
    clear: bool = Form(False),
    people: PeopleService = Depends(get_people_service),
) -> RedirectResponse:
    try:
        people.set_birth_date(person, "" if clear else birth_date)
    except PersonError as e:
        return _back(str(e), "/people")
    return _back(path="/people")


@router.post("/people/horoscope-greeting")
def set_person_horoscope_greeting(
    person: str = Form(...),
    enabled: bool = Form(False),
    people: PeopleService = Depends(get_people_service),
) -> RedirectResponse:
    try:
        people.set_horoscope_in_greeting(person, enabled)
    except PersonError as e:
        return _back(str(e), "/people")
    return _back(path="/people")


@router.post("/people/roles")
def set_person_roles(
    person: str = Form(...),
    roles: str = Form(""),
    people: PeopleService = Depends(get_people_service),
) -> RedirectResponse:
    try:
        people.set_roles(person, roles)
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


@router.get("/weather", response_class=HTMLResponse)
def weather_page(
    request: Request,
    embed: bool = False,
    weather: WeatherService = Depends(get_weather_service),
) -> HTMLResponse:
    """Прогноз по часам на графике: от текущего часа до конца того, что отдал источник.

    ``embed=1`` — только графики, без меню: так страницу открывает телефон у себя в WebView."""
    place, reading = weather.current()
    now_ms = int(time.time() * 1000)
    points = [
        {
            "t": h.time_ms,
            "temp": h.temperature,
            "feels": h.feels_like,
            "wind": h.wind,
            "gust": h.gust,
            "prob": h.precip_prob,
            "mm": h.precip_mm,
            "text": describe(h.symbol) if h.symbol else "",
            "symbol": h.symbol,
        }
        for h in (reading.hours or [] if reading else [])
        if h.time_ms >= now_ms - 3600 * 1000
    ]
    return templates.TemplateResponse(
        request,
        "weather.html",
        {
            "page": "weather",
            "embed": embed,
            "place": place,
            "reading": reading,
            "now_ms": now_ms,
            "chart": {"points": points, "now": now_ms},
        },
    )


@router.get("/integrations", response_class=HTMLResponse)
def integrations_page(
    request: Request,
    error: str | None = None,
    integrations: IntegrationsService = Depends(get_integrations_service),
    horoscope: HoroscopeService = Depends(get_horoscope_service),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "integrations.html",
        {
            "page": "integrations",
            "integrations": integrations.all(),
            "horoscope_providers": horoscope.options(),
            "horoscope_provider": horoscope.provider(),
            "now": time.time(),
            "error": error,
        },
    )


def _integration(action: Callable[[], object]) -> RedirectResponse:
    try:
        action()
    except IntegrationError as e:
        return _back(str(e), "/integrations")
    return _back(path="/integrations")


@router.post("/integrations/horoscope/provider")
def set_horoscope_provider(
    provider: str = Form(""),
    horoscope: HoroscopeService = Depends(get_horoscope_service),
) -> RedirectResponse:
    try:
        horoscope.set_provider(provider)
    except HoroscopeError as e:
        return _back(str(e), "/integrations")
    return _back(path="/integrations")


@router.post("/integrations/{integration_id}/key")
def save_integration_key(
    integration_id: str,
    key: str = Form(...),
    integrations: IntegrationsService = Depends(get_integrations_service),
) -> RedirectResponse:
    return _integration(lambda: integrations.save_key(integration_id, key))


@router.post("/integrations/{integration_id}/key/clear")
def clear_integration_key(
    integration_id: str,
    integrations: IntegrationsService = Depends(get_integrations_service),
) -> RedirectResponse:
    return _integration(lambda: integrations.clear_key(integration_id))


@router.post("/integrations/{integration_id}/enabled")
def set_integration_enabled(
    integration_id: str,
    enabled: int = Form(...),
    integrations: IntegrationsService = Depends(get_integrations_service),
) -> RedirectResponse:
    return _integration(lambda: integrations.set_enabled(integration_id, bool(enabled)))


@router.post("/integrations/{integration_id}/refresh")
def set_integration_refresh(
    integration_id: str,
    minutes: int = Form(...),
    integrations: IntegrationsService = Depends(get_integrations_service),
) -> RedirectResponse:
    return _integration(lambda: integrations.set_refresh(integration_id, minutes))


@router.post("/integrations/{integration_id}/config")
def set_integration_config(
    integration_id: str,
    key: str = Form(...),
    value: int = Form(...),
    integrations: IntegrationsService = Depends(get_integrations_service),
) -> RedirectResponse:
    return _integration(lambda: integrations.set_config(integration_id, key, value))


@router.get("/notes", response_class=HTMLResponse)
def notes_page(
    request: Request,
    notes: NotesService = Depends(get_notes_service),
    integrations: IntegrationsService = Depends(get_integrations_service),
) -> HTMLResponse:
    """Записки в хабе: оригинал, расшифровка и решение — читать текстом или играть запись."""
    groq = integrations.groq()
    return templates.TemplateResponse(
        request,
        "notes.html",
        {
            "page": "notes",
            "notes": notes.recent(),
            "groq_on": groq is not None,
            "min_confidence": groq[1] if groq else None,
        },
    )


@router.get("/notes/{note_id}.wav")
def note_audio(note_id: str, notes: NotesService = Depends(get_notes_service)) -> Response:
    data = notes.audio(note_id) if note_id.isalnum() else None
    if data is None:
        return Response(status_code=404)
    return Response(content=data, media_type="audio/wav")


@router.post("/integrations/{integration_id}/check")
def check_integration(
    integration_id: str,
    integrations: IntegrationsService = Depends(get_integrations_service),
) -> RedirectResponse:
    return _integration(lambda: integrations.check(integration_id))


def _back(error: str | None = None, path: str = "/") -> RedirectResponse:
    # 303 → браузер делает GET, защита от повторной отправки формы.
    url = path if error is None else f"{path}?error={quote(error)}"
    return RedirectResponse(url=url, status_code=303)


@router.get("/home", response_class=HTMLResponse)
def home_page(
    request: Request,
    error: str | None = None,
    home: HomeService = Depends(get_home_service),
) -> HTMLResponse:
    speakers, speakers_error = home.speakers()
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "page": "home",
            "url": home.url() or "",
            "key_hint": home.key_hint(),
            "check": home.check_state(),
            "rooms": home.rooms(),
            "speakers": speakers,
            "speakers_error": speakers_error,
            "now": time.time(),
            "error": error,
        },
    )


def _home(action: Callable[[], object]) -> RedirectResponse:
    try:
        action()
    except HomeSettingError as e:
        return _back(str(e), "/home")
    return _back(path="/home")


@router.post("/home/connection")
def save_home_connection(
    url: str = Form(...),
    token: str = Form(""),
    home: HomeService = Depends(get_home_service),
) -> RedirectResponse:
    return _home(lambda: home.save_connection(url, token))


@router.post("/home/check")
def check_home(home: HomeService = Depends(get_home_service)) -> RedirectResponse:
    return _home(home.check)


@router.post("/home/token/clear")
def clear_home_token(home: HomeService = Depends(get_home_service)) -> RedirectResponse:
    return _home(home.clear_token)


@router.post("/home/rooms")
def add_room(
    title: str = Form(...),
    forms: str = Form(""),
    entity_id: str = Form(...),
    home: HomeService = Depends(get_home_service),
) -> RedirectResponse:
    return _home(lambda: home.add_room(title, forms, entity_id))


@router.post("/home/rooms/{room_id}")
def update_room(
    room_id: str,
    title: str = Form(...),
    forms: str = Form(""),
    entity_id: str = Form(...),
    home: HomeService = Depends(get_home_service),
) -> RedirectResponse:
    return _home(lambda: home.update_room(room_id, title, forms, entity_id))


@router.post("/home/rooms/{room_id}/delete")
def delete_room(room_id: str, home: HomeService = Depends(get_home_service)) -> RedirectResponse:
    return _home(lambda: home.delete_room(room_id))


@router.get("/things", response_class=HTMLResponse)
def things_page(
    request: Request,
    error: str | None = None,
    things: ThingsService = Depends(get_things_service),
) -> HTMLResponse:
    url, store = things.settings()
    records, replaced, records_error = things.records()
    return templates.TemplateResponse(
        request,
        "things.html",
        {
            "page": "things",
            "url": url,
            "store": store,
            "key_hint": things.key_hint(),
            "check": things.check_state(),
            "records": records,
            "replaced": replaced,
            "records_error": records_error,
            "now": time.time(),
            "error": error,
        },
    )


def _things(action: Callable[[], object]) -> RedirectResponse:
    try:
        action()
    except ThingsSettingError as e:
        return _back(str(e), "/things")
    return _back(path="/things")


@router.post("/things/connection")
def save_things_connection(
    url: str = Form(...),
    store: str = Form(""),
    key: str = Form(""),
    things: ThingsService = Depends(get_things_service),
) -> RedirectResponse:
    return _things(lambda: things.save_connection(url, store, key))


@router.post("/things/check")
def check_things(things: ThingsService = Depends(get_things_service)) -> RedirectResponse:
    return _things(things.check)


@router.post("/things/key/clear")
def clear_things_key(things: ThingsService = Depends(get_things_service)) -> RedirectResponse:
    return _things(things.clear_key)


@router.post("/things/{content_hash}/restore")
def restore_thing(
    content_hash: str, things: ThingsService = Depends(get_things_service)
) -> RedirectResponse:
    return _things(lambda: things.restore(content_hash))


@router.post("/things/{content_hash}/delete")
def delete_thing(
    content_hash: str, things: ThingsService = Depends(get_things_service)
) -> RedirectResponse:
    return _things(lambda: things.delete(content_hash))


_TRACE_DAYS = (1, 7, 14)


@router.get("/trace", response_class=HTMLResponse)
def trace_page(
    request: Request,
    days: int = 7,
    trace: TraceService = Depends(get_trace_service),
) -> HTMLResponse:
    days = days if days in _TRACE_DAYS else 7
    return templates.TemplateResponse(
        request,
        "trace.html",
        {
            "page": "trace",
            "report": trace.report(days),
            "days_options": _TRACE_DAYS,
            "stages": {s.key: s.label for s in STAGES},
        },
    )


@router.get("/trace/events.json")
def trace_events(days: int = 7, trace: TraceService = Depends(get_trace_service)) -> Response:
    """Сырые события для своего анализа (pandas, jq)."""
    days = days if days in _TRACE_DAYS else 7
    body = json.dumps(trace.events(days), ensure_ascii=False)
    return Response(body, media_type="application/json", headers={"Cache-Control": "no-store"})


@router.get("/trace/runs.json")
def trace_runs(days: int = 7, trace: TraceService = Depends(get_trace_service)) -> Response:
    """Прогоны с этапами в миллисекундах: готовая таблица для анализа."""
    days = days if days in _TRACE_DAYS else 7
    rows = [
        {
            "run": r.id,
            "start_ms": r.start_ms,
            "intent": r.intent,
            "outcome": r.outcome,
            "fail_stage": r.fail_stage,
            "fail_reason": r.fail_reason,
            "slow": r.slow,
            "heard": r.heard,
            "stages_ms": r.stages,
        }
        for r in trace.report(days).runs
    ]
    return Response(
        json.dumps(rows, ensure_ascii=False),
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/trace/clear")
def trace_clear(trace: TraceService = Depends(get_trace_service)) -> RedirectResponse:
    trace.clear()
    return RedirectResponse("/trace", status_code=303)
