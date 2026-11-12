"""Add private reports and append-only revisions; no article or user changes."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "operation_reports",
        sa.Column("report_id", sa.String(100), primary_key=True),
        *[sa.Column(name, sa.String(200), nullable=False) for name in ["title", "project", "component", "author"]],
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        *[sa.Column(name, sa.Text(), nullable=False) for name in ["summary", "environment", "findings", "changes", "remaining_issues", "next_actions", "raw_markdown"]],
        *[sa.Column(name, JSONB(), nullable=False) for name in ["metrics_before", "metrics_after", "related_report_ids", "related_article_ids"]],
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("status IN ('SUCCESS', 'PARTIAL', 'BLOCKED', 'FAILED')", name="ck_reports_status"),
        sa.CheckConstraint("source IN ('Codex', 'ChatGPT', 'Manual')", name="ck_reports_source"),
        sa.CheckConstraint("revision >= 1", name="ck_reports_revision"),
    )
    op.create_index("ix_reports_created", "operation_reports", ["created_at", "report_id"])
    op.create_index("ix_reports_project_component", "operation_reports", ["project", "component", "created_at", "report_id"])
    op.create_table(
        "report_revisions",
        sa.Column("report_id", sa.String(100), sa.ForeignKey("operation_reports.report_id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("snapshot", JSONB(), nullable=False),
    )
    op.execute("""
        CREATE FUNCTION autolog_report_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Report revisions are append-only'; END $$;
        CREATE TRIGGER report_revisions_immutable BEFORE UPDATE OR DELETE ON report_revisions
        FOR EACH ROW EXECUTE FUNCTION autolog_report_immutable();
        CREATE FUNCTION autolog_report_identity() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.report_id IS DISTINCT FROM OLD.report_id OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'Report identity and creation timestamp are immutable';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER report_identity_immutable BEFORE UPDATE ON operation_reports
        FOR EACH ROW EXECUTE FUNCTION autolog_report_identity();
    """)


def downgrade():
    # Deliberately refuse accidental data loss. Roll applications back while retaining these additive tables.
    if op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM operation_reports)")).scalar():
        raise RuntimeError("Reports exist: retain schema on application rollback; export and obtain explicit deletion approval first")
    op.drop_table("report_revisions")
    op.drop_table("operation_reports")
    op.execute("DROP FUNCTION autolog_report_immutable(); DROP FUNCTION autolog_report_identity()")
