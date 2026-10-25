from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.dependencies import get_item_service
from app.schemas import ItemCreate
from app.services.items import ItemService
from app.templating import templates

router = APIRouter(tags=["pages"])


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    service: ItemService = Depends(get_item_service),
) -> HTMLResponse:
    items = service.list_items()
    return templates.TemplateResponse(request, "index.html", {"items": items})


@router.post("/items")
def create_item(
    title: str = Form(...),
    service: ItemService = Depends(get_item_service),
) -> RedirectResponse:
    service.create_item(ItemCreate(title=title))
    # 303 → браузер делает GET на "/", защита от повторной отправки формы.
    return RedirectResponse(url="/", status_code=303)
