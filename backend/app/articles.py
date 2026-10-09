"""Article storage only. Classification/writing belongs to the external AI client."""
from datetime import datetime, timezone
import hashlib
import re
import unicodedata
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.article_schemas import ArticleCreate, ArticlePage, ArticleRead, ArticleSummary, ArticleUpdate, ArticleUpsert, Status
from app.auth import is_admin, require_write_access
from app.database import get_session
from app.models import Article
from app.repository import get_or_create_category

router = APIRouter(prefix="/api", tags=["articles"])
DB = Annotated[Session, Depends(get_session)]


def make_slug(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title).lower()
    value = re.sub(r"[^a-z0-9가-힣]+", "-", normalized)
    return value.strip("-")[:200].rstrip("-") or "article-" + hashlib.sha256(normalized.encode()).hexdigest()[:24]


def find_article(session: Session, article_id: UUID, lock: bool = False) -> Article:
    query = select(Article).where(Article.id == article_id)
    if lock:
        query = query.with_for_update(of=Article)
    article = session.scalars(query).one_or_none()
    if article is None:
        raise HTTPException(404, "Article not found")
    return article


def resolve_related(session: Session, ids: list[UUID], own_id: UUID | None = None) -> list[Article]:
    if own_id in ids:
        raise HTTPException(422, "An article cannot link to itself")
    records = list(session.scalars(select(Article).where(Article.id.in_(ids))).all()) if ids else []
    if len(records) != len(ids):
        raise HTTPException(422, "A related article does not exist")
    return records


def check_version(article: Article, expected: datetime | None) -> None:
    if expected is not None and article.updated_at != expected:
        raise HTTPException(409, "Article changed; read the latest version before updating")


def save(session: Session, article: Article) -> Article:
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "Slug or related article conflict; read the latest article and retry") from None
    session.refresh(article)
    return article


def new_article(payload: ArticleCreate, session: Session) -> Article:
    related = resolve_related(session, payload.related_articles)
    data = payload.model_dump(exclude={"category", "slug", "related_articles"})
    article = Article(**data, slug=payload.slug or make_slug(payload.title),
                      category_record=get_or_create_category(session, payload.category), related_records=related)
    session.add(article)
    return article


@router.post("/articles", dependencies=[Depends(require_write_access)], response_model=ArticleRead, status_code=201, operation_id="createArticle",
             summary="Save a complete Markdown article written by the client")
def create_article(payload: ArticleCreate, session: DB) -> Article:
    return save(session, new_article(payload, session))


@router.post("/articles/upsert", dependencies=[Depends(require_write_access)], response_model=ArticleRead, operation_id="upsertArticle",
             responses={201: {"model": ArticleRead, "description": "New article created"}},
             summary="Create by slug or update an explicitly chosen article; replace or append Markdown")
def upsert_article(payload: ArticleUpsert, session: DB, response: Response) -> Article:
    slug = payload.slug or make_slug(payload.title)
    # Serialize concurrent creations for one topic without external AI or fuzzy matching.
    lock_key = int.from_bytes(hashlib.sha256(("article-slug:" + slug).encode()).digest()[:8], "big", signed=True)
    session.execute(select(func.pg_advisory_xact_lock(lock_key)))
    if payload.target_article_id:
        article = find_article(session, payload.target_article_id, lock=True)
    else:
        article = session.scalars(select(Article).where(Article.slug == slug).with_for_update(of=Article)).one_or_none()
    if article is None:
        if payload.expected_updated_at is not None:
            raise HTTPException(409, "Expected an existing article but the slug was not found")
        response.status_code = 201
        create = ArticleCreate(**payload.model_dump(exclude={"target_article_id", "mode", "expected_updated_at"}))
        return save(session, new_article(create, session))
    check_version(article, payload.expected_updated_at)
    related = resolve_related(session, payload.related_articles, article.id) if "related_articles" in payload.model_fields_set else article.related_records
    content = payload.content_markdown
    if payload.mode == "append":
        content = article.content_markdown + "\n\n---\n\n" + content
        if len(content) > 200000:
            raise HTTPException(422, "Combined Markdown exceeds 200000 characters")
    values = payload.model_dump(include=payload.model_fields_set, exclude={"category", "slug", "related_articles", "target_article_id", "mode", "expected_updated_at", "content_markdown"})
    if payload.mode == "append":
        related = list({item.id: item for item in [*article.related_records, *related]}.values())
        if "tags" in values:
            values["tags"] = list(dict.fromkeys([*article.tags, *values["tags"]]))
        if len(related) > 20 or len(values.get("tags", article.tags)) > 30:
            raise HTTPException(422, "Combined tags or related articles exceed their limits")
    # Explicit target selection preserves its URL unless the client deliberately supplies a slug.
    article.slug = payload.slug or article.slug
    for name, value in values.items():
        setattr(article, name, value)
    article.content_markdown = content
    article.category_record = get_or_create_category(session, payload.category)
    article.related_records = related
    article.updated_at = datetime.now(timezone.utc)
    return save(session, article)


