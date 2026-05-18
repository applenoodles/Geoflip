import pytest
from app.web import create_app


@pytest.fixture()
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data == {"ok": True}


def test_index(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"GeoFlip" in resp.data


def test_create_app_importable():
    from app.web import create_app as _ca
    app = _ca()
    assert app is not None
