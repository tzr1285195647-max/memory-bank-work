from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from memory_bank.auth import (
    FamilyMembership,
    Permission,
    Principal,
    Role,
    create_bearer_dependency,
    create_family_permission_dependency,
    membership_has_permission,
    role_has_permission,
)
from memory_bank.security import (
    InvalidTokenError,
    TokenExpiredError,
    create_access_token,
    decode_access_token,
    hash_password,
    parse_access_token,
    verify_password,
)


SECRET = "test-only-secret-with-at-least-32-bytes"


def test_password_hash_and_verification_use_argon2():
    encoded = hash_password("correct horse battery staple")

    assert encoded.startswith("$argon2")
    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("wrong", encoded)
    assert not verify_password("correct horse battery staple", "not-a-password-hash")


def test_access_token_round_trip_and_reserved_claim_protection():
    token = create_access_token(
        "user-123",
        SECRET,
        expires_in=timedelta(minutes=5),
        token_id="token-123",
        extra_claims={"session_version": 4},
    )

    payload = decode_access_token(token, SECRET)
    claims = parse_access_token(token, SECRET)
    assert payload["sub"] == "user-123"
    assert payload["session_version"] == 4
    assert claims.subject == "user-123"
    assert claims.token_id == "token-123"
    assert claims.expires_at > claims.issued_at

    with pytest.raises(ValueError):
        create_access_token("user-123", SECRET, extra_claims={"sub": "attacker"})


def test_expired_access_token_is_rejected():
    token = create_access_token(
        "user-123",
        SECRET,
        expires_in=timedelta(seconds=1),
        now=datetime.now(UTC) - timedelta(minutes=2),
    )

    with pytest.raises(TokenExpiredError):
        decode_access_token(token, SECRET)


def test_tampered_access_token_is_rejected():
    token = create_access_token("user-123", SECRET)
    header, payload, signature = token.split(".")
    replacement = "A" if payload[-1] != "A" else "B"
    tampered = f"{header}.{payload[:-1]}{replacement}.{signature}"

    with pytest.raises(InvalidTokenError):
        decode_access_token(tampered, SECRET)


@pytest.mark.parametrize(
    ("role", "allowed", "denied"),
    [
        (Role.OWNER, Permission.DELETE_FAMILY, None),
        (Role.ADMIN, Permission.MANAGE_MEMBERS, Permission.DELETE_FAMILY),
        (Role.EDITOR, Permission.EDIT_MEMORY, Permission.APPROVE_MEMORY),
        (Role.VIEWER, Permission.READ_MEMORY, Permission.EDIT_MEMORY),
        (Role.APPROVER, Permission.APPROVE_MEMORY, Permission.EDIT_MEMORY),
    ],
)
def test_role_permission_matrix(role: Role, allowed: Permission, denied: Permission | None):
    assert role_has_permission(role, allowed)
    if denied is not None:
        assert not role_has_permission(role, denied)


def test_membership_check_prevents_cross_family_access():
    principal = Principal(user_id="user-1")
    membership = FamilyMembership(user_id="user-1", family_id="family-1", role=Role.EDITOR)

    assert membership_has_permission(principal, membership, "family-1", Permission.EDIT_MEMORY)
    assert not membership_has_permission(principal, membership, "family-2", Permission.EDIT_MEMORY)
    assert not membership_has_permission(
        Principal(user_id="user-2"), membership, "family-1", Permission.EDIT_MEMORY
    )


def test_fastapi_bearer_and_family_dependencies_use_lookup_callbacks():
    principal_queries: list[str] = []
    membership_queries: list[tuple[str, str]] = []

    async def load_principal(user_id: str):
        principal_queries.append(user_id)
        return {"id": user_id, "email": "person@example.test", "is_active": True}

    def load_membership(user_id: str, family_id: str):
        membership_queries.append((user_id, family_id))
        return "editor" if family_id == "family-1" else None

    current_principal = create_bearer_dependency(secret=SECRET, principal_loader=load_principal)
    edit_family = create_family_permission_dependency(
        permission=Permission.EDIT_MEMORY,
        principal_dependency=current_principal,
        membership_loader=load_membership,
    )
    approve_family = create_family_permission_dependency(
        permission=Permission.APPROVE_MEMORY,
        principal_dependency=current_principal,
        membership_loader=load_membership,
    )

    app = FastAPI()

    @app.get("/families/{family_id}/edit")
    def edit(access=Depends(edit_family)):
        return {"user_id": access.principal.user_id, "role": access.membership.role}

    @app.get("/families/{family_id}/approve")
    def approve(_access=Depends(approve_family)):
        return {"ok": True}

    client = TestClient(app)
    token = create_access_token("user-1", SECRET)
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/families/family-1/edit").status_code == 401
    allowed = client.get("/families/family-1/edit", headers=headers)
    assert allowed.status_code == 200
    assert allowed.json() == {"user_id": "user-1", "role": "editor"}
    assert client.get("/families/family-1/approve", headers=headers).status_code == 403
    assert client.get("/families/family-2/edit", headers=headers).status_code == 403
    assert principal_queries == ["user-1", "user-1", "user-1"]
    assert membership_queries == [
        ("user-1", "family-1"),
        ("user-1", "family-1"),
        ("user-1", "family-2"),
    ]


def test_bearer_dependency_rejects_expired_and_tampered_tokens():
    def load_principal(user_id: str):
        return Principal(user_id=user_id)

    current_principal = create_bearer_dependency(secret=SECRET, principal_loader=load_principal)
    app = FastAPI()

    @app.get("/me")
    def me(principal: Principal = Depends(current_principal)):
        return {"user_id": principal.user_id}

    client = TestClient(app)
    expired = create_access_token(
        "user-1",
        SECRET,
        expires_in=timedelta(seconds=1),
        now=datetime.now(UTC) - timedelta(minutes=1),
    )
    assert client.get("/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401

    valid = create_access_token("user-1", SECRET)
    bad_signature = valid.rsplit(".", 1)[0] + "." + ("A" * len(valid.rsplit(".", 1)[1]))
    response = client.get("/me", headers={"Authorization": f"Bearer {bad_signature}"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
