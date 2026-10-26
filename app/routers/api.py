from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.dependencies import get_device_service, get_notes_service, get_people_service
from app.schemas import (
    DeviceConfig,
    DeviceRegistration,
    DeviceSync,
    HousePeople,
    HubNote,
    PeopleRequest,
    PeopleUpload,
)
from app.services.devices import DeviceService, UnknownDevice
from app.services.notes import NotesService
from app.services.people import PeopleService, PersonError

router = APIRouter(prefix="/api", tags=["api"])


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
