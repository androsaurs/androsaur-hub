from app.services.devices import DeviceService
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
