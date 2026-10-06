from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, String, Table, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Entry(Base):
    __tablename__ = "entries"
    __table_args__ = (
        CheckConstraint("importance BETWEEN 1 AND 5", name="ck_entries_importance"),
        CheckConstraint("jsonb_typeof(tags) = 'array'", name="ck_entries_tags_array"),
        Index("ix_entries_timeline", "occurred_at", "id"),
        Index("ix_entries_category_timeline", "category_id", "occurred_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    raw_text: Mapped[str] = mapped_column(Text)
    category_id: Mapped[UUID] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"))
    category_record: Mapped[Category] = relationship(lazy="joined")
    subcategory: Mapped[str | None] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    importance: Mapped[int] = mapped_column(default=3, server_default="3")

    @property
    def category(self) -> str:
        return self.category_record.name


article_links = Table(
    "article_links", Base.metadata,
    Column("article_id", ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True),
    Column("related_id", ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True),
    CheckConstraint("article_id <> related_id", name="ck_article_links_not_self"),
)


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'published')", name="ck_articles_status"),
        CheckConstraint("jsonb_typeof(tags) = 'array'", name="ck_articles_tags_array"),
        Index("ix_articles_updated", "updated_at", "id"),
        Index("ix_articles_category_updated", "category_id", "updated_at", "id"),
        Index("ix_articles_tags", "tags", postgresql_using="gin"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(200), unique=True)
    category_id: Mapped[UUID] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"))
    category_record: Mapped[Category] = relationship(lazy="joined")
    subcategory: Mapped[str | None] = mapped_column(String(100))
    summary: Mapped[str] = mapped_column(Text)
    content_markdown: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    source_type: Mapped[str] = mapped_column(String(100), default="manual")
    source_reference: Mapped[str | None] = mapped_column(String(2000))
    status: Mapped[str] = mapped_column(String(20), default="draft", server_default="draft")
    related_records: Mapped[list["Article"]] = relationship(
        secondary=article_links,
        primaryjoin=id == article_links.c.article_id,
        secondaryjoin=id == article_links.c.related_id,
        lazy="selectin", passive_deletes=True,
    )

    @property
    def category(self) -> str:
        return self.category_record.name

    @property
    def related_articles(self) -> list[UUID]:
        return sorted((article.id for article in self.related_records), key=str)
