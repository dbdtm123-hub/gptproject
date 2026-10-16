from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import ai
from app.article_schemas import ArticleRead, ArticleUpsert
from app.articles import upsert_article
from app.models import Article, Category


@pytest.fixture
def article():
    return {
        "title": "OpenStack 위 Kubernetes에 NFS 동적 스토리지 구성하기",
        "slug": "openstack-kubernetes-nfs",
        "category": "홈랩", "subcategory": "Kubernetes",
        "summary": "NFS provisioner의 ContainerCreating 문제를 해결하고 동적 PVC를 구성했다.",
        "content_markdown": "# NFS 동적 스토리지\n\n## 구성 환경\n- OpenStack\n- Ubuntu 24.04\n\n## 해결\n```bash\nsudo apt install nfs-common\n```\n\nPVC → StorageClass → NFS Provisioner → NFS Server",
        "tags": ["OpenStack", "Kubernetes", "NFS"], "source_type": "chatgpt",
        "source_reference": "conversation:test", "status": "published",
    }


def test_article_crud_without_openai(client, article, monkeypatch):
    monkeypatch.setattr(ai.httpx, "post", lambda *args, **kwargs: pytest.fail("Article API called OpenAI"))
    response = client.post("/api/articles", json=article)
    assert response.status_code == 201
    saved = response.json()
    assert saved["content_markdown"] == article["content_markdown"]
    assert saved["created_at"] and saved["updated_at"]
    assert client.get("/api/articles/" + saved["id"]).json() == saved
    assert client.get("/api/categories").json()[0]["id"] == saved["category_id"]
    assert client.get("/api/tags").json() == ["Kubernetes", "NFS", "OpenStack"]
    assert "content_markdown" not in client.get("/api/articles").json()["items"][0]
    updated = client.patch("/api/articles/" + saved["id"], json={"title": "수정된 제목", "status": "draft",
                          "expected_updated_at": saved["updated_at"]})
    assert updated.status_code == 200 and updated.json()["status"] == "draft"
    assert updated.json()["updated_at"] != saved["updated_at"]
    assert client.patch("/api/articles/" + saved["id"], json={"title": "덮어쓰기", "expected_updated_at": saved["updated_at"]}).status_code == 409
    assert client.delete("/api/articles/" + saved["id"]).status_code == 204
    assert client.get("/api/articles/" + saved["id"]).status_code == 404
    assert client.get("/api/tags").json() == []


def test_upsert_replace_append_and_preserve_omitted_metadata(client, article):
    created = client.post("/api/articles/upsert", json=article)
    assert created.status_code == 201
    old = created.json()
    replacement = {**article, "content_markdown": "# 완성된 새 본문", "expected_updated_at": old["updated_at"]}
    replaced = client.post("/api/articles/upsert", json=replacement)
    assert replaced.status_code == 200 and replaced.json()["id"] == old["id"]
    assert replaced.json()["content_markdown"] == "# 완성된 새 본문"
    append = {"target_article_id": old["id"], "mode": "append", "title": article["title"],
              "category": article["category"], "summary": article["summary"],
              "content_markdown": "## 추가 실험\nAnsible로 nfs-common 설치", "tags": ["Ansible"]}
    continued = client.post("/api/articles/upsert", json=append).json()
    assert continued["id"] == old["id"] and continued["slug"] == old["slug"]
    assert continued["content_markdown"] == "# 완성된 새 본문\n\n---\n\n" + append["content_markdown"]
    assert continued["status"] == "published" and continued["source_type"] == "chatgpt"
    assert continued["subcategory"] == "Kubernetes"
    assert "Ansible" in continued["tags"] and "NFS" in continued["tags"]
    assert client.post("/api/articles/upsert", json=replacement).status_code == 409
    assert client.get("/api/articles").json()["total"] == 1


def test_generated_slug_is_stable_for_non_ascii_titles(client, article):
    payload = {**article, "title": "💻技術ノート"}
    payload.pop("slug")
    first = client.post("/api/articles/upsert", json=payload)
    second = client.post("/api/articles/upsert", json=payload)
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]


