from fastapi import APIRouter, Depends, status

from app.dependencies import get_item_service
from app.schemas import Item, ItemCreate
from app.services.items import ItemService

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/items", response_model=list[Item])
def list_items(service: ItemService = Depends(get_item_service)) -> list[Item]:
    return service.list_items()


@router.post("/items", response_model=Item, status_code=status.HTTP_201_CREATED)
def create_item(
    payload: ItemCreate,
    service: ItemService = Depends(get_item_service),
) -> Item:
    return service.create_item(payload)
