from app.db.database import init_db
from tests.test_people import DEVICE, KATYA, OTHER, PAPA, join, person, phone_sync


def choose(client, person, character):
    return client.post("/characters/select", data={"person": person, "character": character})


def test_catalog_can_be_explored_without_selecting_a_person(client):
    page = client.get("/characters")
    assert page.status_code == 200
    assert "Кошечка" in page.text and "Собачка" in page.text
    assert "Пуф" in page.text
    assert "Хомяк" in page.text
    assert "Пингвин" in page.text and "Пингвин 2" in page.text
    assert page.text.count('type="submit" disabled') == 7
    for name in ("dinosaur", "cat", "dog", "puf", "hamster", "penguin", "penguin2"):
        assert client.get(f"/static/characters/{name}.svg").status_code == 200


def test_hamster_is_personal_and_delivered_to_all_phones_and_the_fresh_lookup(client):
    join(client, DEVICE, "Дом", [KATYA])
    join(client, OTHER, "Другой телефон")
    assert "Готово, Катя" in choose(client, KATYA["id"], "hamster").text
    expected = {KATYA["id"]: "hamster"}
    assert phone_sync(client)["characters"] == expected
    assert phone_sync(client, device=OTHER)["characters"] == expected
    assert client.get("/api/characters").json()["characters"] == expected


def test_puf_is_a_personal_choice_on_every_phone_and_in_the_fresh_lookup(client):
    join(client, DEVICE, "Дом", [KATYA])
    join(client, OTHER, "Другой телефон")
    page = choose(client, KATYA["id"], "puf")
    assert "Готово, Катя" in page.text
    expected = {KATYA["id"]: "puf"}
    assert phone_sync(client)["characters"] == expected
    assert phone_sync(client, device=OTHER)["characters"] == expected
    assert client.get("/api/characters").json()["characters"] == expected


def test_choice_follows_person_to_all_devices_and_survives_restart_and_rename(client):
    join(client, DEVICE, "Телефон", [PAPA, KATYA])
    join(client, OTHER, "Кухня")
    page = choose(client, KATYA["id"], "cat")
    assert "Готово, Катя" in page.text
    expected = {KATYA["id"]: "cat"}
    assert phone_sync(client)["characters"] == expected
    assert phone_sync(client, device=OTHER)["characters"] == expected
    init_db()
    client.post("/people/rename", data={"person": KATYA["id"], "to": "Катюша"})
    assert phone_sync(client)["characters"] == expected
    assert "Мой персонаж ✓" in client.get(f"/characters?person={KATYA['id']}").text


def test_invalid_choices_leave_previous_choice_and_default_can_be_restored(client):
    join(client, DEVICE, "Телефон", [PAPA, KATYA])
    choose(client, KATYA["id"], "cat")
    assert "Такого персонажа" in choose(client, KATYA["id"], "../../secret").text
    assert "Выберите человека" in choose(client, "missing", "dog").text
    assert phone_sync(client)["characters"] == {KATYA["id"]: "cat"}
    choose(client, KATYA["id"], "dinosaur")
    assert phone_sync(client)["characters"] == {KATYA["id"]: "dinosaur"}
    assert phone_sync(client, device=DEVICE)["characters"].get(PAPA["id"]) is None


def test_merging_an_incoming_person_preserves_the_households_choice(client):
    join(client, DEVICE, "Дом", [KATYA])
    choose(client, KATYA["id"], "cat")
    incoming = person("ffffffffffff", "Катя", range(200, 210))
    join(client, OTHER, "Другой телефон", [incoming])
    assert "Выберите человека" in choose(client, incoming["id"], "dog").text
    client.post("/people/merge", data={"person": incoming["id"], "into": KATYA["id"]})
    assert phone_sync(client, device=OTHER)["characters"] == {KATYA["id"]: "cat"}


def test_recognition_lookup_sees_changes_immediately_without_waiting_for_sync(client):
    join(client, DEVICE, "Дом", [KATYA])
    assert client.get("/api/characters").json() == {"characters": {}, "accessories": {}}
    choose(client, KATYA["id"], "dog")
    assert client.get("/api/characters").json()["characters"] == {KATYA["id"]: "dog"}
    choose(client, KATYA["id"], "cat")
    current = client.get("/api/characters")
    assert current.json()["characters"] == {KATYA["id"]: "cat"}
    assert current.headers["cache-control"] == "no-store"
    choose(client, KATYA["id"], "dinosaur")
    assert client.get("/api/characters").json()["characters"] == {KATYA["id"]: "dinosaur"}
