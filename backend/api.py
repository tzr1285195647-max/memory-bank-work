"""HTTP 路由：只做协议转换，业务规则在 service.py。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, UploadFile, status
from sqlalchemy.orm import Session

from . import service
from .database import get_session
from .schemas import (
    FamilyOut,
    HealthOut,
    HomeOut,
    LoginRequest,
    LoginResponse,
    ProfileOut,
    RecordingOut,
    RevokeOut,
    StoryActionRequest,
    StoryListOut,
    StoryOut,
    StoryPatchRequest,
    TopicOut,
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


@router.get("/me", response_model=ProfileOut)
def me(session: SessionDep, auth: AuthDep) -> dict:
    return service.profile(session, auth.family_id, auth.user_id)


@router.get("/topics", response_model=list[TopicOut])
def topics(session: SessionDep, auth: AuthDep) -> list[dict]:
    return service.list_topics(session)


@router.get("/home", response_model=HomeOut)
def home(session: SessionDep, auth: AuthDep) -> dict:
    return service.build_home(session, auth.family_id)


@router.get("/family", response_model=FamilyOut)
def family(session: SessionDep, auth: AuthDep) -> dict:
    return service.family_board(session, auth.family_id)


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
    )


@router.get("/stories", response_model=StoryListOut)
def list_stories(session: SessionDep, auth: AuthDep, status_filter: str | None = None) -> dict:
    return service.list_stories(session, auth.family_id, only_status=status_filter)


@router.get("/stories/{story_id}", response_model=StoryOut)
def get_story(story_id: str, session: SessionDep, auth: AuthDep) -> dict:
    return service.get_story(session, auth.family_id, story_id)


@router.patch("/stories/{story_id}", response_model=StoryOut)
def patch_story(story_id: str, payload: StoryPatchRequest, session: SessionDep, auth: AuthDep) -> dict:
    return service.update_story(
        session,
        auth.family_id,
        story_id,
        body=payload.body,
        consent_version=payload.consentVersion,
    )


@router.post("/stories/{story_id}/confirm", response_model=StoryOut)
def confirm_story(story_id: str, payload: StoryActionRequest, session: SessionDep, auth: AuthDep) -> dict:
    return service.confirm_story(
        session, auth.family_id, story_id, consent_version=payload.consentVersion
    )


@router.post("/consent/revoke", response_model=RevokeOut)
def revoke(session: SessionDep, auth: AuthDep) -> dict:
    return service.revoke_consent(session, auth.family_id)
