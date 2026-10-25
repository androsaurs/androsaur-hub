from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies import get_device_service, get_people_service
from app.schemas import (
    DeviceConfig,
    DeviceRegistration,
    DeviceSync,
    HousePeople,
    PeopleRequest,
    PeopleUpload,
)
from app.services.devices import DeviceService, UnknownDevice
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
