"""Проверка доступа: сессия владельца для админки, токен телефона для API устройств."""

from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, Response, status

from app.dependencies import get_auth_service
from app.services.auth import PHONE_SESSION_HOURS, SESSION_DAYS, AuthService

SESSION_COOKIE = "hub_session"
PHONE_COOKIE = "hub_phone"
PHONE_COOKIE_PATH = "/characters"
PHONE_SESSION_SECONDS = PHONE_SESSION_HOURS * 3600
# Страницы, которые привязанный телефон открывает у себя: выбор персонажа ребёнком.
PHONE_PAGE_PATHS = {"/characters", "/characters/select"}
_SAFE = {"GET", "HEAD", "OPTIONS"}
# Страницы, которые телефон открывает у себя во встроенном браузере со своим токеном.
DEVICE_PAGES = {"/weather"}


class LoginRequired(Exception):
    """Нужен вход в админку: GET уводит на страницу входа, остальное получает 401."""


def bearer(request: Request) -> str | None:
    value = request.headers.get("authorization", "")
    scheme, _, token = value.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def same_origin(request: Request) -> bool:
    """Защита форм от подделки запроса с чужого сайта: Origin, если браузер его прислал,
    должен указывать на этот же хаб. Cookie сессии к тому же SameSite=Strict."""
    origin = request.headers.get("origin")
    if origin is None:
        return True
    return urlsplit(origin).netloc.lower() == request.headers.get("host", "").lower()


def is_https(request: Request) -> bool:
    return (
        request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto", "").lower() == "https"
    )


def set_session_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_DAYS * 86400,
        httponly=True,
        samesite="strict",
        secure=is_https(request),
        path="/",
    )


def set_phone_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(
        PHONE_COOKIE,
        token,
        max_age=PHONE_SESSION_SECONDS,
        httponly=True,
        samesite="strict",
        secure=is_https(request),
        path=PHONE_COOKIE_PATH,
    )


def require_owner(request: Request, auth: AuthService = Depends(get_auth_service)) -> str:
    """Доступ к админке: вошедший пользователь (или телефон к своей странице погоды)."""
    if request.method not in _SAFE and not same_origin(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "cross-site request")
    user = auth.session_user(request.cookies.get(SESSION_COOKIE))
    if user:
        return user
    if (
        request.method == "GET"
        and request.url.path in DEVICE_PAGES
        and auth.device_for_token(bearer(request))
    ):
        return "device"
    if request.url.path in PHONE_PAGE_PATHS:
        if auth.phone_session_device(request.cookies.get(PHONE_COOKIE)):
            return "device"
        device = auth.device_for_token(bearer(request)) if request.method == "GET" else None
        if device:
            # Первое открытие страницы с токеном телефона: дальше формы пойдут с cookie.
            request.state.new_phone_session = auth.start_phone_session(device)
            return "device"
    raise LoginRequired()


def require_device(request: Request, auth: AuthService = Depends(get_auth_service)) -> str:
    """ID привязанного телефона по его токену; без токена — 401, телефон просит привязку."""
    device = auth.device_for_token(bearer(request))
    if device is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "pairing required", headers={"WWW-Authenticate": "Bearer"}
        )
    return device


def same_device(authed: str, claimed: str) -> None:
    """Телефон говорит только от своего имени: ID в запросе должен совпасть с ID токена."""
    if authed != claimed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "device mismatch")
