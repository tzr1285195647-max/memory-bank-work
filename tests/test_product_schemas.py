from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from memory_bank.auth import Role
from memory_bank.product_schemas import (
    AssetKind,
    AssetResponse,
    AssetStatus,
    AssetUploadRequest,
    ConsentGrantRequest,
    ConsentResponse,
    ConsentScope,
    ConsentStatus,
    FamilyInvitationCreateRequest,
    FamilyMemberAddRequest,
    JobResponse,
    JobStatus,
    JobType,
    Page,
    PageMeta,
    ProjectCreateRequest,
    UserRegisterRequest,
    UserResponse,
)


def uid() -> str:
    return str(uuid4())


def now() -> datetime:
    return datetime.now(UTC)


def test_registration_normalizes_email_and_rejects_extra_or_short_password():
    request = UserRegisterRequest(
        email="  Person@Example.COM ",
        password="a sufficiently long password",
        display_name=" 小满 ",
    )
    assert request.email == "person@example.com"
    assert request.display_name == "小满"
    assert request.password.get_secret_value() == "a sufficiently long password"
    assert "a sufficiently long password" not in repr(request)

    with pytest.raises(ValidationError):
        UserRegisterRequest(
            email="not-an-email",
            password="short",
            display_name="小满",
            unexpected="must be rejected",
        )


def test_uuid_fields_remain_canonical_strings_and_reject_non_uuid_values():
    user_id = uid().upper()
    response = UserResponse(
        id=user_id,
        email="person@example.com",
        display_name="小满",
        is_active=True,
        created_at=now(),
    )
    assert isinstance(response.id, str)
    assert response.id == user_id.lower()

    with pytest.raises(ValidationError):
        UserResponse(
            id="user-123",
            email="person@example.com",
            display_name="小满",
            is_active=True,
            created_at=now(),
        )


def test_user_response_drops_password_hash_and_other_internal_columns():
    response = UserResponse.model_validate(
        {
            "id": uid(),
            "email": "person@example.com",
            "display_name": "小满",
            "is_active": True,
            "created_at": now(),
            "password_hash": "$argon2id$never-return-this",
            "password_reset_token": "secret",
        }
    )
    output = response.model_dump(mode="json")
    assert "password_hash" not in output
    assert "password_reset_token" not in output
    assert "secret" not in str(output)


def test_member_and_invitation_apis_cannot_assign_owner_role():
    member = FamilyMemberAddRequest(user_id=uid(), role="editor")
    invite = FamilyInvitationCreateRequest(email="New@Example.com", role="approver")
    admin = FamilyMemberAddRequest(user_id=uid(), role="admin")
    assert member.role is Role.EDITOR
    assert invite.email == "new@example.com"
    assert invite.role is Role.APPROVER
    assert admin.role is Role.ADMIN

    with pytest.raises(ValidationError):
        FamilyMemberAddRequest(user_id=uid(), role="owner")
    with pytest.raises(ValidationError):
        FamilyInvitationCreateRequest(email="new@example.com", role="owner")


def test_project_request_has_strict_limits_and_normalizes_duplicate_tags():
    request = ProjectCreateRequest(
        family_id=uid(),
        title="外婆的夏天",
        subject_name="外婆",
        topic="童年生活",
        max_interview_rounds=6,
        tags=["童年", " 家庭 ", "童年"],
    )
    assert request.tags == ["童年", "家庭"]

    with pytest.raises(ValidationError):
        ProjectCreateRequest(
            family_id=uid(),
            title="外婆的夏天",
            subject_name="外婆",
            topic="童年生活",
            max_interview_rounds="6",
        )
    with pytest.raises(ValidationError):
        ProjectCreateRequest(
            family_id=uid(),
            title="外婆的夏天",
            subject_name="外婆",
            topic="童年生活",
            max_interview_rounds=21,
        )


def test_consent_requires_scope_and_revocation_timestamp_is_consistent():
    project_id = uid()
    with pytest.raises(ValidationError):
        ConsentGrantRequest(
            project_id=project_id,
            granted_by_name="外婆本人",
            scopes=set(),
            policy_version="2026-09",
        )

    common = {
        "id": uid(),
        "family_id": uid(),
        "project_id": project_id,
        "granted_by_user_id": uid(),
        "granted_by_name": "外婆本人",
        "scopes": {ConsentScope.INTERVIEW, ConsentScope.AI_PROCESSING},
        "policy_version": "2026-09",
        "version": 2,
        "status": ConsentStatus.REVOKED,
        "granted_at": now(),
    }
    with pytest.raises(ValidationError):
        ConsentResponse(**common)

    consent = ConsentResponse(**common, revoked_at=now(), revocation_reason="本人撤回")
    assert consent.status is ConsentStatus.REVOKED
    assert consent.version == 2


def test_asset_input_rejects_paths_and_response_never_exposes_object_path():
    values = {
        "project_id": uid(),
        "kind": AssetKind.AUDIO,
        "filename": "story.mp3",
        "content_type": "audio/mpeg",
        "size_bytes": 2048,
        "checksum_sha256": "A" * 64,
    }
    request = AssetUploadRequest(**values)
    assert request.checksum_sha256 == "a" * 64

    with pytest.raises(ValidationError):
        AssetUploadRequest(**{**values, "filename": "../../private/story.mp3"})

    response = AssetResponse.model_validate(
        {
            "id": uid(),
            "family_id": uid(),
            "project_id": values["project_id"],
            "uploaded_by_user_id": uid(),
            "kind": "audio",
            "status": "ready",
            "filename": "story.mp3",
            "content_type": "audio/mpeg",
            "size_bytes": 2048,
            "checksum_sha256": "a" * 64,
            "created_at": now(),
            "object_path": r"D:\private\assets\story.mp3",
            "provider_credentials": "secret",
        }
    )
    output = response.model_dump(mode="json")
    assert "object_path" not in output
    assert "provider_credentials" not in output
    assert "D:\\private" not in str(output)


def test_terminal_job_invariants_and_progress_limits():
    common = {
        "id": uid(),
        "family_id": uid(),
        "project_id": uid(),
        "job_type": JobType.TRANSCRIPTION,
        "created_at": now(),
    }
    job = JobResponse(
        **common,
        status=JobStatus.SUCCEEDED,
        progress_percent=100,
        finished_at=now(),
    )
    assert job.progress_percent == 100

    with pytest.raises(ValidationError):
        JobResponse(**common, status=JobStatus.SUCCEEDED, progress_percent=90, finished_at=now())
    with pytest.raises(ValidationError):
        JobResponse(**common, status=JobStatus.FAILED, progress_percent=10)
    with pytest.raises(ValidationError):
        JobResponse(**common, status=JobStatus.RUNNING, progress_percent=101)


def test_typed_page_serializes_public_items():
    user = UserResponse(
        id=uid(),
        email="person@example.com",
        display_name="小满",
        is_active=True,
        created_at=now(),
    )
    page = Page[UserResponse](
        items=[user],
        meta=PageMeta(page=1, page_size=20, total_items=1, total_pages=1),
    )
    data = page.model_dump(mode="json")
    assert data["items"][0]["id"] == user.id
    assert data["meta"]["total_items"] == 1
