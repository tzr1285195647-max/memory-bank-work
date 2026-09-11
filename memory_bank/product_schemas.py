from __future__ import annotations

"""Strict public DTOs for the product-facing HTTP API.

The models in this module are intentionally independent from persistence
models. Request DTOs reject undeclared input; response DTOs select an explicit
public-field allowlist so database-only values such as ``password_hash`` and
local object paths never reach API output.
"""

from datetime import datetime
from enum import StrEnum
from pathlib import PurePath
import re
from typing import Annotated, Generic, TypeVar
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)

from .auth import Role


def _canonical_uuid(value: str) -> str:
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValueError("must be a valid UUID string") from exc
    return str(parsed)


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include a timezone")
    return value


StrictText = Annotated[str, StringConstraints(strict=True, strip_whitespace=True)]
UUIDString = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=32, max_length=36),
    AfterValidator(_canonical_uuid),
]
AwareDateTime = Annotated[datetime, AfterValidator(_aware_datetime)]
EmailString = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=3, max_length=254),
]


class RequestModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class ResponseModel(BaseModel):
    # ``extra='ignore'`` is deliberate: ORM rows may contain secrets, but only
    # declared response fields are selected and serialized.
    model_config = ConfigDict(
        extra="ignore",
        from_attributes=True,
        str_strip_whitespace=True,
    )


class ProjectStatus(StrEnum):
    DRAFT = "draft"
    INTERVIEW = "interview"
    TRANSCRIBING = "transcribing"
    EVIDENCE = "evidence"
    WRITING = "writing"
    REVIEW = "review"
    APPROVED = "approved"
    CREATIVE = "creative"
    DELIVERED = "delivered"
    ARCHIVED = "archived"
    REVOKED = "revoked"
    FAILED = "failed"


class ConsentScope(StrEnum):
    INTERVIEW = "interview"
    TRANSCRIPTION = "transcription"
    AI_PROCESSING = "ai_processing"
    FAMILY_SHARING = "family_sharing"
    VOICE_SYNTHESIS = "voice_synthesis"
    IMAGE_GENERATION = "image_generation"


class ConsentStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class AssetKind(StrEnum):
    AUDIO = "audio"
    IMAGE = "image"
    DOCUMENT = "document"


class AssetStatus(StrEnum):
    PENDING_UPLOAD = "pending_upload"
    UPLOADING = "uploading"
    READY = "ready"
    PROCESSING = "processing"
    FAILED = "failed"
    DELETED = "deleted"


class JobType(StrEnum):
    TRANSCRIPTION = "transcription"
    CLAIM_EXTRACTION = "claim_extraction"
    DRAFT_GENERATION = "draft_generation"
    EVIDENCE_AUDIT = "evidence_audit"
    MEDIA_GENERATION = "media_generation"
    DATA_DELETION = "data_deletion"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class InvitationStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    REVOKED = "revoked"


