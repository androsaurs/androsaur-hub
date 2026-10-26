"""Groq: расшифровка голосовых записок (Whisper, OpenAI-совместимый API).

Ключ — учётные данные: не печатать и не логировать. Звук и текст записок — голоса и слова семьи:
не в логах, только в базе хаба и ``data/notes`` (вне git).
"""

import math
from typing import Any

import httpx

from app.clients.weather import WeatherError

BASE = "https://api.groq.com/openai/v1"
MODEL = "whisper-large-v3-turbo"


class Transcript:
    """Текст записки и насколько ему можно верить, в процентах.

    ``confidence`` — средняя вероятность слов (exp от ``avg_logprob`` Whisper);
    ``speech`` — доля речи (1 − ``no_speech_prob``)."""

    def __init__(self, text: str, confidence: float, speech: float) -> None:
        self.text, self.confidence, self.speech = text, confidence, speech


def transcript_from(data: dict[str, Any]) -> Transcript:
    segments = data.get("segments") or []
    text = " ".join((data.get("text") or "").split())
    if not segments:
        return Transcript(text, 0.0 if not text else 50.0, 0.0 if not text else 50.0)
    # Средние по фрагментам с весом длительности: длинный фрагмент значит больше короткого.
    weights = [max(0.01, float(s.get("end", 0)) - float(s.get("start", 0))) for s in segments]
    total = sum(weights)
    logprob = sum(
        w * float(s.get("avg_logprob", -5)) for w, s in zip(weights, segments, strict=True)
    )
    silence = sum(
        w * float(s.get("no_speech_prob", 1)) for w, s in zip(weights, segments, strict=True)
    )
    return Transcript(
        text,
        round(100 * math.exp(logprob / total), 1),
        round(100 * (1 - silence / total), 1),
    )


class GroqClient:
    def __init__(self, key: str, timeout: float = 60.0) -> None:
        self._key = key
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}"}

    def check(self) -> str:
        """Проверка ключа: список моделей; есть ли среди них нужная."""
        try:
            reply = httpx.get(f"{BASE}/models", headers=self._headers(), timeout=15)
        except httpx.HTTPError as e:
            raise WeatherError(f"Groq не ответил: {type(e).__name__}") from None
        if reply.status_code in (401, 403):
            raise WeatherError(f"ключ не принят ({reply.status_code})")
        if reply.status_code != 200:
            raise WeatherError(f"Groq ответил {reply.status_code}")
        models = {m.get("id") for m in reply.json().get("data") or []}
        if MODEL not in models:
            raise WeatherError(f"нет модели {MODEL}")
        return f"ключ принят, {MODEL} доступна"

    def transcribe(self, wav: bytes) -> Transcript:
        try:
            reply = httpx.post(
                f"{BASE}/audio/transcriptions",
                headers=self._headers(),
                data={
                    "model": MODEL,
                    "language": "ru",
                    "response_format": "verbose_json",
                    "temperature": "0",
                },
                files={"file": ("note.wav", wav, "audio/wav")},
                timeout=self._timeout,
            )
        except httpx.HTTPError as e:
            raise WeatherError(f"Groq не ответил: {type(e).__name__}") from None
        if reply.status_code == 429:
            raise WeatherError("лимит запросов Groq, повторю позже")
        if reply.status_code in (401, 403):
            raise WeatherError(f"ключ не принят ({reply.status_code})")
        if reply.status_code != 200:
            raise WeatherError(f"Groq ответил {reply.status_code}")
        return transcript_from(reply.json())
