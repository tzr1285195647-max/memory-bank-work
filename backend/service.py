"""业务逻辑：授权校验、故事读写、录音落盘。

关键产品规则（写死在服务层，接口层不得绕过）：
- 所有查询按 family_id 范围过滤（禁止仅凭资源 ID 查询）
- 写请求校验 consent_version；撤回后一律拒绝
- 故事未确认前状态为 pending_review，不对外发布
- 原声上传到服务端生成的对象键，不信任原始文件名
"""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .asr import AsrError, create_client, prepare_audio_for_asr
from .database import (
    AuditEvent,
    ConsentGrant,
    Family,
    FamilyNote,
    Membership,
    Recording,
    Story,
    StoryRecording,
    Topic,
    User,
)
from .security import create_access_token, hash_password, verify_password


class ConsentError(HTTPException):
    def __init__(self, detail: str = "授权已撤回或版本过期") -> None:
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


AUDIT_CATEGORY_LABELS = {
    "story": "故事",
    "family": "家庭协作",
    "privacy": "隐私授权",
    "audio": "原声",
}


def append_audit(
    session: Session,
    family_id: str,
    *,
    action: str,
    category: str,
    summary: str,
    actor_user_id: str | None = None,
    target_id: str = "",
) -> None:
    user = session.get(User, actor_user_id) if actor_user_id else None
    session.add(
        AuditEvent(
            id=str(uuid.uuid4()),
            family_id=family_id,
            actor_user_id=actor_user_id,
            actor_name=user.display_name if user else "系统",
            action=action,
            category=category,
            summary=summary,
            target_id=target_id,
        )
    )


def list_audit_events(session: Session, family_id: str, limit: int = 50) -> dict:
    rows = session.scalars(
        select(AuditEvent)
        .filter_by(family_id=family_id)
        .order_by(AuditEvent.created_at.desc())
        .limit(max(1, min(limit, 100)))
    ).all()
    items = [
        {
            "id": row.id,
            "action": row.action,
            "category": row.category,
            "categoryLabel": AUDIT_CATEGORY_LABELS.get(row.category, "其他"),
            "summary": row.summary,
            "actorName": row.actor_name,
            "timeLabel": relative_day(row.created_at),
            "createdAt": row.created_at.isoformat(),
        }
        for row in rows
    ]
    return {"items": items, "total": len(items)}


def mask_phone(phone: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) != 11:
        return phone
    return f"{digits[:3]}****{digits[7:]}"


def format_duration(ms: int) -> str:
    total = max(0, int((ms or 0) / 1000))
    return f"{total // 60:02d}:{total % 60:02d}"


def relative_day(value: datetime) -> str:
    created = value if value.tzinfo else value.replace(tzinfo=UTC)
    days = (datetime.now(UTC).date() - created.date()).days
    if days <= 0:
        return "今天"
    if days == 1:
        return "昨天"
    if days < 7:
        return f"{days} 天前"
    if days < 14:
        return "上周"
    return f"{created.month} 月 {created.day} 日"


# ------------------------------------------------------------------ 认证


def login(session: Session, *, phone: str, password: str, role: str) -> dict:
    """演示友好：手机号首次登录即注册；已有账号校验口令。"""
    user = session.scalar(select(User).filter_by(phone=phone))
    if user is None:
        user = User(
            id=str(uuid.uuid4()),
            phone=phone,
            display_name="林阿姨" if role == "elder" else "家人",
            role=role,
            password_hash=hash_password(password),
        )
        session.add(user)
        session.flush()
    elif not verify_password(password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="手机号或密码不正确")
    user.role = role

    membership = session.scalar(select(Membership).filter_by(user_id=user.id))
    if membership is None:
        family = Family(id=str(uuid.uuid4()), name="我的家")
        session.add(family)
        session.flush()
        session.add(Membership(id=str(uuid.uuid4()), family_id=family.id, user_id=user.id, role=role))
        session.add(ConsentGrant(id=str(uuid.uuid4()), family_id=family.id, subject_user_id=user.id))
        append_audit(
            session, family.id, action="consent_granted", category="privacy",
            summary="同意使用原声整理家庭故事", actor_user_id=user.id,
        )
    else:
        family = session.get(Family, membership.family_id)

    session.commit()

    consent = active_consent(session, family.id)
    token, expires_at = create_access_token(user_id=user.id, family_id=family.id, role=role)
    return {
        "token": token,
        "expiresAt": expires_at.isoformat(),
        "familyId": family.id,
        "consentVersion": consent.version,
        "user": {
            "id": user.id,
            "displayName": user.display_name,
            "phoneMasked": mask_phone(user.phone),
            "role": user.role,
        },
    }


