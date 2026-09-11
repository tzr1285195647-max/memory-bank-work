from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SqlEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utc_now


class StringEnum(str, Enum):
    """Base class for enums persisted by their stable string values."""


class UserStatus(StringEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    DELETED = "deleted"


class MembershipRole(StringEnum):
    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    APPROVER = "approver"
    VIEWER = "viewer"


class MembershipStatus(StringEnum):
    INVITED = "invited"
    ACTIVE = "active"
    REMOVED = "removed"


class ProjectStatus(StringEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    REVIEW = "review"
    DELIVERED = "delivered"
    ARCHIVED = "archived"
    REVOKED = "revoked"


class ConsentStatus(StringEnum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class InterviewStatus(StringEnum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class AssetStatus(StringEnum):
    UPLOADING = "uploading"
    READY = "ready"
    PROCESSING = "processing"
    FAILED = "failed"
    DELETED = "deleted"


class ClaimStatus(StringEnum):
    PROPOSED = "proposed"
    SUBJECT_CONFIRMED = "subject_confirmed"
    CORROBORATED = "corroborated"
    CONTRADICTED = "contradicted"
    CONTESTED = "contested"
    REJECTED = "rejected"


class EvidenceRelationship(StringEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CONTEXT = "context"


class ChapterStatus(StringEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    ARCHIVED = "archived"


class VersionStatus(StringEnum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class ApprovalDecision(StringEnum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    REJECTED = "rejected"


class MediaJobType(StringEnum):
    TRANSCRIPTION = "transcription"
    IMAGE = "image"
    SPEECH = "speech"
    VIDEO = "video"
    EXPORT = "export"


class JobStatus(StringEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DeliveryStatus(StringEnum):
    PENDING = "pending"
    READY = "ready"
    PUBLISHED = "published"
    FAILED = "failed"
    REVOKED = "revoked"


class DeletionStatus(StringEnum):
    REQUESTED = "requested"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


def enum_type(enum_class: type[StringEnum], name: str) -> SqlEnum:
    values = [member.value for member in enum_class]
    return SqlEnum(
        enum_class,
        name=name,
        values_callable=lambda members: [member.value for member in members],
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        length=max(map(len, values)),
    )


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[UserStatus] = mapped_column(
        enum_type(UserStatus, "user_status"), nullable=False, default=UserStatus.ACTIVE
    )
    locale: Mapped[str] = mapped_column(String(16), nullable=False, default="zh-CN")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list[FamilyMembership]] = relationship(
        back_populates="user",
        foreign_keys="FamilyMembership.user_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    created_families: Mapped[list[Family]] = relationship(
        back_populates="created_by", foreign_keys="Family.created_by_user_id"
    )

    __table_args__ = (
        UniqueConstraint("email_normalized", name="uq_users_email_normalized"),
        Index("ix_users_status_created_at", "status", "created_at"),
    )


class Family(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "families"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    created_by: Mapped[User | None] = relationship(
        back_populates="created_families", foreign_keys=[created_by_user_id]
    )
    memberships: Mapped[list[FamilyMembership]] = relationship(
        back_populates="family", cascade="all, delete-orphan", passive_deletes=True
    )
    projects: Mapped[list[MemoryProject]] = relationship(
        back_populates="family", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (Index("ix_families_active_updated_at", "is_active", "updated_at"),)


class FamilyMembership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "family_memberships"

    family_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("families.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[MembershipRole] = mapped_column(
        enum_type(MembershipRole, "membership_role"), nullable=False, default=MembershipRole.VIEWER
    )
    status: Mapped[MembershipStatus] = mapped_column(
        enum_type(MembershipStatus, "membership_status"),
        nullable=False,
        default=MembershipStatus.INVITED,
    )
    invited_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    family: Mapped[Family] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships", foreign_keys=[user_id])
    invited_by: Mapped[User | None] = relationship(foreign_keys=[invited_by_user_id])

    __table_args__ = (
        UniqueConstraint("family_id", "user_id", name="uq_family_memberships_family_user"),
        Index("ix_family_memberships_user_status", "user_id", "status"),
        Index("ix_family_memberships_family_role", "family_id", "role"),
    )


class MemoryProject(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "memory_projects"

    family_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("families.id", ondelete="CASCADE"), nullable=False
    )
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    subject_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    subject_name: Mapped[str] = mapped_column(String(120), nullable=False)
    topic: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    workflow_thread_id: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[ProjectStatus] = mapped_column(
        enum_type(ProjectStatus, "project_status"), nullable=False, default=ProjectStatus.DRAFT
    )
    stage: Mapped[str] = mapped_column(String(40), nullable=False, default="interview")
    current_consent_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="zh-CN")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    family: Mapped[Family] = relationship(back_populates="projects")
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_user_id])
    subject_user: Mapped[User | None] = relationship(foreign_keys=[subject_user_id])
    consent_grants: Mapped[list[ConsentGrant]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )
    interview_sessions: Mapped[list[InterviewSession]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )
    audio_assets: Mapped[list[AudioAsset]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )
    transcript_segments: Mapped[list[TranscriptSegment]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )
    claims: Mapped[list[Claim]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )
    chapters: Mapped[list[Chapter]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )
    approvals: Mapped[list[Approval]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )
    media_jobs: Mapped[list[MediaJob]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )
    deliveries: Mapped[list[Delivery]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint(
            "current_consent_version >= 0", name="ck_memory_projects_current_consent_version"
        ),
        UniqueConstraint("workflow_thread_id", name="uq_memory_projects_workflow_thread_id"),
        Index("ix_memory_projects_family_status", "family_id", "status"),
        Index("ix_memory_projects_family_stage", "family_id", "stage"),
        Index("ix_memory_projects_family_updated_at", "family_id", "updated_at"),
    )


class ConsentGrant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "consent_grants"

    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memory_projects.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[str] = mapped_column(
        String(40), nullable=False, default="v1", server_default="v1"
    )
    status: Mapped[ConsentStatus] = mapped_column(
        enum_type(ConsentStatus, "consent_status"), nullable=False, default=ConsentStatus.ACTIVE
    )
    granted_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    subject_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    granted_by_name: Mapped[str] = mapped_column(String(120), nullable=False)
    purpose: Mapped[str] = mapped_column(String(500), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    revocation_reason: Mapped[str | None] = mapped_column(String(500))

    project: Mapped[MemoryProject] = relationship(back_populates="consent_grants")
    granted_by: Mapped[User | None] = relationship(foreign_keys=[granted_by_user_id])
    subject_user: Mapped[User | None] = relationship(foreign_keys=[subject_user_id])
    revoked_by: Mapped[User | None] = relationship(foreign_keys=[revoked_by_user_id])

    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_consent_grants_project_version"),
        CheckConstraint("version > 0", name="ck_consent_grants_version"),
        Index("ix_consent_grants_project_status", "project_id", "status"),
        Index("ix_consent_grants_expires_at", "expires_at"),
    )


class InterviewSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "interview_sessions"

    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memory_projects.id", ondelete="CASCADE"), nullable=False
    )
    started_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    thread_id: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[InterviewStatus] = mapped_column(
        enum_type(InterviewStatus, "interview_status"),
        nullable=False,
        default=InterviewStatus.PENDING,
    )
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="zh-CN")
    current_round: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    context: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    project: Mapped[MemoryProject] = relationship(back_populates="interview_sessions")
    started_by: Mapped[User | None] = relationship(foreign_keys=[started_by_user_id])
    audio_assets: Mapped[list[AudioAsset]] = relationship(
        back_populates="interview_session", passive_deletes=True
    )
    transcript_segments: Mapped[list[TranscriptSegment]] = relationship(
        back_populates="interview_session", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("current_round >= 0", name="ck_interview_sessions_current_round"),
        CheckConstraint("max_rounds > 0", name="ck_interview_sessions_max_rounds"),
        UniqueConstraint("thread_id", name="uq_interview_sessions_thread_id"),
        Index("ix_interview_sessions_project_status", "project_id", "status"),
    )


class AudioAsset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "audio_assets"

    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memory_projects.id", ondelete="CASCADE"), nullable=False
    )
    interview_session_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("interview_sessions.id", ondelete="SET NULL")
    )
    uploaded_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    asset_kind: Mapped[str] = mapped_column(
        String(40), nullable=False, default="audio", server_default="audio"
    )
    storage_provider: Mapped[str] = mapped_column(String(40), nullable=False, default="local")
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[AssetStatus] = mapped_column(
        enum_type(AssetStatus, "asset_status"), nullable=False, default=AssetStatus.UPLOADING
    )
    consent_version: Mapped[int] = mapped_column(Integer, nullable=False)
    properties: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)

    project: Mapped[MemoryProject] = relationship(back_populates="audio_assets")
    interview_session: Mapped[InterviewSession | None] = relationship(back_populates="audio_assets")
    uploaded_by: Mapped[User | None] = relationship(foreign_keys=[uploaded_by_user_id])
    transcript_segments: Mapped[list[TranscriptSegment]] = relationship(
        back_populates="audio_asset", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        UniqueConstraint("project_id", "storage_key", name="uq_audio_assets_project_storage_key"),
        CheckConstraint("byte_size >= 0", name="ck_audio_assets_byte_size"),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="ck_audio_assets_duration_ms"),
        CheckConstraint("consent_version > 0", name="ck_audio_assets_consent_version"),
        Index("ix_audio_assets_project_status", "project_id", "status"),
        Index("ix_audio_assets_checksum_sha256", "checksum_sha256"),
    )


class TranscriptSegment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "transcript_segments"

    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memory_projects.id", ondelete="CASCADE"), nullable=False
    )
    interview_session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False
    )
    audio_asset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("audio_assets.id", ondelete="CASCADE"), nullable=False
    )
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    speaker_label: Mapped[str | None] = mapped_column(String(120))
    confidence: Mapped[float | None] = mapped_column(Float)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="zh-CN")
    provider: Mapped[str | None] = mapped_column(String(80))
    consent_version: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    project: Mapped[MemoryProject] = relationship(back_populates="transcript_segments")
    interview_session: Mapped[InterviewSession] = relationship(back_populates="transcript_segments")
    audio_asset: Mapped[AudioAsset] = relationship(back_populates="transcript_segments")
    evidence_links: Mapped[list[ClaimEvidenceLink]] = relationship(
        back_populates="transcript_segment", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        UniqueConstraint("audio_asset_id", "segment_index", name="uq_transcript_segments_asset_index"),
        CheckConstraint("segment_index >= 0", name="ck_transcript_segments_index"),
        CheckConstraint("start_ms >= 0", name="ck_transcript_segments_start_ms"),
        CheckConstraint("end_ms >= start_ms", name="ck_transcript_segments_time_range"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_transcript_segments_confidence",
        ),
        CheckConstraint("consent_version > 0", name="ck_transcript_segments_consent_version"),
        Index("ix_transcript_segments_project_time", "project_id", "start_ms"),
        Index("ix_transcript_segments_session_index", "interview_session_id", "segment_index"),
    )


