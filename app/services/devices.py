import time
from collections.abc import Callable

from app.config import get_settings
from app.repositories.devices import DeviceRepository
from app.repositories.people import PeopleRepository
from app.repositories.settings import SettingsRepository
from app.schemas import (
    DeviceConfig,
    DeviceRegistration,
    DeviceSync,
    DeviceView,
    WeatherReading,
)
from app.services.notes import NotesService
from app.services.people import PeopleService
from app.services.weather import WeatherService


class UnknownDevice(Exception):
    """Устройство не зарегистрировано (например, база хаба создана заново).

    Телефон в ответ подключается снова."""


class DeviceService:
    """Подключение телефонов: выдача настроек и команд, приём того, что телефон знает."""

    def __init__(
        self,
        devices: DeviceRepository | None = None,
        settings: SettingsRepository | None = None,
        people: PeopleRepository | None = None,
        clock: Callable[[], float] = time.time,
        weather: WeatherService | None = None,
    ) -> None:
        self._devices = devices or DeviceRepository()
        self._settings = settings or SettingsRepository()
        self._people = people or PeopleRepository()
        self._house = PeopleService(self._people, self._devices, clock)
        self._weather = weather or WeatherService(clock=clock)
        self._notes = NotesService(clock=clock)
        self._clock = clock

    def register(self, reg: DeviceRegistration) -> DeviceConfig:
        self._devices.upsert(reg, self._clock())
        self._settings.upsert_fields(reg.settings)
        return self._config(reg.id)

    def sync(self, report: DeviceSync) -> DeviceConfig:
        now = self._clock()
        if not self._devices.seen(report.device, report.applied, now):
            raise UnknownDevice(report.device)
        for result in report.done:
            command = self._people.finish(report.device, result, now)
            if command is not None and result.ok and command.kind == "rename_person":
                self._people.rename_on(report.device, command.person, command.to)
        upload: list[str] = []
        if report.people is not None:
            upload = self._house.reconcile(report.device, report.people, report.face_model)
        return self._config(report.device, upload)

    def all(self) -> list[DeviceView]:
        now = self._clock()
        rev = self._settings.revision()
        window = get_settings().online_window_s
        views = []
        for device in self._devices.list():
            ago = None if device.last_seen_at is None else int(now - device.last_seen_at)
            views.append(
                DeviceView(
                    device=device,
                    online=ago is not None and ago <= window,
                    seen_ago_s=ago,
                    up_to_date=device.applied_rev >= rev,
                )
            )
        return views

    def _config(self, device_id: str, upload: list[str] | None = None) -> DeviceConfig:
        place, reading = self._weather.current()
        values: dict[str, dict[str, int]] = {}
        for (module, key), value in self._settings.values().items():
            values.setdefault(module, {})[key] = value
        return DeviceConfig(
            rev=self._settings.revision(),
            values=values,
            commands=self._people.pending(device_id),
            people_rev=self._house.revision(),
            upload=upload or [],
            weather_place=place,
            weather=_next_day(reading, self._clock()),
            transcripts=self._notes.texts_for(device_id),
            roles=self._house.roles(),
        )


def _next_day(reading: WeatherReading | None, now: float) -> WeatherReading | None:
    """Погода для телефона: часы только от текущего до суток вперёд — ему хватает для ленты."""
    if reading is None or reading.hours is None:
        return reading
    start, end = (now - now % 3600) * 1000, (now + 24 * 3600) * 1000
    return reading.model_copy(
        update={"hours": [h for h in reading.hours if start <= h.time_ms <= end]}
    )
