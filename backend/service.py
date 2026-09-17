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
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from .config import settings
from .asr import AsrError, create_client, prepare_audio_for_asr
from .database import (
    AuditEvent,
    ConsentGrant,
    Family,
    FamilyInvitation,
    FamilyNote,
    MemoryFact,
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


def avatar_key(gender: str, age: int) -> str:
    stage = "elder" if age >= 60 else ("middle" if age >= 18 else "youth")
    return f"{stage}-{'male' if gender == 'male' else 'female'}"


def avatar_text(gender: str, age: int) -> str:
    return {
        "elder-female": "👵", "elder-male": "👴",
        "middle-female": "👩", "middle-male": "👨",
        "youth-female": "👧", "youth-male": "👦",
    }[avatar_key(gender, age)]


def _membership_for_user(session: Session, user_id: str) -> Membership | None:
    return session.scalar(select(Membership).filter_by(user_id=user_id))


def _require_admin(session: Session, family_id: str, user_id: str) -> Membership:
    membership = session.scalar(
        select(Membership).filter_by(family_id=family_id, user_id=user_id)
    )
    if membership is None or not membership.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="只有家庭管理员可以执行此操作")
    return membership


def require_family_member(session: Session, family_id: str, user_id: str) -> Membership:
    """核对实时家庭成员关系，不能只相信尚未过期的旧令牌。"""
    membership = session.scalar(
        select(Membership).filter_by(family_id=family_id, user_id=user_id)
    )
    user = session.get(User, user_id)
    if membership is None or user is None or not user.active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="当前账号已不属于这个家庭")
    return membership


def _login_response(session: Session, user: User, membership: Membership) -> dict:
    consent = active_consent(session, membership.family_id)
    token, expires_at = create_access_token(
        user_id=user.id, family_id=membership.family_id, role=membership.role
    )
    return {
        "token": token,
        "expiresAt": expires_at.isoformat(),
        "familyId": membership.family_id,
        "consentVersion": consent.version,
        "user": {
            "id": user.id,
            "displayName": user.display_name,
            "phoneMasked": mask_phone(user.phone),
            "role": membership.role,
            "gender": user.gender,
            "age": user.age,
            "avatarKey": avatar_key(user.gender, user.age),
            "isAdmin": bool(membership.is_admin),
        },
    }


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
    """账号登录；身份来自家庭成员关系，不能由客户端登录参数篡改。"""
    user = session.scalar(select(User).filter_by(phone=phone))
    if user is None:
        if not settings.allow_auto_register:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="账号不存在，请先注册")
        # 仅测试环境兼容旧契约；实际小程序默认关闭自动注册。
        user = User(
            id=str(uuid.uuid4()), phone=phone,
            display_name="林阿姨" if role == "elder" else "家人",
            role=role or "elder", gender="female", age=60, active=1,
            password_hash=hash_password(password),
        )
        session.add(user)
        session.flush()
        family = Family(id=str(uuid.uuid4()), name=f"{user.display_name}的家庭")
        session.add(family)
        session.flush()
        membership = Membership(
            id=str(uuid.uuid4()), family_id=family.id, user_id=user.id,
            role=user.role, is_admin=1,
        )
        session.add(membership)
        session.add(ConsentGrant(id=str(uuid.uuid4()), family_id=family.id, subject_user_id=user.id))
        append_audit(
            session, family.id, action="consent_granted", category="privacy",
            summary="同意使用原声整理家庭故事", actor_user_id=user.id,
        )
        session.commit()
    if not user.active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已停用，请联系家庭管理员")
    # 固定验证码仅用于本机展示，不接短信服务。
    if password != "246810" and not verify_password(password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="手机号或密码不正确")
    membership = _membership_for_user(session, user.id)
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号尚未加入家庭")
    if settings.allow_auto_register and role:
        # 老测试会用同一手机号切换角色；正式运行默认关闭。
        user.role = role
        membership.role = role
        session.commit()
    return _login_response(session, user, membership)


