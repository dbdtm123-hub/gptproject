"""Private report API: stable IDs, compare-and-swap upserts and immutable audit snapshots."""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, cast, func, or_, select, text
from sqlalchemy.orm import Session

from app.database import get_session
from app.models import Article, OperationReport, ReportRevision
from app.report_auth import ReportPrincipal, require_report_read, require_report_write
from app.report_schemas import (ReportCreate, ReportFields, ReportID, ReportPage, ReportRead,
                                ReportSearch, ReportSummary, ReportUpdate, ReportRevisionRead, Label)

DB = Annotated[Session, Depends(get_session)]
Reader = Annotated[ReportPrincipal, Depends(require_report_read)]
Writer = Annotated[str, Depends(require_report_write)]
router = APIRouter(prefix="/api/reports", tags=["private-reports"])


def visible(actor: ReportPrincipal):
    return [] if actor.report_ids is None else [OperationReport.report_id.in_(actor.report_ids)]


def get(session: Session, report_id: str, actor: ReportPrincipal) -> OperationReport:
    report = session.scalars(select(OperationReport).where(OperationReport.report_id == report_id, *visible(actor))).first()
    if report is None:
        raise HTTPException(404, "Report not found")
    return report


def conditions(search: ReportSearch):
    terms = []
    for name in ["project", "component", "status", "report_id"]:
        value = getattr(search, name)
        if value is not None:
            terms.append(getattr(OperationReport, name) == value)
    if search.date_from:
        terms.append(OperationReport.created_at >= search.date_from)
    if search.date_to:
        terms.append(OperationReport.created_at <= search.date_to)
    if search.q:
        query = "%" + search.q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        columns = [OperationReport.title, OperationReport.summary, OperationReport.environment,
                   OperationReport.findings, OperationReport.changes, OperationReport.remaining_issues,
                   OperationReport.next_actions, OperationReport.raw_markdown,
                   cast(OperationReport.metrics_before, String), cast(OperationReport.metrics_after, String)]
        terms.append(or_(*(column.ilike(query, escape="\\") for column in columns)))
    return terms


def page(session: Session, search: ReportSearch, actor: ReportPrincipal, unresolved: bool = False) -> ReportPage:
    terms = conditions(search) + visible(actor)
    if unresolved:
        terms.append(func.length(func.trim(OperationReport.remaining_issues)) > 0)
    query = select(OperationReport).where(*terms)
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    columns = [OperationReport.created_at, OperationReport.report_id]
    order = [column.desc() for column in columns] if search.sort == "newest" else columns
    rows = session.scalars(query.order_by(*order).limit(search.limit).offset(search.offset)).all()
    return ReportPage(items=[ReportSummary.model_validate(row) for row in rows], total=total,
                      limit=search.limit, offset=search.offset)


@router.get("", response_model=ReportPage)
def list_reports(session: DB, actor: Reader, search: Annotated[ReportSearch, Query()]):
    return page(session, search, actor)


@router.get("/unresolved", response_model=ReportPage)
def unresolved_issues(session: DB, actor: Reader, search: Annotated[ReportSearch, Query()]):
    # Latest execution per project/component supersedes historical issue snapshots.
    latest = (select(OperationReport.report_id,
                     func.row_number().over(partition_by=[OperationReport.project, OperationReport.component],
                                            order_by=[OperationReport.created_at.desc(), OperationReport.report_id.desc()]).label("position"))
              .where(*visible(actor)).subquery())
    terms = conditions(search) + visible(actor) + [OperationReport.report_id.in_(select(latest.c.report_id).where(latest.c.position == 1)),
                                 func.length(func.trim(OperationReport.remaining_issues)) > 0]
    query = select(OperationReport).where(*terms)
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    order = [OperationReport.created_at, OperationReport.report_id]
    if search.sort == "newest":
        order = [column.desc() for column in order]
    rows = session.scalars(query.order_by(*order).limit(search.limit).offset(search.offset)).all()
    return ReportPage(items=[ReportSummary.model_validate(row) for row in rows], total=total, limit=search.limit, offset=search.offset)


