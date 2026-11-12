import base64
import hashlib
import hmac
import secrets
import threading
import time
from collections.abc import Callable

from app.repositories.auth import AuthRepository

SESSION_DAYS = 30
PHONE_SESSION_HOURS = 12
SETUP_HOURS = 24
PAIRING_MINUTES = 10
# Буквы и цифры без похожих (0/O, 1/I/L): код вводят руками на экране телефона.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LEN = 8
MIN_PASSWORD = 10

# scrypt: ~32 МБ памяти и десятки миллисекунд на попытку — перебор по украденной базе дорог.
_N, _R, _P = 2**15, 8, 1


class AuthError(Exception):
    """Ошибка входа, настройки или привязки; текст — код для страницы."""


def _digest(secret: str) -> str:
    """Хеш случайного секрета (токен, код): соль не нужна, секрет сам длинный и случайный."""
    return hashlib.sha256(secret.encode()).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=64 * 1024**2)
    b64 = base64.b64encode
    return f"scrypt${_N}${_R}${_P}${b64(salt).decode()}${b64(key).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, key = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(key)
        actual = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=64 * 1024**2,
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def normalize_code(code: str) -> str:
    return "".join(c for c in code.upper() if c.isalnum())


def format_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


class Throttle:
    """Ограничение неудачных попыток по адресу: после [limit] ошибок за окно — пауза.

    Хранится в памяти процесса: перезапуск хаба её сбрасывает,
    этого достаточно для домашней сети."""

    def __init__(self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic):
        self._limit, self._window, self._clock = limit, window_s, clock
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str) -> list[float]:
        now = self._clock()
        fails = [t for t in self._fails.get(key, []) if now - t < self._window]
        self._fails[key] = fails
        return fails

    def blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._recent(key)) >= self._limit

    def fail(self, key: str) -> None:
        with self._lock:
            self._recent(key).append(self._clock())

    def reset(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


LOGIN_THROTTLE = Throttle(limit=5, window_s=300)
PAIRING_THROTTLE = Throttle(limit=10, window_s=300)


class AuthService:
    """Вход владельца, первичная настройка, привязка телефонов и проверка их токенов."""

    def __init__(
        self, repo: AuthRepository | None = None, clock: Callable[[], float] = time.time
    ) -> None:
        self._repo = repo or AuthRepository()
        self._clock = clock

    # --- первичная настройка ---

    def needs_setup(self) -> bool:
        return not self._repo.has_users()

    def new_setup_token(self) -> str:
        """Одноразовая ссылка создать владельца; печатается в лог хаба или выдаётся командой."""
        token = secrets.token_urlsafe(24)
        self._repo.add_setup_token(_digest(token), self._clock() + SETUP_HOURS * 3600)
        return token

    def ensure_setup_token(self) -> str | None:
        """На старте: если владельца нет — новая ссылка настройки (прежние остаются в силе)."""
        return self.new_setup_token() if self.needs_setup() else None

    def setup_token_valid(self, token: str) -> bool:
        return (
            bool(token)
            and self.needs_setup()
            and self._repo.setup_token_valid(_digest(token), self._clock())
        )

    def create_owner(self, token: str, username: str, password: str, password2: str) -> str:
        """Создать первого пользователя по ссылке настройки и сразу открыть ему сессию."""
        if not self.setup_token_valid(token):
            raise AuthError("setup_expired")
        username = username.strip()
        if not username or len(username) > 64:
            raise AuthError("username")
        self._check_password(password, password2)
        self._repo.add_user(username, hash_password(password), self._clock())
        self._repo.clear_setup_tokens()
        return self.login(username, password)

    @staticmethod
    def _check_password(password: str, password2: str) -> None:
        if len(password) < MIN_PASSWORD:
            raise AuthError("password_short")
        if password != password2:
            raise AuthError("password_mismatch")

    def set_password(self, username: str, password: str, password2: str) -> None:
        """Смена пароля из командной строки сервера; все сессии пользователя закрываются."""
        self._check_password(password, password2)
        if not self._repo.set_password(username, hash_password(password)):
            raise AuthError("unknown_user")

    def add_user(self, username: str, password: str, password2: str) -> None:
        self._check_password(password, password2)
        self._repo.add_user(username.strip(), hash_password(password), self._clock())

    # --- сессии ---

    def login(self, username: str, password: str) -> str:
        """Новая сессия; один и тот же ответ на неверное имя и неверный пароль."""
        found = self._repo.user_by_name(username.strip())
        if found is None:
            verify_password(password, hash_password("timing"))  # время ответа не выдаёт имя
            raise AuthError("bad_login")
        user_id, stored = found
        if not verify_password(password, stored):
            raise AuthError("bad_login")
        token = secrets.token_urlsafe(32)
        now = self._clock()
        self._repo.add_session(_digest(token), user_id, now, now + SESSION_DAYS * 86400)
        return token

    def session_user(self, token: str | None) -> str | None:
        return self._repo.session_user(_digest(token), self._clock()) if token else None

    def logout(self, token: str | None) -> None:
        if token:
            self._repo.delete_session(_digest(token))

    # --- телефоны ---

    def new_pairing_code(self) -> str:
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LEN))
        now = self._clock()
        self._repo.add_pairing_code(_digest(code), now, now + PAIRING_MINUTES * 60)
        return code

    def pairing_codes_left(self) -> list[int]:
        """Сколько секунд осталось каждому ещё не использованному коду."""
        now = self._clock()
        return [int(t - now) for t in self._repo.active_pairing_codes(now)]

    def pair(self, code: str, device_id: str, name: str) -> str:
        """Обменять одноразовый код на токен телефона. Повторная привязка отзывает старый токен."""
        device_id = device_id.strip()
        if not device_id or len(device_id) > 100:
            raise AuthError("device")
        if not self._repo.use_pairing_code(_digest(normalize_code(code)), self._clock()):
            raise AuthError("bad_code")
        token = secrets.token_urlsafe(32)
        self._repo.set_device_token(device_id, name.strip()[:100], _digest(token), self._clock())
        return token

    def device_for_token(self, token: str | None) -> str | None:
        return self._repo.device_for_token(_digest(token), self._clock()) if token else None

    def start_phone_session(self, device_id: str) -> str:
        """Сессия страниц телефона (каталог персонажей в WebView); токен показывается один раз."""
        token = secrets.token_urlsafe(32)
        now = self._clock()
        self._repo.add_phone_session(
            _digest(token), device_id, now, now + PHONE_SESSION_HOURS * 3600
        )
        return token

    def phone_session_device(self, token: str | None) -> str | None:
        return self._repo.phone_session_device(_digest(token), self._clock()) if token else None

    def paired_devices(self) -> dict[str, float]:
        return self._repo.paired_devices()

    def revoke_device(self, device_id: str) -> bool:
        return self._repo.revoke_device(device_id)