def active_consent(session: Session, family_id: str) -> ConsentGrant:
    consent = session.scalar(
        select(ConsentGrant)
        .filter_by(family_id=family_id, status="active")
        .order_by(ConsentGrant.version.desc())
    )
    if consent is None:
        raise ConsentError("授权不存在或已撤回")
    return consent


def require_consent(session: Session, family_id: str, version: int) -> ConsentGrant:
    consent = active_consent(session, family_id)
    if int(version) != int(consent.version):
        raise ConsentError(f"授权版本过期（当前 {consent.version}）")
    return consent


# ------------------------------------------------------------------ 主题与首页


def list_topics(session: Session) -> list[dict]:
    rows = session.scalars(select(Topic).order_by(Topic.sort_order)).all()
    return [{"id": t.id, "glyph": t.glyph, "title": t.title, "subtitle": t.subtitle} for t in rows]


def build_home(session: Session, family_id: str) -> dict:
    topics = list_topics(session)
    today = next((t for t in topics if t["id"] == "hometown"), topics[0] if topics else None)
    recent = [
        story_to_dict(session, story, index + 1)
        for index, story in enumerate(
            session.scalars(
                select(Story)
                .filter_by(family_id=family_id, status="confirmed")
                .order_by(Story.sort_order)
                .limit(2)
            ).all()
        )
    ]
    return {
        "today": {
            "label": "今日叙事",
            "title": today["title"] if today else "",
            "subtitle": today["subtitle"] if today else "",
            "topicId": today["id"] if today else "",
        },
        "recent": recent,
    }


# ------------------------------------------------------------------ 录音


def save_recording(
    session: Session,
    *,
    family_id: str,
    topic_id: str,
    duration_ms: int,
    consent_version: int,
    upload: UploadFile,
    actor_user_id: str | None = None,
) -> dict:
    require_consent(session, family_id, consent_version)

    payload = upload.file.read()
    if not payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="录音内容为空")
    if len(payload) > settings.max_upload_bytes:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="录音文件过大")

    # 服务端按内容摘要生成对象键，不使用原始文件名
    digest = hashlib.sha256(payload).hexdigest()[:24]
    suffix = Path(upload.filename or "audio.mp3").suffix or ".mp3"
    object_key = f"{family_id}/{digest}{suffix}"
    target = settings.objects_dir / object_key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)

    recording = Recording(
        id=str(uuid.uuid4()),
        family_id=family_id,
        topic_id=topic_id,
        object_key=object_key,
        duration_ms=duration_ms,
        consent_version=consent_version,
    )
    session.add(recording)
    append_audit(
        session,
        family_id,
        action="recording_saved",
        category="audio",
        summary="保存了一段故事原声",
        actor_user_id=actor_user_id,
        target_id=recording.id,
    )
    session.commit()
    return {
        "assetId": recording.id,
        "durationMs": recording.duration_ms,
        "audioUrl": f"/media/{object_key}",
        "transcript": recording.transcript,
        "consentVersion": recording.consent_version,
    }


def asr_status() -> dict:
    """仅返回可用状态，不向前端暴露任何凭据。"""
    return {
        "provider": settings.asr_provider,
        "configured": settings.asr_enabled,
        "engine": settings.tencent_asr_engine if settings.asr_enabled else None,
    }


def _recording_for_family(session: Session, family_id: str, recording_id: str) -> Recording:
    recording = session.scalar(select(Recording).filter_by(id=recording_id, family_id=family_id))
    if recording is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="录音不存在")
    return recording


def transcription_to_dict(recording: Recording) -> dict:
    return {
        "recordingId": recording.id,
        "status": recording.asr_status or "idle",
        "transcript": recording.transcript or "",
        "error": recording.asr_error or "",
        "provider": settings.asr_provider,
    }


