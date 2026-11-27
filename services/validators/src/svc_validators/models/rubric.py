"""Rubric file schema (docs/harness/09 §6). Strict: unknown keys are rejected."""

import re
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_ID = re.compile(r"^[a-z_]+@v\d+$")


class PriorRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    when: str
    score: Annotated[float, Field(ge=0, le=5)]


class Rubric(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dimension: str
    version: Annotated[int, Field(ge=1)]
    id: str
    scale: str
    description: str
    anchors: dict[int, str]
    features: list[str] = Field(min_length=1)
    feature_prior: list[PriorRule] = Field(min_length=1)
    evidence_rule: str
    output_schema: str
    examples: list[Any] = Field(default_factory=list)

    @field_validator("scale")
    @classmethod
    def _scale(cls, value: str) -> str:
        if value != "0-5":
            raise ValueError("scale must be 0-5")
        return value

    @field_validator("anchors")
    @classmethod
    def _anchors(cls, value: dict[int, str]) -> dict[int, str]:
        if sorted(value) != [0, 1, 2, 3, 4, 5]:
            raise ValueError("anchors must define exactly the levels 0..5")
        if any(not text.strip() for text in value.values()):
            raise ValueError("anchor text must not be blank")
        return value

    @model_validator(mode="after")
    def _identity(self) -> "Rubric":
        if not _ID.match(self.id) or self.id != f"{self.dimension}@v{self.version}":
            raise ValueError("id must be <dimension>@v<version>")
        return self