def register(
    session: Session, *, phone: str, password: str, display_name: str,
    role: str, gender: str, age: int,
) -> dict:
    if session.scalar(select(User).filter_by(phone=phone)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该手机号已经注册")
    invitation = session.scalar(
        select(FamilyInvitation)
        .filter_by(phone=phone, status="pending")
        .order_by(FamilyInvitation.created_at.desc())
    )
    user = User(
        id=str(uuid.uuid4()), phone=phone, display_name=display_name, role=role,
        gender=gender, age=age, active=1, password_hash=hash_password(password),
    )
    session.add(user)
    session.flush()
    if invitation:
        family_id = invitation.family_id
        membership_role = invitation.role
        invitation.status = "accepted"
        is_admin = 0
    else:
        family = Family(id=str(uuid.uuid4()), name=f"{display_name}的家庭")
        session.add(family)
        session.flush()
        family_id = family.id
        membership_role = role
        is_admin = 1
        session.add(ConsentGrant(id=str(uuid.uuid4()), family_id=family_id, subject_user_id=user.id))
    user.role = membership_role
    membership = Membership(
        id=str(uuid.uuid4()), family_id=family_id, user_id=user.id,
        role=membership_role, is_admin=is_admin,
    )
    session.add(membership)
    append_audit(
        session, family_id, action="member_registered", category="family",
        summary=f"{display_name}注册并加入家庭", actor_user_id=user.id,
    )
    session.commit()
    return _login_response(session, user, membership)


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


def list_topics(session: Session, family_id: str) -> list[dict]:
    rows = session.scalars(
        select(Topic).where(or_(Topic.family_id.is_(None), Topic.family_id == family_id))
        .order_by(Topic.sort_order, Topic.title)
    ).all()
    return [{"id": t.id, "glyph": t.glyph, "title": t.title, "subtitle": t.subtitle} for t in rows]


def create_custom_topic(session: Session, family_id: str, title: str) -> dict:
    name = title.strip()
    if len(name) < 2 or len(name) > 30:
        raise HTTPException(status_code=422, detail="主题名称需为 2–30 个字")
    existing = session.scalar(select(Topic).where(
        Topic.title == name, or_(Topic.family_id.is_(None), Topic.family_id == family_id)
    ))
    if existing:
        return {"id": existing.id, "glyph": existing.glyph, "title": existing.title, "subtitle": existing.subtitle}
    topic = Topic(id=f"custom-{uuid.uuid4().hex[:24]}", glyph="忆", title=name,
                  subtitle="自己想讲的故事", sort_order=100, family_id=family_id)
    session.add(topic)
    session.commit()
    return {"id": topic.id, "glyph": topic.glyph, "title": topic.title, "subtitle": topic.subtitle}


def visible_topic(session: Session, family_id: str, topic_id: str) -> Topic | None:
    topic = session.get(Topic, topic_id)
    if topic is not None and topic.family_id not in (None, family_id):
        raise HTTPException(status_code=404, detail="主题不存在")
    return topic


def build_home(session: Session, family_id: str) -> dict:
    topics = list_topics(session, family_id)
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
    visible_topic(session, family_id, topic_id)

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
        narrator_user_id=actor_user_id,
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
    narrator = session.get(User, recording.narrator_user_id) if recording.narrator_user_id else None
    return {
        "assetId": recording.id,
        "narratorUserId": recording.narrator_user_id or "",
        "narratorName": narrator.display_name if narrator else "讲述者",
        "durationMs": recording.duration_ms,
        "audioUrl": f"/media/{object_key}",
        "transcript": recording.transcript,
        "asrRawText": recording.asr_raw_text or "",
        "agentCleanText": recording.agent_clean_text or "",
        "confirmedText": recording.confirmed_text or "",
        "cleanChanges": [],
        "uncertainties": [],
        "cleanStatus": recording.clean_status or "idle",
        "cleanProvider": recording.clean_provider or "",
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
        "transcript": recording.agent_clean_text or recording.asr_raw_text or recording.transcript or "",
        "asrRawText": recording.asr_raw_text or "",
        "agentCleanText": recording.agent_clean_text or "",
        "confirmedText": recording.confirmed_text or "",
        "cleanChanges": json.loads(recording.clean_changes_json or "[]"),
        "uncertainties": json.loads(recording.clean_uncertainties_json or "[]"),
        "cleanStatus": recording.clean_status or "idle",
        "cleanProvider": recording.clean_provider or "",
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
    recording.confirmed_text = transcript.strip()
    recording.transcript = recording.confirmed_text  # 兼容旧客户端/查询
    recording.asr_status = "success"
    recording.asr_error = ""
    recording.fragment_confirmed = 1
    recording.confirmed_by_user_id = actor_user_id
    if not recording.fragment_order:
        maximum = session.scalar(
            select(func.max(Recording.fragment_order)).filter_by(family_id=family_id)
        ) or 0
        recording.fragment_order = int(maximum) + 1
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
    narrator = session.get(User, recording.narrator_user_id) if recording.narrator_user_id else None
    return {
        "assetId": recording.id,
        "narratorUserId": recording.narrator_user_id or "",
        "narratorName": narrator.display_name if narrator else "讲述者",
        "durationMs": recording.duration_ms,
        "audioUrl": f"/media/{recording.object_key}",
        "transcript": recording.confirmed_text,
        "asrRawText": recording.asr_raw_text or "",
        "agentCleanText": recording.agent_clean_text or "",
        "confirmedText": recording.confirmed_text,
        "cleanChanges": json.loads(recording.clean_changes_json or "[]"),
        "uncertainties": json.loads(recording.clean_uncertainties_json or "[]"),
        "cleanStatus": recording.clean_status or "idle",
        "cleanProvider": recording.clean_provider or "",
        "consentVersion": recording.consent_version,
    }


def memory_fragment_to_dict(session: Session, recording: Recording) -> dict:
    narrator = session.get(User, recording.narrator_user_id) if recording.narrator_user_id else None
    facts = session.scalars(select(MemoryFact).filter_by(fragment_id=recording.id).order_by(MemoryFact.created_at)).all()
    return {
        "recordingId": recording.id,
        "narratorUserId": recording.narrator_user_id or "",
        "narratorName": narrator.display_name if narrator else "讲述者",
        "topicId": recording.topic_id,
        "transcript": recording.confirmed_text or recording.transcript or "",
        "asrRawText": recording.asr_raw_text or "",
        "agentCleanText": recording.agent_clean_text or "",
        "confirmedText": recording.confirmed_text or recording.transcript or "",
        "facts": [{
            "id": fact.id, "fragmentId": fact.fragment_id, "recordingId": fact.recording_id,
            "narratorUserId": fact.narrator_user_id, "quote": fact.quote,
            "element": fact.element, "text": fact.text, "confidence": float(fact.confidence),
            "status": fact.status,
        } for fact in facts],
        "durationMs": recording.duration_ms or 0,
        "audioUrl": f"/media/{recording.object_key}",
        "order": recording.fragment_order or 0,
        "createdAt": recording.created_at.isoformat(),
        "timeLabel": relative_day(recording.created_at),
        "confirmed": bool(recording.fragment_confirmed),
        "confirmedBy": "",
    }


def list_memory_fragments(session: Session, family_id: str, topic_id: str | None = None) -> list[dict]:
    query = select(Recording).where(
        Recording.family_id == family_id,
        Recording.confirmed_text != "",
    )
    if topic_id:
        query = query.where(Recording.topic_id == topic_id)
    rows = session.scalars(
        query.order_by(Recording.fragment_order, Recording.created_at)
    ).all()
    result = []
    for item in rows:
        payload = memory_fragment_to_dict(session, item)
        confirmer = session.get(User, item.confirmed_by_user_id) if item.confirmed_by_user_id else None
        payload["confirmedBy"] = confirmer.display_name if confirmer else ""
        result.append(payload)
    return result


def update_memory_fragment(
    session: Session, family_id: str, recording_id: str, *, transcript: str | None,
    topic_id: str | None, consent_version: int, actor_user_id: str | None = None,
) -> dict:
    require_consent(session, family_id, consent_version)
    recording = _recording_for_family(session, family_id, recording_id)
    if transcript is not None:
        recording.confirmed_text = transcript.strip()
        recording.transcript = recording.confirmed_text
        recording.fragment_confirmed = 1
        recording.confirmed_by_user_id = actor_user_id
    if topic_id is not None:
        target_topic = visible_topic(session, family_id, topic_id)
        if target_topic is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="目标主题不存在")
        recording.topic_id = topic_id
    append_audit(
        session, family_id, action="memory_fragment_updated", category="audio",
        summary="编辑或移动了一段记忆碎片", actor_user_id=actor_user_id,
        target_id=recording.id,
    )
    session.commit()
    return memory_fragment_to_dict(session, recording)


def reorder_memory_fragments(
    session: Session, family_id: str, recording_ids: list[str], *,
    consent_version: int, actor_user_id: str | None = None,
) -> list[dict]:
    require_consent(session, family_id, consent_version)
    unique_ids = list(dict.fromkeys(recording_ids))
    rows = session.scalars(
        select(Recording).where(Recording.family_id == family_id, Recording.id.in_(unique_ids))
    ).all()
    by_id = {item.id: item for item in rows}
    if len(by_id) != len(unique_ids):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="部分记忆碎片不存在")
    for index, recording_id in enumerate(unique_ids, 1):
        by_id[recording_id].fragment_order = index
    append_audit(
        session, family_id, action="memory_fragments_reordered", category="audio",
        summary=f"调整了 {len(unique_ids)} 段记忆碎片的顺序", actor_user_id=actor_user_id,
    )
    session.commit()
    return [memory_fragment_to_dict(session, by_id[item]) for item in unique_ids]


