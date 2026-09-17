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
from .evidence_audit import unresolved_conflict_findings
from .database import AgentCallLog, FamilyNote, MemoryFact, Recording, Story, StoryRecording, Topic, User, utc_now
from .service import ConsentError, append_audit, require_consent, story_to_dict, visible_topic

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


def _flush_agent_calls(
    session: Session, runtime: AgentRuntime, *, family_id: str, input_refs: list[str]
) -> list[dict[str, Any]]:
    """把模型元数据落库；不记录转写正文、提示词正文或密钥。"""
    provider = runtime.provider
    consume = getattr(provider, "consume_call_records", None)
    if not callable(consume):
        return []
    records = consume()
    for item in records:
        session.add(AgentCallLog(
            id=str(uuid.uuid4()), family_id=family_id, agent_name=str(item.get("agent") or "unknown"),
            model=str(item.get("model") or ""), prompt_version=str(item.get("prompt_version") or "p0-v1"),
            input_refs_json=json.dumps(input_refs, ensure_ascii=False),
            duration_ms=int(item.get("duration_ms") or 0), retry_count=int(item.get("retry_count") or 0),
            fallback_used=1 if item.get("fallback_used") else 0,
            error=str(item.get("error") or "")[:240],
        ))
    session.commit()
    return records


def _workflow_from_view(view: dict[str, Any], calls: list[dict[str, Any]]) -> dict[str, Any]:
    """演示用脱敏协作记录：不保存 Prompt、正文或密钥。"""
    labels = {
        "interview.select_question": "采访提示", "evidence.extract_claims": "证据抽取",
        "evidence.merge": "证据汇总", "evidence.selection_prepare": "证据汇总",
        "writing.draft": "故事写作", "writing.revision": "写作修订",
        "writing.audit": "事实审计", "review.decision": "人工确认",
        "review.apply": "确认后复审",
    }
    steps = []
    for item in view.get("agent_trace", []):
        node = str(item.get("node") or "")
        if node not in labels:
            continue
        steps.append({
            "agent": labels[node], "node": node,
            "claims": item.get("claims"), "findings": item.get("findings"),
            "conflicts": item.get("conflicts"),
            "fragmentCount": item.get("fragment_count"),
            "correctedKnownQuestion": bool(item.get("corrected_known_question")),
        })
    return {
        "steps": steps,
        "provider": view.get("provider_name", "unknown"),
        "calls": [
             {key: item.get(key) for key in
             ("agent", "model", "prompt_version", "duration_ms", "retry_count", "fallback_used")}
            for item in calls
        ],
        "auditPassed": bool(view.get("audit_passed")),
        "revisionCount": int(view.get("revision_count") or 0),
    }


def _record_human_outcome(
    session: Session, *, family_id: str, outcome: str, input_refs: list[str]
) -> None:
    session.add(AgentCallLog(
        id=str(uuid.uuid4()), family_id=family_id, agent_name="human_review",
        agent_version="p0-v1", model="human", prompt_version="human-in-the-loop",
        input_refs_json=json.dumps(input_refs, ensure_ascii=False), outcome=outcome,
    ))
    session.commit()


