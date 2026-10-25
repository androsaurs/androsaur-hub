from app.repositories.items import ItemRepository
from app.schemas import Item, ItemCreate


class ItemService:
    """Бизнес-логика сущности Item.

    Роутеры обращаются сюда, а не в репозиторий напрямую — так доменные
    правила (валидация, нормализация, побочные эффекты) остаются в одном слое.
    """

    def __init__(self, repository: ItemRepository | None = None) -> None:
        self._repository = repository or ItemRepository()

    def list_items(self) -> list[Item]:
        return self._repository.list()

    def create_item(self, data: ItemCreate) -> Item:
        title = data.title.strip()
        return self._repository.add(title)
