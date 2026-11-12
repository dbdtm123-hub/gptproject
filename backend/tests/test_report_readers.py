"""Non-admin identity, explicit ownership, scope separation and fail-closed forwarding."""
import asyncio

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import SecretStr, ValidationError
import pytest
from sqlalchemy.dialects import postgresql
from starlette.requests import Request

from app.config import ReportReader, Settings, get_settings
from app.report_auth import ReportPrincipal, require_report_read, require_report_write
from app.reports import visible
from app import mcp_tools
from test_mcp import environment, rpc, send

RID = "OPENSTACK-RABBITMQ-0020"
OTHER = "OPENSTACK-RABBITMQ-0021"


def request(token=None, cookie=None):
    headers = []
    if token is not None:
        headers.append((b"authorization", ("Bearer " + token).encode()))
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    return Request({"type": "http", "headers": headers})


def credential(token):
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_nonadmin_reader_is_bound_to_operator_assigned_ids(monkeypatch):
    monkeypatch.setattr(get_settings(), "report_readers", {
        "alice": ReportReader(token="a" * 40, report_ids=[RID]),
        "bob": ReportReader(token="b" * 40, report_ids=[OTHER]),
    })
    for subject, token, ids in [("alice", "a" * 40, {RID}), ("bob", "b" * 40, {OTHER})]:
        identity = require_report_read(request(token), credential(token))
        assert identity == ReportPrincipal(subject, frozenset(ids))
        # Exact IDs constrain every SQL read; no author/header impersonation.
        clause = visible(identity)[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
        assert next(iter(ids)) in str(clause)
        assert (OTHER if subject == "alice" else RID) not in str(clause)
        with pytest.raises(HTTPException) as error:
            require_report_write(request(token), credential(token))
        assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        require_report_read(request(), None)
    assert error.value.status_code == 401
    with pytest.raises(HTTPException) as error:
        require_report_read(request("invalid"), credential("invalid"))
    assert error.value.status_code == 401
    assert visible(ReportPrincipal("empty", frozenset()))


def test_reader_configuration_refuses_ambiguous_ownership_and_owner_tokens():
    base = dict(database_url="postgresql://unused", api_token="o" * 40, _env_file=None)
    for readers in [
        {"alice": dict(token="o" * 40, report_ids=[RID])},
        {"alice": dict(token="a" * 40, report_ids=[RID]), "bob": dict(token="b" * 40, report_ids=[RID])},
        {"alice": dict(token="a" * 40, report_ids=[RID]), "bob": dict(token="a" * 40, report_ids=[OTHER])},
        {"": dict(token="a" * 40, report_ids=[RID])},
    ]:
        with pytest.raises(ValidationError):
            Settings(**base, report_readers=readers)
    with pytest.raises(ValidationError):
        ReportReader(token="a" * 40, report_ids=["*"])


def test_report_subject_never_falls_back_to_shared_owner_token(environment):
    settings, claims = environment
    settings.report_backend_token = SecretStr("shared-owner-report-token")
    claims["scope"] = "autolog:reports:read"
    result = send(rpc("tools/call", {"name": "get_report", "arguments": {"report_id": RID}})).json()["result"]
    assert result["isError"] and "403" in result["content"][0]["text"]


def test_report_subject_credential_selection(environment, monkeypatch):
    settings, claims = environment
    settings.report_subject_tokens = {"alice": SecretStr("a" * 40), "bob": SecretStr("b" * 40)}
    selected = []
    import httpx
    async def upstream(request):
        selected.append(request.headers["authorization"])
        return httpx.Response(200, json={"report_id": RID})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(mcp_tools.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(upstream), **kwargs))
    for subject in ["alice", "bob"]:
        asyncio.run(mcp_tools.backend("GET", "/api/reports/" + RID, report_subject=subject))
    assert selected == ["Bearer " + "a" * 40, "Bearer " + "b" * 40]
    with pytest.raises(HTTPException) as error:
        asyncio.run(mcp_tools.backend("GET", "/api/reports/" + RID, report_subject="mallory"))
    assert error.value.status_code == 403 and len(selected) == 2
