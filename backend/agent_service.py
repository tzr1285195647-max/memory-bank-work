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
import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .agents import AgentRuntime, SessionNotFoundError
from .agents.state import SEVEN_ELEMENTS
from .database import FamilyNote, Recording, Story, StoryRecording, Topic, utc_now
from .service import ConsentError, append_audit, require_consent, story_to_dict

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
    duration_ms: int = 0,
    speaker_label: str = "长辈",
) -> dict[str, Any]:
    """提交一轮讲述。结束时把草稿与证据链落库为待确认故事。"""
    require_consent(session, family_id, consent_version)
    # 用户校对并确认后的文字才是“记忆碎片”的最终版本；覆盖 ASR 初稿，
    # 这样稍后重新打开或继续讲述时看到的仍是人工确认文本。
    if recording_id:
        recording = session.get(Recording, recording_id)
        if recording and recording.family_id == family_id:
            recording.transcript = answer
            session.commit()
    try:
        view = runtime.resume(
            session_id,
            {
                "answer": answer,
                "finish": finish,
                "durationMs": max(0, duration_ms),
                "recordingId": recording_id,
                "speakerLabel": speaker_label,
            },
        )
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
    topic_id: str = "",
) -> dict[str, Any]:
    """尊重停止意愿：让图收尾并返回可确认的草稿（若有）。"""
    require_consent(session, family_id, consent_version)
    view = runtime.resume(session_id, {"answer": "", "finish": True})
    if _has_draft(view):
        story = _upsert_draft(
            session, family_id=family_id, view=view, topic_id=topic_id, recording_id=None, duration_ms=0
        )
        view["storyId"] = story.id
    return view


