"""Профилирование сценариев: события телефона и хаба → прогоны, этапы, сбои, медленные места.

Прогон — всё от слова пробуждения до конца ответа; его номер задаёт телефон. События телефона
(звук и главное приложение) лежат на одной шкале времени телефона, поэтому этапы считаются
только по ним.
Хаб часы телефона не использует: его события несут длительности внутри запроса (расшифровка, поиск).
Слов семьи в событиях нет: только названия этапов, миллисекунды и коды причин.
"""

import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

Event = dict[str, Any]  # run, ts (мс, часы телефона), src (audio|app|hub), stage, data

# Ответы хаба, которые не сбой: человек получил то, что просил (в том числе «не нашёл»).
OK_STATUSES = frozenset(
    {
        "found",
        "saved",
        "updated",
        "forgotten",
        "spoken",
        "not_found",
        "nothing_to_forget",
        "ambiguous",
    }
)
# Команды без голосового ответа: готовы, когда приложение приняло команду («стоп» глушит звук).
SILENT_INTENTS = frozenset({"stop"})
RUNNING_MS = 90_000  # прогон без конца моложе этого считается идущим, старше — оборванным


@dataclass(frozen=True)
class StageSpec:
    key: str
    label: str
    budget_ms: int  # дольше — этап считается медленным
    hint: str  # что можно улучшить, когда этап медленный


STAGES: tuple[StageSpec, ...] = (
    StageSpec(
        "window",
        "Открытие окна команд",
        500,
        "Окно команд открывается слишком долго: смотрите нагрузку телефона.",
    ),
    StageSpec(
        "recognize",
        "Распознавание команды (с вашей фразой)",
        4000,
        "Vosk долго решает, что фраза закончилась: проверьте паузу конца реплики и шум в комнате.",
    ),
    StageSpec(
        "prompt_first_audio",
        "Подсказка: синтез до первого звука",
        800,
        "Фиксированные подсказки («Что вспомнить?», «Говорите.») синтезировать заранее, в кэш.",
    ),
    StageSpec(
        "prompt_speech", "Подсказка: речь", 2500, "Подсказка звучит долго: сократите её текст."
    ),
    StageSpec(
        "record_tail",
        "Запись: тишина после фразы",
        1800,
        "Запись долго ждёт конца фразы: паузу можно укоротить, но фраза с паузой обрежется.",
    ),
    StageSpec(
        "record_wait",
        "Запись: ожидание начала речи",
        2500,
        "Человек долго не начинает после подсказки: она длинная или не слышна, либо голос тихий.",
    ),
    StageSpec(
        "record_speech",
        "Запись: сама речь",
        6000,
        "Долгая фраза человека; это не сбой системы.",
    ),
    StageSpec(
        "record_pause",
        "Запись: пауза до конца",
        1800,
        "Конец записи растягивают звуки комнаты: смотрите шум и порог в событии record_gate.",
    ),
    StageSpec(
        "handoff",
        "Передача команды в приложение",
        800,
        "Команда долго доходит до приложения: частота опроса событий звука.",
    ),
    StageSpec(
        "fetch",
        "Загрузка записи из звукового модуля",
        500,
        "Запись долго забирается у звукового модуля.",
    ),
    StageSpec(
        "hub",
        "Хаб: от отправки до ответа",
        2500,
        "Смотрите разбивку ниже: расшифровка Groq, чтение записей, поиск, сеть до хаба.",
    ),
    StageSpec(
        "hub_network",
        "Сеть до хаба",
        300,
        "Wi-Fi или нагрузка на хаб: запрос идёт дольше, чем работает сам хаб.",
    ),
    StageSpec(
        "hub_transcribe",
        "Хаб: расшифровка (Groq)",
        1500,
        "Groq отвечает долго: проверьте длину записи и сеть хаба наружу.",
    ),
    StageSpec(
        "hub_records",
        "Хаб: чтение записей памяти",
        800,
        "Сервер памяти долго отдаёт список: кэшировать записи на хабе на пару минут.",
    ),
    StageSpec(
        "hub_scores",
        "Хаб: смысловой поиск",
        800,
        "Смысловой поиск медленный: вызывать его только когда слова не совпали.",
    ),
    StageSpec(
        "answer_request",
        "Ответ: запрос на озвучку",
        500,
        "Звуковой модуль долго принимает текст ответа.",
    ),
    StageSpec(
        "answer_first_audio",
        "Ответ: синтез до первого звука",
        1500,
        "Синтез до первого звука долгий: прогрев модели Piper, потоковая выдача по предложениям.",
    ),
    StageSpec("answer_speech", "Ответ: речь", 6000, "Ответ звучит долго: сократите текст."),
    StageSpec(
        "total",
        "Весь путь от пробуждения",
        12000,
        "Сложите этапы выше: самые длинные — первые кандидаты на улучшение.",
    ),
)
_BY_KEY = {s.key: s for s in STAGES}

