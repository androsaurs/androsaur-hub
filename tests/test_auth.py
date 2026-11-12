"""Вход в админку, первичная настройка, привязка телефонов и их токены."""

import re
import sqlite3

from fastapi.testclient import TestClient

from app.config import get_settings
from app.services.auth import AuthError, AuthService
from tests.conftest import OWNER, OWNER_PASSWORD

PHONE = "phone-aaaa-0001"
OTHER = "phone-bbbb-0002"
REGISTRATION = {
    "id": PHONE,
    "name": "Детская",
    "model": "Redmi 12",
    "appVersion": "1",
    "settings": [],
}


def _db_dump() -> str:
    conn = sqlite3.connect(str(get_settings().database_path), uri=True)
    try:
        return "\n".join(conn.iterdump())
    finally:
        conn.close()


def _pair(anon: TestClient, client: TestClient, device: str = PHONE) -> str:
    page = client.post("/devices/pair-code")
    assert page.status_code == 200
    code = re.search(r'class="pair-code">([A-Z0-9-]+)<', page.text).group(1)
    reply = anon.post("/api/device/pair", json={"code": code, "id": device, "name": "Детская"})
    assert reply.status_code == 200, reply.text
    return reply.json()["token"]


# --- посторонний в сети ---


def test_admin_pages_need_login(anon: TestClient) -> None:
    for path in ("/", "/people", "/notes", "/medication", "/characters", "/trace", "/weather"):
        r = anon.get(path, follow_redirects=False)
        assert r.status_code == 303, path
        assert r.headers["location"].startswith("/login?next=")
    assert anon.post("/people/rename", data={"person": "x", "name": "y"}).status_code == 401


def test_device_api_needs_a_token(anon: TestClient) -> None:
    for path, body in (
        ("/api/device/register", REGISTRATION),
        ("/api/device/sync", {"device": PHONE, "applied": 0}),
        ("/api/device/people/download", {"device": PHONE}),
    ):
        r = anon.post(path, json=body)
        assert r.status_code == 401, path
        assert r.headers["www-authenticate"] == "Bearer"
    assert anon.get("/api/characters").status_code == 401
    assert anon.get("/api/health").json() == {"status": "ok"}


def test_owner_session_is_not_a_device_token(client: TestClient, anon: TestClient) -> None:
    anon.cookies.update(client.cookies)
    assert anon.get("/").status_code == 200
    assert anon.post("/api/device/people/download", json={"device": PHONE}).status_code == 401


def test_secrets_are_stored_hashed(client: TestClient, anon: TestClient) -> None:
    token = _pair(anon, client)
    dump = _db_dump()
    assert OWNER_PASSWORD not in dump
    assert token not in dump
    assert client.cookies.get("hub_session") not in dump


# --- первичная настройка ---


def test_setup_needs_the_one_time_link(fresh: TestClient) -> None:
    assert fresh.get("/setup").status_code == 404
    assert fresh.get("/setup?token=guess").status_code == 404
    token = AuthService().new_setup_token()
    assert fresh.get(f"/setup?token={token}").status_code == 200
    short = fresh.post(
        "/setup", data={"token": token, "username": "me", "password": "short", "password2": "short"}
    )
    assert short.status_code == 400
    done = fresh.post(
        "/setup",
        data={
            "token": token,
            "username": "me",
            "password": "long enough pw",
            "password2": "long enough pw",
        },
        follow_redirects=False,
    )
    assert done.status_code == 303 and "hub_session" in done.headers["set-cookie"]
    assert fresh.get("/", follow_redirects=False).status_code == 200
    # Ссылка одноразовая, и после появления владельца настройка закрыта совсем.
    assert fresh.get(f"/setup?token={token}").status_code == 404
    again = AuthService().new_setup_token()
    assert fresh.get(f"/setup?token={again}").status_code == 404


def test_login_page_explains_how_to_set_up(fresh: TestClient) -> None:
    assert "setup-link" in fresh.get("/login").text


# --- вход ---


def test_login_cookie_and_logout(anon: TestClient) -> None:
    bad = anon.post("/login", data={"username": OWNER, "password": "wrong password"})
    assert bad.status_code == 401
    unknown = anon.post("/login", data={"username": "nobody", "password": "wrong password"})
    assert "Неверное имя или пароль" in bad.text and "Неверное имя или пароль" in unknown.text
    ok = anon.post(
        "/login", data={"username": OWNER, "password": OWNER_PASSWORD}, follow_redirects=False
    )
    assert ok.status_code == 303
    cookie = ok.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert anon.get("/", follow_redirects=False).status_code == 200
    anon.post("/logout")
    assert anon.get("/", follow_redirects=False).status_code == 303


