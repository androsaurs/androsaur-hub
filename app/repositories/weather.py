from app.db.database import get_connection
from app.schemas import WeatherPlace, WeatherReading


class WeatherRepository:
    """Место погоды дома и последний прогноз (одна строка)."""

    def get(self) -> tuple[WeatherPlace, WeatherReading | None, float] | None:
        """Место, последняя погода и момент, раньше которого не спрашивать снова.

        None — место не задано."""
        with get_connection() as conn:
            row = conn.execute("SELECT * FROM weather WHERE id = 1").fetchone()
        if row is None:
            return None
        place = WeatherPlace(
            name=row["place"], latitude=row["latitude"], longitude=row["longitude"]
        )
        raw = row["reading"]
        reading = None if raw is None else WeatherReading.model_validate_json(raw)
        return place, reading, row["expires_at"] or 0.0

    def set_place(self, place: WeatherPlace) -> None:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO weather (id, place, latitude, longitude) VALUES (1, ?, ?, ?)
                ON CONFLICT (id) DO UPDATE SET place = excluded.place,
                    latitude = excluded.latitude, longitude = excluded.longitude,
                    reading = NULL, fetched_at = NULL, expires_at = NULL
                """,
                (place.name, place.latitude, place.longitude),
            )

    def set_reading(self, reading: WeatherReading, fetched_at: float, expires_at: float) -> None:
        with get_connection() as conn:
            conn.execute(
                "UPDATE weather SET reading = ?, fetched_at = ?, expires_at = ? WHERE id = 1",
                (reading.model_dump_json(), fetched_at, expires_at),
            )

    def clear(self) -> None:
        with get_connection() as conn:
            conn.execute("DELETE FROM weather WHERE id = 1")