# Подсказки по причинам сбоев: (этап, причина) → что делать.
FAILURE_HINTS: dict[tuple[str, str], str] = {
    (
        "recognize",
        "silent",
    ): "Команду не расслышали: фраз не было. Проверьте громкость и расстояние до микрофона.",
    (
        "recognize",
        "unrecognized",
    ): "Фразы были, но не команды: смотрите «Что слышит» в звуковом модуле и словарь команд.",
    ("recognize", "rewake"): "Слово пробуждения прозвучало повторно, пока шла команда.",
    ("record", "no_speech"): "Запись без речи: человек не успел начать или тихий микрофон.",
    (
        "record",
        "too_short",
    ): "Слишком короткая запись: фраза обрезана или порог тишины слишком чувствителен.",
    (
        "hub",
        "unclear",
    ): "Groq не разобрал запись: качество звука или уверенность ниже порога в интеграциях.",
    ("hub", "no_hub"): "Хаб не ответил: проверьте, что он запущен и адрес верный.",
    ("hub", "error"): "Хаб вернул ошибку: смотрите его журнал.",
    ("hub", "no_groq"): "Не подключён Groq: добавьте ключ в интеграциях хаба.",
    ("hub", "no_memory"): "Не подключён сервер памяти: страница «Память».",
    ("hub", "no_home"): "Не подключён Home Assistant: страница «Дом».",
    ("say", "http"): "Звуковой модуль не принял ответ для озвучки.",
}


@dataclass
class Run:
    id: str
    start_ms: int
    intent: str = "?"
    outcome: str = "running"  # ok | failed | unfinished | running
    fail_stage: str = ""
    fail_reason: str = ""
    stages: dict[str, int] = field(default_factory=dict)
    slow: list[str] = field(default_factory=list)
    heard: str = ""  # что услышал распознаватель, только у неудачного окна команд

    @property
    def total_ms(self) -> int | None:
        return self.stages.get("total")


def _first(events: list[Event], src: str, stage: str, after: int | None = None) -> Event | None:
    for e in events:
        if e["src"] == src and e["stage"] == stage and (after is None or e["ts"] >= after):
            return e
    return None


def _span(a: Event | None, b: Event | None) -> int | None:
    return None if a is None or b is None else max(0, int(b["ts"]) - int(a["ts"]))


def _int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _failure(phone: list[Event]) -> tuple[str, str] | None:
    """Первый сбой прогона: (этап, код причины)."""
    for e in phone:
        data, src, stage = e.get("data") or {}, e["src"], e["stage"]
        if src == "audio" and stage == "command_timeout":
            silent = str(data.get("utterances", "0")) in ("0", "")
            return "recognize", "silent" if silent else "unrecognized"
        if src == "audio" and stage == "command_rewake":
            return "recognize", "rewake"
        if src == "audio" and stage == "command_failed":
            return "command", str(data.get("reason", "unknown"))[:60]
        if src == "audio" and stage == "note_record_failed":
            return "record", str(data.get("reason", "unknown"))[:60]
        if src == "audio" and stage == "speech_failed":
            return "speech", str(data.get("reason", "unknown"))[:60]
        if src == "app" and stage == "hub_replied":
            status = str(data.get("status", "no_hub"))
            if status not in OK_STATUSES:
                return "hub", "no_hub" if status in ("no reply", "") else status
        if src == "app" and stage == "say_accepted" and str(data.get("code")) != "200":
            return "say", "http"
    return None


