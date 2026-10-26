import math

from tests.test_settings import DEVICE, register, sync

MODEL = "SFace int8/128"
OTHER = "11111111-2222-3333-4444-555555555555"
THIRD = "99999999-8888-7777-6666-555555555555"


def vec(seed: int) -> list[float]:
    return [round(math.sin(seed * 12.9898 + i * 78.233) / 2, 6) for i in range(128)]


def person(pid: str, name: str, seeds: range) -> dict:
    return {
        "id": pid,
        "name": name,
        "vectors": [vec(s) for s in seeds],
        "digest": f"{pid}-{len(seeds)}",
    }


PAPA = person("a1b2c3d4e5f6", "Папа", range(0, 12))
KATYA = person("0123456789ab", "Катя", range(100, 112))


def report(p: dict) -> dict:
    return {"id": p["id"], "name": p["name"], "samples": len(p["vectors"]), "digest": p["digest"]}


def phone_sync(client, device=DEVICE, people=(), **extra):
    body = {"device": device, "faceModel": MODEL, "people": [report(p) for p in people], **extra}
    return client.post("/api/device/sync", json=body).json()


def upload(client, device, people, model=MODEL):
    body = {"device": device, "faceModel": model, "people": people}
    return client.post("/api/device/people/upload", json=body)


def download(client, device):
    return client.post("/api/device/people/download", json={"device": device}).json()


def join(client, device, name, people=()):
    """Телефон подключается, сообщает людей и отдаёт наборы, которые хаб попросил."""
    client.post("/api/device/register", json={"id": device, "name": name})
    asked = phone_sync(client, device, people)["upload"]
    upload(client, device, [p for p in people if p["id"] in asked])
    return asked


def test_the_first_phone_brings_the_people_of_the_house(client):
    register(client)
    asked = join(client, DEVICE, "Redmi 12", [PAPA, KATYA])
    assert sorted(asked) == sorted([PAPA["id"], KATYA["id"]])

    house = download(client, DEVICE)
    assert house["faceModel"] == MODEL
    assert {p["name"]: len(p["vectors"]) for p in house["people"]} == {"Папа": 12, "Катя": 12}
    page = client.get("/people").text
    assert "Наборов: 12" in page and "Новые с телефонов" not in page


def test_a_clean_phone_gets_everybody_at_once(client):
    register(client)
    join(client, DEVICE, "Redmi 12", [PAPA, KATYA])
    rev = phone_sync(client, DEVICE, [PAPA, KATYA])["peopleRev"]

    assert join(client, OTHER, "Кухня") == []
    assert phone_sync(client, OTHER)["peopleRev"] == rev
    house = download(client, OTHER)
    assert {p["id"]: len(p["vectors"]) for p in house["people"]} == {
        PAPA["id"]: 12,
        KATYA["id"]: 12,
    }
    papa = next(p for p in house["people"] if p["id"] == PAPA["id"])
    assert papa["vectors"][0] == [round(x, 5) for x in vec(0)]


def test_nothing_new_asks_nothing_and_changes_nothing(client):
    register(client)
    join(client, DEVICE, "Redmi 12", [PAPA])
    rev = phone_sync(client, DEVICE, [PAPA])["peopleRev"]
    again = phone_sync(client, DEVICE, [PAPA])
    assert again["upload"] == [] and again["peopleRev"] == rev

    # The same sets again (as after the phone loaded the house's) change nothing either.
    upload(client, DEVICE, [{**PAPA, "digest": "after-import"}])
    assert phone_sync(client, DEVICE, [PAPA])["peopleRev"] == rev


def test_new_samples_on_a_phone_reach_the_others(client):
    register(client)
    join(client, DEVICE, "Redmi 12", [PAPA])
    rev = phone_sync(client, DEVICE, [PAPA])["peopleRev"]

    more = person(PAPA["id"], "Папа", range(0, 20))
    assert phone_sync(client, DEVICE, [more])["upload"] == [PAPA["id"]]
    upload(client, DEVICE, [more])
    assert phone_sync(client, DEVICE, [more])["peopleRev"] > rev
    assert len(download(client, DEVICE)["people"][0]["vectors"]) == 20