@router.get("/articles", response_model=ArticlePage, operation_id="listArticles",
            summary="Search titles, summaries and Markdown; filter category, tag, status or exact slug")
def list_articles(
    session: DB,
    request: Request,
    q: Annotated[str | None, Query(max_length=200)] = None,
    slug: Annotated[str | None, Query(max_length=200)] = None,
    category_id: UUID | None = None,
    tag: Annotated[str | None, Query(max_length=100)] = None,
    status: Status | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ArticlePage:
    filters = []
    if not is_admin(request):
        if status == "draft":
            raise HTTPException(401, "Admin login required for drafts")
        filters.append(Article.status == "published")
    if q and q.strip():
        value = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        filters.append(or_(*(field.ilike("%" + value + "%", escape="\\")
                             for field in [Article.title, Article.summary, Article.content_markdown])))
    if slug:
        filters.append(Article.slug == slug)
    if category_id:
        filters.append(Article.category_id == category_id)
    if tag:
        filters.append(Article.tags.contains([tag]))
    if status:
        filters.append(Article.status == status)
    total = session.scalar(select(func.count()).select_from(Article).where(*filters))
    items = session.scalars(select(Article).where(*filters).order_by(Article.updated_at.desc(), Article.id.desc())
                            .limit(limit).offset(offset)).all()
    return ArticlePage(items=[ArticleSummary.model_validate(item) for item in items], total=total, limit=limit, offset=offset)


@router.get("/articles/{article_id}", response_model=ArticleRead, operation_id="getArticle")
def get_article(article_id: UUID, session: DB, request: Request, status: Status | None = None) -> ArticleRead:
    article = find_article(session, article_id)
    admin = is_admin(request)
    if not admin and article.status != "published":
        raise HTTPException(401, "Admin login required for drafts")
    if status is not None and article.status != status:
        raise HTTPException(404, "Article not found")
    result = ArticleRead.model_validate(article)
    if not admin:
        result.related_articles = [item.id for item in article.related_records if item.status == "published"]
        result.source_reference = None  # Conversation references belong to the private CMS.
    return result


@router.patch("/articles/{article_id}", dependencies=[Depends(require_write_access)], response_model=ArticleRead, operation_id="updateArticle")
def update_article(article_id: UUID, payload: ArticleUpdate, session: DB) -> Article:
    article = find_article(session, article_id, lock=True)
    check_version(article, payload.expected_updated_at)
    related = resolve_related(session, payload.related_articles, article.id) if "related_articles" in payload.model_fields_set else None
    if "category" in payload.model_fields_set:
        article.category_record = get_or_create_category(session, payload.category)
    for name, value in payload.model_dump(exclude_unset=True, exclude={"category", "related_articles", "expected_updated_at"}).items():
        setattr(article, name, value)
    if related is not None:
        article.related_records = related
    article.updated_at = datetime.now(timezone.utc)
    return save(session, article)


@router.delete("/articles/{article_id}", dependencies=[Depends(require_write_access)], status_code=204, operation_id="deleteArticle")
def delete_article(article_id: UUID, session: DB) -> Response:
    session.delete(find_article(session, article_id, lock=True))
    session.commit()
    return Response(status_code=204)


@router.get("/tags", response_model=list[str], operation_id="listTags")
def list_tags(session: DB, request: Request) -> list[str]:
    query = select(func.jsonb_array_elements_text(Article.tags)).distinct()
    if not is_admin(request):
        query = query.where(Article.status == "published")
    return list(session.scalars(query.order_by(func.jsonb_array_elements_text(Article.tags))).all())