@router.get("/projects")
def projects(session: DB, actor: Reader):
    # Bounded by pagination; options are a compact index, never raw Markdown.
    return {"projects": list(session.scalars(select(OperationReport.project.collate("C")).where(*visible(actor)).distinct().order_by(OperationReport.project.collate("C"))).all()),
            "components": list(session.scalars(select(OperationReport.component.collate("C")).where(*visible(actor)).distinct().order_by(OperationReport.component.collate("C"))).all())}


@router.get("/latest", response_model=ReportRead)
def latest_project_report(project: Label, session: DB, actor: Reader, component: Label | None = None):
    query = select(OperationReport).where(OperationReport.project == project, *visible(actor))
    if component is not None:
        query = query.where(OperationReport.component == component)
    report = session.scalars(query.order_by(OperationReport.created_at.desc(), OperationReport.report_id.desc()).limit(1)).first()
    if report is None:
        raise HTTPException(404, "Project report not found")
    return report


@router.get("/{report_id}", response_model=ReportRead)
def read_report(report_id: ReportID, session: DB, actor: Reader):
    return get(session, report_id, actor)


@router.get("/{report_id}/history", response_model=list[ReportRevisionRead])
def history(report_id: ReportID, session: DB, actor: Reader, limit: int = 20, offset: int = 0):
    if not 1 <= limit <= 100 or offset < 0:
        raise HTTPException(422, "Invalid history pagination")
    get(session, report_id, actor)
    return session.scalars(select(ReportRevision).where(ReportRevision.report_id == report_id)
                           .order_by(ReportRevision.revision.desc()).limit(limit).offset(offset)).all()


def save(session: Session, report_id: str, payload: ReportFields, expected: int | None, actor: str,
         require_existing: bool = False) -> OperationReport:
    # Serialize both first creates and updates for this stable ID across API replicas.
    session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:id, 73291))"), {"id": report_id})
    report = session.get(OperationReport, report_id, populate_existing=True)
    data = payload.model_dump(mode="json", exclude={"report_id", "expected_revision"})
    if report and expected is None:
        if ReportFields.model_validate(report, from_attributes=True).model_dump(mode="json") == data:
            session.commit()
            return report
        raise HTTPException(409, "Report ID exists with different content; read it and provide expected_revision")
    if report and (report.project != data["project"] or report.component != data["component"]):
        raise HTTPException(409, "Report ID belongs to a different project/component")
    if report and report.revision != expected:
        raise HTTPException(409, "Report revision changed; read before updating")
    if report is None and (expected is not None or require_existing):
        raise HTTPException(404, "Report not found")
    if report_id in data["related_report_ids"]:
        raise HTTPException(422, "A report cannot relate to itself")
    for related in data["related_report_ids"]:
        if session.get(OperationReport, related) is None:
            raise HTTPException(422, "Related report not found")
    for related in payload.related_article_ids:
        if session.get(Article, related) is None:
            raise HTTPException(422, "Related article not found")
    if report:
        if ReportFields.model_validate(report, from_attributes=True).model_dump(mode="json") == data:
            session.commit()
            return report
        for name, value in data.items():
            setattr(report, name, value)
        report.revision += 1
        report.updated_at = datetime.now(timezone.utc)
    else:
        report = OperationReport(report_id=report_id, revision=1, **data)
        session.add(report)
    session.flush()
    snapshot = ReportRead.model_validate(report).model_dump(mode="json")
    session.add(ReportRevision(report_id=report_id, revision=report.revision, actor=actor, snapshot=snapshot))
    session.commit()
    session.refresh(report)
    return report


@router.post("", response_model=ReportRead)
def create_report(payload: ReportCreate, session: DB, actor: Writer):
    return save(session, payload.report_id, payload, payload.expected_revision, actor)


@router.patch("/{report_id}", response_model=ReportRead)
def update_report(report_id: ReportID, payload: ReportUpdate, session: DB, actor: Writer):
    return save(session, report_id, payload, payload.expected_revision, actor, require_existing=True)
