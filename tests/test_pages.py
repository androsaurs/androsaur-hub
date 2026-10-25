def test_index_renders(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Items" in resp.text


def test_create_item_via_form(client):
    resp = client.post("/items", data={"title": "Купить молоко"}, follow_redirects=False)
    assert resp.status_code == 303

    page = client.get("/")
    assert "Купить молоко" in page.text