def generate_story_from_fragments(
    session: Session,
    runtime: AgentRuntime,
    *,
    family_id: str,
    actor_user_id: str,
    recording_ids: list[str],
    topic_id: str,
    subject_name: str,
    style: str,
    consent_version: int,
) -> dict[str, Any]:
    """只使用用户勾选的记忆碎片运行证据抽取、写作与审计 Agent。"""

    require_consent(session, family_id, consent_version)
    selected_ids = list(dict.fromkeys(recording_ids))
    if not selected_ids:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="请至少勾选一段记忆碎片")

    rows = session.scalars(
        select(Recording).where(
            Recording.family_id == family_id,
            Recording.id.in_(selected_ids),
        )
    ).all()
    by_id = {row.id: row for row in rows}
    if any(recording_id not in by_id for recording_id in selected_ids):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="部分记忆碎片不存在或无权访问")
    recordings = [by_id[recording_id] for recording_id in selected_ids]
    if any(not (recording.transcript or "").strip() for recording in recordings):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="选中的碎片中有尚未确认的文字")

    topic = session.get(Topic, topic_id)
    topic_title = topic.title if topic else topic_id or "家庭记忆"
    provider = runtime.provider
    claims: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    claim_order = 0
    for index, recording in enumerate(recordings):
        turn_id = f"fragment-{index + 1}-{recording.id[:8]}"
        extracted = provider.extract_claims(transcript=recording.transcript.strip(), turn_id=turn_id)
        for item in extracted.get("claims", []):
            claim = {
                "id": f"claim-{uuid.uuid4().hex[:10]}",
                "element": item["element"],
                "text": item["text"],
                "quote": item["quote"],
                "turn_id": turn_id,
                "confidence": item.get("confidence", 0.6),
                "order": claim_order,
            }
            claims.append(claim)
            claim_order += 1
        refs.append(
            {
                "recording_id": recording.id,
                "turn_id": turn_id,
                "round_index": index,
                "duration_ms": recording.duration_ms,
                "speaker_label": "长辈",
            }
        )
    if not claims:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="选中的文字没有可用于生成故事的内容")

    draft = provider.compose_draft(
        subject_name=subject_name or "讲述者",
        topic=topic_title,
        claims=claims,
        style=style,
    )
    draft_sentences = [
        {
            "id": f"selection-s{index:02d}",
            "text": str(item.get("text") or "").strip(),
            "claim_ids": list(item.get("claim_ids") or []),
            "must_cite": bool(item.get("must_cite", True)),
        }
        for index, item in enumerate(draft.get("sentences", []))
        if str(item.get("text") or "").strip()
    ]
    findings = provider.audit_draft(sentences=draft_sentences, claims=claims)
    covered = {str(claim.get("element")) for claim in claims}
    missing = [element for element in SEVEN_ELEMENTS if element not in covered]
    conflicts = provider.resolve_conflicts(claims=claims)
    mode = {"raw": "原味口述", "natural": "自然整理", "book": "适合成书"}.get(style, "自然整理")
    title = str(draft.get("title") or f"《{topic_title}》").strip().strip("《》")
    body = "\n".join(sentence["text"] for sentence in draft_sentences)
    story = Story(
        id=str(uuid.uuid4()),
        family_id=family_id,
        topic_id=topic_id,
        title=title or topic_title,
        body=body,
        mode=mode,
        status="pending_review",
        recording_id=recordings[0].id,
        duration_ms=sum(recording.duration_ms or 0 for recording in recordings),
        sort_order=99,
        life_stage={"hometown": "童年", "school": "求学", "work": "工作", "family": "家庭"}.get(topic_id, "未分类"),
        session_id=f"fragments-{uuid.uuid4().hex[:12]}",
        claims_json=json.dumps(claims, ensure_ascii=False),
        missing_fields_json=json.dumps(missing, ensure_ascii=False),
        findings_json=json.dumps(findings, ensure_ascii=False),
        conflicts_json=json.dumps(conflicts, ensure_ascii=False),
        audit_passed=0 if findings else 1,
    )
    session.add(story)
    session.commit()
    _sync_story_recordings(session, story, refs)
    append_audit(
        session,
        family_id,
        action="story_generated",
        category="story",
        summary=f"使用 {len(recordings)} 段记忆碎片生成故事《{story.title}》",
        actor_user_id=actor_user_id,
        target_id=story.id,
    )
    session.commit()
    return story_to_dict(session, story)


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
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    """故事书里的直接确认：先按证据重审，通过才允许发布。"""
    require_consent(session, family_id, consent_version)
    story = session.scalar(select(Story).filter_by(id=story_id, family_id=family_id))
    if story is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="故事不存在")
    pending_note = session.scalar(
        select(FamilyNote).filter_by(
            story_id=story.id, family_id=family_id, status="pending"
        )
    )
    if pending_note:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="还有家人建议未处理，请先采纳或标记暂不采用",
        )

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
    append_audit(
        session, family_id, action="story_confirmed", category="story",
        summary=f"确认并保存了故事《{story.title}》", actor_user_id=actor_user_id,
        target_id=story.id,
    )
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
    if story.memory_year is None:
        match = re.search(r"(?<!\d)((?:19|20)\d{2})年", str(view.get("draft_text", "")))
        story.memory_year = int(match.group(1)) if match else None
    if not story.life_stage or story.life_stage == "未分类":
        story.life_stage = {
            "hometown": "童年",
            "school": "求学",
            "work": "工作",
            "family": "家庭",
        }.get(topic_id, "未分类")
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
    _sync_story_recordings(session, story, view.get("recordings", []))
    return story


def _sync_story_recordings(
    session: Session, story: Story, refs: list[dict[str, Any]]
) -> None:
    """把图中积累的每轮录音引用幂等写入关联表。"""
    existing = {
        row.turn_id: row
        for row in session.scalars(select(StoryRecording).filter_by(story_id=story.id)).all()
    }
    for ref in refs:
        recording_id = str(ref.get("recording_id") or "")
        turn_id = str(ref.get("turn_id") or "")
        recording = session.get(Recording, recording_id) if recording_id else None
        if not recording or recording.family_id != story.family_id or not turn_id:
            continue
        row = existing.get(turn_id)
        if row is None:
            row = StoryRecording(id=str(uuid.uuid4()), story_id=story.id, recording_id=recording_id, turn_id=turn_id)
            session.add(row)
        row.recording_id = recording_id
        row.round_index = int(ref.get("round_index") or 0)
        row.duration_ms = int(ref.get("duration_ms") or recording.duration_ms or 0)
        row.speaker_label = str(ref.get("speaker_label") or "长辈")
    session.commit()


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
    return story_to_dict(session, story)


__all__ = [
    "get_runtime",
    "generate_story_from_fragments",
    "reset_runtime",
    "review_draft",
    "review_story",
    "start_interview_session",
    "stop_session",
    "submit_answer",
]
