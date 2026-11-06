import pytest

from app.clients.groq import Transcript
from app.clients.memory_service import MemoryServiceError
from app.dependencies import get_integrations_service, get_things_service
from app.services.integrations import IntegrationsService
from app.services.things import ThingsService
from app.things_text import (
    Record,
    clean,
    item_and_place,
    kind_and_item,
    numbers,
    pick,
    same_item,
    words,
)
from tests.test_notes import KEY, NOW, FakeGroq, wav
from tests.test_settings import register

URL = "http://mcp-memory.test"
MEMORY_KEY = "k" * 40


class FakeMemory:
    """Сервер памяти в списке: хранит записи, смысловые оценки задаёт тест."""

    records_: list[Record] = []
    scores_: dict[str, float] = {}
    stored: list[tuple[str, str, str, str, str]] = []
    fail: str | None = None
    counter = 0

    def __init__(self, url: str, key: str, store: str) -> None:
        assert url == URL and key == MEMORY_KEY and store == "androsaur-home"

    def _check(self) -> None:
        if FakeMemory.fail:
            raise MemoryServiceError(FakeMemory.fail)

    def records(self) -> list[Record]:
        self._check()
        return list(FakeMemory.records_)

    def scores(self, query: str, limit: int = 8) -> dict[str, float]:
        self._check()
        return dict(FakeMemory.scores_)

    def store(self, content: str, item: str, kind: str, author: str, author_id: str) -> str:
        self._check()
        FakeMemory.counter += 1
        digest = f"h{FakeMemory.counter}"
        FakeMemory.stored.append((content, item, kind, author, author_id))
        FakeMemory.records_.append(Record(content, digest, item.split(), float(FakeMemory.counter)))
        return digest

    def delete(self, content_hash: str) -> None:
        self._check()
        FakeMemory.records_ = [r for r in FakeMemory.records_ if r.content_hash != content_hash]

    def check(self) -> str:
        self._check()
        return f"Работает: записей — {len(FakeMemory.records_)}"


def record(content: str, hash_: str = "x", score: float = 0.0, created: float = 0.0) -> Record:
    return Record(content, hash_, item_and_place(content)[0], created, score)


