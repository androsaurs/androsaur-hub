from app.db.database import get_connection, init_db
from app.services.people import PeopleService
from tests.test_horoscope import setup
from tests.test_people import KATYA, OTHER, PAPA, join, person


def automatic(client, person_id=KATYA["id"]):
    return client.post("/api/horoscope", json={"person": person_id, "for_greeting": True}).json()


def enable(client, person_id=KATYA["id"], enabled=True):
    return client.post(
        "/people/horoscope-greeting",
        data={
            "person": person_id,
            **({"enabled": "1"} if enabled else {}),
        },
    )


def test_default_is_off_and_automatic_request_never_fetches_without_consent(client):
    service, source, _ = setup(client)
    service.set_sign(KATYA["id"], "leo")
    result = automatic(client)
    assert result["status"] == "greeting_disabled" and not result["greeting_enabled"]
    assert result["text"] is None and source.calls == 0
    # The ordinary voice command remains available without the checkbox.
    explicit = client.post("/api/horoscope", json={"person": KATYA["id"]}).json()
    assert explicit["status"] == "ok" and explicit["greeting_enabled"] is False
    assert source.calls == 1


def test_checkbox_changes_only_this_person_and_takes_effect_without_sync(client):
    service, source, _ = setup(client)
    service.set_sign(KATYA["id"], "leo")
    page = enable(client)
    assert 'value="1" checked' in page.text
    result = automatic(client)
    assert result["status"] == "ok" and result["greeting_enabled"] is True
    assert source.calls == 1
    assert automatic(client, PAPA["id"])["status"] == "greeting_disabled"
    enable(client, enabled=False)
    assert automatic(client)["status"] == "greeting_disabled"
    assert source.calls == 1
    # Opting out does not disable manual requests or remove the cached bundle.
    assert service.today(KATYA["id"]).status == "ok"


def test_missing_sign_disabled_source_and_outage_are_quiet_optional_failures(client):
    service, source, _ = setup(client)
    enable(client)
    assert automatic(client)["status"] == "sign_missing"
    assert source.calls == 0
    PeopleService().set_birth_date(KATYA["id"], "2018-04-03")
    service.set_provider("")
    assert automatic(client)["status"] == "disabled"
    assert source.calls == 0
    service.set_provider("europaplus")
    source.broken = True
    assert automatic(client)["status"] == "unavailable"
    assert source.calls == 1


def test_old_database_migration_defaults_to_off_and_preserves_profiles(client):
    service, _, _ = setup(client)
    service.set_sign(KATYA["id"], "leo")
    PeopleService().set_birth_date(KATYA["id"], "2018-04-03")
    with get_connection() as conn:
        conn.execute("ALTER TABLE house_people DROP COLUMN horoscope_in_greeting")
    init_db()
    init_db()
    profile = next(p for p in PeopleService().all() if p.id == KATYA["id"])
    assert not profile.horoscope_in_greeting and profile.zodiac_sign == "leo"
    assert profile.birth_date.isoformat() == "2018-04-03"
    assert automatic(client)["status"] == "greeting_disabled"


def test_choice_survives_rename_merge_and_restart_and_is_shared_by_all_phones(client):
    service, _, _ = setup(client)
    service.set_sign(KATYA["id"], "leo")
    enable(client)
    client.post("/people/rename", data={"person": KATYA["id"], "to": "Катюша"})
    incoming = person("ffffffffffff", "Катя", range(200, 210))
    join(client, OTHER, "Другой телефон", [incoming])
    assert "Выберите человека дома" in enable(client, incoming["id"]).text
    client.post("/people/merge", data={"person": incoming["id"], "into": KATYA["id"]})
    init_db()
    # The incoming phone now recognizes this same permanent household ID.
    assert automatic(client)["greeting_enabled"] is True
    assert "Выберите человека дома" in enable(client, "missing").text
    assert automatic(client, "missing")["status"] == "unknown_person"