class Claim(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "claims"

    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memory_projects.id", ondelete="CASCADE"), nullable=False
    )
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ClaimStatus] = mapped_column(
        enum_type(ClaimStatus, "claim_status"), nullable=False, default=ClaimStatus.PROPOSED
    )
    confidence: Mapped[float | None] = mapped_column(Float)
    consent_version: Mapped[int] = mapped_column(Integer, nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    project: Mapped[MemoryProject] = relationship(back_populates="claims")
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_user_id])
    evidence_links: Mapped[list[ClaimEvidenceLink]] = relationship(
        back_populates="claim", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_claims_confidence"
        ),
        CheckConstraint("consent_version > 0", name="ck_claims_consent_version"),
        Index("ix_claims_project_status", "project_id", "status"),
        Index("ix_claims_project_created_at", "project_id", "created_at"),
    )


class ClaimEvidenceLink(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "claim_evidence_links"

    claim_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    transcript_segment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("transcript_segments.id", ondelete="CASCADE"), nullable=False
    )
    relationship_type: Mapped[EvidenceRelationship] = mapped_column(
        enum_type(EvidenceRelationship, "evidence_relationship"),
        nullable=False,
        default=EvidenceRelationship.SUPPORTS,
    )
    quote_text: Mapped[str | None] = mapped_column(Text)
    relevance: Mapped[float | None] = mapped_column(Float)

    claim: Mapped[Claim] = relationship(back_populates="evidence_links")
    transcript_segment: Mapped[TranscriptSegment] = relationship(back_populates="evidence_links")

    __table_args__ = (
        UniqueConstraint(
            "claim_id",
            "transcript_segment_id",
            "relationship_type",
            name="uq_claim_evidence_link",
        ),
        CheckConstraint(
            "relevance IS NULL OR (relevance >= 0 AND relevance <= 1)",
            name="ck_claim_evidence_links_relevance",
        ),
        Index("ix_claim_evidence_links_segment", "transcript_segment_id"),
    )