def test_append_limit_failure_preserves_original_article(client, article):
    saved = client.post("/api/articles", json=article).json()
    response = client.post("/api/articles/upsert", json={**article, "mode": "append", "content_markdown": "a" * 200000})
    assert response.status_code == 422
    assert client.get("/api/articles/" + saved["id"]).json()["content_markdown"] == article["content_markdown"]


def test_markdown_indentation_and_newlines_are_preserved(client, article):
    content = "\n    kubectl get pvc\n\n"
    saved = client.post("/api/articles", json={**article, "content_markdown": content}).json()
    assert saved["content_markdown"] == content
    continued = client.post("/api/articles/upsert", json={**article, "mode": "append", "content_markdown": "    helm list\n"}).json()
    assert continued["content_markdown"] == content + "\n\n---\n\n    helm list\n"


def test_article_search_filters_and_slug_conflict(client, article):
    first = client.post("/api/articles", json=article).json()
    second = client.post("/api/articles", json={**article, "slug": "other-topic", "category": "기술", "tags": ["Cilium"],
                         "status": "draft", "content_markdown": "# Cilium\n고유 검색어 50%_완료"}).json()
    assert client.post("/api/articles", json=article).status_code == 409
    for params, id in [({"q": "nfs-common"}, first["id"]), ({"q": "50%_완료"}, second["id"]),
                       ({"category_id": first["category_id"]}, first["id"]), ({"tag": "NFS"}, first["id"]),
                       ({"status": "draft"}, second["id"]), ({"slug": "other-topic"}, second["id"])]:
        page = client.get("/api/articles", params=params).json()
        assert page["total"] == 1 and page["items"][0]["id"] == id
    assert client.get("/api/articles", params={"limit": 1, "offset": 1}).json()["total"] == 2
    assert client.get("/api/articles", params={"status": "unknown"}).status_code == 422


def test_related_articles_validation_and_delete_cleanup(client, article):
    first = client.post("/api/articles", json=article).json()
    second = client.post("/api/articles", json={**article, "slug": "related-topic", "related_articles": [first["id"]]}).json()
    assert second["related_articles"] == [first["id"]]
    assert client.patch("/api/articles/" + second["id"], json={"related_articles": [second["id"]]}).status_code == 422
    assert client.post("/api/articles", json={**article, "slug": "invalid-link", "related_articles": [str(uuid4())]}).status_code == 422
    assert client.delete("/api/articles/" + first["id"]).status_code == 204
    assert client.get("/api/articles/" + second["id"]).json()["related_articles"] == []


@pytest.mark.parametrize("change", [{"content_markdown": " "}, {"slug": "../../bad"}, {"status": "public"},
                                    {"category": " "}, {"tags": [""]}, {"extra": "unknown"}, {"content_markdown": "a" * 200001}])
def test_article_validation_is_atomic(client, article, change):
    assert client.post("/api/articles", json={**article, **change}).status_code == 422
    assert client.get("/api/articles").json()["total"] == 0
    assert client.get("/api/categories").json() == []


@pytest.mark.parametrize("path", ["/api/articles", "/api/articles/upsert"])
def test_article_token_auth(client, article, path):
    headers = dict(client.headers); cookies = dict(client.cookies); client.cookies.clear(); client.headers.pop("authorization", None)
    try:
        response = client.get(path) if path == "/api/tags" else client.post(path, json=article)
        assert response.status_code == 401
    finally:
        client.headers.update(headers); client.cookies.update(cookies)


def test_concurrent_upsert_one_topic_and_append_without_lost_updates(engine, article):
    marker = uuid4().hex
    payload = {**article, "slug": "concurrent-" + marker, "category": "concurrent-" + marker}
    def write(index):
        with Session(engine) as session:
            response = Response()
            saved = upsert_article(ArticleUpsert(**{**payload, "mode": "append", "content_markdown": "## Part " + str(index)}), session, response)
            return ArticleRead.model_validate(saved).id
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(write, range(8)))
        assert len(set(ids)) == 1
        with Session(engine) as session:
            saved = session.get(Article, ids[0])
            assert all("## Part " + str(index) in saved.content_markdown for index in range(8))
    finally:
        with engine.begin() as connection:
            connection.execute(delete(Article).where(Article.slug == payload["slug"]))
            connection.execute(delete(Category).where(Category.name == payload["category"]))
