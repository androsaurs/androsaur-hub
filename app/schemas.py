from datetime import datetime

from pydantic import BaseModel, Field


class ItemCreate(BaseModel):
    """Данные для создания элемента (вход API и формы)."""

    title: str = Field(min_length=1, max_length=200)


class Item(BaseModel):
    """Элемент в том виде, в котором он отдаётся наружу."""

    id: int
    title: str
    created_at: datetime
