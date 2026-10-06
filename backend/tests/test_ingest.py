from types import SimpleNamespace
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app import ai, auth
from app.database import get_session
from app.main import app
from app.models import Entry


@pytest.mark.parametrize("authorization", [None, "Bearer invalid", "Basic dGVzdDp0ZXN0"])
@pytest.mark.parametrize("path", ["/api/ingest", "/api/ingest/batch", "/api/entries"])
def test_token_required(client, payload, authorization, path):
    headers = dict(client.headers)
    client.headers.pop("authorization", None)
    try:
        body = {"entries": [payload]} if path.endswith("/batch") else payload
        response = client.post(path, json=body, headers={"Authorization": authorization} if authorization else {})
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
    finally:
        client.headers.update(headers)
    assert client.get("/api/entries").json()["total"] == 0


def test_missing_server_token_fails_closed(client, payload, monkeypatch):
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(api_token=None))
    assert client.post("/api/ingest", json=payload).status_code == 503
    assert client.get("/api/categories").status_code == 503
    assert client.get("/health/live").status_code == 200


def test_external_ingest_without_openai(client, payload, monkeypatch):
    monkeypatch.setattr(ai, "get_settings", lambda: SimpleNamespace(
        enable_openai_classification=False, openai_api_key=None))
    def forbidden(*args, **kwargs):
        pytest.fail("Structured ingest must never call OpenAI")
    monkeypatch.setattr(ai.httpx, "post", forbidden)
    saved = client.post("/api/ingest", json=payload)
    assert saved.status_code == 201
    data = saved.json()
    assert data["occurred_at"] == "2026-10-07T09:00:00Z"
    assert client.get("/api/entries/" + data["id"]).json()["raw_text"] == payload["raw_text"]
    assert client.get("/api/ingest/categories").json()[0]["name"] == payload["category"]
    assert client.get("/api/categories").json()[0]["name"] == payload["category"]
    assert client.post("/api/entries/parse", json={"raw_text": "오늘 운동"}).status_code == 503
    assert client.post("/api/entries", json=payload).status_code == 201
    assert client.patch("/api/entries/" + data["id"], json={"title": "수정한 기록"}).status_code == 200
    assert client.post("/api/ingest/batch", json={"entries": [payload]}).status_code == 201
    assert client.delete("/api/entries/" + data["id"]).status_code == 204


def test_ingest_batch_validation_is_atomic(client, payload):
    bad = {**payload, "occurred_at": "2026-10-07T09:00:00"}
    assert client.post("/api/ingest/batch", json={"entries": [payload, bad]}).status_code == 422
    assert client.get("/api/entries").json()["total"] == 0
    assert client.get("/api/categories").json() == []
    saved = client.post("/api/ingest/batch", json={"entries": [payload, {**payload, "title": "다른 기록"}]})
    assert saved.status_code == 201 and len(saved.json()) == 2
    assert saved.json()[0]["category_id"] == saved.json()[1]["category_id"]


def test_batch_database_failure_rolls_back_entries_and_categories(client, db_session, payload):
    original = app.dependency_overrides[get_session]
    def request_session():
        # Mirror production request-scoped Session cleanup, within the isolated test transaction.
        with Session(bind=db_session.bind, join_transaction_mode="create_savepoint") as session:
            yield session
    app.dependency_overrides[get_session] = request_session
    inserts = []
    def fail_second_insert(mapper, connection, target):
        inserts.append(target.title)
        if len(inserts) == 2:
            assert connection.scalar(select(func.count()).select_from(Entry)) == 1
            target.title = None  # Actual PostgreSQL NOT NULL failure after the first INSERT.
    event.listen(Entry, "before_insert", fail_second_insert)
    try:
        with TestClient(app, raise_server_exceptions=False,
                        headers={"Authorization": "Bearer " + os.environ["API_TOKEN"]}) as failing_client:
            response = failing_client.post("/api/ingest/batch", json={"entries": [
                {**payload, "category": "첫 카테고리"},
                {**payload, "category": "두 번째 카테고리", "title": "두 번째 기록"},
            ]})
            assert response.status_code == 500
        assert len(inserts) == 2
        assert client.get("/api/entries").json()["total"] == 0
        assert client.get("/api/categories").json() == []
    finally:
        event.remove(Entry, "before_insert", fail_second_insert)
        app.dependency_overrides[get_session] = original


def test_ingest_reuses_existing_category_and_creates_new_category(client, payload):
    existing = client.post("/api/entries", json=payload).json()
    categories = client.get("/api/categories").json()
    assert categories[0]["id"] == existing["category_id"]
    response = client.post("/api/ingest/batch", json={"entries": [
        {**payload, "category": "  " + payload["category"] + "  "},
        {**payload, "category": "새로운 카테고리"},
    ]})
    assert response.status_code == 201
    reused, created = response.json()
    assert reused["category_id"] == existing["category_id"]
    assert created["category_id"] != existing["category_id"]
    assert len(client.get("/api/categories").json()) == 2


@pytest.mark.parametrize("changes", [{"title": " "}, {"tags": [""]}, {"importance": 6}, {"unknown": "field"}])
def test_ingest_rejects_invalid_data(client, payload, changes):
    assert client.post("/api/ingest", json={**payload, **changes}).status_code == 422
    assert client.get("/api/entries").json()["total"] == 0


def test_disabled_ai_never_calls_provider_even_with_key(client, payload, monkeypatch):
    monkeypatch.setattr(ai, "get_settings", lambda: SimpleNamespace(
        enable_openai_classification=False, openai_api_key=SecretStr("fake-test-key")))
    monkeypatch.setattr(ai.httpx, "post", lambda *args, **kwargs: pytest.fail("Disabled provider called"))
    assert client.post("/api/entries/parse", json={"raw_text": payload["raw_text"]}).status_code == 503


def test_action_schema_is_public_scoped_and_secured(client):
    schema = client.get("/openapi-action.json", headers={"Authorization": "Bearer invalid"}).json()
    assert set(schema["paths"]) == {"/api/articles", "/api/articles/upsert", "/api/articles/{article_id}", "/api/categories", "/api/tags"}
    assert schema["components"]["securitySchemes"]["ApiToken"] == {"type": "http", "scheme": "bearer"}
    for path in schema["paths"].values():
        for operation in path.values():
            assert operation["security"] == [{"ApiToken": []}]
    assert schema["paths"]["/api/articles/upsert"]["post"]["operationId"] == "upsertArticle"
    assert "API_TOKEN" not in str(schema)


def test_checked_in_action_schema_matches_backend(client):
    expected = json.loads((Path(__file__).parents[2] / "docs/actions.openapi.json").read_text())
    actual = client.get("/openapi-action.json").json()
    expected.pop("servers")
    actual.pop("servers")  # Deployment hostname is intentionally environment-specific.
    assert expected == actual


@pytest.mark.parametrize("authorization", [None, "Bearer invalid", "Basic dGVzdDp0ZXN0"])
def test_category_api_requires_token(client, authorization):
    headers = dict(client.headers)
    client.headers.pop("authorization", None)
    try:
        assert client.get("/api/categories", headers={"Authorization": authorization} if authorization else {}).status_code == 401
    finally:
        client.headers.update(headers)