def test_no_more_than_sixty_sets_per_person(client):
    register(client)
    join(client, DEVICE, "Redmi 12", [person(PAPA["id"], "Папа", range(0, 80))])
    assert len(download(client, DEVICE)["people"][0]["vectors"]) == 60


def test_a_phone_from_another_system_waits_for_a_decision_and_merges(client):
    register(client)
    join(client, DEVICE, "Redmi 12", [PAPA, KATYA])
    rev = phone_sync(client, DEVICE, [PAPA, KATYA])["peopleRev"]

    their_katya = person("ffffffffffff", "Катя", range(200, 210))
    assert join(client, OTHER, "Старый", [their_katya]) == [their_katya["id"]]
    assert phone_sync(client, DEVICE, [PAPA, KATYA])["peopleRev"] == rev, "not shared yet"
    page = client.get("/people").text
    assert "Новые с телефонов" in page and "С телефона Старый" in page
    assert f'<option value="{KATYA["id"]}" selected>Это Катя' in page

    reply = client.post("/people/merge", data={"person": their_katya["id"], "into": KATYA["id"]})
    assert reply.status_code == 200 and "Новые с телефонов" not in reply.text

    commands = phone_sync(client, OTHER, [their_katya])["commands"]
    assert commands == [
        {
            "id": commands[0]["id"],
            "kind": "merge_person",
            "person": their_katya["id"],
            "name": "Катя",
            "to": "Катя",
            "into": KATYA["id"],
        }
    ]
    katya = next(p for p in download(client, DEVICE)["people"] if p["id"] == KATYA["id"])
    assert len(katya["vectors"]) == 22
    assert phone_sync(client, DEVICE, [PAPA, KATYA])["peopleRev"] > rev


def test_a_stranger_from_another_system_can_be_added(client):
    register(client)
    join(client, DEVICE, "Redmi 12", [PAPA])
    vasya = person("eeeeeeeeeeee", "Вася", range(300, 305))
    join(client, OTHER, "Старый", [vasya])

    client.post("/people/merge", data={"person": vasya["id"], "into": "new"})
    assert "Вася" in [p["name"] for p in download(client, DEVICE)["people"]]


def test_a_namesake_cannot_be_added_as_new(client):
    register(client)
    join(client, DEVICE, "Redmi 12", [KATYA])
    their_katya = person("ffffffffffff", "Катя", range(200, 210))
    join(client, OTHER, "Старый", [their_katya])

    reply = client.post("/people/merge", data={"person": their_katya["id"], "into": "new"})
    assert "уже есть «Катя»" in reply.text


def test_a_joined_phone_teaches_the_house_directly(client):
    register(client)
    join(client, DEVICE, "Redmi 12", [PAPA])
    join(client, OTHER, "Кухня")
    lyosha = person("cccccccccccc", "Лёша", range(400, 412))

    assert phone_sync(client, OTHER, [lyosha])["upload"] == [lyosha["id"]]
    upload(client, OTHER, [lyosha])
    assert "Лёша" in [p["name"] for p in download(client, DEVICE)["people"]]


def test_a_rename_reaches_every_phone_that_knows_the_person(client):
    register(client)
    join(client, DEVICE, "Redmi 12", [PAPA, KATYA])
    join(client, OTHER, "Кухня")
    phone_sync(client, OTHER, [PAPA, KATYA])

    reply = client.post(
        "/people/rename", data={"person": PAPA["id"], "to": " Женя "}, follow_redirects=False
    )
    assert reply.status_code == 303
    for device in (DEVICE, OTHER):
        commands = phone_sync(client, device, [PAPA, KATYA])["commands"]
        assert [(c["kind"], c["person"], c["to"]) for c in commands] == [
            ("rename_person", PAPA["id"], "Женя")
        ]
    join(client, THIRD, "Спальня")
    assert "Женя" in [p["name"] for p in download(client, THIRD)["people"]]

    command = phone_sync(client, DEVICE, [PAPA, KATYA])["commands"][0]
    renamed = {**PAPA, "name": "Женя"}
    phone_sync(client, DEVICE, [renamed, KATYA], done=[{"id": command["id"], "ok": True}])
    assert "готово" in client.get("/people").text