def _speech_pairs(phone: list[Event]) -> list[tuple[Event, Event | None]]:
    pairs: list[tuple[Event, Event | None]] = []
    open_start: Event | None = None
    for e in phone:
        if e["src"] != "audio":
            continue
        if e["stage"] == "speech_started":
            open_start = e
        elif e["stage"] in ("speech_finished", "speech_failed") and open_start is not None:
            pairs.append((open_start, e if e["stage"] == "speech_finished" else None))
            open_start = None
    return pairs


def _stages(phone: list[Event], hub: list[Event]) -> dict[str, int]:
    out: dict[str, int] = {}

    def put(key: str, value: int | None) -> None:
        if value is not None:
            out[key] = value

    wake = _first(phone, "audio", "wake_word")
    listening = _first(phone, "audio", "command_listening")
    heard = _first(phone, "audio", "command_heard")
    command = _first(phone, "audio", "command")
    taken = _first(phone, "app", "command_taken")
    fetched = _first(phone, "app", "wav_fetched")
    replied = _first(phone, "app", "hub_replied")
    say = _first(phone, "app", "say_requested")
    rec_start = _first(phone, "audio", "note_recording_started")
    rec_end = _first(phone, "audio", "announcement_recorded") or _first(
        phone, "audio", "note_recorded"
    )

    put("window", _span(wake, listening))
    put("recognize", _span(listening, heard))
    if rec_start and rec_end:
        shot = _int((rec_end.get("data") or {}).get("durationMs"))
        elapsed = _span(rec_start, rec_end)
        if shot is not None and elapsed is not None:
            put("record_tail", max(0, elapsed - shot))
    gate = _first(phone, "audio", "record_gate")
    if gate:
        g = gate.get("data") or {}
        g_start, g_end, g_rec = (
            _int(g.get("speechStartMs")),
            _int(g.get("speechEndMs")),
            _int(g.get("recordedMs")),
        )
        if g_start is not None and g_start >= 0:
            put("record_wait", g_start)
            if g_end is not None and g_end >= g_start:
                put("record_speech", g_end - g_start)
                if g_rec is not None and g_rec >= g_end:
                    put("record_pause", g_rec - g_end)
    put("handoff", _span(command, taken))
    put("fetch", _span(taken, fetched))
    put("hub", _span(fetched or taken, replied))

    pairs = _speech_pairs(phone)
    cutoff = command["ts"] if command else None
    prompts = [p for p in pairs if cutoff is not None and p[0]["ts"] < cutoff]
    answers = [p for p in pairs if say is not None and p[0]["ts"] >= say["ts"]]
    if prompts and prompts[0][1] is not None:
        put("prompt_first_audio", _int((prompts[0][1].get("data") or {}).get("firstAudioMs")))
        put("prompt_speech", _span(prompts[0][0], prompts[0][1]))
    if say is not None and answers:
        put("answer_request", _span(say, answers[-1][0]))
        if answers[-1][1] is not None:
            put("answer_first_audio", _int((answers[-1][1].get("data") or {}).get("firstAudioMs")))
            put("answer_speech", _span(answers[-1][0], answers[-1][1]))

    inner = next((e for e in hub if e["stage"] in ("things", "announce")), None)
    if inner is not None:
        data = inner.get("data") or {}
        total = _int(data.get("total_ms"))
        for key in ("transcribe", "records", "scores"):
            put(f"hub_{key}", _int(data.get(f"{key}_ms")))
        if total is not None and "hub" in out:
            put("hub_network", max(0, out["hub"] - total))

    end = None
    for e in reversed(phone):
        if e["src"] == "audio" and e["stage"] == "speech_finished":
            end = e
            break
    put("total", _span(wake, end))
    return out


