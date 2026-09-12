"""智能体与业务数据的桥接：把图运行结果落库，并把证据链回传给界面。

职责边界：
- 图（agents/graph.py）负责智能体协同，不碰数据库
- 本模块负责：发起会话、提交讲述、把草稿与证据链写成 Story、人工确认前的重审

产品规则在本层的落地：
- 草稿一律 pending_review，只有人工确认才变 confirmed
- 人工改写后必须重新审计；仍有无证据句子则拒绝发布并退回
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .agents import AgentRuntime, SessionNotFoundError
from .database import Story, Topic, utc_now
from .service import ConsentError, format_duration, relative_day, require_consent

# 单机演示：运行时进程内单例（checkpoint 落 SQLite，服务重启后仍可恢复）
_runtime: AgentRuntime | None = None


def get_runtime(checkpoint_path: Path) -> AgentRuntime:
    global _runtime
    if _runtime is None:
        _runtime = AgentRuntime(checkpoint_path)
    return _runtime


def reset_runtime() -> None:
    """测试用：释放当前运行时。"""
    global _runtime
    if _runtime is not None:
        _runtime.close()
        _runtime = None


# ------------------------------------------------------------------ 会话


def start_interview_session(
    session: Session,
    runtime: AgentRuntime,
    *,
    family_id: str,
    actor_id: str,
    subject_name: str,
    topic_id: str,
    consent_version: int,
    max_rounds: int = 3,
    recording_id: str | None = None,
) -> dict[str, Any]:
    """开一场采访：图会先由采访导演提出第一个问题并挂起等待回答。"""
    require_consent(session, family_id, consent_version)
    topic = session.get(Topic, topic_id)
    session_id = f"interview-{uuid.uuid4().hex[:12]}"
    view = runtime.start_session(
        session_id=session_id,
        family_id=family_id,
        actor_id=actor_id,
        subject_name=subject_name,
        topic=topic.title if topic else topic_id,
        consent_version=consent_version,
        max_rounds=max_rounds,
    )
    view["topicId"] = topic_id
    view["recordingId"] = recording_id
    return view


def submit_answer(
    session: Session,
    runtime: AgentRuntime,
    *,
    family_id: str,
    session_id: str,
    answer: str,
    consent_version: int,
    finish: bool = False,
    recording_id: str | None = None,
    topic_id: str = "",
) -> dict[str, Any]:
    """提交一轮讲述。结束时把草稿与证据链落库为待确认故事。"""
    require_consent(session, family_id, consent_version)
    try:
        view = runtime.resume(session_id, {"answer": answer, "finish": finish})
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="采访会话不存在") from exc

    view["topicId"] = topic_id
    view["recordingId"] = recording_id

    # 图跑到确认点（或收尾）时生成/更新待确认故事
    if _has_draft(view):
        story = _upsert_draft(
            session,
            family_id=family_id,
            view=view,
            topic_id=topic_id,
            recording_id=recording_id,
            duration_ms=_duration_from_view(view),
        )
        view["storyId"] = story.id
    return view


def stop_session(
    session: Session,
    runtime: AgentRuntime,
    *,
    family_id: str,
    session_id: str,
    consent_version: int,
) -> dict[str, Any]:
    """尊重停止意愿：让图收尾并返回可确认的草稿（若有）。"""
    require_consent(session, family_id, consent_version)
    view = runtime.resume(session_id, {"answer": "", "finish": True})
    if _has_draft(view):
        story = _upsert_draft(
            session, family_id=family_id, view=view, topic_id="", recording_id=None, duration_ms=0
        )
        view["storyId"] = story.id
    return view


# ------------------------------------------------------------------ 人工确认


def review_draft(
    session: Session,
    runtime: AgentRuntime,
    *,
    family_id: str,
    session_id: str,
    action: str,
    consent_version: int,
    edited_text: str | None = None,
) -> dict[str, Any]:
    """人工确认点：批准 / 改写重审 / 要求补充 / 拒绝。"""
    require_consent(session, family_id, consent_version)
    if action not in {"approve", "edit", "request_more", "reject"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="未知的确认动作")

    payload: dict[str, Any] = {"action": action}
    if action == "edit" and edited_text is not None:
        payload["edited_text"] = edited_text

    # 状态校验：改写被拒后流程会退回采访，此时再发确认动作是无效的。
    # 明确报错比静默把动作当成"采访回答"要安全得多。
    current = runtime.view(session_id)
    pending = {item["value"].get("kind") for item in current.get("interrupts", [])}
    if "review" not in pending:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "当前会话不在人工确认点，无法执行确认动作。"
                f"当前阶段：{current.get('stage')}，等待：{sorted(pending) or '无'}"
            ),
        )

    try:
        view = runtime.resume(session_id, payload)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="采访会话不存在") from exc

    # 批准或改写后若仍有无证据句子，拒绝发布并说明原因（产品规则：不得新增事实）
    if action in {"approve", "edit"} and not view.get("audit_passed", True):
        findings = view.get("audit_findings", [])
        detail = "；".join(str(f.get("excerpt", "")) for f in findings[:2])
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"正文里有无证据支撑的内容，不能发布：{detail}",
        )
    return view


def review_story(
    session: Session,
    runtime: AgentRuntime,
    *,
    family_id: str,
    story_id: str,
    body: str,
    consent_version: int,
) -> dict[str, Any]:
    """故事书里的直接确认：先按证据重审，通过才允许发布。"""
    require_consent(session, family_id, consent_version)
    story = session.scalar(select(Story).filter_by(id=story_id, family_id=family_id))
    if story is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="故事不存在")

    claims = json.loads(story.claims_json or "[]")
    if claims:
        findings = _audit_edited_text(body=body, claims=claims)
        if findings:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "这段文字里有无法追溯到原声的内容，不能直接发布："
                    + "；".join(str(f.get("excerpt", "")) for f in findings[:2])
                ),
            )

    story.body = body
    story.status = "confirmed"
    story.audit_passed = 1
    session.commit()
    return _story_dict(session, story)


# ------------------------------------------------------------------ 内部


def _has_draft(view: dict[str, Any]) -> bool:
    return bool(view.get("draft_text")) and view.get("stage") in {"review", "delivered"}


def _duration_from_view(view: dict[str, Any]) -> int:
    """从会话里取录音时长（由调用方通过 recording 关联，这里取已有值）。"""
    return int(view.get("duration_ms") or 0)


def _upsert_draft(
    session: Session,
    *,
    family_id: str,
    view: dict[str, Any],
    topic_id: str,
    recording_id: str | None,
    duration_ms: int,
) -> Story:
    """把图产出的草稿写成待确认故事（同一 session 只保留一条）。"""
    story = None
    if view.get("session_id"):
        story = session.scalar(
            select(Story).filter_by(family_id=family_id, session_id=view["session_id"])
        )
    if story is None:
        story = Story(
            id=str(uuid.uuid4()),
            family_id=family_id,
            session_id=view.get("session_id", ""),
            sort_order=99,
        )
        session.add(story)

    story.topic_id = topic_id or story.topic_id
    story.title = _title_from_draft(view) or story.title or "未命名主题"
    story.body = view.get("draft_text", "")
    story.mode = "自然整理"
    story.status = "pending_review"
    story.audit_passed = 1 if view.get("audit_passed", True) else 0
    story.claims_json = json.dumps(view.get("claims", []), ensure_ascii=False)
    story.missing_fields_json = json.dumps(view.get("missing_fields", []), ensure_ascii=False)
    story.findings_json = json.dumps(view.get("audit_findings", []), ensure_ascii=False)
    story.conflicts_json = json.dumps(view.get("conflicts", []), ensure_ascii=False)
    if recording_id:
        story.recording_id = recording_id
    if duration_ms:
        story.duration_ms = duration_ms
    session.commit()
    return story


def _title_from_draft(view: dict[str, Any]) -> str:
    sentences = view.get("draft_sentences") or []
    for sentence in sentences:
        text = str(sentence.get("text", "")).strip()
        if text.startswith("《") and text.endswith("》"):
            return text.strip("《》")
    first_line = (view.get("draft_text") or "").splitlines()
    return first_line[0] if first_line else ""


def _audit_edited_text(*, body: str, claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """对人工改写后的文本做真实核对（规则见 evidence_audit.audit_text）。"""
    from .evidence_audit import audit_text

    return audit_text(body=body, claims=claims)


def _story_dict(session: Session, story: Story) -> dict[str, Any]:
    audio_url = None
    if story.recording_id:
        from .database import Recording

        recording = session.get(Recording, story.recording_id)
        if recording:
            audio_url = f"/media/{recording.object_key}"
    return {
        "id": story.id,
        "index": "",
        "title": story.title,
        "body": story.body,
        "mode": story.mode,
        "status": story.status,
        "durationMs": story.duration_ms,
        "durationText": format_duration(story.duration_ms),
        "dayLabel": relative_day(story.created_at),
        "audioUrl": audio_url,
        "hasAudio": audio_url is not None,
        "auditPassed": bool(story.audit_passed),
        "claims": json.loads(story.claims_json or "[]"),
        "missingFields": json.loads(story.missing_fields_json or "[]"),
        "findings": json.loads(story.findings_json or "[]"),
        "conflicts": json.loads(story.conflicts_json or "[]"),
        "sessionId": story.session_id,
    }


__all__ = [
    "get_runtime",
    "reset_runtime",
    "review_draft",
    "review_story",
    "start_interview_session",
    "stop_session",
    "submit_answer",
]