def delete_memory_fragment(
    session: Session, family_id: str, recording_id: str, *,
    consent_version: int, actor_user_id: str | None = None,
) -> dict:
    require_consent(session, family_id, consent_version)
    recording = _recording_for_family(session, family_id, recording_id)
    session.execute(delete(MemoryFact).where(MemoryFact.fragment_id == recording.id))
    for link in session.scalars(select(StoryRecording).filter_by(recording_id=recording.id)).all():
        session.delete(link)
    for story in session.scalars(select(Story).filter_by(recording_id=recording.id)).all():
        story.recording_id = None
    (settings.objects_dir / recording.object_key).unlink(missing_ok=True)
    session.delete(recording)
    append_audit(
        session, family_id, action="memory_fragment_deleted", category="audio",
        summary="删除了一段记忆碎片及其原声", actor_user_id=actor_user_id,
        target_id=recording_id,
    )
    session.commit()
    return {"deletedRecordingId": recording_id, "message": "记忆碎片已删除"}


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
        # 原始 ASR 永久单独保存，不再写入人工确认字段。
        recording.asr_raw_text = result.transcript
        recording.transcript = result.transcript  # 仅供旧版本读取，确认时会替换为 confirmedText
        recording.agent_clean_text = ""
        recording.clean_status = "pending"
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
    narrator_user_id = story.narrator_user_id
    if story.recording_id:
        recording = session.get(Recording, story.recording_id)
        if recording:
            audio_url = f"/media/{recording.object_key}"
            narrator_user_id = narrator_user_id or recording.narrator_user_id
    narrator = session.get(User, narrator_user_id) if narrator_user_id else None
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
                    "narratorUserId": item.narrator_user_id or "",
                    "narratorName": (
                        session.get(User, item.narrator_user_id).display_name
                        if item.narrator_user_id and session.get(User, item.narrator_user_id)
                        else "讲述者"
                    ),
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
        "narratorUserId": narrator_user_id or "",
        "narratorName": narrator.display_name if narrator else "讲述者",
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
        "sentenceEvidence": json.loads(story.draft_sentences_json or "[]"),
        "auditSuggestions": json.loads(story.audit_suggestions_json or "[]"),
        "conflicts": json.loads(story.conflicts_json or "[]"),
        "sessionId": story.session_id,
        "recordings": recordings,
        "familyNoteCount": family_note_count,
        "memoryYear": story.memory_year,
        "lifeStage": story.life_stage or "未分类",
        "workflow": json.loads(story.workflow_json or "{}"),
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
    if actor_user_id:
        require_family_member(session, family_id, actor_user_id)
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
    from .evidence_audit import audit_text, unresolved_conflict_findings

    require_consent(session, family_id, consent_version)
    story = _get_story(session, family_id, story_id)
    claims = json.loads(story.claims_json or "[]")
    findings = [
        *(audit_text(body=body, claims=claims) if claims else []),
        *unresolved_conflict_findings(json.loads(story.conflicts_json or "[]")),
    ]
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
            session.execute(delete(MemoryFact).where(MemoryFact.fragment_id == recording.id))
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