def clean_recording_transcript(
    session: Session, runtime: AgentRuntime, *, family_id: str, recording_id: str
) -> dict[str, Any]:
    """ASR 成功后生成建议；任何失败都不覆盖或删除原始转写。"""
    recording = session.scalar(select(Recording).filter_by(id=recording_id, family_id=family_id))
    if recording is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="录音不存在")
    raw = (recording.asr_raw_text or recording.transcript or "").strip()
    if not raw:
        return {}
    narrator = session.get(User, recording.narrator_user_id) if recording.narrator_user_id else None
    topic = session.get(Topic, recording.topic_id)
    try:
        result = runtime.provider.clean_transcript(
            asr_raw_text=raw,
            narrator_name=narrator.display_name if narrator else "讲述者",
            topic=topic.title if topic else recording.topic_id,
        )
        recording.agent_clean_text = str(result.get("cleanText") or raw).strip()
        recording.clean_changes_json = json.dumps(result.get("changes", []), ensure_ascii=False)
        recording.clean_uncertainties_json = json.dumps(result.get("uncertainties", []), ensure_ascii=False)
        recording.clean_status = "success"
        recording.clean_provider = runtime.provider_name
    except Exception as exc:  # 原文已落库，清理失败可重试或人工填写
        recording.clean_status = "failed"
        recording.clean_provider = runtime.provider_name
        recording.agent_clean_text = ""
        recording.asr_error = f"口述校对暂不可用：{type(exc).__name__}"
    session.commit()
    _flush_agent_calls(session, runtime, family_id=family_id, input_refs=[recording.id])
    from .service import transcription_to_dict
    return transcription_to_dict(recording)


