import math
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, StrictInt, field_validator, model_validator

FieldType = Literal["int", "string"]
SettingValue = StrictInt | str

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
    default: SettingValue

    @model_validator(mode="after")
    def _default_in_range(self) -> "SettingField":
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min больше max")
        if not in_range(self, self.default):
            raise ValueError("значение по умолчанию вне диапазона")
        return self


def in_range(field: SettingField, value: SettingValue) -> bool:
    if field.type == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            return False
        size = value
    else:
        if not isinstance(value, str):
            return False
        size = len(value)
    return (field.min is None or size >= field.min) and (field.max is None or size <= field.max)


WAKE_PHRASE_FIELD = SettingField(
    module="audio",
    key="phrase",
    type="string",
    title="Фраза пробуждения",
    description=(
        "Для Vosk: 1–4 русских слова, например «зюзя» или «привет петя». "
        "Варианты слова — через |, допуск — ~0…~3 (например «железяка~1»). "
        "У microWakeWord фраза зашита в модель: для этой настройки нужен Vosk. "
        "Пока фраза не задана в хабе, телефон сохраняет свой выбор."
    ),
    min=1,
    max=80,
    default="зюзя",
)


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


class HourPoint(BaseModel):
    """Один час прогноза: для ленты на главном экране телефона и страницы погоды в админке."""

    time_ms: int = Field(serialization_alias="timeMs")
    temperature: float
    feels_like: float | None = Field(default=None, serialization_alias="feelsLike")
    symbol: str = ""
    wind: float | None = None
    gust: float | None = None
    precip_prob: float | None = Field(default=None, serialization_alias="precipProb")
    precip_mm: float | None = Field(default=None, serialization_alias="precipMm")


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
    # Откуда погода («Яндекс Погода», «MET Norway») и до какого момента хаб считает её свежей.
    source: str = ""
    valid_until_ms: int | None = Field(default=None, serialization_alias="validUntilMs")
    # Почасовой прогноз: в базе хранится отдельно, телефону уходят ближайшие 24 часа.
    hours: list[HourPoint] | None = None


class RoleSet(BaseModel):
    """Роли человека для телефона: «передай папе» — это ему (по постоянному ID)."""

    person: str
    name: str
    words: list[str]


class RoomWords(BaseModel):
    """Комната с колонкой для телефона: «передай в детскую» — на её колонку, а не записка."""

    id: str
    name: str
    words: list[str]


class AnnounceReply(BaseModel):
    """Чем закончилось «передай в комнату»: ``spoken`` — сказано на колонке; остальное — причина.

    ``text`` — расшифровка, если она получилась (телефон может её повторить)."""

    status: str
    text: str | None = None


class ThingReply(BaseModel):
    """Чем кончилось «запомни» / «вспомни»: ``saved``, ``updated`` (``previous`` — прежняя
    запись), ``found``, ``not_found``, ``forgotten``, ``ambiguous`` (``text`` — подходящие записи),
    ``nothing_to_forget`` и причины (``unclear``, ``no_groq``,
    ``no_memory``, ``error``).

    ``text`` — что запомнили, что нашли или что расслышали."""

    status: str
    text: str | None = None
    previous: str | None = None


class CharacterAppearance(BaseModel):
    characters: dict[str, str] = Field(default_factory=dict)
    accessories: dict[str, dict[str, str]] = Field(default_factory=dict)


class AccessorySelection(BaseModel):
    person: str = Field(pattern=_PERSON_ID)
    character: str = Field(pattern=_NAME)
    accessory: Literal[
        "none",
        "glasses",
        "bow",
        "scarf",
        "crown",
        "seed",
        "bobble_hat",
        "headphones",
        "cap",
        "hoodie",
    ]


class DeviceConfig(BaseModel):
    """Настройки, которые хаб задаёт устройству, и команды, которые оно ещё не выполнило.

    Чего нет в ``values``, то у устройства по умолчанию."""

    rev: int
    characters: dict[str, str] = Field(default_factory=dict)
    accessories: dict[str, dict[str, str]] = Field(default_factory=dict)
    values: dict[str, dict[str, SettingValue]]
    commands: list[Command] = Field(default_factory=list)
    people_rev: int = Field(default=0, serialization_alias="peopleRev")
    # ID людей, наборы которых хаб просит прислать (новые или изменившиеся на телефоне).
    upload: list[str] = Field(default_factory=list)
    # Место погоды, заданное в доме (null — не задано: телефон берёт своё), и погода для него.
    weather_place: WeatherPlace | None = Field(default=None, serialization_alias="weatherPlace")
    # Надёжные расшифровки записок этого телефона: он кладёт их к запискам.
    transcripts: list["NoteText"] = Field(default_factory=list)
    # Роли людей дома; телефон кладёт их в книгу адресатов.
    roles: list[RoleSet] = Field(default_factory=list)
    # Комнаты с колонками: по ним телефон отличает «передай в детскую» от записки человеку.
    rooms: list[RoomWords] = Field(default_factory=list)
    # Графики приёма лекарств людей дома (только у кого времена заданы).
    medications: list["MedicationPlan"] = Field(default_factory=list)
    # «Не засиживаться»: настройки людей, у кого включено.
    sitting: list["SittingPlan"] = Field(default_factory=list)
    weather: WeatherReading | None = None


