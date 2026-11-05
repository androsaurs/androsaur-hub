import time
from collections.abc import Callable

from app.repositories.settings import SettingsRepository
from app.schemas import SettingValue, SettingView, in_range
from app.services.wake_phrase import normalize_wake_phrase


class SettingError(Exception):
    """Значение нельзя принять; текст показывается в админке."""


class SettingsService:
    """Настройки уровня дома: что задано и что действует."""

    def __init__(
        self,
        repository: SettingsRepository | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repository = repository or SettingsRepository()
        self._clock = clock

    def list(self) -> list[SettingView]:
        values = self._repository.values()
        return [
            SettingView(field=f, house_value=values.get((f.module, f.key)))
            for f in self._repository.fields()
        ]

    def revision(self) -> int:
        return self._repository.revision()

    def set(self, module: str, key: str, raw: str) -> int:
        field = self._repository.field(module, key)
        if field is None:
            raise SettingError("Такой настройки нет")
        value: SettingValue
        if field.type == "int":
            try:
                value = int(raw.strip())
            except ValueError:
                raise SettingError(f"«{field.title}»: нужно целое число") from None
        elif (module, key) == ("audio", "phrase"):
            phrase = normalize_wake_phrase(raw)
            if phrase is None:
                raise SettingError(
                    "Фраза пробуждения: нужно 1–4 русских слова, до 80 символов. "
                    "Варианты — через |, допуск — ~0…~3."
                )
            value = phrase
        else:
            value = raw.strip()
        if not in_range(field, value):
            raise SettingError(f"«{field.title}»: допустимо от {field.min} до {field.max}")
        return self._repository.set_value(module, key, value, self._clock())

    def reset(self, module: str, key: str) -> int:
        if self._repository.field(module, key) is None:
            raise SettingError("Такой настройки нет")
        return self._repository.clear_value(module, key)
