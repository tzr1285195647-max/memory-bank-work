"""业务逻辑：授权校验、故事读写、录音落盘。

关键产品规则（写死在服务层，接口层不得绕过）：
- 所有查询按 family_id 范围过滤（禁止仅凭资源 ID 查询）
- 写请求校验 consent_version；撤回后一律拒绝
- 故事未确认前状态为 pending_review，不对外发布
- 原声上传到服务端生成的对象键，不信任原始文件名
"""

from __future__ import annotations

import hashlib
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .database import ConsentGrant, Family, Membership, Recording, Story, Topic, User
from .security import create_access_token, hash_password, verify_password


class ConsentError(HTTPException):
    def __init__(self, detail: str = "授权已撤回或版本过期") -> None:
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


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
    session.commit()
    return {
        "assetId": recording.id,
        "durationMs": recording.duration_ms,
        "audioUrl": f"/media/{object_key}",
        "transcript": recording.transcript,
        "consentVersion": recording.consent_version,
    }


# ------------------------------------------------------------------ 故事


def story_to_dict(session: Session, story: Story, index: int | None = None) -> dict:
    audio_url = None
    if story.recording_id:
        recording = session.get(Recording, story.recording_id)
        if recording:
            audio_url = f"/media/{recording.object_key}"
    return {
        "id": story.id,
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
    }


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
    session: Session, family_id: str, story_id: str, *, body: str, consent_version: int
) -> dict:
    require_consent(session, family_id, consent_version)
    story = _get_story(session, family_id, story_id)
    story.body = body
    story.status = "pending_review"  # 修改后必须重新确认
    session.commit()
    return story_to_dict(session, story)


def confirm_story(session: Session, family_id: str, story_id: str, *, consent_version: int) -> dict:
    require_consent(session, family_id, consent_version)
    story = _get_story(session, family_id, story_id)
    story.status = "confirmed"
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
    )
    session.add(story)
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


def revoke_consent(session: Session, family_id: str) -> dict:
    """撤回授权：删除内容与全部原声，保留授权记录（审计事件在 P1 落地）。

    注意：不能只删「被故事引用」的录音——刚上传但还没生成草稿的录音同样必须删除，
    否则原声会残留在服务器上，违反产品规则。
    """
    consent = active_consent(session, family_id)

    deleted_stories = 0
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
