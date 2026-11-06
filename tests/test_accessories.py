from app.db.database import get_connection, init_db
from tests.test_characters import choose
from tests.test_people import DEVICE, KATYA, OTHER, PAPA, join, phone_sync


def test_every_supported_outfit_follows_its_owner_and_unsupported_pairs_are_rejected(client):
    join(client, DEVICE, "Дом", [KATYA])
    join(client, OTHER, "Кухня")
    penguin_outfits = ["bobble_hat", "headphones", "scarf", "cap", "hoodie", "none"]
    for character, supported, unsupported in (
        ("puf", ["bow", "scarf", "crown", "glasses", "none"], ["seed"]),
        ("hamster", ["seed", "bow", "glasses", "none"], ["scarf", "crown"]),
        ("penguin", penguin_outfits, ["glasses", "bow", "crown", "seed"]),
        ("penguin2", penguin_outfits, ["glasses", "bow", "crown", "seed"]),
    ):
        choose(client, KATYA["id"], character)
        for accessory in supported:
            result = edit(client, character=character, accessory=accessory)
            assert result.status_code == 200
            assert (
                phone_sync(client, device=OTHER)["accessories"][KATYA["id"]][character] == accessory
            )
        for accessory in unsupported:
            assert edit(client, character=character, accessory=accessory).status_code == 409
            assert (
                client.get("/api/characters").json()["accessories"][KATYA["id"]][character]
                == "none"
            )


def test_upgrading_the_glasses_only_database_keeps_preferences_and_accepts_new_outfits(client):
    join(client, DEVICE, "Дом", [KATYA])
    choose(client, KATYA["id"], "puf")
    with get_connection() as conn:
        conn.execute("DROP TABLE character_accessories")
        conn.execute(
            "CREATE TABLE character_accessories ("
            "person_id TEXT NOT NULL REFERENCES house_people(id) ON DELETE CASCADE, "
            "character_id TEXT NOT NULL, "
            "accessory TEXT NOT NULL CHECK(accessory IN ('none', 'glasses')), "
            "PRIMARY KEY(person_id, character_id))"
        )
        conn.execute(
            "INSERT INTO character_accessories VALUES (?, 'puf', 'glasses')", (KATYA["id"],)
        )
    init_db()
    assert client.get("/api/characters").json()["accessories"] == {KATYA["id"]: {"puf": "glasses"}}
    assert edit(client, accessory="bow").status_code == 200
    init_db()
    assert client.get("/api/characters").json()["accessories"] == {KATYA["id"]: {"puf": "bow"}}


def test_upgrading_the_database_from_before_the_penguin_keeps_preferences(client):
    join(client, DEVICE, "Дом", [KATYA])
    choose(client, KATYA["id"], "hamster")
    with get_connection() as conn:
        conn.execute("DROP TABLE character_accessories")
        conn.execute(
            "CREATE TABLE character_accessories ("
            "person_id TEXT NOT NULL REFERENCES house_people(id) ON DELETE CASCADE, "
            "character_id TEXT NOT NULL, "
            "accessory TEXT NOT NULL CHECK(accessory IN "
            "('none', 'glasses', 'bow', 'scarf', 'crown', 'seed')), "
            "PRIMARY KEY(person_id, character_id))"
        )
        conn.execute(
            "INSERT INTO character_accessories VALUES (?, 'hamster', 'seed')", (KATYA["id"],)
        )
    init_db()
    assert client.get("/api/characters").json()["accessories"] == {KATYA["id"]: {"hamster": "seed"}}
    choose(client, KATYA["id"], "penguin")
    assert edit(client, character="penguin", accessory="hoodie").status_code == 200
    assert client.get("/api/characters").json()["accessories"][KATYA["id"]]["penguin"] == "hoodie"


def edit(client, person=KATYA["id"], character="puf", accessory="glasses"):
    return client.post(
        "/api/characters/accessory",
        json={
            "person": person,
            "character": character,
            "accessory": accessory,
        },
    )


def test_glasses_follow_the_person_across_phones_rename_and_restart(client):
    join(client, DEVICE, "Дом", [KATYA, PAPA])
    join(client, OTHER, "Кухня")
    choose(client, KATYA["id"], "puf")
    response = edit(client)
    assert response.status_code == 200
    expected = {KATYA["id"]: {"puf": "glasses"}}
    assert response.json()["accessories"] == expected
    assert phone_sync(client)["accessories"] == expected
    assert phone_sync(client, device=OTHER)["accessories"] == expected
    init_db()
    client.post("/people/rename", data={"person": KATYA["id"], "to": "Катюша"})
    assert client.get("/api/characters").json()["accessories"] == expected
    assert edit(client, accessory="none").json()["accessories"] == {KATYA["id"]: {"puf": "none"}}


def test_accessory_is_per_character_and_late_edits_cannot_dress_a_different_character(client):
    join(client, DEVICE, "Дом", [KATYA])
    choose(client, KATYA["id"], "puf")
    assert edit(client).status_code == 200
    choose(client, KATYA["id"], "hamster")
    assert edit(client, accessory="none").status_code == 409
    assert edit(client, character="hamster").status_code == 200
    expected = {KATYA["id"]: {"puf": "glasses", "hamster": "glasses"}}
    assert client.get("/api/characters").json()["accessories"] == expected
    assert edit(client, character="hamster", accessory="none").status_code == 200
    choose(client, KATYA["id"], "puf")
    assert client.get("/api/characters").json()["accessories"][KATYA["id"]]["puf"] == "glasses"


def test_invalid_unsupported_and_unknown_people_do_not_change_any_preferences(client):
    join(client, DEVICE, "Дом", [KATYA, PAPA])
    choose(client, KATYA["id"], "puf")
    assert edit(client).status_code == 200
    assert edit(client, accessory="../glasses").status_code == 422
    assert edit(client, person="ffffffffffff").status_code == 404
    choose(client, PAPA["id"], "cat")
    assert edit(client, person=PAPA["id"], character="cat").status_code == 409
    assert edit(client, person=PAPA["id"], character="puf").status_code == 409
    assert client.get("/api/characters").json()["accessories"] == {KATYA["id"]: {"puf": "glasses"}}
