from __future__ import annotations

"""Transaction-scoped business services for the product API.

The service deliberately does not own a database engine or commit/rollback a
transaction.  HTTP handlers and workers pass an existing SQLAlchemy ``Session``
and remain responsible for the unit-of-work boundary.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
from pathlib import Path, PurePath
import re
from typing import Any, BinaryIO, Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .assets import LocalObjectStorage, StoredObject, normalize_media_type
from .auth import Permission, role_has_permission
from .db import (
    AssetStatus,
    AudioAsset,
    AuditEvent,
    ConsentGrant,
    ConsentStatus,
    DeletionReceipt,
    DeletionStatus,
    Delivery,
    DeliveryStatus,
    Family,
    FamilyMembership,
    InterviewSession,
    InterviewStatus,
    JobStatus,
    MediaJob,
    MediaJobType,
    MembershipRole,
    MembershipStatus,
    MemoryProject,
    ProjectStatus,
    User,
    UserStatus,
    new_uuid,
    utc_now,
)
from .security import hash_password, password_needs_rehash, verify_password


class ProductServiceError(RuntimeError):
    """Base class for expected product-domain failures."""


class ValidationError(ProductServiceError, ValueError):
    pass


class ConflictError(ProductServiceError):
    pass


class NotFoundError(ProductServiceError, LookupError):
    pass


class AuthenticationFailedError(ProductServiceError, PermissionError):
    """Intentionally generic so login cannot reveal whether an email exists."""


class AccessDeniedError(ProductServiceError, PermissionError):
    pass


class FamilyScopeError(AccessDeniedError):
    """The actor has no active membership/permission inside this family."""


class ConsentError(ProductServiceError, PermissionError):
    pass


class StaleConsentError(ConsentError):
    pass


class ConsentRevokedError(ConsentError):
    pass


class ConsentExpiredError(ConsentError):
    pass


class MissingConsentScopeError(ConsentError):
    pass


class WorkflowBridgeError(ProductServiceError):
    pass


class StorageUnavailableError(ProductServiceError):
    pass


@runtime_checkable
class WorkflowBridge(Protocol):
    """The small portion of ``MemoryBankWorkflow`` used by this service."""

    def start(self, project: dict[str, Any]) -> dict[str, Any]: ...

    def revoke(self, project_id: str, reason: str) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class PublicUser:
    """Safe projection for responses; notably excludes ``password_hash``."""

    id: str
    email: str
    display_name: str
    locale: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ProjectCreationResult:
    project: MemoryProject
    consent: ConsentGrant
    interview_session: InterviewSession


@dataclass(frozen=True, slots=True)
class ConsentRevocationResult:
    consent: ConsentGrant
    receipt: DeletionReceipt
    cancelled_job_count: int


DEFAULT_CONSENT_SCOPES = (
    "interview",
    "transcription",
    "ai_processing",
    "family_sharing",
)
_EMAIL_PATTERN = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_REDACTED_AUDIT_KEYS = (
    "password",
    "token",
    "secret",
    "authorization",
    "cookie",
    "absolute_path",
    "storage_path",
)


def _required_text(value: str, field: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValidationError(f"{field} is required")
    if len(cleaned) > maximum:
        raise ValidationError(f"{field} must contain at most {maximum} characters")
    return cleaned


def _normalize_email(email: str) -> str:
    normalized = _required_text(email, "email", maximum=320).casefold()
    if _EMAIL_PATTERN.fullmatch(normalized) is None:
        raise ValidationError("email is invalid")
    return normalized


def _aware_utc(value: datetime) -> datetime:
    # SQLite may round-trip a timezone-aware value without tzinfo.  The schema
    # stores all timestamps as UTC, so restoring UTC here is unambiguous.
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _clean_scopes(scopes: Sequence[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw_scope in scopes:
        scope = _required_text(str(raw_scope), "consent scope", maximum=80).casefold()
        if scope not in seen:
            seen.add(scope)
            result.append(scope)
    if not result:
        raise ValidationError("at least one consent scope is required")
    return result


def _safe_audit_value(value: Any, *, key: str = "") -> Any:
    lowered = key.casefold()
    if any(marker in lowered for marker in _REDACTED_AUDIT_KEYS):
        return "[redacted]"
    if isinstance(value, Path):
        return "[path omitted]"
    if isinstance(value, Mapping):
        return {
            str(item_key): _safe_audit_value(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_safe_audit_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class ProductService:
    """Business rules shared by HTTP handlers and background workers."""

    def __init__(
        self,
        *,
        workflow: WorkflowBridge | None = None,
        object_storage: LocalObjectStorage | None = None,
    ) -> None:
        self.workflow = workflow
        self.object_storage = object_storage

    # ------------------------------------------------------------------ users
    @staticmethod
    def public_user(user: User) -> PublicUser:
        return PublicUser(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            locale=user.locale,
            is_active=user.status == UserStatus.ACTIVE,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )

    def register_user(
        self,
        session: Session,
        *,
        email: str,
        password: str,
        display_name: str,
        locale: str = "zh-CN",
    ) -> User:
        normalized = _normalize_email(email)
        if len(password) < 12 or len(password) > 128:
            raise ValidationError("password must contain 12 to 128 characters")
        name = _required_text(display_name, "display_name", maximum=120)
        locale_value = _required_text(locale, "locale", maximum=16)
        if session.scalar(select(User.id).where(User.email_normalized == normalized)) is not None:
            raise ConflictError("an account with this email already exists")

        user = User(
            email=normalized,
            email_normalized=normalized,
            display_name=name,
            password_hash=hash_password(password),
            locale=locale_value,
            status=UserStatus.ACTIVE,
        )
        try:
            with session.begin_nested():
                session.add(user)
                session.flush()
        except IntegrityError as exc:
            raise ConflictError("an account with this email already exists") from exc
        self._audit(
            session,
            event_type="user.registered",
            actor_user_id=user.id,
            entity_type="user",
            entity_id=user.id,
        )
        session.flush()
        return user

    def authenticate_user(self, session: Session, *, email: str, password: str) -> User:
        normalized = _normalize_email(email)
        user = session.scalar(select(User).where(User.email_normalized == normalized))
        if (
            user is None
            or user.status != UserStatus.ACTIVE
            or user.password_hash is None
            or not verify_password(password, user.password_hash)
        ):
            raise AuthenticationFailedError("email or password is incorrect")
        if password_needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
        user.last_login_at = utc_now()
        self._audit(
            session,
            event_type="user.authenticated",
            actor_user_id=user.id,
            entity_type="user",
            entity_id=user.id,
        )
        session.flush()
        return user

    def get_user(self, session: Session, user_id: str, *, include_inactive: bool = False) -> User:
        user = session.get(User, user_id)
        if user is None or (not include_inactive and user.status != UserStatus.ACTIVE):
            raise NotFoundError("user was not found")
        return user

    # --------------------------------------------------------------- families
    def require_family_permission(
        self,
        session: Session,
        *,
        actor_user_id: str,
        family_id: str,
        permission: Permission,
    ) -> FamilyMembership:
        membership = session.scalar(
            select(FamilyMembership)
            .join(Family, Family.id == FamilyMembership.family_id)
            .join(User, User.id == FamilyMembership.user_id)
            .where(
                FamilyMembership.family_id == family_id,
                FamilyMembership.user_id == actor_user_id,
                FamilyMembership.status == MembershipStatus.ACTIVE,
                Family.is_active.is_(True),
                User.status == UserStatus.ACTIVE,
            )
        )
        if membership is None or not role_has_permission(membership.role.value, permission):
            # Use one response for a missing family, another tenant's family, and
            # a role without permission to avoid an ID-enumeration oracle.
            raise FamilyScopeError("family is unavailable to this account")
        return membership

    def create_family(
        self,
        session: Session,
        *,
        actor_user_id: str,
        name: str,
        description: str | None = None,
    ) -> Family:
        actor = self.get_user(session, actor_user_id)
        family_name = _required_text(name, "name", maximum=160)
        description_value = description.strip() if isinstance(description, str) else None
        if description_value and len(description_value) > 500:
            raise ValidationError("description must contain at most 500 characters")
        family = Family(
            name=family_name,
            created_by_user_id=actor.id,
            is_active=True,
            settings={"description": description_value} if description_value else {},
        )
        session.add(family)
        session.flush()
        membership = FamilyMembership(
            family_id=family.id,
            user_id=actor.id,
            role=MembershipRole.OWNER,
            status=MembershipStatus.ACTIVE,
            invited_by_user_id=actor.id,
            joined_at=utc_now(),
        )
        session.add(membership)
        self._audit(
            session,
            event_type="family.created",
            actor_user_id=actor.id,
            family_id=family.id,
            entity_type="family",
            entity_id=family.id,
        )
        session.flush()
        return family

    def list_families(self, session: Session, *, actor_user_id: str) -> list[Family]:
        self.get_user(session, actor_user_id)
        return list(
            session.scalars(
                select(Family)
                .join(FamilyMembership, FamilyMembership.family_id == Family.id)
                .where(
                    FamilyMembership.user_id == actor_user_id,
                    FamilyMembership.status == MembershipStatus.ACTIVE,
                    Family.is_active.is_(True),
                )
                .order_by(Family.updated_at.desc(), Family.id)
            )
        )

    def get_family(self, session: Session, *, actor_user_id: str, family_id: str) -> Family:
        self.require_family_permission(
            session,
            actor_user_id=actor_user_id,
            family_id=family_id,
            permission=Permission.READ_MEMORY,
        )
        family = session.get(Family, family_id)
        if family is None:  # Defensive; the scoped membership query normally catches this.
            raise FamilyScopeError("family is unavailable to this account")
        return family

    def list_family_members(
        self, session: Session, *, actor_user_id: str, family_id: str
    ) -> list[FamilyMembership]:
        self.require_family_permission(
            session,
            actor_user_id=actor_user_id,
            family_id=family_id,
            permission=Permission.READ_MEMORY,
        )
        return list(
            session.scalars(
                select(FamilyMembership)
                .where(
                    FamilyMembership.family_id == family_id,
                    FamilyMembership.status == MembershipStatus.ACTIVE,
                )
                .order_by(FamilyMembership.joined_at, FamilyMembership.id)
            )
        )

    def add_family_member(
        self,
        session: Session,
        *,
        actor_user_id: str,
        family_id: str,
        user_id: str,
        role: MembershipRole | str,
    ) -> FamilyMembership:
        self.require_family_permission(
            session,
            actor_user_id=actor_user_id,
            family_id=family_id,
            permission=Permission.MANAGE_MEMBERS,
        )
        target = self.get_user(session, user_id)
        try:
            normalized_role = MembershipRole(role)
        except ValueError as exc:
            raise ValidationError("membership role is invalid") from exc
        if normalized_role == MembershipRole.OWNER:
            raise ValidationError("owner transfer requires a dedicated operation")

        membership = session.scalar(
            select(FamilyMembership).where(
                FamilyMembership.family_id == family_id,
                FamilyMembership.user_id == target.id,
            )
        )
        if membership is not None and membership.status == MembershipStatus.ACTIVE:
            raise ConflictError("user is already an active family member")
        if membership is None:
            membership = FamilyMembership(family_id=family_id, user_id=target.id)
            session.add(membership)
        membership.role = normalized_role
        membership.status = MembershipStatus.ACTIVE
        membership.invited_by_user_id = actor_user_id
        membership.joined_at = utc_now()
        try:
            with session.begin_nested():
                session.flush()
        except IntegrityError as exc:
            raise ConflictError("user is already a family member") from exc
        self._audit(
            session,
            event_type="family.member_added",
            actor_user_id=actor_user_id,
            family_id=family_id,
            entity_type="family_membership",
            entity_id=membership.id,
            payload={"member_user_id": user_id, "role": normalized_role.value},
        )
        session.flush()
        return membership

    def update_family_member_role(
        self,
        session: Session,
        *,
        actor_user_id: str,
        family_id: str,
        user_id: str,
        role: MembershipRole | str,
    ) -> FamilyMembership:
        self.require_family_permission(
            session,
            actor_user_id=actor_user_id,
            family_id=family_id,
            permission=Permission.MANAGE_MEMBERS,
        )
        try:
            normalized_role = MembershipRole(role)
        except ValueError as exc:
            raise ValidationError("membership role is invalid") from exc
        if normalized_role == MembershipRole.OWNER:
            raise ValidationError("owner transfer requires a dedicated operation")
        membership = session.scalar(
            select(FamilyMembership).where(
                FamilyMembership.family_id == family_id,
                FamilyMembership.user_id == user_id,
                FamilyMembership.status == MembershipStatus.ACTIVE,
            )
        )
        if membership is None:
            raise NotFoundError("active family member was not found")
        if membership.role == MembershipRole.OWNER:
            raise AccessDeniedError("the owner role cannot be changed here")
        membership.role = normalized_role
        self._audit(
            session,
            event_type="family.member_role_updated",
            actor_user_id=actor_user_id,
            family_id=family_id,
            entity_type="family_membership",
            entity_id=membership.id,
            payload={"member_user_id": user_id, "role": normalized_role.value},
        )
        session.flush()
        return membership

    def remove_family_member(
        self,
        session: Session,
        *,
        actor_user_id: str,
        family_id: str,
        user_id: str,
    ) -> FamilyMembership:
        self.require_family_permission(
            session,
            actor_user_id=actor_user_id,
            family_id=family_id,
            permission=Permission.MANAGE_MEMBERS,
        )
        membership = session.scalar(
            select(FamilyMembership).where(
                FamilyMembership.family_id == family_id,
                FamilyMembership.user_id == user_id,
                FamilyMembership.status == MembershipStatus.ACTIVE,
            )
        )
        if membership is None:
            raise NotFoundError("active family member was not found")
        if membership.role == MembershipRole.OWNER:
            raise AccessDeniedError("the family owner cannot be removed")
        membership.status = MembershipStatus.REMOVED
        self._audit(
            session,
            event_type="family.member_removed",
            actor_user_id=actor_user_id,
            family_id=family_id,
            entity_type="family_membership",
            entity_id=membership.id,
            payload={"member_user_id": user_id},
        )
        session.flush()
        return membership

    # --------------------------------------------------------------- projects
    def create_project(
        self,
        session: Session,
        *,
        actor_user_id: str,
        family_id: str,
        title: str,
        subject_name: str,
        topic: str,
        granted_by_name: str,
        description: str | None = None,
        subject_user_id: str | None = None,
        language: str = "zh-CN",
        max_interview_rounds: int = 5,
        consent_purpose: str = "采访、整理并向授权家庭成员展示记忆内容",
        consent_scopes: Sequence[str] = DEFAULT_CONSENT_SCOPES,
        consent_expires_at: datetime | None = None,
        tags: Sequence[str] = (),
    ) -> ProjectCreationResult:
        self.require_family_permission(
            session,
            actor_user_id=actor_user_id,
            family_id=family_id,
            permission=Permission.CREATE_MEMORY,
        )
        if subject_user_id is not None:
            self.get_user(session, subject_user_id)
        if not 1 <= max_interview_rounds <= 20:
            raise ValidationError("max_interview_rounds must be between 1 and 20")
        expires_at = self._validate_future_expiry(consent_expires_at)
        project = MemoryProject(
            family_id=family_id,
            created_by_user_id=actor_user_id,
            subject_user_id=subject_user_id,
            title=_required_text(title, "title", maximum=200),
            subject_name=_required_text(subject_name, "subject_name", maximum=120),
            topic=_required_text(topic, "topic", maximum=240),
            description=description.strip() if isinstance(description, str) and description.strip() else None,
            status=ProjectStatus.ACTIVE,
            stage="interview",
            current_consent_version=1,
            language=_required_text(language, "language", maximum=16),
        )
        if project.description and len(project.description) > 2_000:
            raise ValidationError("description must contain at most 2000 characters")
        session.add(project)
        session.flush()

        consent = ConsentGrant(
            project_id=project.id,
            version=1,
            status=ConsentStatus.ACTIVE,
            granted_by_user_id=actor_user_id,
            granted_by_name=_required_text(granted_by_name, "granted_by_name", maximum=120),
            purpose=_required_text(consent_purpose, "consent_purpose", maximum=500),
            scopes=_clean_scopes(consent_scopes),
            expires_at=expires_at,
        )
        interview = InterviewSession(
            project_id=project.id,
            started_by_user_id=actor_user_id,
            status=InterviewStatus.PENDING,
            language=project.language,
            max_rounds=max_interview_rounds,
            context={"tags": list(dict.fromkeys(str(tag).strip() for tag in tags if str(tag).strip()))},
        )
        session.add_all([consent, interview])
        session.flush()

        if self.workflow is not None:
            thread_id = self._start_workflow(project, consent, interview)
            project.workflow_thread_id = thread_id
            interview.thread_id = thread_id
            interview.status = InterviewStatus.ACTIVE
            interview.started_at = utc_now()

        self._audit(
            session,
            event_type="project.created",
            actor_user_id=actor_user_id,
            family_id=family_id,
            project_id=project.id,
            entity_type="memory_project",
            entity_id=project.id,
            payload={"consent_version": 1, "scopes": consent.scopes},
        )
        session.flush()
        return ProjectCreationResult(project=project, consent=consent, interview_session=interview)

    def list_projects(
        self,
        session: Session,
        *,
        actor_user_id: str,
        family_id: str,
    ) -> list[MemoryProject]:
        self.require_family_permission(
            session,
            actor_user_id=actor_user_id,
            family_id=family_id,
            permission=Permission.READ_MEMORY,
        )
        return list(
            session.scalars(
                select(MemoryProject)
                .where(MemoryProject.family_id == family_id)
                .order_by(MemoryProject.updated_at.desc(), MemoryProject.id)
            )
        )

    def get_project(
        self,
        session: Session,
        *,
        actor_user_id: str,
        project_id: str,
        permission: Permission = Permission.READ_MEMORY,
    ) -> MemoryProject:
        project = session.get(MemoryProject, project_id)
        if project is None:
            raise NotFoundError("project was not found")
        self.require_family_permission(
            session,
            actor_user_id=actor_user_id,
            family_id=project.family_id,
            permission=permission,
        )
        return project

    # --------------------------------------------------------------- consent
    def validate_consent(
        self,
        session: Session,
        *,
        actor_user_id: str,
        project_id: str,
        expected_version: int,
        required_scopes: Sequence[str] = (),
        permission: Permission = Permission.READ_MEMORY,
        now: datetime | None = None,
    ) -> ConsentGrant:
        project = self.get_project(
            session,
            actor_user_id=actor_user_id,
            project_id=project_id,
            permission=permission,
        )
        if expected_version != project.current_consent_version:
            raise StaleConsentError(
                f"consent version {expected_version} is stale; current version is "
                f"{project.current_consent_version}"
            )
        consent = session.scalar(
            select(ConsentGrant).where(
                ConsentGrant.project_id == project.id,
                ConsentGrant.version == expected_version,
            )
        )
        if consent is None:
            raise StaleConsentError("current consent record is missing")
        if project.status == ProjectStatus.REVOKED or consent.status == ConsentStatus.REVOKED:
            raise ConsentRevokedError("project consent has been revoked")
        current_time = _aware_utc(now or utc_now())
        if consent.status == ConsentStatus.EXPIRED or (
            consent.expires_at is not None and _aware_utc(consent.expires_at) <= current_time
        ):
            raise ConsentExpiredError("project consent has expired")
        if consent.status != ConsentStatus.ACTIVE:
            raise ConsentError("project consent is not active")
        missing = set(_clean_scopes(required_scopes)) - set(consent.scopes or []) if required_scopes else set()
        if missing:
            raise MissingConsentScopeError(
                f"consent does not cover required scopes: {', '.join(sorted(missing))}"
            )
        return consent

    def reauthorize_project(
        self,
        session: Session,
        *,
        actor_user_id: str,
        project_id: str,
        expected_version: int,
        granted_by_name: str,
        purpose: str,
        scopes: Sequence[str],
        expires_at: datetime | None = None,
    ) -> ConsentGrant:
        project = self.get_project(
            session,
            actor_user_id=actor_user_id,
            project_id=project_id,
            permission=Permission.MANAGE_CONSENT,
        )
        if expected_version != project.current_consent_version:
            raise StaleConsentError(
                f"consent version {expected_version} is stale; current version is "
                f"{project.current_consent_version}"
            )
        previous = session.scalar(
            select(ConsentGrant).where(
                ConsentGrant.project_id == project.id,
                ConsentGrant.version == expected_version,
            )
        )
        if previous is None:
            raise StaleConsentError("current consent record is missing")
        was_revoked = project.status == ProjectStatus.REVOKED
        next_version = expected_version + 1
        now = utc_now()
        if previous.status == ConsentStatus.ACTIVE:
            previous.status = ConsentStatus.REVOKED
            previous.revoked_at = now
            previous.revoked_by_user_id = actor_user_id
            previous.revocation_reason = f"superseded by consent version {next_version}"

        consent = ConsentGrant(
            project_id=project.id,
            version=next_version,
            status=ConsentStatus.ACTIVE,
            granted_by_user_id=actor_user_id,
            granted_by_name=_required_text(granted_by_name, "granted_by_name", maximum=120),
            purpose=_required_text(purpose, "purpose", maximum=500),
            scopes=_clean_scopes(scopes),
            expires_at=self._validate_future_expiry(expires_at),
        )
        project.current_consent_version = next_version
        project.status = ProjectStatus.ACTIVE
        if project.stage == "revoked":
            project.stage = "interview"
        session.add(consent)
        try:
            with session.begin_nested():
                session.flush()
        except IntegrityError as exc:
            raise ConflictError("consent version already exists") from exc

        if was_revoked:
            interview = InterviewSession(
                project_id=project.id,
                started_by_user_id=actor_user_id,
                status=InterviewStatus.PENDING,
                language=project.language,
                max_rounds=5,
                context={"reauthorized_from_version": expected_version},
            )
            session.add(interview)
            session.flush()
            if self.workflow is not None:
                thread_id = self._start_workflow(project, consent, interview)
                project.workflow_thread_id = thread_id
                interview.thread_id = thread_id
                interview.status = InterviewStatus.ACTIVE
                interview.started_at = now

        self._audit(
            session,
            event_type="consent.reauthorized",
            actor_user_id=actor_user_id,
            family_id=project.family_id,
            project_id=project.id,
            entity_type="consent_grant",
            entity_id=consent.id,
            payload={"previous_version": expected_version, "version": next_version, "scopes": consent.scopes},
        )
        session.flush()
        return consent

    def revoke_consent(
        self,
        session: Session,
        *,
        actor_user_id: str,
        project_id: str,
        expected_version: int,
        reason: str,
    ) -> ConsentRevocationResult:
        project = self.get_project(
            session,
            actor_user_id=actor_user_id,
            project_id=project_id,
            permission=Permission.MANAGE_CONSENT,
        )
        if expected_version != project.current_consent_version:
            raise StaleConsentError(
                f"consent version {expected_version} is stale; current version is "
                f"{project.current_consent_version}"
            )
        consent = session.scalar(
            select(ConsentGrant).where(
                ConsentGrant.project_id == project.id,
                ConsentGrant.version == expected_version,
            )
        )
        if consent is None:
            raise StaleConsentError("current consent record is missing")
        reason_value = _required_text(reason, "reason", maximum=500)

        if project.status == ProjectStatus.REVOKED and consent.status == ConsentStatus.REVOKED:
            existing_receipt = session.scalar(
                select(DeletionReceipt)
                .where(
                    DeletionReceipt.project_id == project.id,
                    DeletionReceipt.consent_grant_id == consent.id,
                )
                .order_by(DeletionReceipt.requested_at.desc())
            )
            if existing_receipt is not None:
                return ConsentRevocationResult(consent, existing_receipt, 0)

        now = utc_now()
        project.status = ProjectStatus.REVOKED
        project.stage = "revoked"
        consent.status = ConsentStatus.REVOKED
        consent.revoked_at = consent.revoked_at or now
        consent.revoked_by_user_id = actor_user_id
        consent.revocation_reason = reason_value

        jobs = list(
            session.scalars(
                select(MediaJob).where(
                    MediaJob.project_id == project.id,
                    MediaJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
                )
            )
        )
        for job in jobs:
            job.status = JobStatus.CANCELLED
            job.finished_at = now
            job.error_code = "consent_revoked"
            job.error_message = "job cancelled because project consent was revoked"

        active_interviews = list(
            session.scalars(
                select(InterviewSession).where(
                    InterviewSession.project_id == project.id,
                    InterviewSession.status.in_([InterviewStatus.PENDING, InterviewStatus.ACTIVE]),
                )
            )
        )
        for interview in active_interviews:
            interview.status = InterviewStatus.CANCELLED
            interview.completed_at = now

        deliveries = list(
            session.scalars(
                select(Delivery).where(
                    Delivery.project_id == project.id,
                    Delivery.status.notin_([DeliveryStatus.REVOKED, DeliveryStatus.FAILED]),
                )
            )
        )
        for delivery in deliveries:
            delivery.status = DeliveryStatus.REVOKED

        receipt_id = new_uuid()
        receipt = DeletionReceipt(
            id=receipt_id,
            family_id=project.family_id,
            project_id=project.id,
            consent_grant_id=consent.id,
            requested_by_user_id=actor_user_id,
            status=DeletionStatus.REQUESTED,
            scope="project-content-and-derived-assets",
            reason=reason_value,
            requested_at=now,
            deleted_counts={},
            external_results={},
            receipt_hash=hashlib.sha256(
                f"{receipt_id}|{project.id}|{consent.id}|{now.isoformat()}".encode("utf-8")
            ).hexdigest(),
        )
        session.add(receipt)

        if self.workflow is not None:
            try:
                self.workflow.revoke(project.workflow_thread_id or project.id, reason_value)
            except Exception as exc:  # Revocation must remain effective even if cleanup is partial.
                receipt.external_results = {"workflow": "failed"}
                receipt.error_message = f"workflow cleanup failed ({type(exc).__name__})"
            else:
                receipt.external_results = {"workflow": "completed"}

        self._audit(
            session,
            event_type="consent.revoked",
            actor_user_id=actor_user_id,
            family_id=project.family_id,
            project_id=project.id,
            entity_type="consent_grant",
            entity_id=consent.id,
            payload={
                "version": expected_version,
                "cancelled_job_count": len(jobs),
                "deletion_receipt_id": receipt.id,
            },
        )
        session.flush()
        return ConsentRevocationResult(consent, receipt, len(jobs))

    # ---------------------------------------------------------------- assets
    def register_audio_asset(
        self,
        session: Session,
        *,
        actor_user_id: str,
        project_id: str,
        expected_consent_version: int,
        stored_object: StoredObject,
        original_filename: str,
        interview_session_id: str | None = None,
        duration_ms: int | None = None,
        properties: Mapping[str, Any] | None = None,
    ) -> AudioAsset:
        project = self.get_project(
            session,
            actor_user_id=actor_user_id,
            project_id=project_id,
            permission=Permission.EDIT_MEMORY,
        )
        self.validate_consent(
            session,
            actor_user_id=actor_user_id,
            project_id=project.id,
            expected_version=expected_consent_version,
            required_scopes=("interview", "transcription"),
            permission=Permission.EDIT_MEMORY,
        )
        filename = _required_text(original_filename, "original_filename", maximum=255)
        if filename in {".", ".."} or PurePath(filename).name != filename or "/" in filename or "\\" in filename:
            raise ValidationError("original_filename must be a basename")
        media_type = normalize_media_type(stored_object.media_type)
        if not media_type.startswith("audio/"):
            raise ValidationError("stored object must be audio")
        if duration_ms is not None and duration_ms < 0:
            raise ValidationError("duration_ms cannot be negative")
        if interview_session_id is None:
            interview = session.scalar(
                select(InterviewSession)
                .where(InterviewSession.project_id == project.id)
                .order_by(InterviewSession.created_at.desc(), InterviewSession.id.desc())
            )
        else:
            interview = session.get(InterviewSession, interview_session_id)
            if interview is None or interview.project_id != project.id:
                raise FamilyScopeError("interview session is outside this project")

        existing = session.scalar(
            select(AudioAsset).where(
                AudioAsset.project_id == project.id,
                AudioAsset.storage_key == stored_object.key,
            )
        )
        if existing is not None:
            if existing.sha256 == stored_object.sha256 and existing.byte_size == stored_object.size_bytes:
                return existing
            raise ConflictError("storage key is already registered with different content")

        asset = AudioAsset(
            project_id=project.id,
            interview_session_id=interview.id if interview is not None else None,
            uploaded_by_user_id=actor_user_id,
            storage_provider="local",
            storage_key=stored_object.key,
            original_filename=filename,
            content_type=media_type,
            byte_size=stored_object.size_bytes,
            duration_ms=duration_ms,
            sha256=stored_object.sha256,
            status=AssetStatus.READY,
            consent_version=expected_consent_version,
            properties=_safe_audit_value(dict(properties or {})),
        )
        session.add(asset)
        try:
            with session.begin_nested():
                session.flush()
        except IntegrityError as exc:
            raise ConflictError("storage key is already registered") from exc
        self._audit(
            session,
            event_type="asset.audio_registered",
            actor_user_id=actor_user_id,
            family_id=project.family_id,
            project_id=project.id,
            entity_type="audio_asset",
            entity_id=asset.id,
            payload={
                "byte_size": asset.byte_size,
                "content_type": asset.content_type,
                "consent_version": asset.consent_version,
            },
        )
        session.flush()
        return asset

    def upload_audio_asset(
        self,
        session: Session,
        *,
        actor_user_id: str,
        project_id: str,
        expected_consent_version: int,
        source: BinaryIO | Iterable[bytes],
        original_filename: str,
        content_type: str,
        interview_session_id: str | None = None,
        duration_ms: int | None = None,
        properties: Mapping[str, Any] | None = None,
    ) -> AudioAsset:
        if self.object_storage is None:
            raise StorageUnavailableError("object storage is not configured")
        project = self.get_project(
            session,
            actor_user_id=actor_user_id,
            project_id=project_id,
            permission=Permission.EDIT_MEMORY,
        )
        # Validate consent before accepting bytes. register_audio_asset validates
        # again after storage, closing the common stale-consent race window.
        self.validate_consent(
            session,
            actor_user_id=actor_user_id,
            project_id=project.id,
            expected_version=expected_consent_version,
            required_scopes=("interview", "transcription"),
            permission=Permission.EDIT_MEMORY,
        )
        key = f"families/{project.family_id}/projects/{project.id}/audio/{new_uuid()}"
        stored = self.object_storage.put_stream(key, source, content_type=content_type)
        try:
            return self.register_audio_asset(
                session,
                actor_user_id=actor_user_id,
                project_id=project.id,
                expected_consent_version=expected_consent_version,
                stored_object=stored,
                original_filename=original_filename,
                interview_session_id=interview_session_id,
                duration_ms=duration_ms,
                properties=properties,
            )
        except Exception:
            self.object_storage.delete(stored.key)
            raise

    def enqueue_transcription(
        self,
        session: Session,
        *,
        actor_user_id: str,
        project_id: str,
        asset_id: str,
        expected_consent_version: int,
        idempotency_key: str,
        provider: str = "mock",
        max_attempts: int = 3,
    ) -> MediaJob:
        project = self.get_project(
            session,
            actor_user_id=actor_user_id,
            project_id=project_id,
            permission=Permission.EDIT_MEMORY,
        )
        key = _required_text(idempotency_key, "idempotency_key", maximum=120)
        if len(key) < 8:
            raise ValidationError("idempotency_key must contain at least 8 characters")
        existing = session.scalar(
            select(MediaJob).where(
                MediaJob.project_id == project.id,
                MediaJob.idempotency_key == key,
            )
        )
        if existing is not None:
            if existing.job_type == MediaJobType.TRANSCRIPTION and existing.input_audio_asset_id == asset_id:
                return existing
            raise ConflictError("idempotency key was already used for another operation")
        if not 1 <= max_attempts <= 100:
            raise ValidationError("max_attempts must be between 1 and 100")

        self.validate_consent(
            session,
            actor_user_id=actor_user_id,
            project_id=project.id,
            expected_version=expected_consent_version,
            required_scopes=("transcription", "ai_processing"),
            permission=Permission.EDIT_MEMORY,
        )
        asset = session.get(AudioAsset, asset_id)
        if asset is None or asset.project_id != project.id:
            raise FamilyScopeError("audio asset is outside this project")
        if asset.status not in {AssetStatus.READY, AssetStatus.PROCESSING}:
            raise ConflictError("audio asset is not ready for transcription")
        if asset.consent_version != expected_consent_version:
            raise StaleConsentError("audio asset belongs to a stale consent version")

        job = MediaJob(
            project_id=project.id,
            requested_by_user_id=actor_user_id,
            input_audio_asset_id=asset.id,
            job_type=MediaJobType.TRANSCRIPTION,
            provider=_required_text(provider, "provider", maximum=80),
            status=JobStatus.QUEUED,
            consent_version=expected_consent_version,
            idempotency_key=key,
            payload={"asset_id": asset.id, "max_attempts": max_attempts},
            available_at=utc_now(),
        )
        session.add(job)
        try:
            with session.begin_nested():
                session.flush()
        except IntegrityError as exc:
            # A concurrent request may have won the unique key race. Resolve it
            # to the durable row when possible instead of duplicating work.
            winner = session.scalar(
                select(MediaJob).where(
                    MediaJob.project_id == project.id,
                    MediaJob.idempotency_key == key,
                )
            )
            if winner is not None and winner.input_audio_asset_id == asset.id:
                return winner
            raise ConflictError("idempotency key was already used") from exc
        asset.status = AssetStatus.PROCESSING
        project.stage = "transcribing"
        self._audit(
            session,
            event_type="job.transcription_queued",
            actor_user_id=actor_user_id,
            family_id=project.family_id,
            project_id=project.id,
            entity_type="media_job",
            entity_id=job.id,
            payload={"asset_id": asset.id, "consent_version": expected_consent_version},
        )
        session.flush()
        return job

    def list_media_jobs(
        self, session: Session, *, actor_user_id: str, project_id: str
    ) -> list[MediaJob]:
        project = self.get_project(
            session,
            actor_user_id=actor_user_id,
            project_id=project_id,
            permission=Permission.READ_MEMORY,
        )
        return list(
            session.scalars(
                select(MediaJob)
                .where(MediaJob.project_id == project.id)
                .order_by(MediaJob.created_at.desc(), MediaJob.id)
            )
        )

    # ---------------------------------------------------------- data deletion
    def cleanup_revoked_project_assets(
        self,
        session: Session,
        *,
        actor_user_id: str,
        project_id: str,
        receipt_id: str | None = None,
    ) -> DeletionReceipt:
        if self.object_storage is None:
            raise StorageUnavailableError("object storage is not configured")
        project = self.get_project(
            session,
            actor_user_id=actor_user_id,
            project_id=project_id,
            permission=Permission.MANAGE_CONSENT,
        )
        if project.status != ProjectStatus.REVOKED:
            raise ConflictError("project must be revoked before physical cleanup")
        if receipt_id is None:
            receipt = session.scalar(
                select(DeletionReceipt)
                .where(DeletionReceipt.project_id == project.id)
                .order_by(DeletionReceipt.requested_at.desc())
            )
        else:
            receipt = session.get(DeletionReceipt, receipt_id)
            if receipt is not None and receipt.project_id != project.id:
                raise FamilyScopeError("deletion receipt is outside this project")
        if receipt is None:
            raise NotFoundError("deletion receipt was not found")

        assets = list(session.scalars(select(AudioAsset).where(AudioAsset.project_id == project.id)))
        deliveries = list(session.scalars(select(Delivery).where(Delivery.project_id == project.id)))
        deleted = 0
        already_missing = 0
        failures = 0
        seen_keys: set[str] = set()
        for key in [asset.storage_key for asset in assets] + [
            delivery.storage_key for delivery in deliveries if delivery.storage_key
        ]:
            if key in seen_keys:
                continue
            seen_keys.add(key)
            try:
                existed = self.object_storage.delete(key)
            except Exception:
                failures += 1
            else:
                if existed:
                    deleted += 1
                else:
                    already_missing += 1

        for asset in assets:
            asset.status = AssetStatus.DELETED
        for delivery in deliveries:
            delivery.status = DeliveryStatus.REVOKED
        receipt.deleted_counts = {
            "objects_deleted": deleted,
            "objects_already_missing": already_missing,
            "audio_assets_marked_deleted": len(assets),
            "deliveries_revoked": len(deliveries),
            "object_failures": failures,
        }
        receipt.status = DeletionStatus.COMPLETED if failures == 0 else DeletionStatus.PARTIAL
        receipt.completed_at = utc_now()
        if failures:
            receipt.error_message = f"{failures} object(s) could not be deleted"
        self._audit(
            session,
            event_type="project.assets_cleanup_completed",
            actor_user_id=actor_user_id,
            family_id=project.family_id,
            project_id=project.id,
            entity_type="deletion_receipt",
            entity_id=receipt.id,
            payload=receipt.deleted_counts,
        )
        session.flush()
        return receipt

    # -------------------------------------------------------------- internals
    @staticmethod
    def _validate_future_expiry(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        normalized = _aware_utc(value)
        if normalized <= utc_now():
            raise ValidationError("consent expiry must be in the future")
        return normalized

    def _start_workflow(
        self,
        project: MemoryProject,
        consent: ConsentGrant,
        interview: InterviewSession,
    ) -> str:
        assert self.workflow is not None
        payload: dict[str, Any]
        thread_id = project.id
        store = getattr(self.workflow, "store", None)
        create_legacy_project = getattr(store, "create_project", None)
        try:
            if callable(create_legacy_project):
                payload = create_legacy_project(
                    subject_name=project.subject_name,
                    topic=project.topic,
                    consent_by=consent.granted_by_name,
                    family_id=project.family_id,
                    actor_id=project.created_by_user_id,
                    max_rounds=interview.max_rounds,
                )
                thread_id = str(payload["id"])
            else:
                payload = {
                    "id": project.id,
                    "family_id": project.family_id,
                    "actor_id": project.created_by_user_id,
                    "subject_name": project.subject_name,
                    "topic": project.topic,
                    "consent_version": consent.version,
                    "max_rounds": interview.max_rounds,
                }
            result = self.workflow.start(payload)
            if not callable(create_legacy_project) and isinstance(result, Mapping):
                candidate = result.get("id")
                if candidate is None and isinstance(result.get("project"), Mapping):
                    candidate = result["project"].get("id")
                if candidate:
                    thread_id = str(candidate)
        except Exception as exc:
            raise WorkflowBridgeError(f"workflow could not start ({type(exc).__name__})") from exc
        return thread_id

    def record_audit_event(
        self,
        session: Session,
        *,
        event_type: str,
        actor_user_id: str | None = None,
        family_id: str | None = None,
        project_id: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        request_id: str | None = None,
        ip_address: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> AuditEvent:
        event = self._audit(
            session,
            event_type=event_type,
            actor_user_id=actor_user_id,
            family_id=family_id,
            project_id=project_id,
            entity_type=entity_type,
            entity_id=entity_id,
            request_id=request_id,
            ip_address=ip_address,
            payload=payload,
        )
        session.flush()
        return event

    @staticmethod
    def _audit(
        session: Session,
        *,
        event_type: str,
        actor_user_id: str | None = None,
        family_id: str | None = None,
        project_id: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        request_id: str | None = None,
        ip_address: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            family_id=family_id,
            project_id=project_id,
            actor_user_id=actor_user_id,
            event_type=_required_text(event_type, "event_type", maximum=160),
            entity_type=entity_type,
            entity_id=entity_id,
            request_id=request_id,
            ip_address=ip_address,
            payload=_safe_audit_value(dict(payload or {})),
        )
        session.add(event)
        return event


__all__ = [
    "AccessDeniedError",
    "AuthenticationFailedError",
    "ConflictError",
    "ConsentError",
    "ConsentExpiredError",
    "ConsentRevocationResult",
    "ConsentRevokedError",
    "DEFAULT_CONSENT_SCOPES",
    "FamilyScopeError",
    "MissingConsentScopeError",
    "NotFoundError",
    "ProductService",
    "ProductServiceError",
    "ProjectCreationResult",
    "PublicUser",
    "StaleConsentError",
    "StorageUnavailableError",
    "ValidationError",
    "WorkflowBridgeError",
]
