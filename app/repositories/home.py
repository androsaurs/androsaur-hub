import json
import uuid

from app.db.database import get_connection

HOME_ASSISTANT = "home_assistant"


class RoomRow:
    def __init__(self, id: str, title: str, forms: list[str], entity_id: str) -> None:
        self.id, self.title, self.forms, self.entity_id = id, title, forms, entity_id


class HomeRepository:
    """Адрес Home Assistant и комнаты с колонками. Токен хранится в ``integrations``."""

    def url(self) -> str | None:
        with get_connection() as conn:
            row = conn.execute("SELECT url FROM home_assistant_settings WHERE id = 1").fetchone()
        return None if row is None else str(row["url"])

    def set_url(self, url: str) -> None:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO home_assistant_settings (id, url) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET url = excluded.url",
                (url,),
            )

    def rooms(self) -> list[RoomRow]:
        with get_connection() as conn:
            rows = conn.execute("SELECT * FROM speaker_rooms ORDER BY title").fetchall()
        return [RoomRow(r["id"], r["title"], json.loads(r["forms"]), r["entity_id"]) for r in rows]

    def room(self, room_id: str) -> RoomRow | None:
        return next((r for r in self.rooms() if r.id == room_id), None)

    def add_room(self, title: str, forms: list[str], entity_id: str) -> str:
        room_id = uuid.uuid4().hex[:12]
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO speaker_rooms (id, title, forms, entity_id) VALUES (?, ?, ?, ?)",
                (room_id, title, json.dumps(forms, ensure_ascii=False), entity_id),
            )
        return room_id

    def update_room(self, room_id: str, title: str, forms: list[str], entity_id: str) -> bool:
        with get_connection() as conn:
            cursor = conn.execute(
                "UPDATE speaker_rooms SET title = ?, forms = ?, entity_id = ? WHERE id = ?",
                (title, json.dumps(forms, ensure_ascii=False), entity_id, room_id),
            )
        return cursor.rowcount > 0

    def delete_room(self, room_id: str) -> None:
        with get_connection() as conn:
            conn.execute("DELETE FROM speaker_rooms WHERE id = ?", (room_id,))
