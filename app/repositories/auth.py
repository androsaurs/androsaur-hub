from app.db.database import get_connection


class AuthRepository:
    """Пользователи, сессии, ссылки настройки, коды привязки и токены телефонов.

    Секреты (пароли, токены, коды) сюда приходят уже хешированными."""

    # --- пользователи ---

    def has_users(self) -> bool:
        with get_connection() as conn:
            return conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None

    def add_user(self, username: str, password_hash: str, now: float) -> int:
        with get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, password_hash, now),
            )
            return int(cursor.lastrowid or 0)

    def user_by_name(self, username: str) -> tuple[int, str] | None:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT id, password_hash FROM users WHERE username = ?", (username,)
            ).fetchone()
        return (row["id"], row["password_hash"]) if row else None

    def set_password(self, username: str, password_hash: str) -> bool:
        with get_connection() as conn:
            cursor = conn.execute(
                "UPDATE users SET password_hash = ? WHERE username = ?", (password_hash, username)
            )
            if cursor.rowcount:
                conn.execute(
                    "DELETE FROM sessions WHERE user_id = "
                    "(SELECT id FROM users WHERE username = ?)",
                    (username,),
                )
        return cursor.rowcount > 0

    # --- сессии ---

    def add_session(self, token_hash: str, user_id: int, now: float, expires_at: float) -> None:
        with get_connection() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            conn.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (token_hash, user_id, now, expires_at),
            )

    def session_user(self, token_hash: str, now: float) -> str | None:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT u.username FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token_hash = ? AND s.expires_at > ?",
                (token_hash, now),
            ).fetchone()
        return row["username"] if row else None

    def delete_session(self, token_hash: str) -> None:
        with get_connection() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    # --- первичная настройка ---

    def add_setup_token(self, token_hash: str, expires_at: float) -> None:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO setup_tokens (token_hash, expires_at) VALUES (?, ?)",
                (token_hash, expires_at),
            )

    def setup_token_valid(self, token_hash: str, now: float) -> bool:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM setup_tokens WHERE token_hash = ? AND expires_at > ?",
                (token_hash, now),
            ).fetchone()
        return row is not None

    def has_setup_token(self, now: float) -> bool:
        with get_connection() as conn:
            row = conn.execute("SELECT 1 FROM setup_tokens WHERE expires_at > ?", (now,)).fetchone()
        return row is not None

    def clear_setup_tokens(self) -> None:
        with get_connection() as conn:
            conn.execute("DELETE FROM setup_tokens")

    # --- коды привязки ---

    def add_pairing_code(self, code_hash: str, now: float, expires_at: float) -> None:
        with get_connection() as conn:
            conn.execute("DELETE FROM pairing_codes WHERE expires_at < ?", (now,))
            conn.execute(
                "INSERT INTO pairing_codes (code_hash, created_at, expires_at) VALUES (?, ?, ?)",
                (code_hash, now, expires_at),
            )

    def use_pairing_code(self, code_hash: str, now: float) -> bool:
        """Погасить код: True, если он был и не истёк. Второй раз тот же код не сработает."""
        with get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM pairing_codes WHERE code_hash = ? AND expires_at > ?",
                (code_hash, now),
            )
        return cursor.rowcount > 0

    def active_pairing_codes(self, now: float) -> list[float]:
        """Сроки действия ещё не использованных кодов (сами коды не хранятся)."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT expires_at FROM pairing_codes WHERE expires_at > ? ORDER BY expires_at",
                (now,),
            ).fetchall()
        return [row["expires_at"] for row in rows]

    # --- токены телефонов ---

    def set_device_token(self, device_id: str, name: str, token_hash: str, now: float) -> None:
        """Новый токен телефона заменяет прежние: повторная привязка отзывает старый."""
        with get_connection() as conn:
            conn.execute("DELETE FROM device_tokens WHERE device_id = ?", (device_id,))
            conn.execute(
                "INSERT INTO device_tokens (token_hash, device_id, name, created_at) "
                "VALUES (?, ?, ?, ?)",
                (token_hash, device_id, name, now),
            )

    def device_for_token(self, token_hash: str, now: float) -> str | None:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT device_id, last_used_at FROM device_tokens WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            if row["last_used_at"] is None or now - row["last_used_at"] > 60:
                conn.execute(
                    "UPDATE device_tokens SET last_used_at = ? WHERE token_hash = ?",
                    (now, token_hash),
                )
        return str(row["device_id"])

    def paired_devices(self) -> dict[str, float]:
        """Привязанные телефоны: ID → когда выдан токен."""
        with get_connection() as conn:
            rows = conn.execute("SELECT device_id, created_at FROM device_tokens").fetchall()
        return {row["device_id"]: row["created_at"] for row in rows}

    def revoke_device(self, device_id: str) -> bool:
        with get_connection() as conn:
            cursor = conn.execute("DELETE FROM device_tokens WHERE device_id = ?", (device_id,))
            conn.execute("DELETE FROM phone_sessions WHERE device_id = ?", (device_id,))
        return cursor.rowcount > 0

    # --- сессии страниц телефона ---

    def add_phone_session(
        self, token_hash: str, device_id: str, now: float, expires_at: float
    ) -> None:
        with get_connection() as conn:
            conn.execute("DELETE FROM phone_sessions WHERE expires_at < ?", (now,))
            conn.execute(
                "INSERT INTO phone_sessions (token_hash, device_id, expires_at) VALUES (?, ?, ?)",
                (token_hash, device_id, expires_at),
            )

    def phone_session_device(self, token_hash: str, now: float) -> str | None:
        """ID телефона, если сессия жива и его токен всё ещё действует (отозванный не проходит)."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT s.device_id FROM phone_sessions s "
                "JOIN device_tokens t ON t.device_id = s.device_id "
                "WHERE s.token_hash = ? AND s.expires_at > ?",
                (token_hash, now),
            ).fetchone()
        return str(row["device_id"]) if row else None
