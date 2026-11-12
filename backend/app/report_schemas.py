"""Private operations report contracts. Missing observations remain null."""
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.article_schemas import Markdown

ReportID = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+-[0-9]{4,}$", max_length=100)]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
ReportStatus = Literal["SUCCESS", "PARTIAL", "BLOCKED", "FAILED"]
ReportSource = Literal["Codex", "ChatGPT", "Manual"]
Section = Annotated[str, StringConstraints(max_length=50000)]


class ReportMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: Label
    value: float | None
    unit: Label
    observed_at: AwareDatetime | None = None
    window_start: AwareDatetime | None = None
    window_end: AwareDatetime | None = None
    note: Annotated[str, Field(max_length=2000)] = ""

    @model_validator(mode="after")
    def valid_window(self):
        if (self.window_start is None) != (self.window_end is None):
            raise ValueError("Provide both observation window boundaries")
        if self.window_start and self.window_end <= self.window_start:
            raise ValueError("Observation window end must be after start")
        return self


class ReportFields(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    title: Label
    project: Label
    component: Label
    author: Label
    source: ReportSource
    status: ReportStatus
    summary: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10000)]
    environment: Section = ""
    findings: Section = ""
    changes: Section = ""
    metrics_before: list[ReportMetric] = Field(default_factory=list, max_length=100)
    metrics_after: list[ReportMetric] = Field(default_factory=list, max_length=100)
    remaining_issues: Section = ""
    next_actions: Section = ""
    raw_markdown: Markdown
    related_report_ids: list[ReportID] = Field(default_factory=list, max_length=50)
    related_article_ids: list[UUID] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def unique_values(self):
        if len(self.related_report_ids) != len(set(self.related_report_ids)) or len(self.related_article_ids) != len(set(self.related_article_ids)):
            raise ValueError("Related identifiers must be unique")
        for metrics in [self.metrics_before, self.metrics_after]:
            if len(metrics) != len({(m.name, m.unit) for m in metrics}):
                raise ValueError("Metric name/unit pairs must be unique within a measurement")
        return self


class ReportCreate(ReportFields):
    report_id: ReportID
    # Replays of identical creates are no-ops; replacing an existing ID requires CAS.
    expected_revision: int | None = Field(default=None, ge=1)


class ReportUpdate(ReportFields):
    # A complete replacement makes omitted sections explicit; creation time/ID stay immutable.
    expected_revision: int = Field(ge=1)


class ReportRead(ReportFields):
    model_config = ConfigDict(from_attributes=True)
    report_id: str
    created_at: datetime
    updated_at: datetime
    revision: int


class ReportSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    report_id: str
    title: str
    project: str
    component: str
    status: ReportStatus
    source: ReportSource
    summary: str
    remaining_issues: str
    created_at: datetime
    updated_at: datetime
    revision: int


class ReportSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project: Label | None = None
    component: Label | None = None
    q: Annotated[str, Field(max_length=200)] | None = None
    status: ReportStatus | None = None
    report_id: ReportID | None = None
    date_from: AwareDatetime | None = None
    date_to: AwareDatetime | None = None
    sort: Literal["newest", "oldest"] = "newest"
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=1000000)

    @model_validator(mode="after")
    def valid_dates(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must precede date_to")
        return self


class ReportPage(BaseModel):
    items: list[ReportSummary]
    total: int
    limit: int
    offset: int


class ReportRevisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    report_id: str
    revision: int
    recorded_at: datetime
    actor: str
    snapshot: dict
