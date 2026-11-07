from app.services.characters import CharacterService
from app.services.devices import DeviceService
from app.services.home import HomeService
from app.services.horoscope import HoroscopeService
from app.services.integrations import IntegrationsService
from app.services.medication import MedicationService
from app.services.notes import NotesService
from app.services.people import PeopleService
from app.services.settings import SettingsService
from app.services.sitting import SittingService
from app.services.things import ThingsService
from app.services.trace import TraceService
from app.services.weather import WeatherService


def get_character_service() -> CharacterService:
    return CharacterService()


def get_horoscope_service() -> HoroscopeService:
    return HoroscopeService()


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


def get_medication_service() -> MedicationService:
    """FastAPI-зависимость: графики приёма лекарств и журнал напоминаний."""
    return MedicationService()


def get_sitting_service() -> SittingService:
    """FastAPI-зависимость: «не засиживаться» и тепловая карта."""
    return SittingService()


def get_notes_service() -> NotesService:
    """FastAPI-зависимость: голосовые записки в хабе."""
    return NotesService()


def get_home_service() -> HomeService:
    """FastAPI-зависимость: Home Assistant и комнаты с колонками."""
    return HomeService()


def get_things_service() -> ThingsService:
    """FastAPI-зависимость: «где что лежит» в сервере памяти."""
    return ThingsService()


def get_trace_service() -> TraceService:
    """FastAPI-зависимость: профилирование сценариев."""
    return TraceService()