def confirm_memory_fragment(
    session: Session,
    *,
    family_id: str,
    recording_id: str,
    transcript: str,
    consent_version: int,
    actor_user_id: str | None = None,
) -> dict:
    """保存人工校对后的碎片文字；生成故事时以此版本为准。"""

    require_consent(session, family_id, consent_version)
    recording = _recording_for_family(session, family_id, recording_id)
    recording.transcript = transcript.strip()
    recording.asr_status = "success"
    recording.asr_error = ""
    append_audit(
        session,
        family_id,
        action="memory_fragment_confirmed",
        category="audio",
        summary="确认并保存了一段记忆碎片",
        actor_user_id=actor_user_id,
        target_id=recording.id,
    )
    session.commit()
    return {
        "assetId": recording.id,
        "durationMs": recording.duration_ms,
        "audioUrl": f"/media/{recording.object_key}",
        "transcript": recording.transcript,
        "consentVersion": recording.consent_version,
    }


def start_transcription(
    session: Session,
    *,
    family_id: str,
    recording_id: str,
    consent_version: int,
    actor_user_id: str | None = None,
) -> dict:
    require_consent(session, family_id, consent_version)
    recording = _recording_for_family(session, family_id, recording_id)
    if recording.transcript:
        recording.asr_status = "success"
        session.commit()
        return transcription_to_dict(recording)
    audio_path = settings.objects_dir / recording.object_key
    if not audio_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="录音文件已不存在")
    try:
        # 先验证真实音频内容。这样即使旧任务仍显示“等待中”，静音或设备实际
        # 输出 WebM 的问题也能在重试时立即给出准确提示。
        prepared_audio = prepare_audio_for_asr(audio_path.read_bytes())
        if recording.asr_task_id and recording.asr_status in {"waiting", "processing"}:
            return transcription_to_dict(recording)
        recording.asr_task_id = create_client().submit(prepared_audio)
    except AsrError as exc:
        recording.asr_status = "failed"
        recording.asr_error = str(exc)
        session.commit()
        if exc.code == "ASR_NOT_CONFIGURED":
            code = status.HTTP_503_SERVICE_UNAVAILABLE
        elif exc.code.startswith("AUDIO_") or exc.code in {"EMPTY_AUDIO", "SILENT_AUDIO"}:
            code = status.HTTP_400_BAD_REQUEST
        else:
            code = status.HTTP_502_BAD_GATEWAY
        raise HTTPException(status_code=code, detail=str(exc)) from exc

    recording.asr_status = "waiting"
    recording.asr_error = ""
    append_audit(
        session,
        family_id,
        action="transcription_started",
        category="audio",
        summary="开始识别一段故事原声",
        actor_user_id=actor_user_id,
        target_id=recording.id,
    )
    session.commit()
    return transcription_to_dict(recording)


def refresh_transcription(
    session: Session,
    *,
    family_id: str,
    recording_id: str,
) -> dict:
    recording = _recording_for_family(session, family_id, recording_id)
    if recording.asr_status in {"success", "failed"}:
        return transcription_to_dict(recording)
    if not recording.asr_task_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该录音尚未提交识别")

    try:
        result = create_client().query(recording.asr_task_id)
    except AsrError as exc:
        # 网络抖动不把云端任务标成永久失败，前端仍可稍后重试查询。
        if exc.retryable:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        recording.asr_status = "failed"
        recording.asr_error = str(exc)
        session.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    recording.asr_status = result.status
    recording.asr_error = result.error
    if result.status == "success":
        recording.transcript = result.transcript
        append_audit(
            session,
            family_id,
            action="transcription_completed",
            category="audio",
            summary="完成一段故事原声的文字识别",
            target_id=recording.id,
        )
    session.commit()
    return transcription_to_dict(recording)


# ------------------------------------------------------------------ 故事


def story_to_dict(session: Session, story: Story, index: int | None = None) -> dict:
    audio_url = None
    if story.recording_id:
        recording = session.get(Recording, story.recording_id)
        if recording:
            audio_url = f"/media/{recording.object_key}"
    recordings = []
    links = session.scalars(
        select(StoryRecording)
        .filter_by(story_id=story.id)
        .order_by(StoryRecording.round_index)
    ).all()
    for link in links:
        item = session.get(Recording, link.recording_id)
        if item:
            recordings.append(
                {
                    "recordingId": item.id,
                    "turnId": link.turn_id,
                    "roundIndex": link.round_index,
                    "durationMs": link.duration_ms or item.duration_ms,
                    "audioUrl": f"/media/{item.object_key}",
                    "transcript": item.transcript or "",
                    "timeLabel": item.created_at.strftime("%m月%d日 %H:%M"),
                    "speakerLabel": link.speaker_label or "长辈",
                }
            )
    if recordings and not audio_url:
        audio_url = recordings[0]["audioUrl"]
    family_note_count = (
        session.scalar(
            select(func.count()).select_from(FamilyNote).filter_by(story_id=story.id)
        )
        or 0
    )
    return {
        "id": story.id,
        "topicId": story.topic_id,
        "index": f"{index:02d}" if index else "",
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
        "recordings": recordings,
        "familyNoteCount": family_note_count,
        "memoryYear": story.memory_year,
        "lifeStage": story.life_stage or "未分类",
    }


