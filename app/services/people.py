import time
from collections.abc import Callable

from app.repositories.devices import DeviceRepository
from app.repositories.people import PeopleRepository
from app.schemas import (
    CommandView,
    HousePeople,
    PeopleUpload,
    PersonReport,
    PersonView,
    valid_person_name,
)


class PersonError(Exception):
    """Действие с человеком нельзя принять; текст показывается в админке."""


# Коды ошибок телефона (камеры) — словами для админки.
ERRORS = {
    "unknown_person": "камера этого телефона не знает такого человека",
    "name_taken": "это имя уже занято на телефоне",
    "invalid_name": "телефон не принял имя",
    "camera_unavailable": "камера телефона не ответила — повторите позже",
    "unknown_command": "приложение на телефоне не знает такой команды — обновите его",
    "audio_unavailable": "модуль звука не ответил — записки ещё не перенесены, повторите позже",
}


class PeopleService:
    """Люди дома: кого узнают все телефоны.

    Хаб хранит людей дома с постоянным ID, именем и наборами чисел лица (без фотографий).
    - Первый телефон, пока в доме никого нет, приносит своих людей — они и становятся людьми дома.
    - Чистый телефон получает всех людей дома и сразу их узнаёт.
    - Люди, с которыми пришёл телефон из другой системы, ждут решения в админке: объединить с
      человеком дома или добавить как нового.
    - Дальше всё, чему учится принятый телефон (новые люди, новые наборы), уходит в хаб и
      раздаётся остальным.
    """

    def __init__(
        self,
        repository: PeopleRepository | None = None,
        devices: DeviceRepository | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repository = repository or PeopleRepository()
        self._devices = devices or DeviceRepository()
        self._clock = clock

    # --- обмен с телефоном ---

    def reconcile(
        self, device_id: str, people: list[PersonReport], face_model: str | None
    ) -> list[str]:
        """Телефон сообщил, кого знает. Возвращает ID людей, наборы которых нужно прислать."""
        state = self._devices.people_state(device_id)
        if state is None or not face_model:
            # Без модели лиц наборы несравнимы: только список имён, без обмена наборами.
            self._repository.replace_device_people(device_id, people)
            return []
        joined, _, _ = state
        house = self._repository.house()
        nobody_home = not any(p.status == "active" for p in house.values())
        received = self._repository.device_people(device_id)
        now = self._clock()
        upload = []
        renamed = False
        for person in people:
            known = house.get(person.id)
            if known is None:
                status = "active" if nobody_home or joined else "pending"
                self._repository.create(person, face_model, status, device_id, now)
                upload.append(person.id)
                continue
            if known.face_model != face_model:
                continue
            if person.digest is None or received.get(person.id) != person.digest:
                upload.append(person.id)
            if known.name != person.name and not self._repository.open_rename(person.id):
                # Переименовали на самом телефоне: новое имя становится именем дома.
                self._repository.set_name(person.id, person.name)
                renamed = known.status == "active"
        self._repository.replace_device_people(device_id, people)
        self._devices.set_people_state(device_id, face_model)
        if renamed:
            self._repository.bump()
        return upload

    def upload(self, data: PeopleUpload) -> None:
        """Наборы с телефона: новые добавляются к людям дома (до предела)."""
        state = self._devices.people_state(data.device)
        if state is None:
            raise PersonError("unknown device")
        house = self._repository.house()
        now = self._clock()
        changed = False
        for person in data.people:
            known = house.get(person.id)
            if known is None or known.face_model != data.face_model:
                continue
            added = self._repository.add_vectors(person.id, person.vectors, data.device, now)
            changed = changed or (added > 0 and known.status == "active")
            self._repository.uploaded(data.device, person.id, person.digest)
        if changed:
            self._repository.bump()

    def download(self, device_id: str) -> HousePeople:
        """Люди дома с наборами — для телефона этой же модели лиц."""
        state = self._devices.people_state(device_id)
        if state is None:
            raise PersonError("unknown device")
        _, face_model, _ = state
        rev = self._repository.revision()
        people = self._repository.with_vectors(face_model) if face_model else []
        self._devices.set_people_rev(device_id, rev)
        return HousePeople(rev=rev, face_model=face_model, people=people)

    def revision(self) -> int:
        return self._repository.revision()

    # --- админка ---

    def all(self) -> list[PersonView]:
        """Люди дома."""
        return [view for status, view in self._repository.views() if status == "active"]

    def incoming(self) -> list[PersonView]:
        """Люди, с которыми пришёл телефон из другой системы, — ждут решения."""
        return [view for status, view in self._repository.views() if status == "pending"]

    def commands(self) -> list[CommandView]:
        return self._repository.recent()

    def rename(self, person_id: str, to: str) -> None:
        """Переименовать человека дома: везде, где он есть. Человек определяется постоянным ID,
        имя — только подпись: записки и всё, что привязано к ID, остаются за ним."""
        to = " ".join(to.split())
        if not valid_person_name(to):
            raise PersonError("Имя: буквы и цифры, внутри можно пробел, «_» и «-», до 40 символов")
        house = self._repository.house()
        person = house.get(person_id)
        if person is None or person.status != "active":
            raise PersonError("Такого человека нет")
        if to == person.name:
            return
        if any(p.id != person_id and p.name.casefold() == to.casefold() for p in house.values()):
            raise PersonError(f"«{to}» уже есть — выберите другое имя")
        now = self._clock()
        self._repository.set_name(person_id, to)
        self._repository.bump()
        args = {"person": person_id, "name": person.name, "to": to}
        for device_id in self._repository.devices_knowing(person_id):
            self._repository.add_command(device_id, "rename_person", args, now)

    def add_new(self, person_id: str) -> None:
        """Человек с телефона из другой системы — новый человек дома."""
        house = self._repository.house()
        person = house.get(person_id)
        if person is None or person.status != "pending":
            raise PersonError("Этот человек уже разобран")
        if any(
            p.id != person_id
            and p.status == "active"
            and p.name.casefold() == person.name.casefold()
            for p in house.values()
        ):
            raise PersonError(
                f"В доме уже есть «{person.name}»: объедините с ним или сначала переименуйте"
            )
        self._repository.activate(person_id)
        self._repository.bump()

    def merge(self, person_id: str, into_id: str) -> None:
        """Человек с телефона из другой системы — это человек дома ``into_id``: его наборы
        добавляются к тому, а телефон переводит на ``into_id`` его и его записки."""
        house = self._repository.house()
        person, into = house.get(person_id), house.get(into_id)
        if person is None or person.status != "pending":
            raise PersonError("Этот человек уже разобран")
        if into is None or into.status != "active":
            raise PersonError("Выберите человека дома")
        if into.face_model != person.face_model:
            raise PersonError("Наборы сняты другой моделью распознавания — объединить нельзя")
        now = self._clock()
        self._repository.move(person_id, into_id, now)
        self._repository.bump()
        args = {"person": person_id, "name": person.name, "to": into.name, "into": into_id}
        for device_id in self._repository.devices_knowing(person_id):
            self._repository.add_command(device_id, "merge_person", args, now)
