DEVICE = "0f6c9a1e-7b2d-4c55-9a51-3d2f0c1b8e77"

DIM = {
    "module": "androsaur",
    "key": "dim_after_s",
    "type": "int",
    "title": "Затемнение экрана, когда никого нет",
    "unit": "с",
    "min": 5,
    "max": 3600,
    "default": 30,
}


def register(client, settings=None):
    return client.post(
        "/api/device/register",
        json={
            "id": DEVICE,
            "name": "Redmi 12",
            "model": "Xiaomi 23053RN02Y",
            "appVersion": "0.1",
            "settings": [DIM] if settings is None else settings,
        },
    )


def sync(client, **report):
    return client.post("/api/device/sync", json={"device": DEVICE, **report})


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_registration_publishes_the_settings_and_returns_no_overrides(client):
    reply = register(client)
    assert reply.status_code == 200
    assert reply.json()["values"] == {}

    page = client.get("/")
    assert "Затемнение экрана, когда никого нет" in page.text
    assert "по умолчанию" in page.text
    assert "Redmi 12" in page.text


def test_a_value_set_in_the_hub_reaches_the_device_with_a_new_revision(client):
    first = register(client).json()["rev"]

    saved = client.post(
        "/settings/androsaur/dim_after_s", data={"value": "45"}, follow_redirects=False
    )
    assert saved.status_code == 303

    config = sync(client, applied=first).json()
    assert config["values"] == {"androsaur": {"dim_after_s": 45}}
    assert config["rev"] > first
    assert "ждёт устройство" in client.get("/").text

    sync(client, applied=config["rev"])
    assert "настройки применены" in client.get("/").text


def test_reset_brings_the_module_default_back(client):
    register(client)
    client.post("/settings/androsaur/dim_after_s", data={"value": "45"})
    client.post("/settings/androsaur/dim_after_s/reset")

    config = sync(client).json()
    assert config["values"] == {}


def test_out_of_range_and_garbage_are_refused(client):
    register(client)
    for bad in ("2", "99999", "полминуты"):
        reply = client.post("/settings/androsaur/dim_after_s", data={"value": bad})
        assert reply.status_code == 200
        assert 'role="alert"' in reply.text
    config = sync(client).json()
    assert config["values"] == {}


def test_unknown_device_is_told_to_register(client):
    reply = sync(client)
    assert reply.status_code == 404


def test_a_schema_with_a_default_out_of_range_is_refused(client):
    reply = register(client, [{**DIM, "default": 1}])
    assert reply.status_code == 422
