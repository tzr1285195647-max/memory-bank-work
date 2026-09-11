"""Create the production product schema.

Revision ID: 0001_product_schema
Revises: None
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0001_product_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def enum_type(name: str, *values: str) -> sa.Enum:
    return sa.Enum(
        *values,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=max(map(len, values)),
    )


def id_column() -> sa.Column:
    return sa.Column("id", sa.String(length=36), nullable=False)


def timestamp_columns() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
    )


def upgrade() -> None:
    op.create_table(
        "users",
        id_column(),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("email_normalized", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column(
            "status", enum_type("user_status", "active", "disabled", "deleted"), nullable=False
        ),
        sa.Column("locale", sa.String(length=16), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        *timestamp_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email_normalized", name="uq_users_email_normalized"),
    )
    op.create_index("ix_users_status_created_at", "users", ["status", "created_at"], unique=False)

    op.create_table(
        "families",
        id_column(),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_families_created_by_user_id", "families", ["created_by_user_id"], unique=False)
    op.create_index("ix_families_active_updated_at", "families", ["is_active", "updated_at"], unique=False)

    op.create_table(
        "family_memberships",
        id_column(),
        sa.Column("family_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column(
            "role",
            enum_type("membership_role", "owner", "admin", "editor", "approver", "viewer"),
            nullable=False,
        ),
        sa.Column(
            "status",
            enum_type("membership_status", "invited", "active", "removed"),
            nullable=False,
        ),
        sa.Column("invited_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["family_id"], ["families.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invited_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("family_id", "user_id", name="uq_family_memberships_family_user"),
    )
    op.create_index(
        "ix_family_memberships_user_status", "family_memberships", ["user_id", "status"], unique=False
    )
    op.create_index(
        "ix_family_memberships_family_role", "family_memberships", ["family_id", "role"], unique=False
    )

    op.create_table(
        "memory_projects",
        id_column(),
        sa.Column("family_id", sa.String(length=36), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("subject_user_id", sa.String(length=36), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("subject_name", sa.String(length=120), nullable=False),
        sa.Column("topic", sa.String(length=240), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("workflow_thread_id", sa.String(length=120), nullable=True),
        sa.Column(
            "status",
            enum_type("project_status", "draft", "active", "review", "delivered", "archived", "revoked"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(length=40), nullable=False),
        sa.Column("current_consent_version", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        *timestamp_columns(),
        sa.CheckConstraint(
            "current_consent_version >= 0", name="ck_memory_projects_current_consent_version"
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["family_id"], ["families.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subject_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_thread_id", name="uq_memory_projects_workflow_thread_id"),
    )
    op.create_index("ix_memory_projects_family_status", "memory_projects", ["family_id", "status"], unique=False)
    op.create_index("ix_memory_projects_family_stage", "memory_projects", ["family_id", "stage"], unique=False)
    op.create_index(
        "ix_memory_projects_family_updated_at", "memory_projects", ["family_id", "updated_at"], unique=False
    )

    op.create_table(
        "consent_grants",
        id_column(),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.String(length=40), server_default="v1", nullable=False),
        sa.Column("status", enum_type("consent_status", "active", "revoked", "expired"), nullable=False),
        sa.Column("granted_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("subject_user_id", sa.String(length=36), nullable=True),
        sa.Column("granted_by_name", sa.String(length=120), nullable=False),
        sa.Column("purpose", sa.String(length=500), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("revocation_reason", sa.String(length=500), nullable=True),
        *timestamp_columns(),
        sa.CheckConstraint("version > 0", name="ck_consent_grants_version"),
        sa.ForeignKeyConstraint(["granted_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["memory_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["revoked_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["subject_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "version", name="uq_consent_grants_project_version"),
    )
    op.create_index("ix_consent_grants_project_status", "consent_grants", ["project_id", "status"], unique=False)
    op.create_index("ix_consent_grants_expires_at", "consent_grants", ["expires_at"], unique=False)

    op.create_table(
        "interview_sessions",
        id_column(),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("started_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("thread_id", sa.String(length=120), nullable=True),
        sa.Column(
            "status",
            enum_type("interview_status", "pending", "active", "completed", "cancelled", "failed"),
            nullable=False,
        ),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("current_round", sa.Integer(), nullable=False),
        sa.Column("max_rounds", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("context", sa.JSON(), nullable=False),
        *timestamp_columns(),
        sa.CheckConstraint("current_round >= 0", name="ck_interview_sessions_current_round"),
        sa.CheckConstraint("max_rounds > 0", name="ck_interview_sessions_max_rounds"),
        sa.ForeignKeyConstraint(["project_id"], ["memory_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["started_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("thread_id", name="uq_interview_sessions_thread_id"),
    )
    op.create_index(
        "ix_interview_sessions_project_status", "interview_sessions", ["project_id", "status"], unique=False
    )

    op.create_table(
        "audio_assets",
        id_column(),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("interview_session_id", sa.String(length=36), nullable=True),
        sa.Column("uploaded_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("asset_kind", sa.String(length=40), server_default="audio", nullable=False),
        sa.Column("storage_provider", sa.String(length=40), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", enum_type("asset_status", "uploading", "ready", "processing", "failed", "deleted"), nullable=False),
        sa.Column("consent_version", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        *timestamp_columns(),
        sa.CheckConstraint("byte_size >= 0", name="ck_audio_assets_byte_size"),
        sa.CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="ck_audio_assets_duration_ms"),
        sa.CheckConstraint("consent_version > 0", name="ck_audio_assets_consent_version"),
        sa.ForeignKeyConstraint(["interview_session_id"], ["interview_sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["memory_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "storage_key", name="uq_audio_assets_project_storage_key"),
    )
    op.create_index("ix_audio_assets_project_status", "audio_assets", ["project_id", "status"], unique=False)
    op.create_index("ix_audio_assets_checksum_sha256", "audio_assets", ["checksum_sha256"], unique=False)

    op.create_table(
        "transcript_segments",
        id_column(),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("interview_session_id", sa.String(length=36), nullable=False),
        sa.Column("audio_asset_id", sa.String(length=36), nullable=False),
        sa.Column("segment_index", sa.Integer(), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("speaker_label", sa.String(length=120), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=True),
        sa.Column("consent_version", sa.Integer(), nullable=False),
        sa.Column("raw_result", sa.JSON(), nullable=False),
        *timestamp_columns(),
        sa.CheckConstraint("segment_index >= 0", name="ck_transcript_segments_index"),
        sa.CheckConstraint("start_ms >= 0", name="ck_transcript_segments_start_ms"),
        sa.CheckConstraint("end_ms >= start_ms", name="ck_transcript_segments_time_range"),
        sa.CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_transcript_segments_confidence"),
        sa.CheckConstraint("consent_version > 0", name="ck_transcript_segments_consent_version"),
        sa.ForeignKeyConstraint(["audio_asset_id"], ["audio_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["interview_session_id"], ["interview_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["memory_projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("audio_asset_id", "segment_index", name="uq_transcript_segments_asset_index"),
    )
    op.create_index("ix_transcript_segments_project_time", "transcript_segments", ["project_id", "start_ms"], unique=False)
    op.create_index("ix_transcript_segments_session_index", "transcript_segments", ["interview_session_id", "segment_index"], unique=False)

    op.create_table(
        "claims",
        id_column(),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=True),
        sa.Column("status", enum_type("claim_status", "proposed", "subject_confirmed", "corroborated", "contradicted", "contested", "rejected"), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("consent_version", sa.Integer(), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        *timestamp_columns(),
        sa.CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_claims_confidence"),
        sa.CheckConstraint("consent_version > 0", name="ck_claims_consent_version"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["memory_projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_claims_project_status", "claims", ["project_id", "status"], unique=False)
    op.create_index("ix_claims_project_created_at", "claims", ["project_id", "created_at"], unique=False)

    op.create_table(
        "claim_evidence_links",
        id_column(),
        sa.Column("claim_id", sa.String(length=36), nullable=False),
        sa.Column("transcript_segment_id", sa.String(length=36), nullable=False),
        sa.Column("relationship_type", enum_type("evidence_relationship", "supports", "contradicts", "context"), nullable=False),
        sa.Column("quote_text", sa.Text(), nullable=True),
        sa.Column("relevance", sa.Float(), nullable=True),
        *timestamp_columns(),
        sa.CheckConstraint("relevance IS NULL OR (relevance >= 0 AND relevance <= 1)", name="ck_claim_evidence_links_relevance"),
        sa.ForeignKeyConstraint(["claim_id"], ["claims.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["transcript_segment_id"], ["transcript_segments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("claim_id", "transcript_segment_id", "relationship_type", name="uq_claim_evidence_link"),
    )
    op.create_index("ix_claim_evidence_links_segment", "claim_evidence_links", ["transcript_segment_id"], unique=False)

    op.create_table(
        "chapters",
        id_column(),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", enum_type("chapter_status", "draft", "in_review", "approved", "archived"), nullable=False),
        sa.Column("latest_version_number", sa.Integer(), nullable=False),
        *timestamp_columns(),
        sa.CheckConstraint("position >= 0", name="ck_chapters_position"),
        sa.CheckConstraint("latest_version_number >= 0", name="ck_chapters_latest_version"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["memory_projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "position", name="uq_chapters_project_position"),
        sa.UniqueConstraint("project_id", "slug", name="uq_chapters_project_slug"),
    )
    op.create_index("ix_chapters_project_status", "chapters", ["project_id", "status"], unique=False)

    op.create_table(
        "chapter_versions",
        id_column(),
        sa.Column("chapter_id", sa.String(length=36), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("based_on_version_id", sa.String(length=36), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", enum_type("version_status", "draft", "pending_review", "approved", "superseded", "rejected"), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=True),
        sa.Column("audit_summary", sa.JSON(), nullable=False),
        *timestamp_columns(),
        sa.CheckConstraint("version > 0", name="ck_chapter_versions_version"),
        sa.ForeignKeyConstraint(["based_on_version_id"], ["chapter_versions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chapter_id", "version", name="uq_chapter_versions_chapter_version"),
    )
    op.create_index("ix_chapter_versions_chapter_status", "chapter_versions", ["chapter_id", "status"], unique=False)

    op.create_table(
        "approvals",
        id_column(),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("chapter_version_id", sa.String(length=36), nullable=True),
        sa.Column("decided_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("approval_type", sa.String(length=80), nullable=False),
        sa.Column("decision", enum_type("approval_decision", "approved", "changes_requested", "rejected"), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("consent_version", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        *timestamp_columns(),
        sa.CheckConstraint("consent_version > 0", name="ck_approvals_consent_version"),
        sa.ForeignKeyConstraint(["chapter_version_id"], ["chapter_versions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["memory_projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_approvals_project_idempotency"),
    )
    op.create_index("ix_approvals_version_decision", "approvals", ["chapter_version_id", "decision"], unique=False)
    op.create_index("ix_approvals_project_decided_at", "approvals", ["project_id", "decided_at"], unique=False)

    op.create_table(
        "media_jobs",
        id_column(),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("requested_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("input_audio_asset_id", sa.String(length=36), nullable=True),
        sa.Column("chapter_version_id", sa.String(length=36), nullable=True),
        sa.Column("job_type", enum_type("media_job_type", "transcription", "image", "speech", "video", "export"), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("provider_job_id", sa.String(length=255), nullable=True),
        sa.Column("status", enum_type("job_status", "queued", "running", "succeeded", "failed", "cancelled"), nullable=False),
        sa.Column("consent_version", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("progress_percent", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *timestamp_columns(),
        sa.CheckConstraint("consent_version > 0", name="ck_media_jobs_consent_version"),
        sa.CheckConstraint("attempts >= 0", name="ck_media_jobs_attempts"),
        sa.CheckConstraint("max_attempts > 0", name="ck_media_jobs_max_attempts"),
        sa.CheckConstraint("progress_percent >= 0 AND progress_percent <= 100", name="ck_media_jobs_progress_percent"),
        sa.CheckConstraint("revision >= 0", name="ck_media_jobs_revision"),
        sa.ForeignKeyConstraint(["chapter_version_id"], ["chapter_versions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["input_audio_asset_id"], ["audio_assets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["memory_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_media_jobs_project_idempotency"),
    )
    op.create_index("ix_media_jobs_status_available", "media_jobs", ["status", "available_at"], unique=False)
    op.create_index("ix_media_jobs_status_lease", "media_jobs", ["status", "lease_expires_at"], unique=False)
    op.create_index("ix_media_jobs_project_type", "media_jobs", ["project_id", "job_type"], unique=False)
    op.create_index("ix_media_jobs_provider_job", "media_jobs", ["provider", "provider_job_id"], unique=False)

    op.create_table(
        "deliveries",
        id_column(),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("chapter_version_id", sa.String(length=36), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("delivery_type", sa.String(length=80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", enum_type("delivery_status", "pending", "ready", "published", "failed", "revoked"), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=True),
        sa.Column("public_url", sa.Text(), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("consent_version", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        *timestamp_columns(),
        sa.CheckConstraint("version > 0", name="ck_deliveries_version"),
        sa.CheckConstraint("consent_version > 0", name="ck_deliveries_consent_version"),
        sa.ForeignKeyConstraint(["chapter_version_id"], ["chapter_versions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["memory_projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "delivery_type", "version", name="uq_deliveries_project_type_version"),
        sa.UniqueConstraint("project_id", "idempotency_key", name="uq_deliveries_project_idempotency"),
    )
    op.create_index("ix_deliveries_project_status", "deliveries", ["project_id", "status"], unique=False)

    op.create_table(
        "audit_events",
        id_column(),
        sa.Column("family_id", sa.String(length=36), nullable=True),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("actor_user_id", sa.String(length=36), nullable=True),
        sa.Column("event_type", sa.String(length=160), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=True),
        sa.Column("entity_id", sa.String(length=120), nullable=True),
        sa.Column("request_id", sa.String(length=120), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["family_id"], ["families.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["memory_projects.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_family_occurred", "audit_events", ["family_id", "occurred_at"], unique=False)
    op.create_index("ix_audit_events_project_occurred", "audit_events", ["project_id", "occurred_at"], unique=False)
    op.create_index("ix_audit_events_event_occurred", "audit_events", ["event_type", "occurred_at"], unique=False)
    op.create_index("ix_audit_events_request_id", "audit_events", ["request_id"], unique=False)

    op.create_table(
        "deletion_receipts",
        id_column(),
        sa.Column("family_id", sa.String(length=36), nullable=True),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("consent_grant_id", sa.String(length=36), nullable=True),
        sa.Column("requested_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("status", enum_type("deletion_status", "requested", "running", "completed", "partial", "failed"), nullable=False),
        sa.Column("scope", sa.String(length=160), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_counts", sa.JSON(), nullable=False),
        sa.Column("external_results", sa.JSON(), nullable=False),
        sa.Column("receipt_hash", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["consent_grant_id"], ["consent_grants.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["family_id"], ["families.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["memory_projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("receipt_hash", name="uq_deletion_receipts_receipt_hash"),
    )
    op.create_index("ix_deletion_receipts_project_requested", "deletion_receipts", ["project_id", "requested_at"], unique=False)
    op.create_index("ix_deletion_receipts_status_requested", "deletion_receipts", ["status", "requested_at"], unique=False)


def downgrade() -> None:
    op.drop_table("deletion_receipts")
    op.drop_table("audit_events")
    op.drop_table("deliveries")
    op.drop_table("media_jobs")
    op.drop_table("approvals")
    op.drop_table("chapter_versions")
    op.drop_table("chapters")
    op.drop_table("claim_evidence_links")
    op.drop_table("claims")
    op.drop_table("transcript_segments")
    op.drop_table("audio_assets")
    op.drop_table("interview_sessions")
    op.drop_table("consent_grants")
    op.drop_table("memory_projects")
    op.drop_table("family_memberships")
    op.drop_table("families")
    op.drop_table("users")
