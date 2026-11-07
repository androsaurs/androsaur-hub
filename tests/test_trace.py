import tests.test_things as things_tests
from app.trace import advice, build_runs, failures, stage_stats

T0 = 1_794_000_000_000


def ev(run, at, src, stage, **data):
    return {"run": run, "ts": T0 + at, "src": src, "stage": stage, "data": data}


def good_run(run="r1", shift=0, first_audio=1866):
    """Прогон «вспомни где ключи» из реальной трассы."""
    s = shift
    return [
        ev(run, s + 0, "audio", "wake_word"),
        ev(run, s + 40, "audio", "command_listening"),
        ev(run, s + 2590, "audio", "command_heard", understood="find"),
        ev(run, s + 2590, "audio", "speech_started"),
        ev(run, s + 5540, "audio", "speech_finished", firstAudioMs=first_audio, audioMs=940),
        ev(run, s + 5550, "audio", "note_recording_started"),
        ev(run, s + 9130, "audio", "announcement_recorded", durationMs=2176),
        ev(run, s + 9130, "audio", "command", intent="find"),
        ev(run, s + 9460, "app", "command_taken", intent="find"),
        ev(run, s + 9480, "app", "wav_fetched", bytes=69676),
        ev(run, s + 10360, "app", "hub_replied", status="found"),
        ev(run, s + 10370, "app", "say_requested", chars=34),
        ev(run, s + 10380, "audio", "speech_started"),
        ev(run, s + 15490, "audio", "speech_finished", firstAudioMs=2533, audioMs=2414),
        ev(run, s + 10360, "hub", "things", total_ms=700, transcribe_ms=500, records_ms=100,
           scores_ms=80),
    ]  # fmt: skip


NOW = T0 + 10 * 60_000
hub = things_tests.hub  # фикстура: хаб с подставными Groq и памятью


def test_good_run_is_laid_out_by_stage():
    (run,) = build_runs(good_run(), NOW)
    assert run.outcome == "ok" and run.intent == "find"
    assert run.stages["total"] == 15490
    assert run.stages["recognize"] == 2550
    assert run.stages["prompt_first_audio"] == 1866
    assert run.stages["record_tail"] == 9130 - 5550 - 2176
    assert run.stages["handoff"] == 330
    assert run.stages["hub"] == 880
    assert run.stages["hub_network"] == 180  # 880 мс ждал, 700 мс работал сам хаб
    assert run.stages["hub_transcribe"] == 500
    assert run.stages["answer_first_audio"] == 2533
    assert "answer_first_audio" in run.slow and "prompt_first_audio" in run.slow


def test_silent_window_is_a_failure_of_recognition():
    events = [
        ev("r2", 0, "audio", "wake_word"),
        ev("r2", 40, "audio", "command_listening"),
        ev("r2", 7040, "audio", "command_timeout", utterances="0", peakDbfs="-60"),
    ]
    (run,) = build_runs(events, NOW)
    assert (run.outcome, run.fail_stage, run.fail_reason) == ("failed", "recognize", "silent")
    (fail,) = failures([run])
    assert fail.count == 1 and "громкость" in fail.hint


def test_unrecognized_speech_is_told_from_silence():
    events = [
        ev("r3", 0, "audio", "wake_word"),
        ev("r3", 7040, "audio", "command_timeout", utterances="2"),
    ]
    assert build_runs(events, NOW)[0].fail_reason == "unrecognized"


def test_hub_failures_by_status_but_not_found_is_an_answer():
    bad = [*good_run("r4")]
    bad[10] = ev("r4", 10360, "app", "hub_replied", status="unclear")
    ok = [*good_run("r5")]
    ok[10] = ev("r5", 10360, "app", "hub_replied", status="not_found")
    runs = {r.id: r for r in build_runs(bad + ok, NOW)}
    assert (runs["r4"].outcome, runs["r4"].fail_reason) == ("failed", "unclear")
    assert runs["r5"].outcome == "ok"


def test_hub_that_did_not_answer_and_unfinished_run():
    gone = [e for e in good_run("r6") if e["stage"] not in ("hub_replied", "say_requested")]
    gone = [e for e in gone if not (e["stage"] == "speech_started" and e["ts"] > T0 + 10000)]
    gone = [e for e in gone if not (e["stage"] == "speech_finished" and e["ts"] > T0 + 10000)]
    gone.append(ev("r6", 10360, "app", "hub_replied", status="no reply"))
    (run,) = build_runs(gone, NOW)
    assert (run.fail_stage, run.fail_reason) == ("hub", "no_hub")

    cut = [e for e in good_run("r7") if e["ts"] < T0 + 5000]
    assert build_runs(cut, T0 + 5000 + 10_000)[0].outcome == "running"
    assert build_runs(cut, T0 + 5000 + 200_000)[0].outcome == "unfinished"


