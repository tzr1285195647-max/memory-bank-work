from __future__ import annotations

"""Authentication dependencies and family-scoped role authorization."""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
import inspect
from typing import Any, Awaitable, Callable, Mapping, TypeAlias

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .security import (
    DEFAULT_AUDIENCE,
    DEFAULT_ISSUER,
    AuthenticationError,
    parse_access_token,
)


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"
    APPROVER = "approver"


class Permission(StrEnum):
    READ_MEMORY = "memory:read"
    CREATE_MEMORY = "memory:create"
    EDIT_MEMORY = "memory:edit"
    APPROVE_MEMORY = "memory:approve"
    MANAGE_MEMBERS = "family:manage_members"
    MANAGE_CONSENT = "family:manage_consent"
    DELETE_FAMILY = "family:delete"


ROLE_PERMISSIONS: Mapping[Role, frozenset[Permission]] = {
    Role.OWNER: frozenset(Permission),
    Role.ADMIN: frozenset(
        permission for permission in Permission if permission is not Permission.DELETE_FAMILY
    ),
    Role.EDITOR: frozenset(
        {Permission.READ_MEMORY, Permission.CREATE_MEMORY, Permission.EDIT_MEMORY}
    ),
    Role.VIEWER: frozenset({Permission.READ_MEMORY}),
    Role.APPROVER: frozenset({Permission.READ_MEMORY, Permission.APPROVE_MEMORY}),
}


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    email: str | None = None
    display_name: str | None = None
    is_active: bool = True
    token_id: str | None = None
    token_expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class FamilyMembership:
    user_id: str
    family_id: str
    role: Role
    is_active: bool = True


@dataclass(frozen=True, slots=True)
class FamilyAccess:
    principal: Principal
    membership: FamilyMembership
    permission: Permission


PrincipalRecord: TypeAlias = Principal | Mapping[str, Any] | None
MembershipRecord: TypeAlias = FamilyMembership | Role | str | Mapping[str, Any] | None
PrincipalLoader: TypeAlias = Callable[[str], PrincipalRecord | Awaitable[PrincipalRecord]]
MembershipLoader: TypeAlias = Callable[[str, str], MembershipRecord | Awaitable[MembershipRecord]]


def role_has_permission(role: Role | str, permission: Permission | str) -> bool:
    """Pure role-matrix check; invalid roles and permissions are denied."""

    try:
        normalized_role = Role(role)
        normalized_permission = Permission(permission)
    except (TypeError, ValueError):
        return False
    return normalized_permission in ROLE_PERMISSIONS[normalized_role]


def membership_has_permission(
    principal: Principal,
    membership: FamilyMembership | None,
    family_id: str,
    permission: Permission | str,
) -> bool:
    """Check identity, family boundary, active state, and role permission."""

    return bool(
        principal.is_active
        and membership is not None
        and membership.is_active
        and membership.user_id == principal.user_id
        and membership.family_id == family_id
        and role_has_permission(membership.role, permission)
    )


async def _resolve(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _principal_from_record(record: PrincipalRecord, expected_user_id: str) -> Principal | None:
    if record is None:
        return None
    if isinstance(record, Principal):
        return record if record.user_id == expected_user_id else None
    if not isinstance(record, Mapping):
        raise TypeError("Principal loader must return Principal, mapping, or None.")
    user_id = record.get("user_id", record.get("id"))
    if not isinstance(user_id, str) or user_id != expected_user_id:
        return None
    return Principal(
        user_id=user_id,
        email=record.get("email"),
        display_name=record.get("display_name", record.get("name")),
        is_active=bool(record.get("is_active", record.get("active", True))),
    )


def _membership_from_record(
    record: MembershipRecord,
    *,
    user_id: str,
    family_id: str,
) -> FamilyMembership | None:
    if record is None:
        return None
    if isinstance(record, FamilyMembership):
        return record
    if isinstance(record, (Role, str)):
        try:
            role = Role(record)
        except ValueError:
            return None
        return FamilyMembership(user_id=user_id, family_id=family_id, role=role)
    if not isinstance(record, Mapping):
        raise TypeError("Membership loader must return FamilyMembership, role, mapping, or None.")
    try:
        role = Role(record.get("role"))
    except (TypeError, ValueError):
        return None
    return FamilyMembership(
        user_id=str(record.get("user_id", user_id)),
        family_id=str(record.get("family_id", family_id)),
        role=role,
        is_active=bool(record.get("is_active", record.get("active", True))),
    )


def create_bearer_dependency(
    *,
    secret: str | bytes,
    principal_loader: PrincipalLoader,
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
    leeway_seconds: int = 0,
) -> Callable[..., Awaitable[Principal]]:
    """Build a FastAPI bearer dependency around an injected user lookup.

    ``principal_loader`` receives the token subject and may be synchronous or
    asynchronous.  This is the only persistence hook, so the authentication
    layer remains independent from SQLite, SQLAlchemy, or any specific schema.
    """

    bearer = HTTPBearer(auto_error=False)

    async def current_principal(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    ) -> Principal:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise _unauthorized("Bearer access token is required.")
        try:
            claims = parse_access_token(
                credentials.credentials,
                secret,
                issuer=issuer,
                audience=audience,
                leeway_seconds=leeway_seconds,
            )
        except AuthenticationError as exc:
            raise _unauthorized(str(exc)) from exc

        record = await _resolve(principal_loader(claims.subject))
        principal = _principal_from_record(record, claims.subject)
        if principal is None or not principal.is_active:
            raise _unauthorized("User does not exist or is inactive.")
        return replace(
            principal,
            token_id=claims.token_id,
            token_expires_at=claims.expires_at,
        )

    return current_principal


# Readable alias for projects that prefer the ``make_*`` naming convention.
make_bearer_dependency = create_bearer_dependency


async def require_family_permission(
    *,
    principal: Principal,
    family_id: str,
    permission: Permission | str,
    membership_loader: MembershipLoader,
) -> FamilyAccess:
    """Resolve membership through a callback and enforce a family permission."""

    try:
        normalized_permission = Permission(permission)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Unknown permission: {permission!r}") from exc

    record = await _resolve(membership_loader(principal.user_id, family_id))
    membership = _membership_from_record(record, user_id=principal.user_id, family_id=family_id)
    if not membership_has_permission(principal, membership, family_id, normalized_permission):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to perform this action in this family.",
        )
    return FamilyAccess(principal=principal, membership=membership, permission=normalized_permission)


def create_family_permission_dependency(
    *,
    permission: Permission | str,
    principal_dependency: Callable[..., Awaitable[Principal]],
    membership_loader: MembershipLoader,
    family_id_parameter: str = "family_id",
) -> Callable[..., Awaitable[FamilyAccess]]:
    """Build a route dependency that reads the family id from path parameters."""

    try:
        normalized_permission = Permission(permission)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Unknown permission: {permission!r}") from exc
    if not family_id_parameter:
        raise ValueError("family_id_parameter cannot be empty.")

    async def family_access(
        request: Request,
        principal: Principal = Depends(principal_dependency),
    ) -> FamilyAccess:
        family_id = request.path_params.get(family_id_parameter)
        if not family_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Missing path parameter: {family_id_parameter}.",
            )
        return await require_family_permission(
            principal=principal,
            family_id=str(family_id),
            permission=normalized_permission,
            membership_loader=membership_loader,
        )

    return family_access


make_family_permission_dependency = create_family_permission_dependency


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )
