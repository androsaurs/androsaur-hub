from dataclasses import dataclass

from app.repositories.characters import CharacterRepository
from app.schemas import CharacterAppearance


@dataclass(frozen=True)
class Character:
    id: str
    title: str
    color: str
    description: str


CATALOG = (
    Character(
        "dinosaur",
        "Динозаврик",
        "#63e6a4",
        "Зелёный исследователь. Всегда дома и готов к приключениям.",
    ),
    Character(
        "cat",
        "Кошечка",
        "#bd9aff",
        "Сиреневая мечтательница с розовыми ушками и любопытными усами.",
    ),
    Character("dog", "Собачка", "#f5ae64", "Рыжий друг с мягкими ушами и пятнышком вокруг глаза."),
    Character(
        "puf",
        "Пуф",
        "#a99bff",
        "Пушистый сиреневый компаньон с озорными хохолками и мягкими лапками.",
    ),
    Character(
        "hamster",
        "Хомяк",
        "#e9c89b",
        "Пухлый золотистый друг с круглыми ушками, усами и мягкими щёчками.",
    ),
)


class CharacterError(Exception):
    pass


SUPPORTED_ACCESSORIES = {
    "puf": {"none", "glasses", "bow", "scarf", "crown"},
    "hamster": {"none", "glasses", "bow", "seed"},
}


class CharacterService:
    def __init__(self, repository: CharacterRepository | None = None) -> None:
        self._repository = repository or CharacterRepository()

    def catalog(self) -> tuple[Character, ...]:
        return CATALOG

    def assignments(self) -> dict[str, str]:
        known = {c.id for c in CATALOG}
        return {p: c for p, c in self._repository.assignments().items() if c in known}

    def appearance(self) -> CharacterAppearance:
        return CharacterAppearance(
            characters=self.assignments(), accessories=self._repository.accessories()
        )

    def set_accessory(self, person: str, character: str, accessory: str) -> None:
        if accessory not in SUPPORTED_ACCESSORIES.get(character, set()):
            raise CharacterError("accessory_not_supported")
        error = self._repository.set_accessory(person, character, accessory)
        if error is not None:
            raise CharacterError(error)

    def select(self, person: str, character: str) -> None:
        if character not in {c.id for c in CATALOG}:
            raise CharacterError("Такого персонажа пока нет в каталоге")
        if not self._repository.select(person, character):
            raise CharacterError("Выберите человека из раздела «Люди»")
