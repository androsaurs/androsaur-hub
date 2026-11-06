"""Сервер памяти (mcp-memory-service, REST): записи «где что лежит» в отдельном сторе.

Ключ — учётные данные: не печатать и не логировать. Тексты записей — слова семьи: не в логах.
Стор — раздел общей базы сервера: поиск в одном сторе не видит записи другого.
"""

from typing import Any

import httpx

from app.things_text import Record, item_and_place

PAGE = 100
MAX_PAGES = 20


class MemoryServiceError(Exception):
    """Сервер памяти не ответил как нужно; текст показывается в админке."""


class MemoryServiceClient:
    def __init__(self, url: str, key: str, store: str, timeout: float = 15.0) -> None:
        self._url = url.rstrip("/")
        self._key = key
        self._store = store
        self._timeout = timeout

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            reply = httpx.request(
                method,
                f"{self._url}{path}",
                headers={"X-API-Key": self._key},
                timeout=self._timeout,
                **kwargs,
            )
        except httpx.HTTPError as e:
            raise MemoryServiceError(f"Сервер памяти недоступен: {type(e).__name__}") from None
        if reply.status_code in (401, 403):
            raise MemoryServiceError("Сервер памяти не принял ключ")
        if reply.status_code >= 400:
            raise MemoryServiceError(f"Сервер памяти ответил {reply.status_code}")
        try:
            return reply.json()
        except ValueError:
            raise MemoryServiceError("Сервер памяти прислал не JSON") from None

    @staticmethod
    def _record(raw: dict[str, Any], score: float = 0.0) -> Record:
        content = str(raw.get("content", ""))
        meta = raw.get("metadata") or {}
        item = str(meta.get("item") or "").split() or item_and_place(content)[0]
        return Record(
            content=content,
            content_hash=str(raw.get("content_hash", "")),
            item=item,
            created=float(raw.get("created_at") or 0.0),
            score=score,
        )

    def records(self) -> list[Record]:
        """Все записи стора (их десятки или сотни, поэтому постранично и целиком)."""
        found: list[Record] = []
        for page in range(1, MAX_PAGES + 1):
            data = self._request(
                "GET",
                "/api/memories",
                params={"store": self._store, "page": page, "page_size": PAGE},
            )
            found.extend(self._record(m) for m in data.get("memories", []))
            if not data.get("has_more"):
                break
        return found

    def scores(self, query: str, limit: int = 8) -> dict[str, float]:
        """Смысловое сходство ближайших записей: hash → оценка (0–1)."""
        data = self._request(
            "POST", "/api/search", json={"query": query, "n_results": limit, "store": self._store}
        )
        return {
            str(r["memory"]["content_hash"]): float(r.get("similarity_score") or 0.0)
            for r in data.get("results", [])
        }

    def store(self, content: str, item: str, kind: str, author: str, author_id: str) -> str:
        tags = ["home-item" if kind == "location" else "home-fact"]
        tags += [f"by:{author_id}"] if author_id else []
        data = self._request(
            "POST",
            "/api/memories",
            json={
                "content": content,
                "tags": tags,
                "store": self._store,
                "metadata": {
                    "item": item,
                    "kind": kind,
                    "author": author,
                    "author_id": author_id,
                },
                # Свою проверку «тот же предмет» делает хаб; серверная смысловая сверка путает
                # «в нижнем ящике» и «в верхнем» с дублем.
                "conversation_id": "androsaur-hub",
            },
        )
        if not data.get("success"):
            raise MemoryServiceError("Сервер памяти не сохранил запись")
        return str(data.get("content_hash", ""))

    def delete(self, content_hash: str) -> None:
        self._request("DELETE", f"/api/memories/{content_hash}", params={"store": self._store})

    def check(self) -> str:
        return f"Работает: записей в сторе «{self._store}» — {len(self.records())}"
