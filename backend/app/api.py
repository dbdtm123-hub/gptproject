from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import AwareDatetime
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_session
from app.models import Article, Category, Entry
from app.repository import find_entry, get_or_create_category
from app.schemas import CategoryRead, EntryCreate, EntryPage, EntryRead, EntryUpdate
from app.schemas import EntryBatch, ParseRequest
from app.ai import parse_entries
from app.auth import is_admin, require_admin, require_write_access

router = APIRouter(prefix="/api")
DB = Annotated[Session, Depends(get_session)]


@router.get("/capabilities", dependencies=[Depends(require_admin)], tags=["configuration"])
def capabilities() -> dict[str, bool]:
    from app.config import get_settings
    settings = get_settings()
    return {"server_ai_classification": bool(settings.enable_openai_classification and
            settings.openai_api_key and settings.openai_api_key.get_secret_value())}


@router.post("/entries/parse", dependencies=[Depends(require_write_access)], response_model=list[EntryCreate], tags=["entries"])
def classify_entries(payload: ParseRequest, session: DB) -> list[EntryCreate]:
    categories = list(session.scalars(select(Category.name).order_by(Category.name)).all())
    return parse_entries(payload, categories)


@router.post("/entries/batch", dependencies=[Depends(require_write_access)], response_model=list[EntryRead], status_code=201, tags=["entries"])
@router.post("/ingest/batch", dependencies=[Depends(require_write_access)], response_model=list[EntryRead], status_code=201, tags=["ingest"],
             operation_id="ingestBatch", summary="Save up to 20 already classified entries atomically")
def create_batch(payload: EntryBatch, session: DB) -> list[Entry]:
    entries = []
    for candidate in payload.entries:
        category = get_or_create_category(session, candidate.category)
        entry = Entry(**candidate.model_dump(exclude={"category"}), category_record=category)
        session.add(entry)
        entries.append(entry)
    session.commit()
    for entry in entries:
        session.refresh(entry)
    return entries


@router.post("/entries", dependencies=[Depends(require_write_access)], response_model=EntryRead, status_code=status.HTTP_201_CREATED, tags=["entries"])
@router.post("/ingest", dependencies=[Depends(require_write_access)], response_model=EntryRead, status_code=201, tags=["ingest"],
             operation_id="ingestEntry", summary="Validate and save an already classified entry")
def create_entry(payload: EntryCreate, session: DB) -> Entry:
    data = payload.model_dump(exclude={"category"})
    entry = Entry(**data, category_record=get_or_create_category(session, payload.category))
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


@router.get("/entries", dependencies=[Depends(require_admin)], response_model=EntryPage, tags=["entries"])
def list_entries(
    session: DB,
    category_id: UUID | None = None,
    from_at: AwareDatetime | None = None,
    to_at: AwareDatetime | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EntryPage:
    if from_at is not None and to_at is not None and from_at >= to_at:
        raise HTTPException(status_code=422, detail="from_at must be earlier than to_at")
    filters = []
    if category_id is not None:
        filters.append(Entry.category_id == category_id)
    if from_at is not None:
        filters.append(Entry.occurred_at >= from_at)
    if to_at is not None:
        filters.append(Entry.occurred_at < to_at)
    total = session.scalar(select(func.count()).select_from(Entry).where(*filters))
    entries = session.scalars(
        select(Entry).where(*filters).order_by(Entry.occurred_at.desc(), Entry.id.desc()).limit(limit).offset(offset)
    ).all()
    return EntryPage(items=[EntryRead.model_validate(entry) for entry in entries], total=total, limit=limit, offset=offset)


@router.get("/entries/{entry_id}", dependencies=[Depends(require_admin)], response_model=EntryRead, tags=["entries"])
def get_entry(entry_id: UUID, session: DB) -> Entry:
    return find_entry(session, entry_id)


@router.patch("/entries/{entry_id}", dependencies=[Depends(require_write_access)], response_model=EntryRead, tags=["entries"])
def update_entry(entry_id: UUID, payload: EntryUpdate, session: DB) -> Entry:
    entry = find_entry(session, entry_id)
    if "category" in payload.model_fields_set:
        entry.category_record = get_or_create_category(session, payload.category)
    for name, value in payload.model_dump(exclude_unset=True, exclude={"category"}).items():
        setattr(entry, name, value)
    session.commit()
    session.refresh(entry)
    return entry


@router.delete("/entries/{entry_id}", dependencies=[Depends(require_write_access)], status_code=status.HTTP_204_NO_CONTENT, tags=["entries"])
def delete_entry(entry_id: UUID, session: DB) -> Response:
    session.delete(find_entry(session, entry_id))
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/categories", response_model=list[CategoryRead], tags=["categories"],
            operation_id="listIngestCategories", summary="List existing categories before classifying")
@router.get("/ingest/categories", response_model=list[CategoryRead], tags=["ingest"],
            summary="Compatibility alias for the category list")
def list_categories(session: DB, request: Request) -> list[Category]:
    query = select(Category)
    if not is_admin(request):
        query = query.where(select(Article.id).where(Article.category_id == Category.id,
                                                   Article.status == "published").exists())
    return list(session.scalars(query.order_by(Category.name, Category.id)).all())