class PersonView(BaseModel):
    """Человек для админки: постоянный ID, имя, сколько наборов в хабе и какие телефоны его знают.
    Для нового с телефона — с какого он пришёл."""

    id: str
    name: str
    samples: int
    devices: list[str]
    origin: str = ""
    roles: list[str] = Field(default_factory=list)
    zodiac_sign: str = ""
    birth_date: date | None = None
    horoscope_in_greeting: bool = False
    age: int | None = None
    derived_zodiac_sign: str = ""
    effective_zodiac_sign: str = ""


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


class ConfigFieldView(BaseModel):
    key: str
    label: str
    unit: str
    low: int
    high: int


class IntegrationView(BaseModel):
    """Карточка интеграции для админки (без ключа: только его последние символы)."""

    id: str
    title: str
    description: str
    builtin: bool
    enabled: bool
    key_hint: str | None
    config: dict[str, int]
    fields: list[ConfigFieldView] = Field(default_factory=list)
    checked_at: float | None
    check_ok: bool | None
    check_message: str | None


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
    house_value: SettingValue | None

    @property
    def effective(self) -> SettingValue:
        return self.field.default if self.house_value is None else self.house_value


class HubNote(BaseModel):
    """Записка в хабе: что о ней известно и чем закончилась расшифровка."""

    id: str
    device_id: str
    to_label: str
    from_label: str
    created_ms: int
    duration_ms: int
    status: str
    text: str | None
    confidence: float | None
    speech: float | None
    usable: bool | None
    error: str | None
    has_audio: bool


class MedicationPlan(BaseModel):
    """График приёма лекарств человека для телефона: времена ЧЧ:ММ; телефон хранит его сам."""

    person: str
    name: str
    times: list[str]


class SittingPlan(BaseModel):
    """Настройки «не засиживаться» человека для телефона (у кого включено), минуты."""

    person: str
    name: str
    first_min: int = Field(serialization_alias="firstMin")
    repeat_min: int = Field(serialization_alias="repeatMin")
    break_min: int = Field(serialization_alias="breakMin")


class SittingBucket(BaseModel):
    """15-минутная ячейка: сколько секунд человека видели и самая долгая серия (минуты)."""

    person: str = Field(min_length=1, max_length=64)
    bucket: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:(00|15|30|45)$")
    present_s: int = Field(ge=0, le=900, alias="presentS")
    streak_min: int = Field(ge=0, le=1440, alias="streakMin")
    level: int = Field(ge=0, le=2)

    model_config = {"populate_by_name": True}


class SittingReport(BaseModel):
    device: str = Field(pattern=r"^[A-Za-z0-9-]{8,64}$")
    buckets: list[SittingBucket] = Field(max_length=300)


class MedicationEvent(BaseModel):
    """Что произошло на телефоне: ``reminded`` — напомнил, ``taken`` — человек сказал, что принял.

    ``slot`` — начало интервала по местным часам («ГГГГ-ММ-ДДTЧЧ:ММ»), ``at`` — время, мс."""

    person: str = Field(min_length=1, max_length=64)
    slot: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
    kind: Literal["reminded", "taken"]
    at: int = Field(ge=0)


class MedicationReport(BaseModel):
    device: str = Field(pattern=r"^[A-Za-z0-9-]{8,64}$")
    events: list[MedicationEvent] = Field(max_length=200)


class NoteText(BaseModel):
    """Расшифровка для телефона: только надёжные — их Петя читает голосом."""

    id: str
    text: str


class TraceEvent(BaseModel):
    run: str = Field(min_length=1, max_length=40)
    ts: int
    src: Literal["audio", "app"]
    stage: str = Field(min_length=1, max_length=40)
    data: dict[str, Any] = Field(default_factory=dict)


class TraceBatch(BaseModel):
    device: str = Field(default="", max_length=64)
    events: list[TraceEvent] = Field(max_length=300)