class Chapter(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chapters"

    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memory_projects.id", ondelete="CASCADE"), nullable=False
    )
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[ChapterStatus] = mapped_column(
        enum_type(ChapterStatus, "chapter_status"), nullable=False, default=ChapterStatus.DRAFT
    )
    latest_version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    project: Mapped[MemoryProject] = relationship(back_populates="chapters")
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_user_id])
    versions: Mapped[list[ChapterVersion]] = relationship(
        back_populates="chapter", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        UniqueConstraint("project_id", "slug", name="uq_chapters_project_slug"),
        UniqueConstraint("project_id", "position", name="uq_chapters_project_position"),
        CheckConstraint("position >= 0", name="ck_chapters_position"),
        CheckConstraint("latest_version_number >= 0", name="ck_chapters_latest_version"),
        Index("ix_chapters_project_status", "project_id", "status"),
    )


class ChapterVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chapter_versions"

    chapter_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False
    )
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    based_on_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("chapter_versions.id", ondelete="SET NULL")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[VersionStatus] = mapped_column(
        enum_type(VersionStatus, "version_status"), nullable=False, default=VersionStatus.DRAFT
    )
    source_hash: Mapped[str | None] = mapped_column(String(64))
    audit_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    chapter: Mapped[Chapter] = relationship(back_populates="versions", foreign_keys=[chapter_id])
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_user_id])
    based_on_version: Mapped[ChapterVersion | None] = relationship(
        remote_side="ChapterVersion.id", foreign_keys=[based_on_version_id]
    )
    approvals: Mapped[list[Approval]] = relationship(
        back_populates="chapter_version", passive_deletes=True
    )
    media_jobs: Mapped[list[MediaJob]] = relationship(
        back_populates="chapter_version", passive_deletes=True
    )
    deliveries: Mapped[list[Delivery]] = relationship(
        back_populates="chapter_version", passive_deletes=True
    )

    __table_args__ = (
        UniqueConstraint("chapter_id", "version", name="uq_chapter_versions_chapter_version"),
        CheckConstraint("version > 0", name="ck_chapter_versions_version"),
        Index("ix_chapter_versions_chapter_status", "chapter_id", "status"),
    )