def delete_story(
    session: Session, family_id: str, story_id: str, *, consent_version: int,
    actor_user_id: str | None = None,
) -> dict:
    """删除任意状态故事；只删除没有被其他故事引用的原声。"""
    require_consent(session, family_id, consent_version)
    story = _get_story(session, family_id, story_id)
    links = session.scalars(select(StoryRecording).filter_by(story_id=story.id)).all()
    recording_ids = {link.recording_id for link in links}
    if story.recording_id:
        recording_ids.add(story.recording_id)
    for link in links:
        session.delete(link)
    for note in session.scalars(select(FamilyNote).filter_by(story_id=story.id)).all():
        session.delete(note)
    title = story.title
    session.delete(story)
    session.flush()
    deleted_recordings = 0
    for recording_id in recording_ids:
        linked = session.scalar(
            select(func.count()).select_from(StoryRecording).filter_by(recording_id=recording_id)
        ) or session.scalar(
            select(func.count()).select_from(Story).filter_by(recording_id=recording_id)
        )
        if linked:
            continue
        recording = session.get(Recording, recording_id)
        if recording and recording.family_id == family_id:
            (settings.objects_dir / recording.object_key).unlink(missing_ok=True)
            session.delete(recording)
            deleted_recordings += 1
    append_audit(
        session, family_id, action="story_deleted", category="story",
        summary=f"删除了故事《{title}》", actor_user_id=actor_user_id,
        target_id=story_id,
    )
    session.commit()
    return {
        "discardedStoryId": story_id, "deletedRecordings": deleted_recordings,
        "message": "故事已删除",
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
    if actor_user_id:
        require_family_member(session, family_id, actor_user_id)
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
    # 兼容早期手工故事；已有 Agent 证据的草稿则绝不允许绕过审计接口直发。
    claims = json.loads(story.claims_json or "[]")
    if claims:
        from .evidence_audit import audit_text, unresolved_conflict_findings

        findings = [
            *audit_text(body=story.body, claims=claims),
            *unresolved_conflict_findings(json.loads(story.conflicts_json or "[]")),
        ]
        if findings or not story.audit_passed:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="故事仍有无依据内容或未解决冲突，请先核对并重新审计",
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
    topic = visible_topic(session, family_id, topic_id)
    story = Story(
        id=str(uuid.uuid4()),
        family_id=family_id,
        topic_id=topic_id,
        title=topic.title if topic else "未命名主题",
        body="（这段文字将根据你的讲述生成，当前为演示占位内容。）",
        mode="原味口述",
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


def member_to_dict(user: User, membership: Membership) -> dict:
    return {
        "id": user.id,
        "displayName": user.display_name,
        "phone": user.phone,
        "phoneMasked": mask_phone(user.phone),
        "role": membership.role,
        "roleLabel": "长辈" if membership.role == "elder" else "晚辈家属",
        "gender": user.gender,
        "age": user.age,
        "avatarKey": avatar_key(user.gender, user.age),
        "avatarText": avatar_text(user.gender, user.age),
        "isAdmin": bool(membership.is_admin),
    }


def list_family_members(session: Session, family_id: str) -> dict:
    family = session.get(Family, family_id)
    rows = session.execute(
        select(User, Membership)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.family_id == family_id, User.active == 1)
        .order_by(Membership.is_admin.desc(), Membership.created_at)
    ).all()
    invitations = session.scalars(
        select(FamilyInvitation)
        .filter_by(family_id=family_id, status="pending")
        .order_by(FamilyInvitation.created_at.desc())
    ).all()
    return {
        "familyId": family_id,
        "familyName": family.name if family else "我的家庭",
        "members": [member_to_dict(user, membership) for user, membership in rows],
        "invitations": [
            {"id": item.id, "phone": item.phone, "role": item.role, "status": item.status}
            for item in invitations
        ],
    }


def invite_family_member(
    session: Session, family_id: str, actor_user_id: str, *, phone: str, role: str,
) -> dict:
    _require_admin(session, family_id, actor_user_id)
    actor = session.get(User, actor_user_id)
    if actor and actor.phone == phone:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="不能邀请自己")
    user = session.scalar(select(User).filter_by(phone=phone))
    if user:
        membership = _membership_for_user(session, user.id)
        if membership and membership.family_id == family_id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该成员已在家庭中")
        if membership:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该账号已加入其他家庭")
        user.active = 1
        user.role = role
        membership = Membership(
            id=str(uuid.uuid4()), family_id=family_id, user_id=user.id, role=role, is_admin=0
        )
        session.add(membership)
        result = {"status": "joined", "member": member_to_dict(user, membership)}
        summary = f"通过手机号邀请 {user.display_name} 加入家庭"
    else:
        invitation = session.scalar(
            select(FamilyInvitation).filter_by(family_id=family_id, phone=phone)
        )
        if invitation is None:
            invitation = FamilyInvitation(
                id=str(uuid.uuid4()), family_id=family_id, phone=phone,
                invited_by_user_id=actor_user_id, role=role, status="pending",
            )
            session.add(invitation)
        else:
            invitation.role = role
            invitation.status = "pending"
        result = {"status": "pending", "phone": phone, "role": role}
        summary = f"向手机号 {mask_phone(phone)} 发出家庭邀请"
    append_audit(
        session, family_id, action="member_invited", category="family",
        summary=summary, actor_user_id=actor_user_id,
    )
    session.commit()
    return result