def test_login_is_throttled(anon: TestClient) -> None:
    for _ in range(5):
        anon.post("/login", data={"username": OWNER, "password": "wrong password"})
    blocked = anon.post("/login", data={"username": OWNER, "password": OWNER_PASSWORD})
    assert blocked.status_code == 429


def test_login_redirects_only_inside_the_hub(anon: TestClient) -> None:
    for target, expected in (
        ("/people", "/people"),
        ("//evil.example/x", "/"),
        ("https://evil.example", "/"),
    ):
        r = anon.post(
            "/login",
            data={"username": OWNER, "password": OWNER_PASSWORD, "next": target},
            follow_redirects=False,
        )
        assert r.headers["location"] == expected


def test_cross_site_forms_are_refused(client: TestClient) -> None:
    evil = {"Origin": "http://evil.example"}
    assert client.post("/devices/pair-code", headers=evil).status_code == 403
    assert client.post("/logout", headers=evil).status_code == 403
    same = {"Origin": "http://testserver"}
    assert client.post("/devices/pair-code", headers=same).status_code == 200


def test_set_password_closes_sessions(client: TestClient) -> None:
    AuthService().set_password(OWNER, "a brand new pw", "a brand new pw")
    assert client.get("/", follow_redirects=False).status_code == 303


def test_security_headers(client: TestClient) -> None:
    r = client.get("/")
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"


# --- привязка телефонов ---


def test_pairing_gives_a_token_for_one_phone(client: TestClient, anon: TestClient) -> None:
    token = _pair(anon, client)
    auth = {"Authorization": f"Bearer {token}"}
    assert anon.post("/api/device/register", json=REGISTRATION, headers=auth).status_code == 200
    sync = anon.post("/api/device/sync", json={"device": PHONE, "applied": 0}, headers=auth)
    assert sync.status_code == 200
    # Телефон говорит только от своего имени.
    other = dict(REGISTRATION, id=OTHER)
    assert anon.post("/api/device/register", json=other, headers=auth).status_code == 403
    r = anon.post("/api/device/people/download", json={"device": OTHER}, headers=auth)
    assert r.status_code == 403


