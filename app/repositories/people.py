import json
import sqlite3

from app.db.database import get_connection
from app.schemas import (
    MAX_VECTORS,
    Command,
    CommandResult,
    CommandView,
    PersonReport,
    PersonVectors,
    PersonView,
    RoleSet,
)


class HousePerson:
    """Человек дома, как он лежит в базе (без наборов)."""

    def __init__(self, row: sqlite3.Row) -> None:
        self.id: str = row["id"]
        self.name: str = row["name"]
        self.face_model: str = row["face_model"]
        self.status: str = row["status"]
        self.origin_device: str = row["origin_device"]


def _vector_key(vector: list[float]) -> str:
    # Наборы сравниваются с округлением: один и тот же набор, прошедший через JSON и float32
    # на телефоне, не должен считаться новым.
    return json.dumps([round(x, 5) for x in vector])


_INSERT_VECTOR = (
    "INSERT OR IGNORE INTO house_vectors (person_id, vector, source_device, added_at) "
    "VALUES (?, ?, ?, ?)"
)


class PeopleRepository:
    """Люди дома и их наборы, что знает каждый телефон, и команды телефонам."""

    # --- что знает телефон ---

    def device_people(self, device_id: str) -> dict[str, str | None]:
        """ID людей телефона → отпечаток наборов, которые хаб уже получил от него."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT person_id, digest FROM device_people WHERE device_id = ?", (device_id,)
            ).fetchall()
        return {row["person_id"]: row["digest"] for row in rows}

    def replace_device_people(self, device_id: str, people: list[PersonReport]) -> None:
        """Телефон прислал полный список: лишних убрать, имена и число наборов обновить,
        отпечаток уже полученных наборов оставить."""
        ids = [p.id for p in people]
        with get_connection() as conn:
            if ids:
                marks = ",".join("?" * len(ids))
                conn.execute(
                    f"DELETE FROM device_people WHERE device_id = ? AND person_id NOT IN ({marks})",
                    (device_id, *ids),
                )
            else:
                conn.execute("DELETE FROM device_people WHERE device_id = ?", (device_id,))
            conn.executemany(
                """
                INSERT INTO device_people (device_id, person_id, name, samples) VALUES (?, ?, ?, ?)
                ON CONFLICT (device_id, person_id) DO UPDATE SET
                    name = excluded.name, samples = excluded.samples
                """,
                [(device_id, p.id, p.name, p.samples) for p in people],
            )

    def uploaded(self, device_id: str, person_id: str, digest: str | None) -> None:
        with get_connection() as conn:
            conn.execute(
                "UPDATE device_people SET digest = ? WHERE device_id = ? AND person_id = ?",
                (digest, device_id, person_id),
            )

    def devices_knowing(self, person_id: str) -> list[str]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT device_id FROM device_people WHERE person_id = ?", (person_id,)
            ).fetchall()
        return [row["device_id"] for row in rows]

    def rename_on(self, device_id: str, person_id: str, to: str) -> None:
        with get_connection() as conn:
            conn.execute(
                "UPDATE device_people SET name = ? WHERE device_id = ? AND person_id = ?",
                (to, device_id, person_id),
            )

    # --- люди дома ---

    def house(self) -> dict[str, HousePerson]:
        with get_connection() as conn:
            rows = conn.execute("SELECT * FROM house_people").fetchall()
        return {row["id"]: HousePerson(row) for row in rows}

    def create(
        self, person: PersonReport, face_model: str, status: str, device: str, now: float
    ) -> None:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO house_people
                    (id, name, face_model, status, origin_device, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (person.id, person.name, face_model, status, device, now),
            )

    def set_name(self, person_id: str, name: str) -> None:
        with get_connection() as conn:
            conn.execute("UPDATE house_people SET name = ? WHERE id = ?", (name, person_id))

    def activate(self, person_id: str) -> None:
        with get_connection() as conn:
            conn.execute("UPDATE house_people SET status = 'active' WHERE id = ?", (person_id,))

    def add_vectors(
        self, person_id: str, vectors: list[list[float]], device: str, now: float
    ) -> int:
        """Добавить наборы, которых у человека ещё нет, пока их меньше предела.
        Возвращает, сколько добавлено."""
        with get_connection() as conn:
            have = conn.execute(
                "SELECT COUNT(*) AS n FROM house_vectors WHERE person_id = ?", (person_id,)
            ).fetchone()["n"]
            added = 0
            for vector in vectors:
                if have + added >= MAX_VECTORS:
                    break
                cursor = conn.execute(_INSERT_VECTOR, (person_id, _vector_key(vector), device, now))
                added += cursor.rowcount
        return added

    def move(self, from_id: str, into_id: str, now: float) -> None:
        """Наборы человека ``from_id`` переходят к ``into_id`` (до предела), сам он удаляется."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT vector, source_device FROM house_vectors WHERE person_id = ? "
                "ORDER BY added_at, rowid",
                (from_id,),
            ).fetchall()
            have = conn.execute(
                "SELECT COUNT(*) AS n FROM house_vectors WHERE person_id = ?", (into_id,)
            ).fetchone()["n"]
            for row in rows:
                if have >= MAX_VECTORS:
                    break
                cursor = conn.execute(
                    _INSERT_VECTOR, (into_id, row["vector"], row["source_device"], now)
                )
                have += cursor.rowcount
            conn.execute("DELETE FROM house_vectors WHERE person_id = ?", (from_id,))
            conn.execute("DELETE FROM house_people WHERE id = ?", (from_id,))

    def with_vectors(self, face_model: str) -> list[PersonVectors]:
        """Люди дома этой модели, у которых уже есть наборы, — то, что получает телефон."""
        with get_connection() as conn:
            people = conn.execute(
                "SELECT id, name FROM house_people WHERE status = 'active' AND face_model = ? "
                "ORDER BY created_at, id",
                (face_model,),
            ).fetchall()
            result = []
            for p in people:
                rows = conn.execute(
                    "SELECT vector FROM house_vectors WHERE person_id = ? ORDER BY added_at, rowid",
                    (p["id"],),
                ).fetchall()
                if rows:
                    result.append(
                        PersonVectors(
                            id=p["id"],
                            name=p["name"],
                            vectors=[json.loads(r["vector"]) for r in rows],
                        )
                    )
        return result

    def views(self) -> list[tuple[str, PersonView]]:
        """Люди дома и новые с телефонов для админки (статус и вид): имена и числа, без наборов."""
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT h.id, h.name, h.status, h.roles,
                       (SELECT COUNT(*) FROM house_vectors v WHERE v.person_id = h.id) AS samples,
                       COALESCE(o.name, '') AS origin
                FROM house_people h LEFT JOIN devices o ON o.id = h.origin_device
                ORDER BY h.name, h.id
                """
            ).fetchall()
            knows = conn.execute(
                """
                SELECT p.person_id, d.name FROM device_people p
                JOIN devices d ON d.id = p.device_id ORDER BY d.name
                """
            ).fetchall()
        devices: dict[str, list[str]] = {}
        for row in knows:
            devices.setdefault(row["person_id"], []).append(row["name"])
        return [
            (
                row["status"],
                PersonView(
                    id=row["id"],
                    name=row["name"],
                    samples=row["samples"],
                    devices=devices.get(row["id"], []),
                    origin=row["origin"],
                    roles=json.loads(row["roles"] or "[]"),
                ),
            )
            for row in rows
        ]

    def set_roles(self, person_id: str, roles: list[str]) -> None:
        with get_connection() as conn:
            conn.execute(
                "UPDATE house_people SET roles = ? WHERE id = ?",
                (json.dumps(roles, ensure_ascii=False), person_id),
            )

    def roles(self) -> list[RoleSet]:
        """Роли людей дома (у кого они есть)."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, name, roles FROM house_people WHERE status = 'active' AND roles != '[]'"
            ).fetchall()
        return [RoleSet(person=r["id"], name=r["name"], words=json.loads(r["roles"])) for r in rows]

    def bump(self) -> int:
        with get_connection() as conn:
            conn.execute("UPDATE people_revision SET rev = rev + 1 WHERE id = 1")
            row = conn.execute("SELECT rev FROM people_revision WHERE id = 1").fetchone()
        return int(row["rev"])

    def revision(self) -> int:
        with get_connection() as conn:
            row = conn.execute("SELECT rev FROM people_revision WHERE id = 1").fetchone()
        return int(row["rev"])

    # --- команды ---

    def add_command(self, device_id: str, kind: str, args: dict[str, str], now: float) -> None:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO commands (device_id, kind, args, created_at) VALUES (?, ?, ?, ?)",
                (device_id, kind, json.dumps(args, ensure_ascii=False), now),
            )

    def open_rename(self, person_id: str) -> bool:
        """Ждёт ли этот человек переименования (тогда старое имя с телефона — не новость:
        телефон просто ещё не успел)."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT args FROM commands WHERE done_at IS NULL AND kind = 'rename_person'"
            ).fetchall()
        return any(json.loads(row["args"]).get("person") == person_id for row in rows)

    def pending(self, device_id: str) -> list[Command]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, kind, args FROM commands WHERE device_id = ? AND done_at IS NULL "
                "ORDER BY id",
                (device_id,),
            ).fetchall()
        commands = []
        for row in rows:
            args = json.loads(row["args"])
            # Команда, поставленная до ID людей, телефону уже непонятна: её не отдаём.
            if "person" in args:
                commands.append(Command(id=row["id"], kind=row["kind"], **args))
        return commands

    def finish(self, device_id: str, result: CommandResult, now: float) -> Command | None:
        """Записать результат. Возвращает команду, если она была этого телефона и ещё не закрыта."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT id, kind, args FROM commands "
                "WHERE id = ? AND device_id = ? AND done_at IS NULL",
                (result.id, device_id),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE commands SET done_at = ?, ok = ?, error = ? WHERE id = ?",
                (now, int(result.ok), result.error, result.id),
            )
        args = json.loads(row["args"])
        return Command(id=row["id"], kind=row["kind"], **args) if "person" in args else None

    def recent(self, limit: int = 20) -> list[CommandView]:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT c.id, d.name AS device_name, c.kind, c.args, c.done_at, c.ok, c.error
                FROM commands c JOIN devices d ON d.id = c.device_id
                ORDER BY c.id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            CommandView(
                id=row["id"],
                device_name=row["device_name"],
                kind=row["kind"],
                done=row["done_at"] is not None,
                ok=None if row["ok"] is None else bool(row["ok"]),
                error=row["error"],
                **json.loads(row["args"]),
            )
            for row in rows
        ]