def test_record_failure_and_speech_failure():
    events = [
        ev("r8", 0, "audio", "wake_word"),
        ev("r8", 3000, "audio", "note_record_failed", reason="too_short"),
    ]
    assert build_runs(events, NOW)[0].fail_reason == "too_short"
    events = [
        ev("r9", 0, "audio", "wake_word"),
        ev("r9", 100, "audio", "speech_failed", reason="X"),
    ]
    assert build_runs(events, NOW)[0].fail_stage == "speech"


def test_statistics_and_advice_point_at_the_slow_stage():
    events = good_run("a") + good_run("b", 20_000) + good_run("c", 40_000, first_audio=500)
    runs = build_runs(events, NOW)
    stats = {s.spec.key: s for s in stage_stats(runs)}
    assert stats["answer_first_audio"].n == 3 and stats["answer_first_audio"].slow
    assert stats["prompt_first_audio"].p50 == 1866 and stats["prompt_first_audio"].worst == 1866
    lines = advice(list(stats.values()), failures(runs))
    assert any("Подсказка: синтез" in line and "кэш" in line for line in lines)
    assert not any("Хаб: расшифровка" in line for line in lines)  # 0,5 с — в бюджете


# --- хаб целиком ---


def test_phone_events_are_stored_without_words_and_reported(hub):
    batch = {
        "device": "phone-1234",
        "events": [
            {"run": "r1", "ts": T0, "src": "audio", "stage": "wake_word", "data": {}},
            {
                "run": "r1",
                "ts": T0 + 100,
                "src": "audio",
                "stage": "command_heard",
                "data": {"understood": "find", "text": "где лежат мои секретные ключи"},
            },
        ],
    }
    assert hub.post("/api/device/trace", json=batch).status_code == 204
    stored = hub.get("/trace/events.json?days=14").json()
    assert [e["stage"] for e in stored] == ["wake_word", "command_heard"]
    assert "text" not in stored[1]["data"] and "секретные" not in str(stored)


def test_trace_rejects_unknown_source(hub):
    bad = {"events": [{"run": "r", "ts": 1, "src": "hub", "stage": "x"}]}
    assert hub.post("/api/device/trace", json=bad).status_code == 422


def test_things_request_leaves_hub_timings_under_the_run(hub):
    from tests.test_notes import wav

    reply = hub.post(
        "/api/device/things/find",
        content=wav(),
        headers={"Content-Type": "audio/wav", "X-Trace-Run": "run-42"},
    )
    assert reply.status_code == 200
    events = hub.get("/trace/events.json?days=14").json()
    assert events == []  # без событий телефона прогон не показывается
    assert hub.get("/trace").status_code == 200


def test_page_exports_and_clear(hub):
    hub.post(
        "/api/device/trace",
        json={"events": [{"run": "r1", "ts": 1, "src": "audio", "stage": "wake_word"}]},
    )
    assert hub.get("/trace").status_code == 200
    assert hub.get("/trace?days=1").status_code == 200
    assert hub.get("/trace/runs.json").json() == []  # старше суток по часам хаба
    assert hub.post("/trace/clear", follow_redirects=False).status_code == 303
    assert hub.get("/trace/events.json?days=14").json() == []


def test_record_gate_splits_the_recording_into_wait_speech_and_pause():
    events = good_run()
    events.append(ev("r1", 9120, "audio", "record_gate", speechStartMs=4000, speechEndMs=5800,
                     recordedMs=7300, floorDbfs=-70, thresholdDbfs=-58, speechMs=900))  # fmt: skip
    (run,) = build_runs(events, NOW)
    assert run.stages["record_wait"] == 4000
    assert run.stages["record_speech"] == 1800
    assert run.stages["record_pause"] == 1500
    assert "record_wait" in run.slow and "record_pause" not in run.slow


def test_a_service_restart_after_the_run_does_not_stretch_it():
    events = good_run() + [
        ev("r1", 140_000, "audio", "service_started"),
        ev("r1", 170_000, "audio", "speech_started"),
        ev("r1", 171_000, "audio", "speech_finished", firstAudioMs=5, audioMs=900),
    ]  # fmt: skip
    (run,) = build_runs(events, NOW)
    assert run.stages["total"] == 15490