@pytest.fixture
def hub(client, tmp_path, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("HUB_NOTES_DIR", str(tmp_path / "notes"))
    get_settings.cache_clear()
    FakeGroq.result, FakeGroq.fail, FakeGroq.calls = (
        Transcript("батарейки в верхнем ящике на кухне", 82.0, 97.0),
        None,
        0,
    )
    FakeMemory.records_, FakeMemory.scores_, FakeMemory.stored = [], {}, []
    FakeMemory.fail, FakeMemory.counter = None, 0

    def integrations() -> IntegrationsService:
        return IntegrationsService(groq=FakeGroq, clock=lambda: NOW)

    def things() -> ThingsService:
        return ThingsService(integrations=integrations(), client=FakeMemory, clock=lambda: NOW)

    client.app.dependency_overrides[get_integrations_service] = integrations
    client.app.dependency_overrides[get_things_service] = things
    return client


def connect(client) -> None:
    client.post("/integrations/groq/key", data={"key": KEY})
    client.post("/things/connection", data={"url": URL, "store": "", "key": MEMORY_KEY})


def say(client, kind, **params):
    return client.post(
        f"/api/device/things/{kind}",
        params={"from": "Женя", "fromId": "p1", **params},
        content=wav(),
        headers={"Content-Type": "audio/wav"},
    ).json()


# --- разбор слов -------------------------------------------------------------------------------


def test_the_item_ends_where_the_place_begins():
    assert item_and_place("Ключи от дачи на крючке в прихожей") == (
        ["ключи", "от", "дачи"],
        ["на", "крючке", "в", "прихожей"],
    )
    assert item_and_place("Батарейки лежат в ящике")[0] == ["батарейки"]
    assert item_and_place("в ящике батарейки")[0] == ["в", "ящике", "батарейки"]
    assert item_and_place("батарейки") == (["батарейки"], [])


def test_a_repeated_command_word_and_the_final_stop_are_dropped():
    assert clean("Запомни, что батарейки в ящике.") == "батарейки в ящике"
    assert clean("Запомни батарейки в ящике!") == "батарейки в ящике"
    assert clean("Вспомни три плюс три") == "три плюс три"
    assert clean("  Ключи   на  крючке. ") == "Ключи на крючке"
    assert words("Отвёртка, ёжик") == ["отвертка", "ежик"]


def test_one_item_in_different_forms_is_the_same_item():
    assert same_item(["батарейки"], ["батарейка"])
    assert same_item(["ключи", "от", "дачи"], ["ключ", "от", "дачи"])
    assert same_item(words("отвёртка"), words("Отвертку"))
    assert not same_item(["батарейки"], ["батарейки", "aaa"])
    assert not same_item(["ключи", "от", "дачи"], ["ключи", "от", "машины"])
    assert not same_item(["пылесос"], ["батарейки"])


def test_a_question_finds_the_record_by_its_words_not_by_the_servers_similarity():
    records = [
        record("батарейки в верхнем ящике на кухне", "a", score=0.71),
        record("отвёртка в ящике с инструментами", "b", score=0.74),
        record("ключи от дачи на крючке", "c", score=0.82),
    ]
    # Short words confuse the server (0.82 to the keys); the words decide.
    assert [r.content_hash for r in pick("где лежит отвертка", records)[0]] == ["b"]
    assert [r.content_hash for r in pick("батарейку", records)[0]] == ["a"]
    assert [r.content_hash for r in pick("Где лежат ключи?", records)[0]] == ["c"]


def test_an_unknown_thing_is_not_found_whatever_the_server_thinks_of_it():
    records = [record("батарейки в ящике", "a", score=0.71), record("ключи на крючке", "c", 0.65)]
    assert pick("пылесос", records) == ([], 0)
    assert pick("", records) == ([], 0)


def test_only_a_high_similarity_stands_in_for_shared_words():
    records = [record("зарядка от ноутбука в шкафу", "z", score=0.90)]
    assert [r.content_hash for r in pick("зарядное устройство", records)[0]] == ["z"]
    records[0].score = 0.87
    assert pick("зарядное устройство", records) == ([], 0)


def test_the_numbers_of_the_question_must_be_in_the_record():
    sums = [record("Два плюс два равно пять", "s", score=0.87)]
    # The server finds sums alike (0.87 for «три плюс три»); the words and the numbers decide.
    assert pick("три плюс три", sums) == ([], 0)
    assert pick("два плюс три", sums) == ([], 0)
    assert pick("сколько будет семь плюс восемь", sums) == ([], 0)
    assert [r.content_hash for r in pick("два плюс два", sums)[0]] == ["s"]
    assert [r.content_hash for r in pick("чему равно пять", sums)[0]] == ["s"]
    assert [r.content_hash for r in pick("2 плюс 2", sums)[0]] == ["s"]
    assert pick("2 плюс 7", sums) == ([], 0)
    assert numbers(words("двух и 5 и сто")) == {2, 5, 100}


def test_at_most_two_records_and_the_newer_one_first_when_equally_good():
    records = [
        record("ключи на крючке", "old", score=0.9, created=1.0),
        record("ключи в ящике", "new", score=0.9, created=2.0),
        record("ключи у папы", "third", score=0.9, created=0.5),
    ]
    chosen, more = pick("ключи", records)
    assert [r.content_hash for r in chosen] == ["new", "old"]
    assert more == 1


# --- настройка и голос -------------------------------------------------------------------------


def test_the_connection_is_saved_checked_and_the_key_is_never_shown(hub):
    page = hub.get("/things")
    assert page.status_code == 200 and "не подключён" in page.text
    assert "Адрес вида" in hub.post("/things/connection", data={"url": "x", "key": MEMORY_KEY}).text
    assert (
        "Стор"
        in hub.post(
            "/things/connection", data={"url": URL, "store": "Не Так", "key": MEMORY_KEY}
        ).text
    )
    connect(hub)
    page = hub.get("/things")
    assert "подключён" in page.text and "…kkkk" in page.text and MEMORY_KEY not in page.text
    assert "Работает" in hub.post("/things/check").text
    FakeMemory.fail = "Сервер памяти не принял ключ"
    assert "Не вышло" in hub.post("/things/check").text
    hub.post("/things/key/clear")
    assert "не подключён" in hub.get("/things").text


def test_something_said_is_remembered_with_who_said_it(hub):
    register(hub)
    connect(hub)
    assert say(hub, "remember") == {
        "status": "saved",
        "text": "батарейки в верхнем ящике на кухне",
        "previous": None,
    }
    assert FakeMemory.stored == [
        ("батарейки в верхнем ящике на кухне", "батарейки", "location", "Женя", "p1")
    ]


def test_moving_a_thing_replaces_the_old_record_instead_of_adding_a_second(hub):
    connect(hub)
    say(hub, "remember")
    FakeGroq.result = Transcript("Запомни, батарейки в нижнем ящике.", 82.0, 97.0)
    reply = say(hub, "remember")
    assert reply["status"] == "updated"
    assert reply["previous"] == "батарейки в верхнем ящике на кухне"
    assert [r.content for r in FakeMemory.records_] == ["батарейки в нижнем ящике"]
    FakeGroq.result = Transcript("батарейки ААА в шкафу", 82.0, 97.0)
    assert say(hub, "remember")["status"] == "saved"
    assert len(FakeMemory.records_) == 2


def test_a_phrase_with_a_place_is_a_location_and_any_other_is_a_fact():
    assert kind_and_item("батарейки в верхнем ящике") == ("location", ["батарейки"])
    assert kind_and_item("У Полины день рождения двенадцатого мая")[0] == "fact"
    assert kind_and_item("врача зовут Иван Петрович") == (
        "fact",
        ["врача", "зовут", "иван", "петрович"],
    )


def test_any_fact_is_remembered_and_facts_pile_up_instead_of_replacing_each_other(hub):
    connect(hub)
    for phrase in ("врача зовут Иван Петрович", "врача принимает по вторникам"):
        FakeGroq.result = Transcript(phrase, 82.0, 97.0)
        assert say(hub, "remember")["status"] == "saved"
    assert [r.content for r in FakeMemory.records_] == [
        "врача зовут Иван Петрович",
        "врача принимает по вторникам",
    ]
    assert FakeMemory.stored[0][2] == "fact"
    FakeGroq.result = Transcript("Врача зовут Иван Петрович.", 82.0, 97.0)
    assert say(hub, "remember")["status"] == "saved"
    assert len(FakeMemory.records_) == 2


def test_a_false_fact_is_kept_as_told_and_recalled_by_its_words(hub):
    connect(hub)
    FakeGroq.result = Transcript("Два плюс два равно пять.", 82.0, 97.0)
    assert say(hub, "remember")["status"] == "saved"
    assert FakeMemory.stored[0][2] == "fact"
    FakeGroq.result = Transcript("вспомни два плюс два", 82.0, 97.0)
    assert say(hub, "find") == {
        "status": "found",
        "text": "Два плюс два равно пять",
        "previous": None,
    }


def test_a_single_word_is_not_remembered(hub):
    connect(hub)
    FakeGroq.result = Transcript("батарейки", 82.0, 97.0)
    assert say(hub, "remember") == {"status": "unclear", "text": "батарейки", "previous": None}
    assert FakeMemory.records_ == []


def test_a_fact_is_recalled_by_any_of_its_words_and_extra_records_are_counted(hub):
    connect(hub)
    for phrase in (
        "у Полины день рождения двенадцатого мая",
        "Полина любит синий цвет",
        "Полина ходит на плавание по средам",
    ):
        FakeGroq.result = Transcript(phrase, 82.0, 97.0)
        say(hub, "remember")
    FakeGroq.result = Transcript("вспомни день рождения Полины", 82.0, 97.0)
    reply = say(hub, "find")
    assert reply["status"] == "found"
    assert reply["text"] == "у Полины день рождения двенадцатого мая"
    FakeGroq.result = Transcript("Полина", 82.0, 97.0)
    reply = say(hub, "find")
    assert reply["status"] == "found" and reply["text"].endswith("Есть ещё записи: 1")


def test_the_question_is_answered_with_the_stored_phrase(hub):
    connect(hub)
    say(hub, "remember")
    FakeGroq.result = Transcript("где лежат батарейки", 82.0, 97.0)
    assert say(hub, "find") == {
        "status": "found",
        "text": "батарейки в верхнем ящике на кухне",
        "previous": None,
    }
    FakeGroq.result = Transcript("пылесос", 82.0, 97.0)
    assert say(hub, "find")["status"] == "not_found"


def test_unclear_speech_is_neither_stored_nor_searched(hub):
    connect(hub)
    FakeGroq.result = Transcript("мм", 20.0, 97.0)
    assert say(hub, "remember")["status"] == "unclear"
    assert say(hub, "find")["status"] == "unclear"
    assert FakeMemory.records_ == []


def test_the_phone_is_told_why_there_is_no_answer(hub):
    assert say(hub, "find")["status"] == "no_memory"
    hub.post("/things/connection", data={"url": URL, "store": "", "key": MEMORY_KEY})
    assert say(hub, "find")["status"] == "no_groq"
    hub.post("/integrations/groq/key", data={"key": KEY})
    FakeMemory.fail = "Сервер памяти недоступен"
    assert say(hub, "find")["status"] == "error"
    assert say(hub, "remember")["status"] == "error"
    FakeMemory.fail = None
    FakeGroq.fail = "лимит"
    assert say(hub, "find")["status"] == "error"


def test_the_records_can_be_seen_and_removed_in_the_admin(hub):
    connect(hub)
    say(hub, "remember")
    page = hub.get("/things")
    assert "батарейки в верхнем ящике на кухне" in page.text
    hub.post(f"/things/{FakeMemory.records_[0].content_hash}/delete")
    assert FakeMemory.records_ == []


def test_a_sound_that_is_not_a_wav_and_an_unknown_kind_are_refused(hub):
    connect(hub)
    bad = hub.post(
        "/api/device/things/find", content=b"not a wav", headers={"Content-Type": "audio/wav"}
    )
    assert bad.status_code == 422
    assert hub.post("/api/device/things/forget", content=wav()).status_code == 404