def test_many_phones_each_with_own_code(client: TestClient, anon: TestClient) -> None:
    tokens = {device: _pair(anon, client, device) for device in (PHONE, OTHER, "phone-cccc-0003")}
    assert len(set(tokens.values())) == 3
    for device, token in tokens.items():
        r = anon.post(
            "/api/device/register",
            json=dict(REGISTRATION, id=device),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200


def test_a_code_works_once_and_wrong_codes_are_throttled(
    client: TestClient, anon: TestClient
) -> None:
    code = AuthService().new_pairing_code()
    assert anon.post("/api/device/pair", json={"code": code, "id": PHONE}).status_code == 200
    assert anon.post("/api/device/pair", json={"code": code, "id": OTHER}).status_code == 403
    for _ in range(9):
        anon.post("/api/device/pair", json={"code": "AAAA-BBBB", "id": OTHER})
    fresh_code = AuthService().new_pairing_code()
    blocked = anon.post("/api/device/pair", json={"code": fresh_code, "id": OTHER})
    assert blocked.status_code == 429


def test_codes_expire(client: TestClient) -> None:
    now = [1000.0]
    auth = AuthService(clock=lambda: now[0])
    code = auth.new_pairing_code()
    now[0] += 11 * 60
    try:
        auth.pair(code, PHONE, "x")
    except AuthError as e:
        assert str(e) == "bad_code"
    else:
        raise AssertionError("an expired code paired a phone")


def test_code_is_typed_by_hand(client: TestClient) -> None:
    auth = AuthService()
    code = auth.new_pairing_code()
    assert auth.pair(f" {code[:4].lower()}-{code[4:]} ", PHONE, "x")


def test_revoke_and_re_pair(client: TestClient, anon: TestClient) -> None:
    token = _pair(anon, client)
    auth = {"Authorization": f"Bearer {token}"}
    anon.post("/api/device/register", json=REGISTRATION, headers=auth)
    assert PHONE in client.get("/devices").text
    assert client.post(f"/devices/{PHONE}/revoke", follow_redirects=False).status_code == 303
    assert (
        anon.post(
            "/api/device/sync", json={"device": PHONE, "applied": 0}, headers=auth
        ).status_code
        == 401
    )
    assert "не привязан" in client.get("/devices").text
    new = {"Authorization": f"Bearer {_pair(anon, client)}"}
    assert (
        anon.post("/api/device/sync", json={"device": PHONE, "applied": 0}, headers=new).status_code
        == 200
    )


def test_re_pairing_replaces_the_old_token(client: TestClient, anon: TestClient) -> None:
    old = _pair(anon, client)
    _pair(anon, client)
    r = anon.post(
        "/api/device/register", json=REGISTRATION, headers={"Authorization": f"Bearer {old}"}
    )
    assert r.status_code == 401


def test_phone_opens_its_weather_page_with_its_token(client: TestClient, anon: TestClient) -> None:
    token = _pair(anon, client)
    r = anon.get(
        "/weather?embed=1", headers={"Authorization": f"Bearer {token}"}, follow_redirects=False
    )
    assert r.status_code == 200
    assert (
        anon.get(
            "/people", headers={"Authorization": f"Bearer {token}"}, follow_redirects=False
        ).status_code
        == 303
    )


def test_auth_pages_keep_the_origin_header(fresh: TestClient) -> None:
    """С no-referrer браузер шлёт форму с Origin: null, и защита форм её отвергает."""
    token = AuthService().new_setup_token()
    for page in (fresh.get(f"/setup?token={token}"), fresh.get("/login")):
        assert "no-referrer" not in page.text
        assert page.headers.get("referrer-policy") != "no-referrer"
    done = fresh.post(
        "/setup",
        data={
            "token": token,
            "username": "me",
            "password": "long enough pw",
            "password2": "long enough pw",
        },
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert done.status_code == 303


def test_devices_have_their_own_page(client: TestClient) -> None:
    assert 'href="/devices"' in client.get("/").text
    page = client.get("/devices")
    assert page.status_code == 200 and "Добавить телефон" in page.text
    assert 'aria-current="page"' in page.text


def _register(anon: TestClient, client: TestClient, device: str, name: str = "Redmi 12") -> str:
    token = _pair(anon, client, device)
    reply = anon.post(
        "/api/device/register",
        json={
            "id": device,
            "name": name,
            "model": "Xiaomi Redmi 12",
            "appVersion": "1",
            "settings": [],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert reply.status_code == 200
    return token


def test_phones_of_the_same_model_can_be_told_apart(client: TestClient, anon: TestClient) -> None:
    _register(anon, client, PHONE)
    _register(anon, client, OTHER)
    done = client.post(
        f"/devices/{PHONE}/rename", data={"name": "  Кухня  "}, follow_redirects=False
    )
    assert done.status_code == 303
    page = client.get("/devices").text
    assert 'class="device-name">Кухня<' in page
    assert 'class="device-name">Redmi 12<' in page  # второй ещё без имени
    assert 'value="Кухня"' in page


def test_a_new_registration_keeps_the_name_from_the_owner(
    client: TestClient, anon: TestClient
) -> None:
    token = _register(anon, client, PHONE)
    client.post(f"/devices/{PHONE}/rename", data={"name": "Детская"})
    anon.post(
        "/api/device/register",
        json={
            "id": PHONE,
            "name": "Redmi 12 (новое имя)",
            "model": "X",
            "appVersion": "2",
            "settings": [],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    page = client.get("/devices").text
    assert 'class="device-name">Детская<' in page and "Redmi 12 (новое имя)" in page


def test_an_empty_name_returns_the_phones_own(client: TestClient, anon: TestClient) -> None:
    _register(anon, client, PHONE)
    client.post(f"/devices/{PHONE}/rename", data={"name": "Кухня"})
    client.post(f"/devices/{PHONE}/rename", data={"name": ""})
    assert 'class="device-name">Redmi 12<' in client.get("/devices").text


def test_a_long_name_is_refused_and_markup_is_escaped(client: TestClient, anon: TestClient) -> None:
    _register(anon, client, PHONE)
    long = client.post(f"/devices/{PHONE}/rename", data={"name": "я" * 41}, follow_redirects=False)
    assert long.headers["location"] == "/devices?error=label_long"
    assert "не длиннее 40" in client.get("/devices?error=label_long").text
    client.post(f"/devices/{PHONE}/rename", data={"name": "<script>alert(1)</script>"})
    page = client.get("/devices").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_only_the_owner_renames(client: TestClient, anon: TestClient) -> None:
    _register(anon, client, PHONE)
    assert anon.post(f"/devices/{PHONE}/rename", data={"name": "x"}).status_code == 401
    assert (
        client.post(
            f"/devices/{PHONE}/rename",
            data={"name": "ok"},
            headers={"Origin": "http://evil.example"},
        ).status_code
        == 403
    )
