import math
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# Пока устройства описывают только целые настройки; другие типы добавляются, когда появятся.
FieldType = Literal["int"]

_NAME = r"^[a-z][a-z0-9_]{0,63}$"


class SettingField(BaseModel):
    """Одна настройка в том виде, в котором её описывает модуль на телефоне."""

    module: str = Field(pattern=_NAME)
    key: str = Field(pattern=_NAME)
    type: FieldType
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)
    unit: str = Field(default="", max_length=20)
    min: int | None = None
    max: int | None = None
    default: int

    @model_validator(mode="after")
    def _default_in_range(self) -> "SettingField":
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min больше max")
        if not in_range(self, self.default):
            raise ValueError("значение по умолчанию вне диапазона")
        return self


def in_range(field: SettingField, value: int) -> bool:
    return (field.min is None or value >= field.min) and (field.max is None or value <= field.max)


class DeviceRegistration(BaseModel):
    """Что телефон сообщает о себе при подключении."""

    id: str = Field(pattern=r"^[A-Za-z0-9-]{8,64}$")
    name: str = Field(min_length=1, max_length=100)
    model: str = Field(default="", max_length=100)
    app_version: str = Field(default="", max_length=50, alias="appVersion")
    settings: list[SettingField] = Field(default_factory=list, max_length=200)

    model_config = {"populate_by_name": True}


def valid_person_name(name: str) -> bool:
    """Имя человека — те же правила, что у камеры: буквы и цифры, внутри ещё пробел, «_» и «-»,
    до 40 символов, начинается с буквы или цифры."""
    return (
        0 < len(name) <= 40 and name[0].isalnum() and all(c.isalnum() or c in " _-" for c in name)
    )


class CommandResult(BaseModel):
    """Что телефон сделал с командой: ``error`` — код ошибки модуля (например, ``name_taken``)."""

    id: int
    ok: bool
    error: str | None = Field(default=None, max_length=100)


_PERSON_ID = r"^[A-Za-z0-9-]{1,64}$"

# Наборов на человека в хабе и на телефоне не больше этого: старые остаются, новые сверх — нет.
MAX_VECTORS = 60


class PersonReport(BaseModel):
    """Человек, которого знает камера телефона: постоянный ID, текущее имя, число наборов и
    отпечаток наборов (без самих наборов) — по отпечатку хаб видит, что наборы изменились."""

    id: str = Field(pattern=_PERSON_ID)
    name: str = Field(min_length=1, max_length=40)
    samples: int = Field(default=0, ge=0)
    digest: str | None = Field(default=None, max_length=64)


class PersonVectors(BaseModel):
    """Человек с наборами чисел лица — для обмена между телефоном и хабом."""

    id: str = Field(pattern=_PERSON_ID)
    name: str = Field(min_length=1, max_length=40)
    vectors: list[list[float]] = Field(max_length=MAX_VECTORS * 2)
    digest: str | None = Field(default=None, max_length=64)

    @field_validator("vectors")
    @classmethod
    def _sane(cls, vectors: list[list[float]]) -> list[list[float]]:
        dims = {len(v) for v in vectors}
        if len(dims) > 1 or (dims and not 16 <= dims.pop() <= 1024):
            raise ValueError("наборы разной или странной длины")
        if any(not math.isfinite(x) for v in vectors for x in v):
            raise ValueError("в наборе не число")
        return vectors


class PeopleUpload(BaseModel):
    """Телефон отдаёт хабу наборы людей, которые тот попросил."""

    device: str = Field(pattern=r"^[A-Za-z0-9-]{8,64}$")
    face_model: str = Field(min_length=1, max_length=50, alias="faceModel")
    people: list[PersonVectors] = Field(max_length=50)

    model_config = {"populate_by_name": True}


class PeopleRequest(BaseModel):
    device: str = Field(pattern=r"^[A-Za-z0-9-]{8,64}$")


class HousePeople(BaseModel):
    """Люди дома для телефона: всё, что ему нужно, чтобы узнавать их без обучения."""

    rev: int
    face_model: str = Field(serialization_alias="faceModel")
    people: list[PersonVectors]