class Approval(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "approvals"

    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memory_projects.id", ondelete="CASCADE"), nullable=False
    )
    chapter_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("chapter_versions.id", ondelete="SET NULL")
    )
    decided_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    approval_type: Mapped[str] = mapped_column(String(80), nullable=False, default="content")
    decision: Mapped[ApprovalDecision] = mapped_column(
        enum_type(ApprovalDecision, "approval_decision"), nullable=False
    )
    comment: Mapped[str | None] = mapped_column(Text)
    consent_version: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    project: Mapped[MemoryProject] = relationship(back_populates="approvals")
    chapter_version: Mapped[ChapterVersion | None] = relationship(back_populates="approvals")
    decided_by: Mapped[User | None] = relationship(foreign_keys=[decided_by_user_id])

    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key", name="uq_approvals_project_idempotency"),
        CheckConstraint("consent_version > 0", name="ck_approvals_consent_version"),
        Index("ix_approvals_version_decision", "chapter_version_id", "decision"),
        Index("ix_approvals_project_decided_at", "project_id", "decided_at"),
    )


class MediaJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "media_jobs"

    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memory_projects.id", ondelete="CASCADE"), nullable=False
    )
    requested_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    input_audio_asset_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("audio_assets.id", ondelete="SET NULL")
    )
    chapter_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("chapter_versions.id", ondelete="SET NULL")
    )
    job_type: Mapped[MediaJobType] = mapped_column(
        enum_type(MediaJobType, "media_job_type"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(80), nullable=False, default="mock")
    provider_job_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[JobStatus] = mapped_column(
        enum_type(JobStatus, "job_status"), nullable=False, default=JobStatus.QUEUED
    )
    consent_version: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3, server_default="3")
    progress_percent: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    lease_owner: Mapped[str | None] = mapped_column(String(120))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[MemoryProject] = relationship(back_populates="media_jobs")
    requested_by: Mapped[User | None] = relationship(foreign_keys=[requested_by_user_id])
    input_audio_asset: Mapped[AudioAsset | None] = relationship(foreign_keys=[input_audio_asset_id])
    chapter_version: Mapped[ChapterVersion | None] = relationship(back_populates="media_jobs")

    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key", name="uq_media_jobs_project_idempotency"),
        CheckConstraint("consent_version > 0", name="ck_media_jobs_consent_version"),
        CheckConstraint("attempts >= 0", name="ck_media_jobs_attempts"),
        CheckConstraint("max_attempts > 0", name="ck_media_jobs_max_attempts"),
        CheckConstraint(
            "progress_percent >= 0 AND progress_percent <= 100",
            name="ck_media_jobs_progress_percent",
        ),
        CheckConstraint("revision >= 0", name="ck_media_jobs_revision"),
        Index("ix_media_jobs_status_available", "status", "available_at"),
        Index("ix_media_jobs_status_lease", "status", "lease_expires_at"),
        Index("ix_media_jobs_project_type", "project_id", "job_type"),
        Index("ix_media_jobs_provider_job", "provider", "provider_job_id"),
    )


