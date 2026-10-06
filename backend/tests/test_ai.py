import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from app import ai


def test_batch_validation_and_atomic_save(client, payload):
    invalid = {**payload, "title": ""}
    assert client.post("/api/entries/batch", json={"entries": [payload, invalid]}).status_code == 422
    assert client.get("/api/entries").json()["total"] == 0
    assert client.get("/api/categories").json() == []
    response = client.post("/api/entries/batch", json={"entries": [payload, {**payload, "title": "두 번째 기록"}]})
    assert response.status_code == 201
    assert len(response.json()) == 2
    assert response.json()[0]["category_id"] == response.json()[1]["category_id"]


def test_missing_ai_key_keeps_manual_mode(client, payload, monkeypatch):
    monkeypatch.setattr(ai, "get_settings", lambda: SimpleNamespace(openai_api_key=None, enable_openai_classification=True))
    assert client.post("/api/entries/parse", json={"raw_text": payload["raw_text"]}).status_code == 503
    assert client.post("/api/entries", json=payload).status_code == 201


@pytest.fixture
def fake_settings(monkeypatch):
    monkeypatch.setattr(ai, "get_settings", lambda: SimpleNamespace(
        openai_api_key=SecretStr("test-secret-never-return"), openai_model="gpt-4o-mini", enable_openai_classification=True))


def test_structured_parse_does_not_save(client, payload, monkeypatch, fake_settings):
    entry = {key: payload[key] for key in ("category", "subcategory", "title", "summary", "tags", "importance")}
    def post(url, **kwargs):
        assert url == "https://api.openai.com/v1/chat/completions"
        assert kwargs["json"]["response_format"]["json_schema"]["strict"] is True
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "choices": [{"message": {"content": json.dumps({"entries": [entry, {**entry, "category": "기술", "title": "Cilium 학습"}]})}}]})
    monkeypatch.setattr(ai.httpx, "post", post)
    response = client.post("/api/entries/parse", json={"raw_text": payload["raw_text"], "occurred_at": payload["occurred_at"]})
    assert response.status_code == 200 and len(response.json()) == 2
    assert response.json()[0]["raw_text"] == payload["raw_text"]
    assert "test-secret-never-return" not in response.text
    assert client.get("/api/entries").json()["total"] == 0


@pytest.mark.parametrize("content", ["not JSON", '{"entries":[]}', '{"entries":[{"importance":99}]}'])
def test_invalid_ai_output_rejected(client, monkeypatch, fake_settings, content):
    monkeypatch.setattr(ai.httpx, "post", lambda url, **kwargs: httpx.Response(
        200, request=httpx.Request("POST", url), json={"choices": [{"message": {"content": content}}]}))
    assert client.post("/api/entries/parse", json={"raw_text": "일상"}).status_code == 502
    assert client.get("/api/entries").json()["total"] == 0


def test_provider_error_is_redacted(client, monkeypatch, fake_settings):
    monkeypatch.setattr(ai.httpx, "post", lambda url, **kwargs: httpx.Response(
        401, request=httpx.Request("POST", url), json={"error": "test-secret-never-return"}))
    response = client.post("/api/entries/parse", json={"raw_text": "일상"})
    assert response.status_code == 502
    assert "test-secret-never-return" not in response.text
