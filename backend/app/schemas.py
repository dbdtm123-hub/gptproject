from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
RawText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20000)]
Summary = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]
Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Tags = Annotated[list[Tag], Field(max_length=30)]
Importance = Annotated[int, Field(strict=True, ge=1, le=5)]


class EntryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_text: RawText
    category: Name
    subcategory: Name | None = None
    title: Title
    summary: Summary
    tags: Tags = Field(default_factory=list)
    importance: Importance = 3
    occurred_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("occurred_at")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


class EntryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_text: RawText | None = None
    category: Name | None = None
    subcategory: Name | None = None
    title: Title | None = None
    summary: Summary | None = None
    tags: Tags | None = None
    importance: Importance | None = None
    occurred_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def reject_empty_or_null(self) -> "EntryUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one field must be supplied")
        for name in self.model_fields_set - {"subcategory"}:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self

    @field_validator("occurred_at")
    @classmethod
    def utc_time(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(timezone.utc) if value else None

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value: list[str] | None) -> list[str] | None:
        return list(dict.fromkeys(value)) if value is not None else None


class EntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
    occurred_at: datetime
    raw_text: str
    category_id: UUID
    category: str
    subcategory: str | None
    title: str
    summary: str
    tags: list[str]
    importance: int


class EntryPage(BaseModel):
    items: list[EntryRead]
    total: int
    limit: int
    offset: int


class CategoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    created_at: datetime


class ParseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    raw_text: RawText
    occurred_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EntryBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entries: list[EntryCreate] = Field(min_length=1, max_length=20)
