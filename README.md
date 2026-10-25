# template-python-fastapi-jinja

Шаблон веб-портала с собственным бэкендом: **Python + FastAPI + Jinja2**, слоистая
архитектура, SQLite «из коробки», готовая обвязка тестов и инструментов.

Шаблон **обезличен** — в нём нет привязки к какой-либо предметной области. Есть лишь
одна демонстрационная сущность `Item`, которая показывает, как проходит запрос через
все слои. Создавая новый проект, замените её на свою модель и удалите пример.

## Стек

| Компонент            | Решение                          |
|----------------------|----------------------------------|
| Язык                 | Python 3.11+                     |
| Веб-фреймворк        | FastAPI                          |
| ASGI-сервер          | Uvicorn                          |
| Шаблоны              | Jinja2                           |
| Конфигурация         | pydantic-settings                |
| Хранилище            | SQLite (стандартный `sqlite3`)   |
| Тесты                | pytest + httpx (TestClient)      |
| Линтер / формат      | ruff                             |
| Типы                 | mypy (strict)                    |

## Структура

```
app/
  main.py              ← фабрика приложения create_app() + lifespan (init_db)
  config.py            ← настройки из окружения (pydantic-settings, префикс PORTAL_)
  templating.py        ← общий объект Jinja2Templates + фильтры
  dependencies.py      ← FastAPI-зависимости (внедрение сервисов)
  schemas.py           ← pydantic-модели (контракты вход/выход)
  routers/
    pages.py           ← HTML-страницы (Jinja2)
    api.py             ← JSON API (/api/...)
  services/
    items.py           ← бизнес-логика (слой между роутером и репозиторием)
  repositories/
    items.py           ← доступ к данным, единственное место с SQL
  db/
    database.py        ← соединение с SQLite + схема + init_db()
  templates/           ← Jinja2-шаблоны (base.html, index.html)
  static/              ← CSS / JS / картинки
tests/                 ← pytest, изолированная БД на каждый тест
```

Поток запроса: **router → service → repository → SQLite**. Слой выше не лезет в детали
слоя ниже на два уровня (роутер не пишет SQL, сервис не знает про HTTP).

## Быстрый старт

```bash
# 1. Зависимости (лучше в виртуальном окружении)
make install            # = pip install -e ".[dev]"

# 2. Настройки
cp .env.example .env    # при необходимости поправьте значения

# 3. Запуск
make dev                # uvicorn с авто-перезагрузкой
# открыть http://127.0.0.1:8000  — UI
#         http://127.0.0.1:8000/docs — Swagger
```

## Команды

```bash
make run         # запуск без reload (PORTAL_HOST / PORTAL_PORT)
make dev         # запуск с авто-перезагрузкой
make test        # pytest
make lint        # ruff check
make format      # ruff format
make typecheck   # mypy app
make check       # lint + typecheck + test
```

## Конфигурация

Все переменные читаются с префиксом `PORTAL_` (см. `app/config.py` и `.env.example`):

| Переменная              | По умолчанию          | Назначение                       |
|-------------------------|-----------------------|----------------------------------|
| `PORTAL_APP_NAME`       | `FastAPI Jinja Portal`| заголовок страниц и OpenAPI      |
| `PORTAL_DEBUG`          | `false`               | режим отладки FastAPI            |
| `PORTAL_DATABASE_PATH`  | `data/app.sqlite3`    | путь к файлу SQLite              |
| `PORTAL_HOST`           | `127.0.0.1`           | хост для `make run`              |
| `PORTAL_PORT`           | `8000`                | порт для `make run`              |

## Как использовать как шаблон

1. Создайте репозиторий «из шаблона» (или склонируйте и смените `origin`).
2. Поправьте `name`/`description` в `pyproject.toml`.
3. Замените демонстрационную сущность `Item` на свою предметную модель:
   - `app/schemas.py` — контракты;
   - `app/db/database.py` — схема таблиц;
   - `app/repositories/` — доступ к данным;
   - `app/services/` — бизнес-логика;
   - `app/routers/` — endpoints и страницы.
4. Удалите примеры в `templates/index.html` и тестах, оставив свою функциональность.
5. `make check` — убедитесь, что всё зелёное.

## Куда расти

- **Внешние API / клиенты** — кладите async-клиенты (httpx) отдельным пакетом и
  инициализируйте в `lifespan` (`app/main.py`).
- **Миграции** — простых `CREATE TABLE IF NOT EXISTS` хватает на старте; при росте
  замените на Alembic или другой мигратор.
- **Другая СУБД / ORM** — `repositories/` спроектированы как точка замены: меняете
  реализацию репозитория, слои выше не трогаете.
