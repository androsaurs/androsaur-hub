"""Home Assistant: список колонок Яндекса и озвучивание текста на них (REST API).

Токен — учётные данные: не печатать и не логировать. Тексты сообщений — слова семьи: не в логах.
Колонки подключены интеграцией YandexStation: ``media_player.play_media`` с типом ``text``
произносит текст голосом Алисы.
"""

from typing import Any

import httpx

SPEAKER_PREFIX = "media_player.yandex_station"


class HomeError(Exception):
    """Home Assistant не ответил как нужно; текст показывается в админке."""


class Speaker:
    def __init__(self, entity_id: str, name: str) -> None:
        self.entity_id, self.name = entity_id, name


class HomeAssistantClient:
    def __init__(self, url: str, token: str, timeout: float = 10.0) -> None:
        self._url = url.rstrip("/")
        self._token = token
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            reply = httpx.request(
                method,
                f"{self._url}{path}",
                headers=self._headers(),
                timeout=self._timeout,
                **kwargs,
            )
        except httpx.HTTPError as e:
            raise HomeError(f"Home Assistant недоступен: {type(e).__name__}") from None
        if reply.status_code == 401:
            raise HomeError("Home Assistant не принял токен")
        if reply.status_code >= 400:
            raise HomeError(f"Home Assistant ответил {reply.status_code}")
        return reply

    def speakers(self) -> list[Speaker]:
        """Колонки Яндекса, которые видит Home Assistant."""
        reply = self._request("GET", "/api/states")
        try:
            states = reply.json()
        except ValueError:
            raise HomeError("Home Assistant прислал не список состояний") from None
        found = []
        for state in states:
            entity_id = str(state.get("entity_id", ""))
            if entity_id.startswith(SPEAKER_PREFIX):
                name = (state.get("attributes") or {}).get("friendly_name") or entity_id
                found.append(Speaker(entity_id, str(name)))
        return sorted(found, key=lambda s: s.name)

    def check(self) -> str:
        speakers = self.speakers()
        return f"Работает: колонок Яндекса — {len(speakers)}"

    def speak(self, entity_id: str, text: str) -> None:
        self._request(
            "POST",
            "/api/services/media_player/play_media",
            json={
                "entity_id": entity_id,
                "media_content_id": text,
                "media_content_type": "text",
            },
        )