def test_a_rename_made_on_the_phone_becomes_the_houses(client):
    register(client)
    join(client, DEVICE, "Redmi 12", [PAPA])
    rev = phone_sync(client, DEVICE, [PAPA])["peopleRev"]
    reply = phone_sync(client, DEVICE, [{**PAPA, "name": "Батя"}])
    assert reply["peopleRev"] > rev
    assert download(client, DEVICE)["people"][0]["name"] == "Батя"


def test_bad_or_taken_names_are_refused(client):
    register(client)
    join(client, DEVICE, "Redmi 12", [PAPA, KATYA])
    for bad in ("", "-Женя", "Ж" * 41, "Женя!", "катя"):
        reply = client.post("/people/rename", data={"person": PAPA["id"], "to": bad})
        assert reply.status_code in (200, 422)
    assert client.post("/people/rename", data={"person": "nobody", "to": "Вася"}).status_code == 200
    assert phone_sync(client, DEVICE, [PAPA, KATYA])["commands"] == []


def test_sets_of_another_face_model_are_not_mixed(client):
    register(client)
    join(client, DEVICE, "Redmi 12", [PAPA])
    client.post("/api/device/register", json={"id": OTHER, "name": "Другая камера"})
    body = {"device": OTHER, "faceModel": "Other/512", "people": []}
    client.post("/api/device/sync", json=body)
    assert download(client, OTHER)["people"] == []


def test_vectors_never_show_in_the_admin(client):
    register(client)
    join(client, DEVICE, "Redmi 12", [PAPA])
    page = client.get("/people").text
    assert str(round(vec(0)[1], 5)) not in page


def test_a_command_of_another_phone_cannot_be_closed(client):
    register(client)
    join(client, DEVICE, "Redmi 12", [PAPA])
    client.post("/people/rename", data={"person": PAPA["id"], "to": "Женя"})
    command = phone_sync(client, DEVICE, [PAPA])["commands"][0]

    join(client, OTHER, "Другой")
    phone_sync(client, OTHER, done=[{"id": command["id"], "ok": True}])
    assert phone_sync(client, DEVICE, [PAPA])["commands"] != []


def test_one_phone_shows_no_phone_names(client):
    register(client)
    join(client, DEVICE, "Redmi 12", [PAPA])
    assert "телефоны:" not in client.get("/people").text
    join(client, OTHER, "Кухня")
    assert "телефоны: Redmi 12" in client.get("/people").text
    assert sync(client).status_code == 200


def test_roles_reach_the_phones_and_follow_the_person(client):
    register(client)
    join(client, DEVICE, "Redmi 12", [PAPA, KATYA])
    # The owner's case: called «Женя» again, and «папа» as his role.
    client.post("/people/rename", data={"person": PAPA["id"], "to": "Женя"})
    reply = client.post("/people/roles", data={"person": PAPA["id"], "roles": " Папа ,  муж, папа"})
    assert reply.status_code == 200 and 'value="папа, муж"' in reply.text
    roles = phone_sync(client, DEVICE, [PAPA, KATYA])["roles"]
    assert roles == [{"person": PAPA["id"], "name": "Женя", "words": ["папа", "муж"]}]


def test_bad_roles_are_refused(client):
    register(client)
    join(client, DEVICE, "Redmi 12", [PAPA, KATYA])
    client.post("/people/roles", data={"person": PAPA["id"], "roles": "муж"})
    cases = {
        "катя": "уже имя человека",
        "муж": "уже у Папа",
        "папа": "уже имя человека",
        "мама!": "буквы, пробел и дефис",
        "а, б, в, г, д, е": "Не больше пяти",
    }
    for roles, error in cases.items():
        reply = client.post("/people/roles", data={"person": KATYA["id"], "roles": roles})
        assert error in reply.text, roles
    client.post("/people/roles", data={"person": PAPA["id"], "roles": ""})
    assert phone_sync(client, DEVICE, [PAPA, KATYA])["roles"] == []
