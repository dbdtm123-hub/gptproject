from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.schemas import Name, Summary, Tags, Title

Slug = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200,
                                      pattern=r"^[a-z0-9가-힣]+(?:-[a-z0-9가-힣]+)*$")]
def nonblank_markdown(value: str) -> str:
    if not value.strip():
        raise ValueError("Markdown cannot contain only whitespace")
    return value  # Preserve indentation and newlines used by Markdown/code blocks.


Markdown = Annotated[str, StringConstraints(min_length=1, max_length=200000), AfterValidator(nonblank_markdown)]
Reference = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Status = Literal["draft", "published"]
Related = Annotated[list[UUID], Field(max_length=20)]


class ArticleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Title
    slug: Slug | None = None
    category: Name
    subcategory: Name | None = None
    summary: Summary
    content_markdown: Markdown
    tags: Tags = Field(default_factory=list)
    source_type: Name = "manual"
    source_reference: Reference | None = None
    related_articles: Related = Field(default_factory=list)
    status: Status = "draft"

    @field_validator("tags", "related_articles")
    @classmethod
    def unique_values(cls, value: list) -> list:
        return list(dict.fromkeys(value))


class ArticleUpsert(ArticleCreate):
    target_article_id: UUID | None = None
    mode: Literal["replace", "append"] = "replace"
    expected_updated_at: AwareDatetime | None = None


class ArticleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Title | None = None
    slug: Slug | None = None
    category: Name | None = None
    subcategory: Name | None = None
    summary: Summary | None = None
    content_markdown: Markdown | None = None
    tags: Tags | None = None
    source_type: Name | None = None
    source_reference: Reference | None = None
    related_articles: Related | None = None
    status: Status | None = None
    expected_updated_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def valid_patch(self) -> "ArticleUpdate":
        changes = self.model_fields_set - {"expected_updated_at"}
        if not changes:
            raise ValueError("At least one article field must be supplied")
        for name in changes - {"subcategory", "source_reference"}:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self

    @field_validator("tags", "related_articles")
    @classmethod
    def unique_values(cls, value: list | None) -> list | None:
        return list(dict.fromkeys(value)) if value is not None else None


class ArticleSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str
    slug: str
    category_id: UUID
    category: str
    subcategory: str | None
    summary: str
    tags: list[str]
    created_at: datetime
    updated_at: datetime
    status: Status


class ArticleRead(ArticleSummary):
    content_markdown: str
    source_type: str
    source_reference: str | None
    related_articles: list[UUID]


class ArticlePage(BaseModel):
    items: list[ArticleSummary]
    total: int
    limit: int
    offset: int
