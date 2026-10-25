def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_create_and_list_items(client):
    created = client.post("/api/items", json={"title": "example"})
    assert created.status_code == 201
    body = created.json()
    assert body["title"] == "example"
    assert body["id"] >= 1

    listing = client.get("/api/items")
    assert listing.status_code == 200
    titles = [item["title"] for item in listing.json()]
    assert "example" in titles


def test_create_item_validation(client):
    resp = client.post("/api/items", json={"title": ""})
    assert resp.status_code == 422
