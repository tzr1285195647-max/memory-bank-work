"""HTTP 路由：只做协议转换，业务规则在 service.py。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Response, UploadFile, status
from sqlalchemy.orm import Session

from . import service
from .agent_service import clean_recording_transcript, extract_recording_evidence, get_runtime
from .config import settings
from .database import get_session
from .schemas import (
    AuditListOut,
    FamilyOut,
    FamilyInviteRequest,
    FamilyMemberOut,
    FamilyMemberPatchRequest,
    FamilyMembersOut,
    FamilyNoteActionRequest,
    FamilyNoteCreateRequest,
    FamilyNoteOut,
    HealthOut,
    HomeOut,
    LoginRequest,
    LoginResponse,
    ProfilePatchRequest,
    RegisterRequest,
    MemoryFragmentConfirmRequest,
    MemoryFragmentOut,
    MemoryFragmentPatchRequest,
    MemoryFragmentReorderRequest,
    ProfileOut,
    RecordingOut,
    TranscriptionOut,
    RevokeOut,
    StoryActionRequest,
    StoryAuditOut,
    StoryAuditRequest,
    StoryDiscardOut,
    StoryListOut,
    StoryOut,
    StoryPatchRequest,
    TopicOut,
    TopicCreateRequest,
)
from .security import decode_access_token

router = APIRouter(prefix="/api")

SessionDep = Annotated[Session, Depends(get_session)]


class AuthContext:
    def __init__(self, user_id: str, family_id: str, role: str) -> None:
        self.user_id = user_id
        self.family_id = family_id
        self.role = role


def current_auth(authorization: Annotated[str | None, Header()] = None) -> AuthContext:
    """从 Bearer token 解析身份；family_id 来自令牌，不信任请求体。"""
    from fastapi import HTTPException

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少访问令牌")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_access_token(token)
    except Exception as exc:  # noqa: BLE001 - jwt 会抛多种异常
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="令牌无效或已过期") from exc
    return AuthContext(user_id=payload["sub"], family_id=payload["fid"], role=payload.get("role", "elder"))


AuthDep = Annotated[AuthContext, Depends(current_auth)]


@router.get("/health", response_model=HealthOut)
def health() -> dict:
    return {"status": "ok", "mode": "local", "version": "0.1.0"}


@router.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest, session: SessionDep) -> dict:
    return service.login(session, phone=payload.phone, password=payload.password, role=payload.role)


@router.post("/auth/register", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, session: SessionDep) -> dict:
    return service.register(
        session, phone=payload.phone, password=payload.password,
        display_name=payload.displayName, role=payload.role,
        gender=payload.gender, age=payload.age,
    )


@router.get("/me", response_model=ProfileOut)
def me(session: SessionDep, auth: AuthDep) -> dict:
    return service.profile(session, auth.family_id, auth.user_id)


@router.patch("/me", response_model=ProfileOut)
def patch_me(payload: ProfilePatchRequest, session: SessionDep, auth: AuthDep) -> dict:
    return service.update_profile(
        session, auth.family_id, auth.user_id, display_name=payload.displayName
    )


@router.get("/topics", response_model=list[TopicOut])
def topics(session: SessionDep, auth: AuthDep) -> list[dict]:
    return service.list_topics(session, auth.family_id)


@router.post("/topics", response_model=TopicOut, status_code=status.HTTP_201_CREATED)
def create_topic(payload: TopicCreateRequest, session: SessionDep, auth: AuthDep) -> dict:
    return service.create_custom_topic(session, auth.family_id, payload.title)


@router.get("/home", response_model=HomeOut)
def home(session: SessionDep, auth: AuthDep) -> dict:
    return service.build_home(session, auth.family_id)


@router.get("/family", response_model=FamilyOut)
def family(session: SessionDep, auth: AuthDep) -> dict:
    return service.family_board(session, auth.family_id)


@router.get("/family/book.pdf")
def export_family_book(
    session: SessionDep,
    auth: AuthDep,
    title: str = Query(default="我们的家庭纪念册", max_length=30),
) -> Response:
    """按时间线导出本家庭已确认故事，草稿和录音不进入 PDF。"""
    from .book_pdf import render_family_book

    service.require_family_member(session, auth.family_id, auth.user_id)
    stories = service.list_stories(session, auth.family_id, only_status="confirmed")["items"]
    try:
        content = render_family_book(stories, title)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'attachment; filename="memory-bank-family-book.pdf"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/family/members", response_model=FamilyMembersOut)
def family_members(session: SessionDep, auth: AuthDep) -> dict:
    return service.list_family_members(session, auth.family_id)


@router.post("/family/invitations", status_code=status.HTTP_201_CREATED)
def invite_family_member(payload: FamilyInviteRequest, session: SessionDep, auth: AuthDep) -> dict:
    return service.invite_family_member(
        session, auth.family_id, auth.user_id, phone=payload.phone, role=payload.role
    )


@router.patch("/family/members/{member_user_id}", response_model=FamilyMemberOut)
def patch_family_member(
    member_user_id: str, payload: FamilyMemberPatchRequest,
    session: SessionDep, auth: AuthDep,
) -> dict:
    return service.update_family_member(
        session, auth.family_id, auth.user_id, member_user_id,
        display_name=payload.displayName, role=payload.role,
        gender=payload.gender, age=payload.age,
    )


@router.delete("/family/members/{member_user_id}")
def delete_family_member(member_user_id: str, session: SessionDep, auth: AuthDep) -> dict:
    return service.remove_family_member(
        session, auth.family_id, auth.user_id, member_user_id
    )


@router.post("/recordings", response_model=RecordingOut, status_code=status.HTTP_201_CREATED)
def upload_recording(
    session: SessionDep,
    auth: AuthDep,
    file: Annotated[UploadFile, File()],
    topicId: Annotated[str, Form()] = "",
    durationMs: Annotated[int, Form()] = 0,
    consentVersion: Annotated[int, Form()] = 1,
) -> dict:
    return service.save_recording(
        session,
        family_id=auth.family_id,
        topic_id=topicId,
        duration_ms=durationMs,
        consent_version=consentVersion,
        upload=file,
        actor_user_id=auth.user_id,
    )


@router.get("/asr/status")
def get_asr_status() -> dict:
    return service.asr_status()


@router.post("/recordings/{recording_id}/transcription", response_model=TranscriptionOut)
def start_recording_transcription(
    recording_id: str,
    payload: StoryActionRequest,
    session: SessionDep,
    auth: AuthDep,
) -> dict:
    return service.start_transcription(
        session,
        family_id=auth.family_id,
        recording_id=recording_id,
        consent_version=payload.consentVersion,
        actor_user_id=auth.user_id,
    )


@router.get("/recordings/{recording_id}/transcription", response_model=TranscriptionOut)
def get_recording_transcription(recording_id: str, session: SessionDep, auth: AuthDep) -> dict:
    result = service.refresh_transcription(
        session,
        family_id=auth.family_id,
        recording_id=recording_id,
    )
    if result.get("status") == "success" and result.get("asrRawText") and result.get("cleanStatus") in {"idle", "pending", "failed"}:
        return clean_recording_transcript(
            session, get_runtime(settings.data_dir / "checkpoints.sqlite3"),
            family_id=auth.family_id, recording_id=recording_id,
        )
    return result


@router.put("/recordings/{recording_id}/fragment", response_model=RecordingOut)
def confirm_recording_fragment(
    recording_id: str,
    payload: MemoryFragmentConfirmRequest,
    session: SessionDep,
    auth: AuthDep,
) -> dict:
    result = service.confirm_memory_fragment(
        session,
        family_id=auth.family_id,
        recording_id=recording_id,
        transcript=payload.transcript,
        consent_version=payload.consentVersion,
        actor_user_id=auth.user_id,
    )
    extract_recording_evidence(
        session, get_runtime(settings.data_dir / "checkpoints.sqlite3"),
        family_id=auth.family_id, recording_id=recording_id,
    )
    return result


@router.get("/fragments", response_model=list[MemoryFragmentOut])
def memory_fragments(session: SessionDep, auth: AuthDep, topicId: str | None = None) -> list[dict]:
    return service.list_memory_fragments(session, auth.family_id, topicId)


@router.patch("/fragments/{recording_id}", response_model=MemoryFragmentOut)
def patch_memory_fragment(
    recording_id: str, payload: MemoryFragmentPatchRequest,
    session: SessionDep, auth: AuthDep,
) -> dict:
    result = service.update_memory_fragment(
        session, auth.family_id, recording_id, transcript=payload.transcript,
        topic_id=payload.topicId, consent_version=payload.consentVersion,
        actor_user_id=auth.user_id,
    )
    if payload.transcript is not None:
        extract_recording_evidence(
            session, get_runtime(settings.data_dir / "checkpoints.sqlite3"),
            family_id=auth.family_id, recording_id=recording_id,
        )
        result = service.memory_fragment_to_dict(session, service._recording_for_family(session, auth.family_id, recording_id))
    return result


@router.put("/fragments/reorder", response_model=list[MemoryFragmentOut])
def reorder_memory_fragments(
    payload: MemoryFragmentReorderRequest, session: SessionDep, auth: AuthDep,
) -> list[dict]:
    return service.reorder_memory_fragments(
        session, auth.family_id, payload.recordingIds,
        consent_version=payload.consentVersion, actor_user_id=auth.user_id,
    )


@router.delete("/fragments/{recording_id}")
def delete_memory_fragment(
    recording_id: str, consentVersion: int, session: SessionDep, auth: AuthDep,
) -> dict:
    return service.delete_memory_fragment(
        session, auth.family_id, recording_id, consent_version=consentVersion,
        actor_user_id=auth.user_id,
    )


@router.post("/stories/draft", response_model=StoryOut, status_code=status.HTTP_201_CREATED)
def create_draft(
    session: SessionDep,
    auth: AuthDep,
    topicId: Annotated[str, Form()] = "",
    durationMs: Annotated[int, Form()] = 0,
    consentVersion: Annotated[int, Form()] = 1,
    recordingId: Annotated[str | None, Form()] = None,
) -> dict:
    return service.create_draft(
        session,
        family_id=auth.family_id,
        topic_id=topicId,
        duration_ms=durationMs,
        consent_version=consentVersion,
        recording_id=recordingId,
        actor_user_id=auth.user_id,
    )


@router.get("/stories", response_model=StoryListOut)
def list_stories(session: SessionDep, auth: AuthDep, status_filter: str | None = None) -> dict:
    return service.list_stories(session, auth.family_id, only_status=status_filter)


@router.get("/stories/{story_id}", response_model=StoryOut)
def get_story(story_id: str, session: SessionDep, auth: AuthDep) -> dict:
    return service.get_story(session, auth.family_id, story_id)


@router.get("/stories/{story_id}/family-notes", response_model=list[FamilyNoteOut])
def get_family_notes(story_id: str, session: SessionDep, auth: AuthDep) -> list[dict]:
    return service.list_family_notes(session, auth.family_id, story_id)


@router.post(
    "/stories/{story_id}/family-notes",
    response_model=FamilyNoteOut,
    status_code=status.HTTP_201_CREATED,
)
def create_family_note(
    story_id: str,
    payload: FamilyNoteCreateRequest,
    session: SessionDep,
    auth: AuthDep,
) -> dict:
    return service.add_family_note(
        session,
        auth.family_id,
        story_id,
        user_id=auth.user_id,
        role=auth.role,
        kind=payload.kind,
        content=payload.content,
        consent_version=payload.consentVersion,
    )


@router.post(
    "/stories/{story_id}/family-notes/{note_id}/resolve",
    response_model=FamilyNoteOut,
)
def resolve_family_note(
    story_id: str,
    note_id: str,
    payload: FamilyNoteActionRequest,
    session: SessionDep,
    auth: AuthDep,
) -> dict:
    return service.resolve_family_note(
        session,
        auth.family_id,
        story_id,
        note_id,
        role=auth.role,
        action=payload.action,
        consent_version=payload.consentVersion,
        actor_user_id=auth.user_id,
    )


@router.patch("/stories/{story_id}", response_model=StoryOut)
def patch_story(story_id: str, payload: StoryPatchRequest, session: SessionDep, auth: AuthDep) -> dict:
    return service.update_story(
        session,
        auth.family_id,
        story_id,
        body=payload.body,
        mode=payload.mode,
        memory_year=payload.memoryYear,
        life_stage=payload.lifeStage,
        consent_version=payload.consentVersion,
        actor_user_id=auth.user_id,
    )


@router.post("/stories/{story_id}/audit", response_model=StoryAuditOut)
def audit_story(
    story_id: str, payload: StoryAuditRequest, session: SessionDep, auth: AuthDep
) -> dict:
    return service.audit_story_text(
        session,
        auth.family_id,
        story_id,
        body=payload.body,
        consent_version=payload.consentVersion,
    )


@router.post("/stories/{story_id}/discard", response_model=StoryDiscardOut)
def discard_story(
    story_id: str, payload: StoryActionRequest, session: SessionDep, auth: AuthDep
) -> dict:
    return service.discard_story(
        session, auth.family_id, story_id, consent_version=payload.consentVersion,
        actor_user_id=auth.user_id,
    )


@router.delete("/stories/{story_id}", response_model=StoryDiscardOut)
def delete_story(story_id: str, consentVersion: int, session: SessionDep, auth: AuthDep) -> dict:
    return service.delete_story(
        session, auth.family_id, story_id, consent_version=consentVersion,
        actor_user_id=auth.user_id,
    )


@router.post("/stories/{story_id}/confirm", response_model=StoryOut)
def confirm_story(story_id: str, payload: StoryActionRequest, session: SessionDep, auth: AuthDep) -> dict:
    return service.confirm_story(
        session,
        auth.family_id,
        story_id,
        role=auth.role,
        consent_version=payload.consentVersion,
        actor_user_id=auth.user_id,
    )


@router.get("/audit-events", response_model=AuditListOut)
def audit_events(session: SessionDep, auth: AuthDep, limit: int = 50) -> dict:
    return service.list_audit_events(session, auth.family_id, limit)


@router.post("/consent/revoke", response_model=RevokeOut)
def revoke(session: SessionDep, auth: AuthDep) -> dict:
    return service.revoke_consent(session, auth.family_id, auth.user_id)