def extract_recording_evidence(
    session: Session, runtime: AgentRuntime, *, family_id: str, recording_id: str
) -> list[dict[str, Any]]:
    """只从 confirmedText 抽取并持久化事实，quote 必须是其真实子串。"""
    recording = session.scalar(select(Recording).filter_by(id=recording_id, family_id=family_id))
    if recording is None or not recording.fragment_confirmed:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="文字尚未人工确认")
    confirmed = (recording.confirmed_text or recording.transcript or "").strip()
    result = runtime.provider.extract_claims(transcript=confirmed, turn_id=f"fragment-{recording.id}")
    for old in session.scalars(select(MemoryFact).filter_by(fragment_id=recording.id)).all():
        session.delete(old)
    output: list[dict[str, Any]] = []
    for item in result.get("claims", []):
        quote = str(item.get("quote") or "").strip()
        if not quote or quote not in confirmed:
            continue
        fact = MemoryFact(
            id=str(uuid.uuid4()), family_id=family_id, fragment_id=recording.id,
            recording_id=recording.id, narrator_user_id=str(recording.narrator_user_id or ""),
            element=str(item["element"]), text=str(item.get("text") or quote), quote=quote,
            confidence=str(float(item.get("confidence", 0.6))),
            status=str(item.get("status") or ("needs_confirmation" if float(item.get("confidence", 0.6)) < 0.7 else "confident")),
        )
        session.add(fact)
        output.append({"id": fact.id, "fragmentId": fact.fragment_id, "recordingId": fact.recording_id,
                       "narratorUserId": fact.narrator_user_id, "quote": fact.quote,
                       "element": fact.element, "text": fact.text, "confidence": float(fact.confidence),
                       "status": fact.status})
    session.commit()
    _flush_agent_calls(session, runtime, family_id=family_id, input_refs=[recording.id])
    return output


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
    max_rounds: int = 10,
    recording_id: str | None = None,
) -> dict[str, Any]:
    """开一场采访：先装入同一讲述者、同一主题的已确认碎片。"""
    require_consent(session, family_id, consent_version)
    topic = visible_topic(session, family_id, topic_id)
    actor = session.get(User, actor_id)
    narrator_name = actor.display_name if actor else "讲述者"
    recordings = session.scalars(
        select(Recording).where(
            Recording.family_id == family_id,
            Recording.narrator_user_id == actor_id,
            Recording.topic_id == topic_id,
            Recording.fragment_confirmed == 1,
        ).order_by(Recording.fragment_order, Recording.created_at)
    ).all()
    confirmed_by_id = {
        recording.id: (recording.confirmed_text or recording.transcript or "").strip()
        for recording in recordings
    }
    confirmed_by_id = {key: value for key, value in confirmed_by_id.items() if value}
    facts = session.scalars(
        select(MemoryFact).where(
            MemoryFact.family_id == family_id,
            MemoryFact.fragment_id.in_(list(confirmed_by_id)),
        )
    ).all() if confirmed_by_id else []
    context_claims = [
        {
            "id": fact.id, "element": fact.element, "text": fact.text,
            "quote": fact.quote, "turn_id": f"fragment-{fact.fragment_id}",
            "fragment_id": fact.fragment_id, "recording_id": fact.recording_id,
            "narrator_user_id": fact.narrator_user_id,
            "confidence": float(fact.confidence), "status": fact.status,
        }
        for fact in facts
        if fact.element in SEVEN_ELEMENTS
        and fact.quote and fact.quote in confirmed_by_id.get(fact.fragment_id, "")
    ]
    covered = {
        fact.element for fact in facts
        if fact.element in SEVEN_ELEMENTS
        and fact.quote and fact.quote in confirmed_by_id.get(fact.fragment_id, "")
    }
    # 证据 Agent 偶尔会漏掉时间标签；明确写在确认原文里的年份/季节不能再被问一遍。
    if any(re.search(r"(?<!\d)(?:18|19|20)\d{2}年|春天|夏天|秋天|冬天", text)
           for text in confirmed_by_id.values()):
        covered.add("time")
    context_fragments = [confirmed_by_id[item.id] for item in recordings if item.id in confirmed_by_id]
    session_id = f"interview-{uuid.uuid4().hex[:12]}"
    view = runtime.start_session(
        session_id=session_id,
        family_id=family_id,
        actor_id=actor_id,
        # 采访发起人就是本轮讲述者；不信任客户端传来的名字，避免账号切换串名。
        subject_name=narrator_name,
        topic=topic.title if topic else topic_id,
        consent_version=consent_version,
        max_rounds=max_rounds,
        context_fragments=context_fragments,
        context_covered_elements=sorted(covered),
        context_claims=context_claims,
    )
    view["topicId"] = topic_id
    view["recordingId"] = recording_id
    _flush_agent_calls(
        session, runtime, family_id=family_id,
        input_refs=[*confirmed_by_id, *([recording_id] if recording_id else [])],
    )
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
    calls = _flush_agent_calls(session, runtime, family_id=family_id, input_refs=[recording_id] if recording_id else [])

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
            calls=calls,
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
    calls = _flush_agent_calls(session, runtime, family_id=family_id, input_refs=[])
    if _has_draft(view):
        story = _upsert_draft(
            session, family_id=family_id, view=view, topic_id=topic_id, recording_id=None, duration_ms=0,
            calls=calls,
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
    if any(not recording.fragment_confirmed for recording in recordings):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="选中的碎片还没有经过人工确认")

    # 讲述者由录音创建人决定，而不是由当前登录的审核人或客户端参数决定。
    # 这保证小刘替林奶奶校对、排序和生成故事时，署名仍然是林奶奶。
    narrator_ids = {recording.narrator_user_id for recording in recordings if recording.narrator_user_id}
    if len(narrator_ids) > 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="一次只能选择同一位讲述人的记忆碎片生成故事",
        )
    narrator_user_id = next(iter(narrator_ids), None)
    narrator = session.get(User, narrator_user_id) if narrator_user_id else None
    narrator_name = narrator.display_name if narrator else "讲述者"

    topic = visible_topic(session, family_id, topic_id)
    topic_title = topic.title if topic else topic_id or "家庭记忆"
    claims: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    claim_order = 0
    for index, recording in enumerate(recordings):
        turn_id = f"fragment-{index + 1}-{recording.id[:8]}"
        persisted = session.scalars(select(MemoryFact).filter_by(fragment_id=recording.id)).all()
        if not persisted:
            extract_recording_evidence(session, runtime, family_id=family_id, recording_id=recording.id)
            persisted = session.scalars(select(MemoryFact).filter_by(fragment_id=recording.id)).all()
        for fact in persisted:
            claim = {
                "id": fact.id,
                "element": fact.element,
                "text": fact.text,
                "quote": fact.quote,
                "turn_id": turn_id,
                "fragment_id": recording.id,
                "recording_id": recording.id,
                "narrator_user_id": recording.narrator_user_id,
                "confidence": float(fact.confidence),
                "status": fact.status,
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

    graph_session_id = f"fragments-{uuid.uuid4().hex[:12]}"
    view = runtime.start_selected_story(
        session_id=graph_session_id, family_id=family_id,
        actor_id=actor_user_id, subject_name=narrator_name,
        topic=topic_title, consent_version=consent_version,
        claims=claims, recording_refs=refs, style=style,
    )
    draft_sentences = []
    by_claim_id = {str(item["id"]): item for item in claims}
    for item in view.get("draft_sentences", []):
        cited = list(item.get("claim_ids") or [])
        draft_sentences.append({
            **item,
            "fragment_ids": sorted({str(by_claim_id[cid].get("fragment_id"))
                                    for cid in cited if cid in by_claim_id and by_claim_id[cid].get("fragment_id")}),
        })
    findings = view.get("audit_findings", [])
    missing = view.get("missing_fields", [])
    conflicts = view.get("conflicts", [])
    mode = {"raw": "原味口述", "book": "适合成书"}.get(style, "原味口述")
    title = _title_from_draft(view) or topic_title
    body = str(view.get("draft_text") or "")
    story = Story(
        id=str(uuid.uuid4()),
        family_id=family_id,
        narrator_user_id=narrator_user_id,
        topic_id=topic_id,
        title=title or topic_title,
        body=body,
        mode=mode,
        status="pending_review",
        recording_id=recordings[0].id,
        duration_ms=sum(recording.duration_ms or 0 for recording in recordings),
        sort_order=99,
        life_stage={"hometown": "童年", "school": "求学", "work": "工作", "family": "家庭"}.get(topic_id, "未分类"),
        session_id=graph_session_id,
        claims_json=json.dumps(claims, ensure_ascii=False),
        missing_fields_json=json.dumps(missing, ensure_ascii=False),
        findings_json=json.dumps(findings, ensure_ascii=False),
        conflicts_json=json.dumps(conflicts, ensure_ascii=False),
        audit_passed=0 if findings else 1,
        draft_sentences_json=json.dumps(draft_sentences, ensure_ascii=False),
        revision_count=int(view.get("revision_count") or 0),
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
    calls = _flush_agent_calls(session, runtime, family_id=family_id, input_refs=selected_ids)
    workflow = _workflow_from_view(view, calls)
    earlier_steps = []
    cleaned_count = sum(1 for item in recordings if item.clean_status == "success")
    if cleaned_count:
        earlier_steps.append({"agent": "口述校对", "node": "transcript.cleaned_and_reviewed",
                              "fragmentCount": cleaned_count, "source": "persisted"})
    earlier_steps.append({"agent": "证据抽取", "node": "evidence.persisted",
                          "claims": len(claims), "source": "persisted"})
    workflow["steps"] = [*earlier_steps, *workflow["steps"]]
    story.workflow_json = json.dumps(workflow, ensure_ascii=False)
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
    try:
        current = runtime.view(session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="采访会话不存在") from exc
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
    _flush_agent_calls(session, runtime, family_id=family_id, input_refs=[])

    story = session.scalar(select(Story).filter_by(family_id=family_id, session_id=session_id))
    refs = [str(item.get("recording_id")) for item in view.get("recordings", []) if item.get("recording_id")]
    review_outcome = (
        "blocked_by_audit" if action in {"approve", "edit"} and not view.get("audit_passed", True)
        else action
    )
    _record_human_outcome(session, family_id=family_id, outcome=review_outcome, input_refs=refs)

    # 批准或改写后若仍有无证据句子，拒绝发布并说明原因（产品规则：不得新增事实）
    if action in {"approve", "edit"} and not view.get("audit_passed", True):
        findings = view.get("audit_findings", [])
        detail = "；".join(str(f.get("excerpt", "")) for f in findings[:2])
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"正文里有无证据支撑的内容，不能发布：{detail}",
        )
    if action == "reject" and story is not None:
        for link in session.scalars(select(StoryRecording).filter_by(story_id=story.id)).all():
            session.delete(link)
        session.delete(story)
        session.commit()
    elif action in {"approve", "edit"} and story is not None and view.get("audit_passed", True):
        story.body = str(view.get("approved_text") or view.get("draft_text") or story.body)
        story.status = "confirmed"
        story.audit_passed = 1
        story.findings_json = "[]"
        session.commit()
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
        findings = [
            *_audit_edited_text(body=body, claims=claims),
            *unresolved_conflict_findings(json.loads(story.conflicts_json or "[]")),
        ]
        if not findings:
            sentences = [
                {"id": f"review-s{index:02d}", "text": line.strip(),
                 "claim_ids": [str(item.get("id")) for item in claims],
                 "fragment_ids": sorted({str(item.get("fragment_id") or "") for item in claims} - {""}),
                 "must_cite": not line.strip().startswith(("《", "这是", "——"))}
                for index, line in enumerate(body.splitlines()) if line.strip()
            ]
            findings = runtime.provider.audit_draft(sentences=sentences, claims=claims)
            story.draft_sentences_json = json.dumps(sentences, ensure_ascii=False)
            story.findings_json = json.dumps(findings, ensure_ascii=False)
            story.audit_passed = 0 if findings else 1
            session.commit()
            _flush_agent_calls(session, runtime, family_id=family_id,
                               input_refs=sorted({str(item.get("fragment_id") or "") for item in claims} - {""}))
        if findings:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "这段文字里有无法追溯到原声的内容，不能直接发布："
                    + "；".join(str(f.get("excerpt", "")) for f in findings[:2])
                ),
            )

    # 新版勾选碎片故事有真正的 LangGraph 人工确认点；旧版草稿仍可沿用
    # 证据复审，避免迁移时让已保存故事失去确认入口。
    if story.session_id.startswith("fragments-"):
        try:
            graph_view = runtime.view(story.session_id)
        except SessionNotFoundError:
            graph_view = None
        if graph_view is not None:
            pending = {item["value"].get("kind") for item in graph_view.get("interrupts", [])}
            if "review" in pending:
                action = "edit" if body != graph_view.get("draft_text", "") else "approve"
                graph_view = runtime.resume(
                    story.session_id,
                    {"action": action, **({"edited_text": body} if action == "edit" else {})},
                )
                _flush_agent_calls(session, runtime, family_id=family_id,
                                   input_refs=[str(item.get("fragment_id")) for item in claims if item.get("fragment_id")])
                if not graph_view.get("audit_passed", False):
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                        detail="协作图审计未通过，不能确认故事")

    story.body = body
    story.status = "confirmed"
    story.audit_passed = 1
    _record_human_outcome(
        session, family_id=family_id, outcome="approve",
        input_refs=sorted({str(item.get("fragment_id") or "") for item in claims} - {""}),
    )
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
    calls: list[dict[str, Any]] | None = None,
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
    story.mode = "原味口述"
    story.status = "pending_review"
    story.audit_passed = 1 if view.get("audit_passed", True) else 0
    story.claims_json = json.dumps(view.get("claims", []), ensure_ascii=False)
    story.missing_fields_json = json.dumps(view.get("missing_fields", []), ensure_ascii=False)
    story.findings_json = json.dumps(view.get("audit_findings", []), ensure_ascii=False)
    story.conflicts_json = json.dumps(view.get("conflicts", []), ensure_ascii=False)
    story.draft_sentences_json = json.dumps(view.get("draft_sentences", []), ensure_ascii=False)
    story.workflow_json = json.dumps(_workflow_from_view(view, calls or []), ensure_ascii=False)
    if recording_id:
        story.recording_id = recording_id
        recording = session.get(Recording, recording_id)
        if recording and recording.family_id == family_id:
            story.narrator_user_id = recording.narrator_user_id
    if not story.narrator_user_id and view.get("actor_id"):
        story.narrator_user_id = str(view["actor_id"])
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
