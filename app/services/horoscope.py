import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.clients.europaplus import EuropaPlusProvider
from app.config import get_settings
from app.horoscope import SIGNS, HoroscopeError, HoroscopeReply, HoroscopeSource
from app.profiles import effective_zodiac_sign
from app.repositories.horoscope import HoroscopeRepository

PROVIDERS: dict[str, HoroscopeSource] = {
    "europaplus": HoroscopeSource("Europa Plus", EuropaPlusProvider, "радио Европа Плюс"),
}
_FETCH_LOCK = threading.Lock()


class HoroscopeService:
    def __init__(
        self,
        repository: HoroscopeRepository | None = None,
        providers: dict[str, HoroscopeSource] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repository = repository or HoroscopeRepository()
        self._providers = PROVIDERS if providers is None else providers
        self._clock = clock

    def options(self) -> dict[str, str]:
        return {key: entry.name for key, entry in self._providers.items()}

    def provider(self) -> str:
        return self._repository.provider()

    def set_provider(self, provider: str) -> None:
        if provider and provider not in self._providers:
            raise HoroscopeError("Такого источника гороскопа нет")
        self._repository.set_provider(provider)

    def set_sign(self, person: str, sign: str) -> None:
        if sign and sign not in SIGNS:
            raise HoroscopeError("Выберите знак зодиака из списка")
        if not self._repository.set_sign(person, sign):
            raise HoroscopeError("Выберите человека дома")

    def today(self, person: str, for_greeting: bool = False) -> HoroscopeReply:
        zone_name = get_settings().horoscope_timezone
        zone = ZoneInfo(zone_name)
        now = self._clock()
        local = datetime.fromtimestamp(now, zone)
        day = local.date()
        midnight = datetime.combine(day + timedelta(days=1), datetime.min.time(), zone)
        reply = HoroscopeReply(
            status="unknown_person",
            person=person,
            date=day,
            timezone=zone_name,
            valid_until_ms=int(midnight.timestamp() * 1000),
        )
        profile = self._repository.person(person)
        if profile is None or profile[0] != "active":
            return reply
        reply = reply.model_copy(update={"greeting_enabled": profile[3]})
        if for_greeting and not profile[3]:
            return reply.model_copy(update={"status": "greeting_disabled"})
        sign = effective_zodiac_sign(profile[1], profile[2])
        if sign not in SIGNS:
            return reply.model_copy(update={"status": "sign_missing"})
        reply = reply.model_copy(update={"sign": sign, "sign_name": SIGNS[sign]})
        provider = self.provider()
        if not provider:
            return reply.model_copy(update={"status": "disabled"})
        if provider not in self._providers:
            return reply.model_copy(update={"status": "unavailable"})
        source = self._providers[provider]
        reply = reply.model_copy(
            update={
                "source": provider,
                "source_name": source.name,
                "source_speech_name": source.speech_name or source.name,
            }
        )
        # One upstream request for all phones/signs, including concurrent voice requests.
        with _FETCH_LOCK:
            cached, fetched, retry = self._repository.cached(provider, day.isoformat())
            if now >= retry:
                try:
                    fresh = source.factory().today(day)
                    if fresh.date != day or set(fresh.texts) != set(SIGNS):
                        raise HoroscopeError("Источник вернул другой день или не все знаки")
                    if any(not text.strip() or len(text) > 3000 for text in fresh.texts.values()):
                        raise HoroscopeError("Источник вернул пустой или слишком длинный прогноз")
                    self._repository.save(provider, fresh, now)
                    cached, fetched = fresh, now
                except HoroscopeError:
                    self._repository.failed(provider, day.isoformat(), now)
        if cached is None:
            return reply.model_copy(update={"status": "unavailable"})
        return reply.model_copy(
            update={
                "status": "ok",
                "text": cached.texts[sign],
                "fetched_at_ms": int(fetched * 1000),
                "source_date_verified": cached.source_date_verified,
            }
        )
