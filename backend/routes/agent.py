"""多智能体流程的 HTTP 接口。

流程：开启采访 → 逐轮提交讲述（含停止）→ 人工确认（批准/改写/补充/拒绝）→ 故事。
所有写操作都校验 consent_version，family_id 只从令牌解析。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..agent_service import (
    get_runtime,
    reset_runtime,
    review_draft,
    review_story,
    start_interview_session,
    stop_session,
    submit_answer,
)
from ..config import settings
from ..database import get_session
from ..schemas import StoryOut
from ..api import AuthDep, SessionDep

router = APIRouter(prefix="/api/agent", tags=["agents"])


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    topicId: str = Field(min_length=1, max_length=32)
    subjectName: str = Field(default="讲述者", max_length=60)
    maxRounds: int = Field(default=3, ge=1, le=8)
    consentVersion: int = Field(ge=1)
    recordingId: str | None = None


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sessionId: str = Field(min_length=4, max_length=64)
    answer: str = Field(min_length=1, max_length=6000)
    finish: bool = False
    topicId: str = Field(default="", max_length=32)
    recordingId: str | None = None
    durationMs: int = Field(default=0, ge=0)
    consentVersion: int = Field(ge=1)


class StopRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessionId: str = Field(min_length=4, max_length=64)
    consentVersion: int = Field(ge=1)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sessionId: str = Field(min_length=4, max_length=64)
    action: str = Field(pattern=r"^(approve|edit|request_more|reject)$")
    editedText: str | None = Field(default=None, max_length=20000)
    consentVersion: int = Field(ge=1)


class StoryReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    body: str = Field(min_length=1, max_length=20000)
    consentVersion: int = Field(ge=1)


def _runtime():
    return get_runtime(settings.data_dir / "checkpoints.sqlite3")


@router.post("/interviews", status_code=status.HTTP_201_CREATED)
def start(payload: StartRequest, session: SessionDep, auth: AuthDep) -> dict[str, Any]:
    """开启采访：返回采访导演提出的第一个问题，图挂起等待回答。"""
    return start_interview_session(
        session,
        _runtime(),
        family_id=auth.family_id,
        actor_id=auth.user_id,
        subject_name=payload.subjectName,
        topic_id=payload.topicId,
        consent_version=payload.consentVersion,
        max_rounds=payload.maxRounds,
        recording_id=payload.recordingId,
    )


@router.post("/interviews/answers")
def answer(payload: AnswerRequest, session: SessionDep, auth: AuthDep) -> dict[str, Any]:
    """提交一轮讲述。finish=true 或要素齐全时进入写作与审计，产出待确认草稿。"""
    return submit_answer(
        session,
        _runtime(),
        family_id=auth.family_id,
        session_id=payload.sessionId,
        answer=payload.answer,
        consent_version=payload.consentVersion,
        finish=payload.finish,
        recording_id=payload.recordingId,
        topic_id=payload.topicId,
    )


@router.post("/interviews/stop")
def stop(payload: StopRequest, session: SessionDep, auth: AuthDep) -> dict[str, Any]:
    """尊重停止意愿：不再追问，直接收尾。"""
    return stop_session(
        session,
        _runtime(),
        family_id=auth.family_id,
        session_id=payload.sessionId,
        consent_version=payload.consentVersion,
    )


@router.post("/interviews/review")
def review(payload: ReviewRequest, session: SessionDep, auth: AuthDep) -> dict[str, Any]:
    """人工确认：批准 / 改写重审 / 要求补充 / 拒绝。

    改写后若仍有无证据句子，返回 403 并说明原因——AI 与人都不允许新增事实。
    """
    return review_draft(
        session,
        _runtime(),
        family_id=auth.family_id,
        session_id=payload.sessionId,
        action=payload.action,
        consent_version=payload.consentVersion,
        edited_text=payload.editedText,
    )


@router.post("/stories/{story_id}/review", response_model=StoryOut)
def review_existing(
    story_id: str, payload: StoryReviewRequest, session: SessionDep, auth: AuthDep
) -> dict[str, Any]:
    """故事书里的确认：先按证据核对改写后的正文，通过才允许发布。"""
    result = review_story(
        session,
        _runtime(),
        family_id=auth.family_id,
        story_id=story_id,
        body=payload.body,
        consent_version=payload.consentVersion,
    )
    return result


@router.delete("/runtime", status_code=status.HTTP_204_NO_CONTENT)
def release_runtime(auth: AuthDep) -> None:
    """释放图运行时（仅用于开发期重置）"""
    reset_runtime()
