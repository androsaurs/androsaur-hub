from app.services.items import ItemService


def get_item_service() -> ItemService:
    """FastAPI-зависимость: сервис сущности Item.

    Вынесена отдельно, чтобы тесты могли переопределять её через
    ``app.dependency_overrides``.
    """
    return ItemService()
