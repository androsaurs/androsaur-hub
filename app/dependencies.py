from app.services.devices import DeviceService
from app.services.people import PeopleService
from app.services.settings import SettingsService


def get_device_service() -> DeviceService:
    """FastAPI-зависимость: подключение устройств и их настройки."""
    return DeviceService()


def get_settings_service() -> SettingsService:
    """FastAPI-зависимость: настройки дома для админки."""
    return SettingsService()


def get_people_service() -> PeopleService:
    """FastAPI-зависимость: люди и их переименование."""
    return PeopleService()