def family_note_to_dict(note: FamilyNote) -> dict:
    return {
        "id": note.id,
        "storyId": note.story_id,
        "authorName": note.author_name,
        "kind": note.kind,
        "kindLabel": "补充回忆" if note.kind == "supplement" else "修改建议",
        "content": note.content,
        "status": note.status,
        "statusLabel": {
            "pending": "等待长辈处理",
            "accepted": "已采纳",
            "ignored": "暂不采用",
        }.get(note.status, note.status),
        "dayLabel": relative_day(note.created_at),
    }


def list_family_notes(session: Session, family_id: str, story_id: str) -> list[dict]:
    _get_story(session, family_id, story_id)
    rows = session.scalars(
        select(FamilyNote)
        .filter_by(story_id=story_id, family_id=family_id)
        .order_by(FamilyNote.created_at.desc())
    ).all()
    return [family_note_to_dict(item) for item in rows]


def add_family_note(
    session: Session,
    family_id: str,
    story_id: str,
    *,
    user_id: str,
    role: str,
    kind: str,
    content: str,
    consent_version: int,
) -> dict:
    require_consent(session, family_id, consent_version)
    if role != "family":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="只有家人账号可以提交建议")
    story = _get_story(session, family_id, story_id)
    if story.status != "pending_review":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="只能对待确认故事提交建议")
    user = session.get(User, user_id)
    note = FamilyNote(
        id=str(uuid.uuid4()),
        story_id=story.id,
        family_id=family_id,
        author_user_id=user_id,
        author_name=(user.display_name if user else "家人"),
        kind=kind,
        content=content,
        status="pending",
    )
    session.add(note)
    append_audit(
        session, family_id, action="family_note_added", category="family",
        summary=f"给《{story.title}》提交了{'补充回忆' if kind == 'supplement' else '修改建议'}",
        actor_user_id=user_id, target_id=story.id,
    )
    session.commit()
    return family_note_to_dict(note)


def resolve_family_note(
    session: Session,
    family_id: str,
    story_id: str,
    note_id: str,
    *,
    role: str,
    action: str,
    consent_version: int,
    actor_user_id: str | None = None,
) -> dict:
    require_consent(session, family_id, consent_version)
    if role != "elder":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="只有长辈账号可以处理建议")
    story = _get_story(session, family_id, story_id)
    note = session.scalar(
        select(FamilyNote).filter_by(id=note_id, story_id=story_id, family_id=family_id)
    )
    if note is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="家庭建议不存在")
    if note.status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="这条建议已经处理")
    note.status = "accepted" if action == "accept" else "ignored"
    note.resolved_at = datetime.now(UTC)
    append_audit(
        session, family_id, action="family_note_resolved", category="family",
        summary=f"{'采纳' if action == 'accept' else '暂不采用'}了《{story.title}》的一条家人建议",
        actor_user_id=actor_user_id, target_id=story.id,
    )
    session.commit()
    return family_note_to_dict(note)


def _get_story(session: Session, family_id: str, story_id: str) -> Story:
    story = session.scalar(select(Story).filter_by(id=story_id, family_id=family_id))
    if story is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="故事不存在")
    return story


def list_stories(session: Session, family_id: str, *, only_status: str | None = None) -> dict:
    query = select(Story).filter_by(family_id=family_id)
    if only_status:
        query = query.filter_by(status=only_status)
    rows = session.scalars(query.order_by(Story.sort_order)).all()
    items = [story_to_dict(session, story, i + 1) for i, story in enumerate(rows)]
    return {"items": items, "total": len(items)}


def get_story(session: Session, family_id: str, story_id: str) -> dict:
    return story_to_dict(session, _get_story(session, family_id, story_id))


