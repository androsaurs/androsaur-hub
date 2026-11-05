import pytest

from tests.test_settings import DIM, register, sync


def test_wake_phrase_is_available_before_any_phone_connects(client):
    page = client.get("/")
    assert "Фраза пробуждения" in page.text
    assert 'id="v-audio-phrase" type="text"' in page.text
    assert 'value="зюзя"' in page.text


def test_phrase_is_normalized_stored_and_sent_as_text_with_acknowledgement(client):
    before = register(client).json()["rev"]
    assert sync(client).json()["values"] == {}  # Do not override a phone on first contact.
    reply = client.post("/settings/audio/phrase", data={"value": "  ЗЮЗЯ!  "})
    assert reply.status_code == 200
    config = sync(client, applied=before).json()
    assert config["values"] == {"audio": {"phrase": "зюзя"}}
    assert config["rev"] > before
    assert "ждёт устройство" in client.get("/").text
    sync(client, applied=config["rev"])
    assert "настройки применены" in client.get("/").text
    # Re-registering an old phone must not erase the new field or its saved value.
    register(client)
    assert sync(client).json()["values"]["audio"]["phrase"] == "зюзя"


@pytest.mark.parametrize(
    "bad", ["hello", "я", "зюзя~4", "зюзя||петя", "раз два три четыре пять", "<script>", "ы" * 81]
)
def test_invalid_phrase_is_refused_without_changing_previous_value(client, bad):
    register(client)
    client.post("/settings/audio/phrase", data={"value": "зюзя"})
    before = sync(client).json()["rev"]
    page = client.post("/settings/audio/phrase", data={"value": bad})
    assert 'role="alert"' in page.text
    config = sync(client).json()
    assert config["rev"] == before
    assert config["values"]["audio"]["phrase"] == "зюзя"


def test_variants_and_tolerance_are_preserved_and_reset_clears_override(client):
    register(client)
    client.post("/settings/audio/phrase", data={"value": "Привет|здравствуй Петя~1"})
    assert sync(client).json()["values"]["audio"]["phrase"] == "привет|здравствуй петя~1"
    client.post("/settings/audio/phrase/reset")
    assert sync(client).json()["values"] == {}


def test_schema_types_do_not_coerce_string_defaults_to_integers(client):
    assert register(client, [{**DIM, "default": "30"}]).status_code == 422
    assert register(client, [{**DIM, "type": "string", "default": 30}]).status_code == 422