class UserRegisterRequest(RequestModel):
    email: EmailString
    password: SecretStr = Field(min_length=12, max_length=128)
    display_name: StrictText = Field(min_length=1, max_length=80)
    locale: StrictText = Field(default="zh-CN", pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.casefold()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
            raise ValueError("must be a valid email address")
        return normalized


class UserLoginRequest(RequestModel):
    email: EmailString
    password: SecretStr = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return UserRegisterRequest.normalize_email(value)


class UserResponse(ResponseModel):
    id: UUIDString
    email: EmailString
    display_name: StrictText = Field(min_length=1, max_length=80)
    locale: StrictText = Field(default="zh-CN", max_length=35)
    is_active: bool
    created_at: AwareDateTime
    updated_at: AwareDateTime | None = None


class AccessTokenResponse(ResponseModel):
    access_token: StrictText = Field(min_length=20, max_length=8192)
    token_type: str = Field(default="bearer", pattern=r"^bearer$")
    expires_in: int = Field(gt=0, le=86_400, strict=True)
    user: UserResponse


class FamilyCreateRequest(RequestModel):
    name: StrictText = Field(min_length=1, max_length=100)
    description: StrictText | None = Field(default=None, max_length=500)


class FamilyResponse(ResponseModel):
    id: UUIDString
    name: StrictText = Field(min_length=1, max_length=100)
    description: StrictText | None = Field(default=None, max_length=500)
    owner_id: UUIDString
    member_count: int = Field(default=1, ge=1, strict=True)
    created_at: AwareDateTime
    updated_at: AwareDateTime | None = None


def _non_owner_role(role: Role) -> Role:
    if role is Role.OWNER:
        raise ValueError("owner cannot be assigned through member or invitation APIs")
    return role


class FamilyMemberAddRequest(RequestModel):
    user_id: UUIDString
    role: Role

    _validate_role = field_validator("role")(_non_owner_role)


class FamilyInvitationCreateRequest(RequestModel):
    email: EmailString
    role: Role = Role.VIEWER

    _validate_role = field_validator("role")(_non_owner_role)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return UserRegisterRequest.normalize_email(value)


class FamilyMemberRoleUpdateRequest(RequestModel):
    role: Role

    _validate_role = field_validator("role")(_non_owner_role)


class FamilyMemberResponse(ResponseModel):
    family_id: UUIDString
    user_id: UUIDString
    role: Role
    display_name: StrictText | None = Field(default=None, max_length=80)
    email: EmailString | None = None
    is_active: bool = True
    joined_at: AwareDateTime


class FamilyInvitationResponse(ResponseModel):
    id: UUIDString
    family_id: UUIDString
    email: EmailString
    role: Role
    status: InvitationStatus
    invited_by_user_id: UUIDString
    expires_at: AwareDateTime
    created_at: AwareDateTime


class ProjectCreateRequest(RequestModel):
    family_id: UUIDString
    title: StrictText = Field(min_length=1, max_length=120)
    subject_name: StrictText = Field(min_length=1, max_length=80)
    subject_user_id: UUIDString | None = None
    topic: StrictText = Field(min_length=1, max_length=300)
    description: StrictText | None = Field(default=None, max_length=2000)
    language: StrictText = Field(default="zh-CN", pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
    max_interview_rounds: int = Field(default=5, ge=1, le=20, strict=True)
    tags: list[StrictText] = Field(default_factory=list, max_length=20)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not item or len(item) > 40:
                raise ValueError("each tag must contain 1 to 40 characters")
            key = item.casefold()
            if key not in seen:
                seen.add(key)
                cleaned.append(item)
        return cleaned


class ProjectResponse(ResponseModel):
    id: UUIDString
    family_id: UUIDString
    created_by_user_id: UUIDString
    title: StrictText = Field(min_length=1, max_length=120)
    subject_name: StrictText = Field(min_length=1, max_length=80)
    subject_user_id: UUIDString | None = None
    topic: StrictText = Field(min_length=1, max_length=300)
    description: StrictText | None = Field(default=None, max_length=2000)
    language: StrictText = Field(default="zh-CN", max_length=35)
    status: ProjectStatus
    max_interview_rounds: int = Field(ge=1, le=20, strict=True)
    current_round: int = Field(default=0, ge=0, le=20, strict=True)
    created_at: AwareDateTime
    updated_at: AwareDateTime


class ProjectDetailResponse(ProjectResponse):
    consent_status: ConsentStatus | None = None
    turn_count: int = Field(default=0, ge=0, strict=True)
    claim_count: int = Field(default=0, ge=0, strict=True)
    asset_count: int = Field(default=0, ge=0, strict=True)
    pending_job_count: int = Field(default=0, ge=0, strict=True)


class ConsentGrantRequest(RequestModel):
    project_id: UUIDString
    granted_by_name: StrictText = Field(min_length=1, max_length=80)
    subject_user_id: UUIDString | None = None
    scopes: set[ConsentScope] = Field(min_length=1, max_length=len(ConsentScope))
    policy_version: StrictText = Field(min_length=1, max_length=40)
    expires_at: AwareDateTime | None = None


class ConsentRevokeRequest(RequestModel):
    reason: StrictText = Field(min_length=1, max_length=500)
    expected_version: int = Field(ge=1, strict=True)


class ConsentResponse(ResponseModel):
    id: UUIDString
    family_id: UUIDString
    project_id: UUIDString
    granted_by_user_id: UUIDString
    granted_by_name: StrictText = Field(min_length=1, max_length=80)
    subject_user_id: UUIDString | None = None
    scopes: set[ConsentScope] = Field(min_length=1)
    policy_version: StrictText = Field(min_length=1, max_length=40)
    version: int = Field(ge=1, strict=True)
    status: ConsentStatus
    granted_at: AwareDateTime
    expires_at: AwareDateTime | None = None
    revoked_at: AwareDateTime | None = None
    revocation_reason: StrictText | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_revocation_fields(self) -> "ConsentResponse":
        if self.status is ConsentStatus.REVOKED and self.revoked_at is None:
            raise ValueError("revoked consent must include revoked_at")
        if self.status is not ConsentStatus.REVOKED and self.revoked_at is not None:
            raise ValueError("only revoked consent may include revoked_at")
        return self


class AssetUploadRequest(RequestModel):
    project_id: UUIDString
    kind: AssetKind
    filename: StrictText = Field(min_length=1, max_length=255)
    content_type: StrictText = Field(min_length=3, max_length=127, pattern=r"^[\w.+-]+/[\w.+-]+$")
    size_bytes: int = Field(gt=0, le=1_073_741_824, strict=True)
    checksum_sha256: StrictText = Field(pattern=r"^[0-9a-fA-F]{64}$")

    @field_validator("filename")
    @classmethod
    def filename_must_be_a_basename(cls, value: str) -> str:
        if value in {".", ".."} or "/" in value or "\\" in value or PurePath(value).name != value:
            raise ValueError("filename must not contain a path")
        return value

    @field_validator("checksum_sha256")
    @classmethod
    def normalize_checksum(cls, value: str) -> str:
        return value.lower()


class AssetResponse(ResponseModel):
    id: UUIDString
    family_id: UUIDString
    project_id: UUIDString
    uploaded_by_user_id: UUIDString
    kind: AssetKind
    status: AssetStatus
    filename: StrictText = Field(min_length=1, max_length=255)
    content_type: StrictText = Field(min_length=3, max_length=127)
    size_bytes: int = Field(gt=0, le=1_073_741_824, strict=True)
    checksum_sha256: StrictText = Field(pattern=r"^[0-9a-f]{64}$")
    duration_ms: int | None = Field(default=None, ge=0, le=86_400_000, strict=True)
    created_at: AwareDateTime
    updated_at: AwareDateTime | None = None


class JobCreateRequest(RequestModel):
    project_id: UUIDString
    job_type: JobType
    asset_id: UUIDString | None = None
    idempotency_key: StrictText = Field(min_length=8, max_length=128)


class JobResponse(ResponseModel):
    id: UUIDString
    family_id: UUIDString
    project_id: UUIDString
    asset_id: UUIDString | None = None
    job_type: JobType
    status: JobStatus
    progress_percent: int = Field(default=0, ge=0, le=100, strict=True)
    attempt: int = Field(default=0, ge=0, le=100, strict=True)
    max_attempts: int = Field(default=3, ge=1, le=100, strict=True)
    error_code: StrictText | None = Field(default=None, max_length=80)
    error_message: StrictText | None = Field(default=None, max_length=1000)
    created_at: AwareDateTime
    started_at: AwareDateTime | None = None
    finished_at: AwareDateTime | None = None

    @model_validator(mode="after")
    def validate_terminal_job(self) -> "JobResponse":
        terminal = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}
        if self.status in terminal and self.finished_at is None:
            raise ValueError("terminal job must include finished_at")
        if self.status is JobStatus.SUCCEEDED and self.progress_percent != 100:
            raise ValueError("succeeded job must have 100 percent progress")
        return self


class PageMeta(ResponseModel):
    page: int = Field(ge=1, strict=True)
    page_size: int = Field(ge=1, le=100, strict=True)
    total_items: int = Field(ge=0, strict=True)
    total_pages: int = Field(ge=0, strict=True)


PageItem = TypeVar("PageItem")


class Page(ResponseModel, Generic[PageItem]):
    items: list[PageItem]
    meta: PageMeta


# Concise compatibility names for route modules that do not use the domain
# prefix. They remain aliases, not separate schemas.
RegisterRequest = UserRegisterRequest
LoginRequest = UserLoginRequest
TokenResponse = AccessTokenResponse
FamilyInviteRequest = FamilyInvitationCreateRequest
FamilyRoleUpdateRequest = FamilyMemberRoleUpdateRequest
MemoryProjectCreateRequest = ProjectCreateRequest
MemoryProjectResponse = ProjectResponse
RevokeConsentRequest = ConsentRevokeRequest


__all__ = [
    "AccessTokenResponse",
    "AssetKind",
    "AssetResponse",
    "AssetStatus",
    "AssetUploadRequest",
    "ConsentGrantRequest",
    "ConsentResponse",
    "ConsentRevokeRequest",
    "ConsentScope",
    "ConsentStatus",
    "FamilyCreateRequest",
    "FamilyInvitationCreateRequest",
    "FamilyInvitationResponse",
    "FamilyMemberAddRequest",
    "FamilyMemberResponse",
    "FamilyMemberRoleUpdateRequest",
    "FamilyResponse",
    "InvitationStatus",
    "JobCreateRequest",
    "JobResponse",
    "JobStatus",
    "JobType",
    "Page",
    "PageMeta",
    "ProjectCreateRequest",
    "ProjectDetailResponse",
    "ProjectResponse",
    "ProjectStatus",
    "Role",
    "UserLoginRequest",
    "UserRegisterRequest",
    "UserResponse",
    "UUIDString",
]
