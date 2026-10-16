"""Add Markdown articles and related links; preserve legacy entries.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "articles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(200), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column("subcategory", sa.String(100), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("content_markdown", sa.Text(), nullable=False),
        sa.Column("tags", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("source_type", sa.String(100), nullable=False),
        sa.Column("source_reference", sa.String(2000), nullable=True),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False),
        sa.CheckConstraint("status IN ('draft', 'published')", name="ck_articles_status"),
        sa.CheckConstraint("jsonb_typeof(tags) = 'array'", name="ck_articles_tags_array"),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("slug"),
    )
    op.create_index("ix_articles_updated", "articles", ["updated_at", "id"])
    op.create_index("ix_articles_category_updated", "articles", ["category_id", "updated_at", "id"])
    op.create_index("ix_articles_tags", "articles", ["tags"], postgresql_using="gin")
    op.create_table(
        "article_links",
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("related_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("article_id <> related_id", name="ck_article_links_not_self"),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["related_id"], ["articles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("article_id", "related_id"),
    )


def downgrade() -> None:
    op.drop_table("article_links")
    op.drop_table("articles")