class Delivery(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "deliveries"

    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("memory_projects.id", ondelete="CASCADE"), nullable=False
    )
    chapter_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("chapter_versions.id", ondelete="SET NULL")
    )
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    delivery_type: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[DeliveryStatus] = mapped_column(
        enum_type(DeliveryStatus, "delivery_status"), nullable=False, default=DeliveryStatus.PENDING
    )
    storage_key: Mapped[str | None] = mapped_column(String(1024))
    public_url: Mapped[str | None] = mapped_column(Text)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    consent_version: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    properties: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)

    project: Mapped[MemoryProject] = relationship(back_populates="deliveries")
    chapter_version: Mapped[ChapterVersion | None] = relationship(back_populates="deliveries")
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_user_id])

    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key", name="uq_deliveries_project_idempotency"),
        UniqueConstraint(
            "project_id", "delivery_type", "version", name="uq_deliveries_project_type_version"
        ),
        CheckConstraint("version > 0", name="ck_deliveries_version"),
        CheckConstraint("consent_version > 0", name="ck_deliveries_consent_version"),
        Index("ix_deliveries_project_status", "project_id", "status"),
    )


class AuditEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_events"

    family_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("families.id", ondelete="SET NULL")
    )
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memory_projects.id", ondelete="SET NULL")
    )
    actor_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    event_type: Mapped[str] = mapped_column(String(160), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(80))
    entity_id: Mapped[str | None] = mapped_column(String(120))
    request_id: Mapped[str | None] = mapped_column(String(120))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    family: Mapped[Family | None] = relationship(foreign_keys=[family_id])
    project: Mapped[MemoryProject | None] = relationship(foreign_keys=[project_id])
    actor: Mapped[User | None] = relationship(foreign_keys=[actor_user_id])

    __table_args__ = (
        Index("ix_audit_events_family_occurred", "family_id", "occurred_at"),
        Index("ix_audit_events_project_occurred", "project_id", "occurred_at"),
        Index("ix_audit_events_event_occurred", "event_type", "occurred_at"),
        Index("ix_audit_events_request_id", "request_id"),
    )


class DeletionReceipt(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "deletion_receipts"

    family_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("families.id", ondelete="SET NULL")
    )
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("memory_projects.id", ondelete="SET NULL")
    )
    consent_grant_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("consent_grants.id", ondelete="SET NULL")
    )
    requested_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL")
    )
    status: Mapped[DeletionStatus] = mapped_column(
        enum_type(DeletionStatus, "deletion_status"),
        nullable=False,
        default=DeletionStatus.REQUESTED,
    )
    scope: Mapped[str] = mapped_column(String(160), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_counts: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    external_results: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    receipt_hash: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)

    family: Mapped[Family | None] = relationship(foreign_keys=[family_id])
    project: Mapped[MemoryProject | None] = relationship(foreign_keys=[project_id])
    consent_grant: Mapped[ConsentGrant | None] = relationship(foreign_keys=[consent_grant_id])
    requested_by: Mapped[User | None] = relationship(foreign_keys=[requested_by_user_id])

    __table_args__ = (
        UniqueConstraint("receipt_hash", name="uq_deletion_receipts_receipt_hash"),
        Index("ix_deletion_receipts_project_requested", "project_id", "requested_at"),
        Index("ix_deletion_receipts_status_requested", "status", "requested_at"),
    )
