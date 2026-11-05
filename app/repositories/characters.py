from app.db.database import get_connection


class CharacterRepository:
    def accessories(self) -> dict[str, dict[str, str]]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT a.person_id, a.character_id, a.accessory FROM character_accessories a "
                "JOIN house_people p ON p.id = a.person_id WHERE p.status = 'active'"
            ).fetchall()
        result: dict[str, dict[str, str]] = {}
        for row in rows:
            result.setdefault(row["person_id"], {})[row["character_id"]] = row["accessory"]
        return result

    def set_accessory(self, person: str, character: str, accessory: str) -> str | None:
        # Match the current personal character atomically. A delayed offline edit must not
        # accidentally dress a different character selected from another phone.
        with get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO character_accessories(person_id, character_id, accessory) "
                "SELECT p.id, c.character_id, ? FROM house_people p "
                "JOIN person_characters c ON c.person_id = p.id "
                "WHERE p.id = ? AND p.status = 'active' AND c.character_id = ? "
                "ON CONFLICT(person_id, character_id) DO UPDATE SET accessory = excluded.accessory",
                (accessory, person, character),
            )
            if cursor.rowcount == 1:
                return None
            active = conn.execute(
                "SELECT 1 FROM house_people WHERE id = ? AND status = 'active'", (person,)
            ).fetchone()
            return "character_changed" if active else "person_unknown"

    def assignments(self) -> dict[str, str]:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT c.person_id, c.character_id FROM person_characters c "
                "JOIN house_people p ON p.id = c.person_id WHERE p.status = 'active'"
            ).fetchall()
        return {r["person_id"]: r["character_id"] for r in rows}

    def select(self, person: str, character: str) -> bool:
        with get_connection() as conn:
            if not conn.execute(
                "SELECT 1 FROM house_people WHERE id = ? AND status = 'active'", (person,)
            ).fetchone():
                return False
            conn.execute(
                "INSERT INTO person_characters(person_id, character_id) VALUES (?, ?) "
                "ON CONFLICT(person_id) DO UPDATE SET character_id = excluded.character_id",
                (person, character),
            )
        return True
