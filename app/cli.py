"""Команды для сервера хаба (нужен доступ к его базе):

python -m app.cli setup-link        ссылка, чтобы создать владельца (пока его нет)
python -m app.cli pair-code         одноразовый код привязки телефона (10 минут)
python -m app.cli set-password NAME сменить пароль; все сессии пользователя закрываются
python -m app.cli add-user NAME     добавить ещё одного пользователя админки
"""

import getpass
import sys

from app.db.database import init_db
from app.services.auth import AuthError, AuthService, format_code


def _passwords() -> tuple[str, str]:
    return getpass.getpass("Пароль: "), getpass.getpass("Пароль ещё раз: ")


def main(argv: list[str]) -> int:
    init_db()
    auth = AuthService()
    command, args = (argv[0], argv[1:]) if argv else ("", [])
    try:
        if command == "setup-link":
            if not auth.needs_setup():
                print("Владелец уже создан; войдите на /login или смените пароль: set-password.")
                return 1
            print(f"http://<адрес хаба>:<порт>/setup?token={auth.new_setup_token()}")
        elif command == "pair-code":
            print(format_code(auth.new_pairing_code()))
        elif command == "set-password" and len(args) == 1:
            auth.set_password(args[0], *_passwords())
            print("Пароль изменён, сессии закрыты.")
        elif command == "add-user" and len(args) == 1:
            auth.add_user(args[0], *_passwords())
            print("Пользователь добавлен.")
        else:
            print(__doc__)
            return 2
    except AuthError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
