import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.repositories.trace import TraceRepository
from app.trace import (
    Event,
    Failure,
    Run,
    StageStat,
    advice,
    build_runs,
    failures,
    stage_stats,
)

MAX_BATCH = 300
# Только эти поля данных сохраняются: слов семьи среди них нет.
ALLOWED = frozenset(
    {
        "reason", "understood", "intent", "peakDbfs", "thresholdDbfs", "utterances", "speaker",
        "firstAudioMs", "audioMs", "synthMs", "loadMs", "durationMs", "chars", "engine",
        "levelDbfs",
        "end", "status", "code", "bytes", "total_ms", "transcribe_ms", "records_ms", "scores_ms",
        "wav_bytes", "kind", "floorDbfs", "speechMs", "recordedMs", "speechStartMs", "speechEndMs",
    }
)  # fmt: skip


@dataclass
class Report:
    days: int
    runs: list[Run]
    stats: list[StageStat]
    failures: list[Failure]
    advice: list[str]
    counts: dict[str, int]


class TraceService:
    def __init__(
        self,
        repository: TraceRepository | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repository = repository or TraceRepository()
        self._clock = clock

    def _now_ms(self) -> int:
        return round(self._clock() * 1000)

    def add(self, device: str, events: list[dict[str, Any]]) -> int:
        """Сохраняет пачку событий телефона без лишних полей данных; возвращает принятое."""
        clean: list[Event] = []
        for e in events[:MAX_BATCH]:
            run, stage = str(e.get("run", "")), str(e.get("stage", ""))
            if not run or not stage or e.get("src") not in ("audio", "app"):
                continue
            data = {
                k: (v if isinstance(v, int | float) else str(v)[:60])
                for k, v in (e.get("data") or {}).items()
                if k in ALLOWED
            }
            clean.append(
                {
                    "run": run,
                    "ts": int(e.get("ts", 0)),
                    "src": e["src"],
                    "stage": stage,
                    "data": data,
                }
            )
        if clean:
            self._repository.add(clean, device, self._now_ms())
        return len(clean)

    def hub_event(self, run: str, stage: str, data: dict[str, Any]) -> None:
        """Событие самого хаба: длительности внутри запроса, привязанные к прогону телефона."""
        if not run:
            return
        event: Event = {
            "run": run[:40],
            "ts": self._now_ms(),
            "src": "hub",
            "stage": stage,
            "data": data,
        }
        self._repository.add([event], "hub", self._now_ms())

    def events(self, days: int) -> list[Event]:
        return self._repository.since(self._now_ms() - days * 86_400_000)

    def report(self, days: int) -> Report:
        # Часы телефона и хаба считаются согласованными: «идёт» или «оборван» решает возраст.
        runs = build_runs(self.events(days), self._now_ms())
        stats, fails = stage_stats(runs), failures(runs)
        counts = {
            k: sum(1 for r in runs if r.outcome == k)
            for k in ("ok", "failed", "unfinished", "running")
        }
        return Report(days, runs, stats, fails, advice(stats, fails), counts)

    def clear(self) -> None:
        self._repository.clear()
