import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.config import get_settings

# Схема БД. Для одного небольшого портала простых миграций «CREATE TABLE IF NOT
# EXISTS» достаточно; при росте проекта замените на полноценный мигратор.
_SCHEMA = """
-- Телефоны: подключается главное приложение androsaur, по одному на устройство.
CREATE TABLE IF NOT EXISTS devices (
    id            TEXT    PRIMARY KEY,
    name          TEXT    NOT NULL,
    model         TEXT    NOT NULL DEFAULT '',
    app_version   TEXT    NOT NULL DEFAULT '',
    registered_at REAL    NOT NULL,
    last_seen_at  REAL,
    applied_rev   INTEGER NOT NULL DEFAULT 0,
    -- 1, когда хаб разобрал людей, с которыми телефон пришёл: новых с него дальше берёт сразу.
    joined        INTEGER NOT NULL DEFAULT 0,
    -- Модель распознавания лиц камеры телефона; наборы чисел другой модели несовместимы.
    face_model    TEXT    NOT NULL DEFAULT '',
    -- Какую версию людей дома телефон уже загрузил.
    people_rev    INTEGER NOT NULL DEFAULT 0
);

-- Описание настроек присылают сами устройства (схема модуля).
CREATE TABLE IF NOT EXISTS setting_fields (
    module        TEXT NOT NULL,
    key           TEXT NOT NULL,
    type          TEXT NOT NULL,
    title         TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    unit          TEXT NOT NULL DEFAULT '',
    min_value     REAL,
    max_value     REAL,
    default_value TEXT NOT NULL,
    PRIMARY KEY (module, key)
);

-- Значения уровня дома (для всех устройств). Нет строки — действует умолчание модуля.
CREATE TABLE IF NOT EXISTS setting_values (
    module     TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (module, key)
);

-- Номер ревизии настроек: растёт при каждом изменении, устройство подтверждает применённую.
CREATE TABLE IF NOT EXISTS revision (
    id  INTEGER PRIMARY KEY CHECK (id = 1),
    rev INTEGER NOT NULL
);
INSERT OR IGNORE INTO revision (id, rev) VALUES (1, 1);

-- Кого знает камера телефона: только имена (векторы лиц сюда не приходят).
-- Телефон присылает список целиком.
-- person_id — постоянный ID человека в камере: имя меняется, ID нет.
-- digest — отпечаток наборов на телефоне, которые хаб уже получил (NULL — ещё не получал).
CREATE TABLE IF NOT EXISTS device_people (
    device_id TEXT    NOT NULL REFERENCES devices (id),
    person_id TEXT    NOT NULL,
    name      TEXT    NOT NULL,
    samples   INTEGER NOT NULL DEFAULT 0,
    digest    TEXT,
    PRIMARY KEY (device_id, person_id)
);

-- Люди дома: их получает каждый телефон. status: active — человек дома; pending — пришёл с
-- телефона из другой системы и ждёт решения в админке (объединить или добавить).
CREATE TABLE IF NOT EXISTS house_people (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    face_model    TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('active', 'pending')),
    origin_device TEXT NOT NULL,
    created_at    REAL NOT NULL,
    -- Роли: как ещё называют человека («папа», «мама») — по ним тоже оставляют записки.
    roles         TEXT NOT NULL DEFAULT '[]'
);

-- Наборы чисел лица (биометрия семьи): только в этой базе, не в логах и не в админке.
CREATE TABLE IF NOT EXISTS house_vectors (
    person_id     TEXT NOT NULL REFERENCES house_people (id),
    vector        TEXT NOT NULL,
    source_device TEXT NOT NULL,
    added_at      REAL NOT NULL,
    PRIMARY KEY (person_id, vector)
);

-- Версия людей дома: растёт, когда меняется что-то, что телефонам нужно загрузить.
CREATE TABLE IF NOT EXISTS people_revision (
    id  INTEGER PRIMARY KEY CHECK (id = 1),
    rev INTEGER NOT NULL
);
INSERT OR IGNORE INTO people_revision (id, rev) VALUES (1, 1);

-- Интеграции с внешними сервисами (карточки раздела «Интеграции»). secret — ключ доступа: только
-- здесь, не в логах и не в админке (там последние 4 символа).
CREATE TABLE IF NOT EXISTS integrations (
    id            TEXT PRIMARY KEY,
    enabled       INTEGER NOT NULL DEFAULT 0,
    secret        TEXT,
    config        TEXT NOT NULL DEFAULT '{}',
    checked_at    REAL,
    check_ok      INTEGER,
    check_message TEXT
);

-- Голосовые записки, которые телефон отдал для расшифровки (Groq). Звук — файл в notes_dir
-- (пока его хранят: отладка), текст и уверенность Whisper — здесь. status: pending, done, failed.
CREATE TABLE IF NOT EXISTS hub_notes (
    id             TEXT PRIMARY KEY,
    device_id      TEXT NOT NULL,
    to_label       TEXT NOT NULL DEFAULT '',
    from_label     TEXT NOT NULL DEFAULT '',
    created_ms     INTEGER NOT NULL,
    duration_ms    INTEGER NOT NULL DEFAULT 0,
    received_at    REAL NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',
    text           TEXT,
    confidence     REAL,
    speech         REAL,
    usable         INTEGER,
    error          TEXT,
    transcribed_at REAL,
    has_audio      INTEGER NOT NULL DEFAULT 1
);

-- Погода дома: место (задаётся в админке) и последний прогноз.
-- Одна строка; нет строки — место не задано.
CREATE TABLE IF NOT EXISTS weather (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    place      TEXT NOT NULL,
    latitude   REAL NOT NULL,
    longitude  REAL NOT NULL,
    reading    TEXT,
    fetched_at REAL,
    expires_at REAL,
    hours      TEXT
);

-- Команды телефону (например, переименовать человека). Телефон получает их при опросе
-- и сообщает результат; невыполненные отдаются снова.
CREATE TABLE IF NOT EXISTS commands (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id  TEXT    NOT NULL REFERENCES devices (id),
    kind       TEXT    NOT NULL,
    args       TEXT    NOT NULL,
    created_at REAL    NOT NULL,
    done_at    REAL,
    ok         INTEGER,
    error      TEXT
);
"""