def build_runs(events: list[Event], now_ms: int) -> list[Run]:
    """Прогоны из событий; новые первыми. ``now_ms`` — часы телефона (или хаба, если иных нет)."""
    by_run: dict[str, list[Event]] = {}
    for e in events:
        if e.get("run"):
            by_run.setdefault(str(e["run"]), []).append(e)
    runs = []
    for run_id, items in by_run.items():
        phone = sorted((e for e in items if e["src"] != "hub"), key=lambda e: e["ts"])
        hub = [e for e in items if e["src"] == "hub"]
        # Перезапуск служб после начала прогона к нему не относится.
        restart = next(
            (i for i, e in enumerate(phone) if i > 0 and e["stage"] == "service_started"), None
        )
        if restart is not None:
            phone = phone[:restart]
        if not phone:
            continue
        run = Run(run_id, int(phone[0]["ts"]))
        for e in phone:
            if e["stage"] == "command" and (e.get("data") or {}).get("intent"):
                run.intent = str(e["data"]["intent"])
                break
            if e["stage"] == "command_heard" and run.intent == "?":
                understood = str((e.get("data") or {}).get("understood", ""))
                if understood and understood != "none":
                    run.intent = understood.split(":")[0]
        words = []
        for e in phone:
            d = e.get("data") or {}
            if e["stage"] == "command_heard" and d.get("understood") == "none":
                words.append(str(d.get("text", "")))
            elif e["stage"] == "command_timeout":
                words.append(str(d.get("lastPartial", "")))
        run.heard = "; ".join(w for w in dict.fromkeys(words) if w)
        run.stages = _stages(phone, hub)
        failure = _failure(phone)
        stages = {e["stage"] for e in phone}
        if failure:
            run.outcome = "failed"
            run.fail_stage, run.fail_reason = failure
        elif "total" in run.stages or "note_recorded" in stages:
            run.outcome = "ok"
        elif run.intent in SILENT_INTENTS and "command_taken" in stages:
            run.outcome = "ok"
            run.stages["total"] = max(0, int(phone[-1]["ts"]) - int(phone[0]["ts"]))
        elif now_ms - int(phone[-1]["ts"]) < RUNNING_MS:
            run.outcome = "running"
        else:
            run.outcome = "unfinished"
        run.slow = [k for k, v in run.stages.items() if k in _BY_KEY and v > _BY_KEY[k].budget_ms]
        runs.append(run)
    return sorted(runs, key=lambda r: r.start_ms, reverse=True)


def _percentile(values: list[int], q: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


@dataclass
class StageStat:
    spec: StageSpec
    n: int
    p50: int
    p95: int
    worst: int
    slow_n: int

    @property
    def slow(self) -> bool:
        return self.p50 > self.spec.budget_ms


def stage_stats(runs: list[Run]) -> list[StageStat]:
    stats = []
    for spec in STAGES:
        values = [r.stages[spec.key] for r in runs if spec.key in r.stages]
        if not values:
            continue
        stats.append(
            StageStat(
                spec,
                len(values),
                int(statistics.median(values)),
                _percentile(values, 0.95),
                max(values),
                sum(1 for v in values if v > spec.budget_ms),
            )
        )
    return stats


@dataclass
class Failure:
    intent: str
    stage: str
    reason: str
    count: int
    last_run: str
    hint: str


def failures(runs: list[Run]) -> list[Failure]:
    counts: Counter[tuple[str, str, str]] = Counter()
    last: dict[tuple[str, str, str], str] = {}
    for r in runs:  # новые первыми: первая встреченная — самая свежая
        key = (r.intent, r.fail_stage or "?", r.fail_reason or "не завершён")
        if r.outcome in ("failed", "unfinished"):
            if r.outcome == "unfinished":
                key = (r.intent, "?", "оборван без причины")
            counts[key] += 1
            last.setdefault(key, r.id)
    result = []
    for (intent, stage, reason), count in counts.most_common():
        hint = FAILURE_HINTS.get((stage, reason)) or FAILURE_HINTS.get(
            (stage, reason.split(" ")[0]), ""
        )
        result.append(Failure(intent, stage, reason, count, last[(intent, stage, reason)], hint))
    return result


def advice(stats: list[StageStat], fails: list[Failure]) -> list[str]:
    """Что улучшать, по убыванию вклада: медленные этапы (медиана выше бюджета) и частые сбои."""
    lines = []
    ranked = sorted(
        (s for s in stats if s.slow and s.spec.key != "total"),
        key=lambda s: s.p50 - s.spec.budget_ms,
        reverse=True,
    )
    for s in ranked:
        lines.append(
            f"{s.spec.label}: медиана {s.p50 / 1000:.1f} с при бюджете "
            f"{s.spec.budget_ms / 1000:.1f} с "
            f"(медленно в {s.slow_n} из {s.n}). {s.spec.hint}"
        )
    for f in fails[:3]:
        if f.hint:
            lines.append(f"Сбой «{f.reason}» на этапе «{f.stage}» ({f.count} раз): {f.hint}")
    return lines
