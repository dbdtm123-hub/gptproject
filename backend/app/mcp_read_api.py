"""Opt-in token-protected private reads for the personal MCP integration.

Existing public routes keep their original cookie-only draft policy.
"""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.article_schemas import ArticlePage, ArticleRead, Status
from app.articles import DB, query_articles, read_article
from app.auth import require_api_token
from app.config import get_settings


def require_private_reads():
    if not get_settings().mcp_private_reads_enabled:
        raise HTTPException(503, "Private integration reads are disabled")


router = APIRouter(prefix="/api/integrations", tags=["integration-private-reads"],
                   dependencies=[Depends(require_private_reads), Depends(require_api_token)])


@router.get("/articles", response_model=ArticlePage)
def private_articles(
    session: DB,
    q: Annotated[str | None, Query(max_length=200)] = None,
    slug: Annotated[str | None, Query(max_length=200)] = None,
    category_id: UUID | None = None,
    tag: Annotated[str | None, Query(max_length=100)] = None,
    status: Status | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return query_articles(session, True, q, slug, category_id, tag, status, limit, offset)


@router.get("/articles/{article_id}", response_model=ArticleRead)
def private_article(article_id: UUID, session: DB):
    return read_article(session, article_id, True)
