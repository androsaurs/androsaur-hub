from app.db.database import get_connection
from app.schemas import Item


class ItemRepository:
    """Доступ к данным сущности Item.

    Единственное место, где живёт SQL по таблице ``items``. Слои выше
    (сервисы, роутеры) работают только с объектами :class:`Item`.
    """

    def list(self) -> list[Item]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, title, created_at FROM items ORDER BY id DESC"
            ).fetchall()
        return [Item(**dict(row)) for row in rows]

    def add(self, title: str) -> Item:
        with get_connection() as conn:
            cursor = conn.execute("INSERT INTO items (title) VALUES (?)", (title,))
            row = conn.execute(
                "SELECT id, title, created_at FROM items WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        return Item(**dict(row))