class DeviceSync(BaseModel):
    """Регулярное обращение телефона: какую ревизию настроек он применил, кого знает его камера
    (только имена) и чем закончились команды."""

    device: str = Field(pattern=r"^[A-Za-z0-9-]{8,64}$")
    applied: int | None = Field(default=None, ge=0)
    people: list[PersonReport] | None = Field(default=None, max_length=200)
    face_model: str | None = Field(default=None, max_length=50, alias="faceModel")
    done: list[CommandResult] = Field(default_factory=list, max_length=100)

    model_config = {"populate_by_name": True}


class Command(BaseModel):
    """Команда телефону.

    - ``rename_person``: человека с постоянным ID ``person`` назвать ``to``;
    - ``merge_person``: человек ``person`` этого телефона — это человек дома ``into``
      (по имени ``to``): телефон переводит его наборы и записки на ``into``.

    ``name`` — имя ``person`` на момент команды, для истории в админке."""

    id: int
    kind: Literal["rename_person", "merge_person"]
    person: str
    name: str
    to: str
    into: str | None = None


class WeatherPlace(BaseModel):
    """Место, для которого показывается погода."""

    name: str
    latitude: float
    longitude: float


class WeatherReading(BaseModel):
    """Погода сейчас и на ближайшие 12 часов. ``symbol`` — код MET Norway (``cloudy``,
    ``lightrain_day``…), описание словами телефон делает сам. Время — миллисекунды Unix."""

    place: str
    temperature: float
    symbol: str
    wind: float | None = None
    min: float | None = None
    max: float | None = None
    time_ms: int = Field(serialization_alias="timeMs")
    fetched_at_ms: int = Field(serialization_alias="fetchedAtMs")
    # Ближайшие осадки в следующие 12 часов, если сейчас их нет: код MET и час (мс Unix).
    later_symbol: str | None = Field(default=None, serialization_alias="laterSymbol")
    later_time_ms: int | None = Field(default=None, serialization_alias="laterTimeMs")
    # Завтра по местному времени дома: наименьшая и наибольшая температура и что ожидается днём.
    tomorrow_min: float | None = Field(default=None, serialization_alias="tomorrowMin")
    tomorrow_max: float | None = Field(default=None, serialization_alias="tomorrowMax")
    tomorrow_symbol: str | None = Field(default=None, serialization_alias="tomorrowSymbol")


class DeviceConfig(BaseModel):
    """Настройки, которые хаб задаёт устройству, и команды, которые оно ещё не выполнило.

    Чего нет в ``values``, то у устройства по умолчанию."""

    rev: int
    values: dict[str, dict[str, int]]
    commands: list[Command] = Field(default_factory=list)
    people_rev: int = Field(default=0, serialization_alias="peopleRev")
    # ID людей, наборы которых хаб просит прислать (новые или изменившиеся на телефоне).
    upload: list[str] = Field(default_factory=list)
    # Место погоды, заданное в доме (null — не задано: телефон берёт своё), и погода для него.
    weather_place: WeatherPlace | None = Field(default=None, serialization_alias="weatherPlace")
    weather: WeatherReading | None = None


class PersonView(BaseModel):
    """Человек для админки: постоянный ID, имя, сколько наборов в хабе и какие телефоны его знают.
    Для нового с телефона — с какого он пришёл."""

    id: str
    name: str
    samples: int
    devices: list[str]
    origin: str = ""


class CommandView(BaseModel):
    """Команда для админки: что, на каком телефоне и чем закончилась."""

    id: int
    device_name: str
    kind: str
    person: str | None = None
    name: str
    to: str
    into: str | None = None
    done: bool
    ok: bool | None
    error: str | None


class Device(BaseModel):
    id: str
    name: str
    model: str
    app_version: str
    last_seen_at: float | None
    applied_rev: int


class DeviceView(BaseModel):
    """Устройство для страницы админки."""

    device: Device
    online: bool
    seen_ago_s: int | None
    up_to_date: bool


class SettingView(BaseModel):
    """Настройка для страницы админки: описание, заданное значение дома и действующее."""

    field: SettingField
    house_value: int | None

    @property
    def effective(self) -> int:
        return self.field.default if self.house_value is None else self.house_value