def update_story(
    session: Session,
    family_id: str,
    story_id: str,
    *,
    body: str,
    mode: str | None = None,
    memory_year: int | None = None,
    life_stage: str | None = None,
    actor_user_id: str | None = None,
    consent_version: int,
) -> dict:
    require_consent(session, family_id, consent_version)
    story = _get_story(session, family_id, story_id)
    content_changed = story.body != body or bool(mode and story.mode != mode)
    story.body = body
    if mode:
        story.mode = mode
    if memory_year is not None:
        story.memory_year = memory_year
    if life_stage:
        story.life_stage = life_stage
    if content_changed:
        story.status = "pending_review"  # 正文或整理方式改变后必须重新确认
    append_audit(
        session,
        family_id,
        action="story_updated" if content_changed else "timeline_updated",
        category="story",
        summary=f"{'修改了故事' if content_changed else '补充了记忆坐标'}《{story.title}》",
        actor_user_id=actor_user_id,
        target_id=story.id,
    )
    session.commit()
    return story_to_dict(session, story)


def audit_story_text(
    session: Session,
    family_id: str,
    story_id: str,
    *,
    body: str,
    consent_version: int,
) -> dict:
    """预确认审计：只检查并记录结果，不改变发布状态。"""
    from .evidence_audit import audit_text

    require_consent(session, family_id, consent_version)
    story = _get_story(session, family_id, story_id)
    claims = json.loads(story.claims_json or "[]")
    findings = audit_text(body=body, claims=claims) if claims else []
    story.findings_json = json.dumps(findings, ensure_ascii=False)
    story.audit_passed = 0 if findings else 1
    story.status = "pending_review"
    session.commit()
    return {"auditPassed": not findings, "findings": findings}


def discard_story(
    session: Session, family_id: str, story_id: str, *, consent_version: int,
    actor_user_id: str | None = None,
) -> dict:
    """放弃待确认草稿，并删除仅由它引用的临时原声。"""
    require_consent(session, family_id, consent_version)
    story = _get_story(session, family_id, story_id)
    if story.status != "pending_review":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="已确认故事不能作为草稿放弃")

    links = session.scalars(select(StoryRecording).filter_by(story_id=story.id)).all()
    recording_ids = {link.recording_id for link in links}
    if story.recording_id:
        recording_ids.add(story.recording_id)
    for link in links:
        session.delete(link)
    for note in session.scalars(select(FamilyNote).filter_by(story_id=story.id)).all():
        session.delete(note)
    story_title = story.title
    session.delete(story)
    session.flush()

    deleted_recordings = 0
    for recording_id in recording_ids:
        used_by_link = session.scalar(
            select(func.count()).select_from(StoryRecording).filter_by(recording_id=recording_id)
        )
        used_by_story = session.scalar(
            select(func.count()).select_from(Story).filter_by(recording_id=recording_id)
        )
        if used_by_link or used_by_story:
            continue
        recording = session.get(Recording, recording_id)
        if recording and recording.family_id == family_id:
            (settings.objects_dir / recording.object_key).unlink(missing_ok=True)
            session.delete(recording)
            deleted_recordings += 1
    append_audit(
        session, family_id, action="story_discarded", category="story",
        summary=f"放弃了待确认故事《{story_title}》", actor_user_id=actor_user_id,
    )
    session.commit()
    return {
        "discardedStoryId": story_id,
        "deletedRecordings": deleted_recordings,
        "message": "本次草稿与临时原声已删除",
    }


def confirm_story(
    session: Session,
    family_id: str,
    story_id: str,
    *,
    role: str,
    consent_version: int,
    actor_user_id: str | None = None,
) -> dict:
    require_consent(session, family_id, consent_version)
    if role != "elder":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="只有长辈账号可以确认故事")
    story = _get_story(session, family_id, story_id)
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
    story.status = "confirmed"
    append_audit(
        session, family_id, action="story_confirmed", category="story",
        summary=f"确认并保存了故事《{story.title}》", actor_user_id=actor_user_id,
        target_id=story.id,
    )
    session.commit()
    return story_to_dict(session, story)


def create_draft(
    session: Session,
    *,
    family_id: str,
    topic_id: str,
    duration_ms: int,
    consent_version: int,
    recording_id: str | None = None,
    actor_user_id: str | None = None,
) -> dict:
    """录音结束后生成待确认草稿。

    真实版本由 Agent 依据证据链生成正文，当前用明确标注的占位文案，
    避免把非证据内容伪装成讲述原文。
    """
    require_consent(session, family_id, consent_version)
    topic = session.get(Topic, topic_id)
    story = Story(
        id=str(uuid.uuid4()),
        family_id=family_id,
        topic_id=topic_id,
        title=topic.title if topic else "未命名主题",
        body="（这段文字将根据你的讲述生成，当前为演示占位内容。）",
        mode="自然整理",
        status="pending_review",
        recording_id=recording_id,
        duration_ms=duration_ms,
        sort_order=99,
        life_stage={
            "hometown": "童年",
            "school": "求学",
            "work": "工作",
            "family": "家庭",
        }.get(topic_id, "未分类"),
    )
    session.add(story)
    append_audit(
        session, family_id, action="draft_created", category="story",
        summary=f"创建了待确认故事《{story.title}》", actor_user_id=actor_user_id,
        target_id=story.id,
    )
    session.commit()
    return story_to_dict(session, story)


