from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field


Stage = Literal[
    "interview",
    "evidence",
    "writing",
    "review",
    "creative",
    "delivered",
    "rejected",
    "revoked",
]


def merge_unique(left: list[str], right: list[str]) -> list[str]:
    """用于并行 Send 的稳定去重 reducer。"""
    return list(dict.fromkeys([*(left or []), *(right or [])]))


class MemoryBankState(TypedDict, total=False):
    project_id: str
    family_id: str
    actor_id: str
    consent_version: int
    stage: Stage
    round_index: int
    max_rounds: int
    current_question: str
    turn_ids: list[str]
    claim_ids: Annotated[list[str], merge_unique]
    draft_id: str | None
    delivery_id: str | None
    decision: dict[str, Any]
    audit_findings: list[dict[str, Any]]
    next_action: str | None
    errors: Annotated[list[dict[str, Any]], operator.add]


class EvidenceTask(TypedDict):
    project_id: str
    family_id: str
    actor_id: str
    consent_version: int
    turn_id: str
    claim_ids: Annotated[list[str], merge_unique]


class ProjectCreate(BaseModel):
    subject_name: str = Field(min_length=1, max_length=60)
    topic: str = Field(min_length=1, max_length=120)
    consent_by: str = Field(min_length=1, max_length=60)
    family_id: str | None = Field(default=None, max_length=80)
    actor_id: str | None = Field(default=None, max_length=80)
    max_rounds: int = Field(default=3, ge=1, le=8)


class InterviewResponse(BaseModel):
    answer: str = Field(min_length=1, max_length=6000)
    finish: bool = False
    decision_id: str | None = None


class ReviewResponse(BaseModel):
    action: Literal["approve", "edit", "request_more", "reject"]
    edited_text: str | None = Field(default=None, max_length=20000)
    decision_id: str | None = None


class RevokeRequest(BaseModel):
    reason: str = Field(default="用户主动撤回授权", max_length=500)

