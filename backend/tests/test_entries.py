from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Category, Entry
from app.repository import get_or_create_category


def create(client, payload, **changes):
    response = client.post("/api/entries", json={**payload, **changes})
    assert response.status_code == 201, response.text
    return response.json()


def test_crud(client, payload):
    entry = create(client, payload)
    assert entry["category"] == "운동"
    assert entry["occurred_at"] == "2026-10-07T09:00:00Z"
    assert entry["created_at"]
    assert client.get(f"/api/entries/{entry['id']}").json() == entry
    response = client.patch(f"/api/entries/{entry['id']}", json={"title": "개인 기록", "subcategory": None})
    assert response.status_code == 200
    assert response.json()["title"] == "개인 기록"
    assert response.json()["subcategory"] is None
    response = client.delete(f"/api/entries/{entry['id']}")
    assert response.status_code == 204 and response.content == b""
    assert client.get(f"/api/entries/{entry['id']}").status_code == 404
    assert client.delete(f"/api/entries/{entry['id']}").status_code == 404
    assert client.get("/api/entries").json()["total"] == 0
    # Categories survive entry deletion.
    assert len(client.get("/api/categories").json()) == 1


def test_reuse_category_and_normalize_tags(client, payload):
    first = create(client, payload)
    second = create(client, payload, category="  운동  ", tags=[" 60kg ", "60kg", "5x5"])
    assert first["category_id"] == second["category_id"]
    assert second["tags"] == ["60kg", "5x5"]
    assert len(client.get("/api/categories").json()) == 1


def test_timeline_filters_pagination_and_category_update(client, payload):
    older = create(client, payload, occurred_at="2026-10-06T09:00:00Z")
    newer = create(client, payload, category="기술", occurred_at="2026-10-07T09:00:00Z")
    page = client.get("/api/entries", params={"limit": 1}).json()
    assert page["total"] == 2 and page["items"][0]["id"] == newer["id"]
    assert client.get("/api/entries", params={"limit": 1, "offset": 1}).json()["items"][0]["id"] == older["id"]
    filtered = client.get("/api/entries", params={"category_id": older["category_id"]}).json()
    assert filtered["total"] == 1 and filtered["items"][0]["id"] == older["id"]
    period = client.get("/api/entries", params={"from_at": "2026-10-06T09:00:00Z", "to_at": "2026-10-07T09:00:00Z"}).json()
    assert period["total"] == 1 and period["items"][0]["id"] == older["id"]
    assert client.get("/api/entries", params={"category_id": str(uuid4())}).json()["total"] == 0
    response = client.patch(f"/api/entries/{older['id']}", json={"category": "기술", "importance": 5})
    assert response.status_code == 200
    assert response.json()["category_id"] == newer["category_id"]
    assert response.json()["importance"] == 5


def test_default_time_and_empty_list(client, payload):
    assert client.get("/api/entries").json() == {"items": [], "total": 0, "limit": 20, "offset": 0}
    del payload["occurred_at"]
    del payload["tags"]
    del payload["importance"]
    entry = create(client, payload)
    assert entry["occurred_at"].endswith("Z") and entry["tags"] == [] and entry["importance"] == 3


@pytest.mark.parametrize("changes", [
    {"category": " "}, {"title": ""}, {"raw_text": " "}, {"summary": ""},
    {"importance": 0}, {"importance": 6}, {"importance": True},
    {"occurred_at": "2026-10-07T18:00:00"}, {"tags": [""]}, {"tags": "tag"},
    {"unexpected": "field"}, {"title": "a" * 201}, {"tags": ["a"] * 31},
])
def test_invalid_create_has_no_writes(client, payload, changes):
    assert client.post("/api/entries", json={**payload, **changes}).status_code == 422
    assert client.get("/api/entries").json()["total"] == 0
    assert client.get("/api/categories").json() == []


@pytest.mark.parametrize("changes", [{}, {"title": None}, {"category": None}, {"tags": None}, {"importance": 7}])
def test_invalid_update(client, payload, changes):
    entry = create(client, payload)
    assert client.patch(f"/api/entries/{entry['id']}", json=changes).status_code == 422
    assert client.get(f"/api/entries/{entry['id']}").json() == entry


@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": 101}, {"offset": -1}, {"category_id": "bad"},
    {"from_at": "2026-10-07T18:00:00"},
    {"from_at": "2026-10-08T00:00:00Z", "to_at": "2026-10-07T00:00:00Z"},
])
def test_invalid_filters(client, params):
    assert client.get("/api/entries", params=params).status_code == 422


def test_not_found_and_invalid_id(client):
    missing = str(uuid4())
    assert client.get(f"/api/entries/{missing}").status_code == 404
    assert client.patch(f"/api/entries/{missing}", json={"title": "수정"}).status_code == 404
    assert client.get("/api/entries/not-a-uuid").status_code == 422


def test_health_and_cors(client):
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").json() == {"status": "ready"}
    headers = {"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"}
    response = client.options("/api/entries", headers=headers)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    headers["Origin"] = "https://untrusted.example"
    response = client.options("/api/entries", headers=headers)
    assert response.status_code == 400 and "access-control-allow-origin" not in response.headers


def test_database_enforces_importance(client, payload, db_session):
    entry = create(client, payload)
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            record = db_session.get(Entry, entry["id"])
            record.importance = 99
            db_session.flush()


def test_concurrent_category_creation(engine):
    name = f"concurrent-{uuid4()}"

    def insert_category(_):
        with Session(engine) as session:
            category = get_or_create_category(session, name)
            category_id = category.id
            session.commit()
            return category_id

    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            ids = list(executor.map(insert_category, range(4)))
        assert len(set(ids)) == 1
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(Category).where(Category.name == name)) == 1
    finally:
        with Session(engine) as session:
            session.execute(delete(Category).where(Category.name == name))
            session.commit()