# ------------------------------------------------------------------ 家庭与撤回


def family_board(session: Session, family_id: str) -> dict:
    member_count = (
        session.scalar(select(func.count()).select_from(Membership).filter_by(family_id=family_id)) or 0
    )
    confirmed = (
        session.scalar(
            select(func.count()).select_from(Story).filter_by(family_id=family_id, status="confirmed")
        )
        or 0
    )
    total = session.scalar(select(func.count()).select_from(Story).filter_by(family_id=family_id)) or 0
    pending = [
        story_to_dict(session, story, i + 1)
        for i, story in enumerate(
            session.scalars(
                select(Story).filter_by(family_id=family_id, status="pending_review")
            ).all()
        )
    ]
    return {
        "memberCount": member_count,
        "invitedCount": max(0, member_count - 1),
        "doneStories": confirmed,
        "totalStories": total,
        "pending": pending,
    }


def revoke_consent(session: Session, family_id: str, actor_user_id: str | None = None) -> dict:
    """撤回授权：删除内容与全部原声，保留授权与审计记录。

    注意：不能只删「被故事引用」的录音——刚上传但还没生成草稿的录音同样必须删除，
    否则原声会残留在服务器上，违反产品规则。
    """
    consent = active_consent(session, family_id)

    deleted_stories = 0
    story_ids = [
        item.id for item in session.scalars(select(Story).filter_by(family_id=family_id)).all()
    ]
    if story_ids:
        for note in session.scalars(
            select(FamilyNote).where(FamilyNote.story_id.in_(story_ids))
        ).all():
            session.delete(note)
        for link in session.scalars(
            select(StoryRecording).where(StoryRecording.story_id.in_(story_ids))
        ).all():
            session.delete(link)
    for story in session.scalars(select(Story).filter_by(family_id=family_id)).all():
        session.delete(story)
        deleted_stories += 1

    deleted_recordings = 0
    for recording in session.scalars(select(Recording).filter_by(family_id=family_id)).all():
        (settings.objects_dir / recording.object_key).unlink(missing_ok=True)
        session.delete(recording)
        deleted_recordings += 1

    # 兜底：清掉该家庭的对象目录，防止孤儿文件（对象键以 family_id 开头）
    family_dir = settings.objects_dir / family_id
    if family_dir.exists():
        shutil.rmtree(family_dir, ignore_errors=True)

    consent.status = "revoked"
    consent.revoked_at = datetime.now(UTC)
    session.add(
        ConsentGrant(
            id=str(uuid.uuid4()),
            family_id=family_id,
            subject_user_id=consent.subject_user_id,
            scope=consent.scope,
            version=consent.version + 1,
            status="revoked",
            revoked_at=datetime.now(UTC),
        )
    )
    append_audit(
        session, family_id, action="consent_revoked", category="privacy",
        summary=f"撤回授权，删除 {deleted_stories} 个故事和 {deleted_recordings} 段原声",
        actor_user_id=actor_user_id,
    )
    session.commit()
    return {
        "revokedAt": consent.revoked_at.isoformat(),
        "deletedStories": deleted_stories,
        "deletedRecordings": deleted_recordings,
        "message": "内容与原声已删除，审计记录保留",
    }


def profile(session: Session, family_id: str, user_id: str) -> dict:
    user = session.get(User, user_id)
    board = family_board(session, family_id)
    minutes = sum(
        int((story.duration_ms or 0) / 60000)
        for story in session.scalars(select(Story).filter_by(family_id=family_id)).all()
    )
    name = user.display_name if user else ""
    return {
        "displayName": name,
        "avatarText": (name or "记")[0],
        "roleLabel": "长辈账号" if (user and user.role == "elder") else "家人账号",
        "phoneMasked": mask_phone(user.phone) if user else "",
        "stats": {
            "storyCount": board["doneStories"],
            "audioMinutes": minutes,
            "memberCount": board["memberCount"],
        },
    }
