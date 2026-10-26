from app.services.devices import DeviceService
from app.services.integrations import IntegrationsService
from app.services.notes import NotesService
from app.services.people import PeopleService
from app.services.settings import SettingsService
from app.services.weather import WeatherService


def get_device_service() -> DeviceService:
    """FastAPI-зависимость: подключение устройств и их настройки."""
    return DeviceService()


def get_settings_service() -> SettingsService:
    """FastAPI-зависимость: настройки дома для админки."""
    return SettingsService()


def get_people_service() -> PeopleService:
    """FastAPI-зависимость: люди и их переименование."""
    return PeopleService()


def get_weather_service() -> WeatherService:
    """FastAPI-зависимость: погода дома."""
    return WeatherService()


def get_integrations_service() -> IntegrationsService:
    """FastAPI-зависимость: раздел «Интеграции»."""
    return IntegrationsService()


def get_notes_service() -> NotesService:
    """FastAPI-зависимость: голосовые записки в хабе."""
    return NotesService()
