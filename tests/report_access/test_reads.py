"""Portable read-query integration tests. SQLite is explicit, not a PostgreSQL replacement.

Production migration/CAS/JSONB constraints remain covered by backend/tests on PostgreSQL.
"""
from datetime import datetime, timezone, timedelta
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://unused/autolog_portable_test")

import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import ReportReader, get_settings
from app.report_auth import ReportPrincipal
from app.report_schemas import ReportSearch
from app import reports
from app.main import app
from app.models import OperationReport, ReportRevision


@compiles(JSONB, "sqlite")
def portable_json(type_, compiler, **kwargs):
    return "JSON"


@pytest.fixture
def database(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "api_token", SecretStr("o" * 40))
    monkeypatch.setattr(settings, "report_readers", {
        "alice": ReportReader(token="a" * 40, report_ids=["OPENSTACK-RABBITMQ-0020"]),
        "bob": ReportReader(token="b" * 40, report_ids=["OPENSTACK-RABBITMQ-0021"]),
        "empty": ReportReader(token="e" * 40, report_ids=[]),
    })
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    with engine.connect() as connection:
        connection.connection.driver_connection.create_collation("C", lambda a, b: (a > b) - (a < b))
    OperationReport.__table__.create(engine)
    ReportRevision.__table__.create(engine)
    with Session(engine) as session:
        for n in [20, 21]:
            rid = f"OPENSTACK-RABBITMQ-{n:04d}"
            report = OperationReport(report_id=rid, title=f"Private {n}", project="OpenStack", component="RabbitMQ",
                author="untrusted display name", source="Codex", status="PARTIAL", summary=f"Summary {n}",
                environment="Private", findings="Observation", changes="Change", remaining_issues="Pending", next_actions="Review",
                raw_markdown=f"# Private {n}", revision=1, created_at=datetime(2026, 11, 12, tzinfo=timezone.utc) + timedelta(minutes=n))
            session.add(report)
            session.flush()
            session.add(ReportRevision(report_id=rid, revision=1, actor="owner-api-token", snapshot={"raw_markdown":report.raw_markdown}))
        session.commit()
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


@pytest.mark.parametrize("subject,own,other", [("alice", "0020", "0021"), ("bob", "0021", "0020")])
def test_nonadmin_queries_only_own_reports_on_every_route(database, subject, own, other):
    own_id, other_id = "OPENSTACK-RABBITMQ-" + own, "OPENSTACK-RABBITMQ-" + other
    actor = ReportPrincipal(subject, frozenset([own_id]))
    detail = reports.read_report(own_id, database, actor)
    assert detail.raw_markdown == "# Private " + str(int(own))
    assert reports.history(own_id, database, actor)[0].report_id == own_id
    for filters in [{}, {"q":"Private"}, {"project":"OpenStack", "status":"PARTIAL", "limit":1}]:
        result = reports.list_reports(database, actor, ReportSearch(**filters))
        assert result.total == 1 and [r.report_id for r in result.items] == [own_id]
        assert other_id not in result.model_dump_json()
    result = reports.unresolved_issues(database, actor, ReportSearch())
    assert result.total == 1 and result.items[0].report_id == own_id
    assert reports.latest_project_report("OpenStack", database, actor).report_id == own_id
    assert reports.projects(database, actor) == {"projects":["OpenStack"], "components":["RabbitMQ"]}
    assert reports.list_reports(database, actor, ReportSearch(report_id=other_id)).total == 0
    for rid in [other_id, "MISSING-REPORT-9999"]:
        for read in [reports.read_report, reports.history]:
            with pytest.raises(HTTPException) as error:
                read(rid, database, actor)
            assert error.value.status_code == 404 and error.value.detail == "Report not found"


def test_empty_grant_and_owner_queries(database):
    empty = ReportPrincipal("empty", frozenset())
    assert reports.list_reports(database, empty, ReportSearch()).total == 0
    assert reports.unresolved_issues(database, empty, ReportSearch()).total == 0
    assert reports.projects(database, empty) == {"projects":[], "components":[]}
    with pytest.raises(HTTPException) as error:
        reports.latest_project_report("OpenStack", database, empty)
    assert error.value.status_code == 404
    with pytest.raises(HTTPException):
        reports.read_report("OPENSTACK-RABBITMQ-0020", database, empty)
    assert reports.list_reports(database, ReportPrincipal("owner-api-token"), ReportSearch()).total == 2