_DEVICE_COLUMNS = {
    "joined": "joined INTEGER NOT NULL DEFAULT 0",
    "face_model": "face_model TEXT NOT NULL DEFAULT ''",
    "people_rev": "people_rev INTEGER NOT NULL DEFAULT 0",
}


def _open() -> sqlite3.Connection:
    """Открыть соединение по пути из настроек.

    Поддерживает два режима (выбираются значением ``HUB_DATABASE_PATH``):
    - обычный файл — для прод/локального запуска (durability сохраняется);
    - in-memory (``:memory:`` или URI ``file:...?mode=memory&cache=shared``) —
      для тестов: без диска и без fsync, отдельные соединения видят одну БД,
      пока живо хотя бы одно из них (anchor держит фикстура).
    """
    raw = str(get_settings().database_path)
    if raw == ":memory:" or raw.startswith("file:"):
        dsn = "file:hub?mode=memory&cache=shared" if raw == ":memory:" else raw
        conn = sqlite3.connect(dsn, uri=True)
    else:
        Path(raw).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(raw)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    """Открыть соединение с БД. Коммитит при успешном выходе, всегда закрывает.

    Путь к БД читается из настроек на каждый вызов — это позволяет тестам
    подменять базу через переменную окружения.
    """
    conn = _open()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Создать таблицы, если их ещё нет. Вызывается на старте приложения."""
    with get_connection() as conn:
        # Список людей — копия того, что знает телефон, и приходит заново при каждом опросе. Таблицу
        # без ID людей (до их появления) можно просто пересоздать.
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(device_people)")]
        if columns and ("person_id" not in columns or "digest" not in columns):
            conn.execute("DROP TABLE device_people")
        # Колонки, появившиеся позже, добавляются в уже созданную базу.
        house = {row["name"] for row in conn.execute("PRAGMA table_info(house_people)")}
        if house and "roles" not in house:
            conn.execute("ALTER TABLE house_people ADD COLUMN roles TEXT NOT NULL DEFAULT '[]'")
        weather = {row["name"] for row in conn.execute("PRAGMA table_info(weather)")}
        if weather and "hours" not in weather:
            conn.execute("ALTER TABLE weather ADD COLUMN hours TEXT")
        have = {row["name"] for row in conn.execute("PRAGMA table_info(devices)")}
        for column, ddl in _DEVICE_COLUMNS.items():
            if have and column not in have:
                conn.execute(f"ALTER TABLE devices ADD COLUMN {ddl}")
        conn.executescript(_SCHEMA)
