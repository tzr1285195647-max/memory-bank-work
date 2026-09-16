"""五个核心 Agent 的结构化输出模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TranscriptChange(StrictModel):
    type: Literal["delete_filler", "correct_typo", "reduce_repetition", "punctuate", "paragraph"]
    before: str = ""
    after: str = ""
    reason: str = ""


class TranscriptUncertainty(StrictModel):
    text: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class TranscriptCleanResult(StrictModel):
    cleanText: str = Field(min_length=1)
    changes: list[TranscriptChange] = Field(default_factory=list)
    uncertainties: list[TranscriptUncertainty] = Field(default_factory=list)


class QuestionDecisionModel(StrictModel):
    should_stop: bool = False
    stop_reason: str | None = None
    question: str | None = None
    sub_question: str | None = None
    target_element: Literal["time", "place", "people", "event", "result", "impact", "feeling"] | None = None
    closing: str | None = None
    complete: bool = False
    complete_reason: str | None = None


class ExtractedClaimModel(StrictModel):
    element: Literal["time", "place", "people", "event", "result", "impact", "feeling"]
    text: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    confidence: float = Field(default=0.8, ge=0, le=1)
    status: Literal["confident", "needs_confirmation"] = "confident"


class ExtractResultModel(StrictModel):
    claims: list[ExtractedClaimModel] = Field(default_factory=list)
    missing_fields: list[Literal["time", "place", "people", "event", "result", "impact", "feeling"]] = Field(default_factory=list)


class DraftSentenceModel(StrictModel):
    text: str = Field(min_length=1)
    claim_ids: list[str] = Field(default_factory=list)
    fragment_ids: list[str] = Field(default_factory=list)
    must_cite: bool = True


class DraftResultModel(StrictModel):
    title: str = Field(min_length=1)
    sentences: list[DraftSentenceModel] = Field(min_length=1)


class AuditFindingModel(StrictModel):
    sentence_id: str
    status: Literal["passed", "needs_confirmation", "unsupported"]
    kind: str
    message: str
    excerpt: str = ""
    claim_ids: list[str] = Field(default_factory=list)
    fragment_ids: list[str] = Field(default_factory=list)


class AuditResultModel(StrictModel):
    findings: list[AuditFindingModel] = Field(default_factory=list)
    conflicts: list[dict] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    passed: bool

    @model_validator(mode="after")
    def unsupported_cannot_pass(self):
        if any(item.status == "unsupported" for item in self.findings):
            self.passed = False
        return self


class ConflictGroupModel(StrictModel):
    element: str
    quote_a: str
    turn_a: str
    quote_b: str
    turn_b: str
    note: str


class ConflictResultModel(StrictModel):
    conflicts: list[ConflictGroupModel] = Field(default_factory=list)


__all__ = [
    "AuditResultModel", "ConflictResultModel", "DraftResultModel", "ExtractResultModel",
    "QuestionDecisionModel", "TranscriptCleanResult",
]
