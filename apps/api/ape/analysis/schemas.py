"""Structured output the LLM has to produce (see section 4 of mvp-plan.md)."""

from typing import Literal

from pydantic import BaseModel, Field


class ResourceRef(BaseModel):
    kind: str
    namespace: str | None
    name: str


class Patch(BaseModel):
    format: Literal["strategic_merge", "json6902"]
    target: ResourceRef
    content: str = Field(
        description="YAML. May contain masked placeholders, never real secret values."
    )


class EnrichedFinding(BaseModel):
    fingerprint: str = Field(description="Copied from the input. New findings cannot be added.")
    severity: Literal["critical", "high", "medium", "low", "info"]
    title: str = Field(max_length=120)
    explanation: str = Field(description="For the customer, in plain language.")
    business_impact: str
    remediation: str = Field(description="Step by step.")
    patch: Patch | None = Field(description="null when the change cannot be made safely.")
    confidence: float = Field(ge=0, le=1)


class AuditAnalysis(BaseModel):
    executive_summary: str = Field(max_length=1200)
    top_risks: list[str] = Field(max_length=5)
    findings: list[EnrichedFinding]
