from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Category, Entry


def get_or_create_category(session: Session, name: str) -> Category:
    # DO NOTHING avoids updating existing rows and is safe under concurrent inserts.
    session.execute(
        insert(Category).values(id=uuid4(), name=name).on_conflict_do_nothing(index_elements=[Category.name])
    )
    return session.scalars(select(Category).where(Category.name == name)).one()


def find_entry(session: Session, entry_id: UUID) -> Entry:
    entry = session.get(Entry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    return entry
