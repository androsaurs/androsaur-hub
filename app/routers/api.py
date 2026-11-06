from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool

from app.dependencies import (
    get_character_service,
    get_device_service,
    get_home_service,
    get_horoscope_service,
    get_notes_service,
    get_people_service,
    get_things_service,
)
from app.horoscope import HoroscopeReply, HoroscopeRequest
from app.schemas import (
    AccessorySelection,
    AnnounceReply,
    CharacterAppearance,
    DeviceConfig,
    DeviceRegistration,
    DeviceSync,
    HousePeople,
    HubNote,
    PeopleRequest,
    PeopleUpload,
    ThingReply,
)
from app.services.characters import CharacterError, CharacterService
from app.services.devices import DeviceService, UnknownDevice
from app.services.home import HomeService
from app.services.horoscope import HoroscopeService
from app.services.notes import NotesService
from app.services.people import PeopleService, PersonError
from app.services.things import ThingsService

router = APIRouter(prefix="/api", tags=["api"])


@router.post("/horoscope", response_model=HoroscopeReply)
def daily_horoscope(
    payload: HoroscopeRequest,
    response: Response,
    service: HoroscopeService = Depends(get_horoscope_service),
) -> HoroscopeReply:
    response.headers["Cache-Control"] = "no-store"
    return service.today(payload.person, for_greeting=payload.for_greeting)


@router.get("/characters")
def current_characters(
    response: Response,
    service: CharacterService = Depends(get_character_service),
) -> CharacterAppearance:
    """Актуальные личные выборы: короткий запрос при узнавании, без полной синхронизации."""
    response.headers["Cache-Control"] = "no-store"
    return service.appearance()


@router.post("/characters/accessory")
def set_character_accessory(
    payload: AccessorySelection,
    response: Response,
    service: CharacterService = Depends(get_character_service),
) -> CharacterAppearance:
    try:
        service.set_accessory(payload.person, payload.character, payload.accessory)
    except CharacterError as e:
        code = status.HTTP_404_NOT_FOUND if str(e) == "person_unknown" else status.HTTP_409_CONFLICT
        raise HTTPException(code, str(e)) from None
    response.headers["Cache-Control"] = "no-store"
    return service.appearance()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/device/register", response_model=DeviceConfig)
def register(
    payload: DeviceRegistration,
    service: DeviceService = Depends(get_device_service),
) -> DeviceConfig:
    """Телефон сообщает о себе и о своих настройках и сразу получает действующие значения."""
    return service.register(payload)


@router.post("/device/sync", response_model=DeviceConfig)
def sync(
    payload: DeviceSync,
    service: DeviceService = Depends(get_device_service),
) -> DeviceConfig:
    """Регулярное обращение телефона: он сообщает применённую ревизию, имена людей и результаты
    команд, а получает настройки и невыполненные команды."""
    try:
        return service.sync(payload)
    except UnknownDevice:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown device") from None


@router.post("/device/people/upload", status_code=status.HTTP_204_NO_CONTENT)
def upload_people(
    payload: PeopleUpload,
    service: PeopleService = Depends(get_people_service),
) -> None:
    """Телефон присылает наборы людей, которые хаб попросил в ответе на sync (``upload``)."""
    try:
        service.upload(payload)
    except PersonError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown device") from None


@router.post("/device/people/download", response_model=HousePeople)
def download_people(
    payload: PeopleRequest,
    service: PeopleService = Depends(get_people_service),
) -> HousePeople:
    """Люди дома с наборами: телефон загружает их в камеру, когда ``peopleRev`` в sync новее."""
    try:
        return service.download(payload.device)
    except PersonError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown device") from None


_MAX_NOTE_BYTES = 6 * 1024 * 1024  # 120 с моно 16 кГц — около 4 МБ


@router.post("/device/notes/{note_id}", status_code=status.HTTP_201_CREATED)
async def upload_note(
    note_id: str,
    request: Request,
    device: str = Query(pattern=r"^[A-Za-z0-9-]{8,64}$"),
    to: str = Query(default="", max_length=60),
    sender: str = Query(default="", max_length=60, alias="from"),
    created_ms: int = Query(alias="createdMs"),
    duration_ms: int = Query(default=0, alias="durationMs"),
    service: NotesService = Depends(get_notes_service),
) -> dict[str, bool]:
    """Телефон отдаёт звук записки (WAV в теле запроса) для расшифровки."""
    if not note_id.isalnum() or len(note_id) > 40:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "bad note id")
    wav = await request.body()
    if len(wav) > _MAX_NOTE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "too large")
    note = HubNote(
        id=note_id,
        device_id=device,
        to_label=to,
        from_label=sender,
        created_ms=created_ms,
        duration_ms=duration_ms,
        status="pending",
        text=None,
        confidence=None,
        speech=None,
        usable=None,
        error=None,
        has_audio=True,
    )
    try:
        added = service.receive(note, wav)
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "not a wav") from None
    return {"added": added}


@router.post("/device/announce/{room_id}", response_model=AnnounceReply)
async def announce(
    room_id: str,
    request: Request,
    response: Response,
    sender: str = Query(default="", max_length=60, alias="from"),
    person: str = Query(default="", max_length=64, alias="fromId"),
    service: HomeService = Depends(get_home_service),
) -> AnnounceReply:
    """Звук сообщения (WAV в теле) расшифровывается и произносится на колонке комнаты."""
    response.headers["Cache-Control"] = "no-store"
    wav = await request.body()
    if len(wav) > _MAX_NOTE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "too large")
    if not wav.startswith(b"RIFF") or len(wav) < 100:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "not a wav")
    return await run_in_threadpool(service.announce, room_id, wav, person, sender)


@router.post("/device/things/{kind}", response_model=ThingReply)
async def things(
    kind: str,
    request: Request,
    response: Response,
    sender: str = Query(default="", max_length=60, alias="from"),
    person: str = Query(default="", max_length=64, alias="fromId"),
    service: ThingsService = Depends(get_things_service),
) -> ThingReply:
    """Голос после «запомни» (remember), «вспомни» (find) или «забудь» (forget): WAV в теле."""
    response.headers["Cache-Control"] = "no-store"
    if kind not in ("remember", "find", "forget"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown kind")
    wav = await request.body()
    if len(wav) > _MAX_NOTE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "too large")
    if not wav.startswith(b"RIFF") or len(wav) < 100:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "not a wav")
    if kind == "remember":
        return await run_in_threadpool(service.remember, wav, person, sender)
    if kind == "forget":
        return await run_in_threadpool(service.forget, wav)
    return await run_in_threadpool(service.find, wav)
