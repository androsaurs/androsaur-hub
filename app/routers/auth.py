"""Вход, выход и первичная настройка: эти страницы открыты без сессии."""

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.dependencies import get_auth_service
from app.security import SESSION_COOKIE, same_origin, set_session_cookie
from app.services.auth import LOGIN_THROTTLE, MIN_PASSWORD, AuthError, AuthService
from app.templating import templates

router = APIRouter(tags=["auth"])

_MESSAGES = {
    "bad_login": "Неверное имя или пароль.",
    "throttled": "Слишком много попыток. Подождите пять минут.",
    "username": "Укажите имя пользователя (до 64 символов).",
    "password_short": f"Пароль должен быть не короче {MIN_PASSWORD} символов.",
    "password_mismatch": "Пароли не совпадают.",
    "setup_expired": "Ссылка настройки устарела или уже использована.",
}


def _safe_next(value: str | None) -> str:
    """Возврат после входа только на страницу этого же хаба."""
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    parts = urlsplit(value)
    return value if not parts.scheme and not parts.netloc else "/"


def _page(request: Request, setup: bool, **extra: object) -> HTMLResponse:
    context = {
        "title": "Первая настройка" if setup else "Вход",
        "lead": (
            "Создайте владельца хаба. Без входа админка, люди и лица семьи недоступны."
            if setup
            else "Войдите, чтобы управлять домом."
        ),
        "action": "/setup" if setup else "/login",
        "button": "Создать и войти" if setup else "Войти",
        "setup": setup,
        "min_password": MIN_PASSWORD,
        **extra,
    }
    response = templates.TemplateResponse(request, "auth.html", context)
    response.headers["Cache-Control"] = "no-store"
    return response


def _check_origin(request: Request) -> None:
    if not same_origin(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "cross-site request")


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request, next: str | None = None, auth: AuthService = Depends(get_auth_service)
) -> Response:
    if auth.needs_setup():
        return _page(
            request,
            setup=False,
            error="Владелец ещё не создан. Откройте ссылку первой настройки из журнала хаба "
            "или получите новую: python -m app.cli setup-link",
        )
    return _page(request, setup=False, next_url=_safe_next(next))


@router.post("/login")
def login(
    request: Request,
    username: str = Form(max_length=64),
    password: str = Form(max_length=1024),
    next: str = Form(default="/"),
    auth: AuthService = Depends(get_auth_service),
) -> Response:
    _check_origin(request)
    key = request.client.host if request.client else "?"
    if LOGIN_THROTTLE.blocked(key):
        page = _page(request, setup=False, error=_MESSAGES["throttled"], username=username)
        page.status_code = status.HTTP_429_TOO_MANY_REQUESTS
        return page
    try:
        token = auth.login(username, password)
    except AuthError as e:
        LOGIN_THROTTLE.fail(key)
        page = _page(
            request,
            setup=False,
            error=_MESSAGES[str(e)],
            username=username,
            next_url=_safe_next(next),
        )
        page.status_code = status.HTTP_401_UNAUTHORIZED
        return page
    LOGIN_THROTTLE.reset(key)
    response = RedirectResponse(_safe_next(next), status_code=status.HTTP_303_SEE_OTHER)
    set_session_cookie(request, response, token)
    return response


@router.post("/logout")
def logout(request: Request, auth: AuthService = Depends(get_auth_service)) -> Response:
    _check_origin(request)
    auth.logout(request.cookies.get(SESSION_COOKIE))
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/setup", response_class=HTMLResponse)
def setup_page(
    request: Request, token: str = "", auth: AuthService = Depends(get_auth_service)
) -> Response:
    if not auth.setup_token_valid(token):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    return _page(request, setup=True, token=token)


@router.post("/setup")
def setup(
    request: Request,
    token: str = Form(max_length=200),
    username: str = Form(max_length=64),
    password: str = Form(max_length=1024),
    password2: str = Form(max_length=1024),
    auth: AuthService = Depends(get_auth_service),
) -> Response:
    _check_origin(request)
    try:
        session = auth.create_owner(token, username, password, password2)
    except AuthError as e:
        if str(e) == "setup_expired":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found") from None
        page = _page(request, setup=True, token=token, error=_MESSAGES[str(e)], username=username)
        page.status_code = status.HTTP_400_BAD_REQUEST
        return page
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    set_session_cookie(request, response, session)
    return response