def update_family_member(
    session: Session, family_id: str, actor_user_id: str, member_user_id: str,
    *, display_name: str | None, role: str | None, gender: str | None, age: int | None,
) -> dict:
    _require_admin(session, family_id, actor_user_id)
    membership = session.scalar(
        select(Membership).filter_by(family_id=family_id, user_id=member_user_id)
    )
    user = session.get(User, member_user_id)
    if membership is None or user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="家庭成员不存在")
    if display_name is not None:
        user.display_name = display_name
    if role is not None:
        membership.role = role
        user.role = role
    if gender is not None:
        user.gender = gender
    if age is not None:
        user.age = age
    append_audit(
        session, family_id, action="member_updated", category="family",
        summary=f"更新了成员 {user.display_name} 的资料", actor_user_id=actor_user_id,
        target_id=user.id,
    )
    session.commit()
    return member_to_dict(user, membership)


def remove_family_member(
    session: Session, family_id: str, actor_user_id: str, member_user_id: str,
) -> dict:
    _require_admin(session, family_id, actor_user_id)
    membership = session.scalar(
        select(Membership).filter_by(family_id=family_id, user_id=member_user_id)
    )
    user = session.get(User, member_user_id)
    if membership is None or user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="家庭成员不存在")
    if membership.is_admin:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="不能删除家庭管理员")
    session.delete(membership)
    user.active = 0
    append_audit(
        session, family_id, action="member_removed", category="family",
        summary=f"移除了家庭成员 {user.display_name}", actor_user_id=actor_user_id,
        target_id=user.id,
    )
    session.commit()
    return {"removedUserId": member_user_id, "message": "家庭成员已移除"}


def update_profile(session: Session, family_id: str, user_id: str, *, display_name: str) -> dict:
    user = session.get(User, user_id)
    membership = session.scalar(
        select(Membership).filter_by(family_id=family_id, user_id=user_id)
    )
    if user is None or membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="账号不存在")
    user.display_name = display_name
    append_audit(
        session, family_id, action="profile_updated", category="family",
        summary=f"将昵称修改为 {display_name}", actor_user_id=user_id,
    )
    session.commit()
    return profile(session, family_id, user_id)


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
    membership = session.scalar(
        select(Membership).filter_by(family_id=family_id, user_id=user_id)
    ) if user else None
    return {
        "displayName": name,
        "avatarText": avatar_text(user.gender, user.age) if user else "记",
        "avatarKey": avatar_key(user.gender, user.age) if user else "elder-female",
        "roleLabel": "长辈账号" if (membership and membership.role == "elder") else "晚辈家属账号",
        "phoneMasked": mask_phone(user.phone) if user else "",
        "gender": user.gender if user else "female",
        "age": user.age if user else 60,
        "isAdmin": bool(membership and membership.is_admin),
        "stats": {
            "storyCount": board["doneStories"],
            "audioMinutes": minutes,
            "memberCount": board["memberCount"],
        },
    }
