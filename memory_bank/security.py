from __future__ import annotations

"""Low-level password and access-token primitives.

This module deliberately has no dependency on the application's database or
FastAPI.  Keeping the primitives framework-free makes them straightforward to
test and reuse from workers and command-line maintenance jobs.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping
import uuid


DEFAULT_ISSUER = "memory-bank"
DEFAULT_AUDIENCE = "memory-bank-api"
DEFAULT_ACCESS_TOKEN_TTL = timedelta(minutes=15)
JWT_ALGORITHM = "HS256"
_MINIMUM_HS256_SECRET_BYTES = 32
_RESERVED_CLAIMS = frozenset({"sub", "exp", "iat", "nbf", "iss", "aud", "jti", "token_type"})


class SecurityConfigurationError(RuntimeError):
    """Raised when a required security package or safe configuration is absent."""


class AuthenticationError(ValueError):
    """Base class for access-token validation failures."""


class InvalidTokenError(AuthenticationError):
    """The token is malformed, has invalid claims, or failed verification."""


class TokenExpiredError(InvalidTokenError):
    """The access token is validly signed but expired."""


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    subject: str
    token_id: str
    issued_at: datetime
    expires_at: datetime
    issuer: str
    audience: str
    raw: Mapping[str, Any]


def _password_backend() -> tuple[str, Any]:
    """Load the best supported Argon2 backend lazily.

    ``pwdlib[argon2]`` is preferred for new deployments.  ``argon2-cffi`` is a
    compatible fallback for the existing prototype environment; there is no
    downgrade to a fast general-purpose hash.
    """

    try:
        from pwdlib import PasswordHash

        return "pwdlib", PasswordHash.recommended()
    except ImportError:
        try:
            from argon2 import PasswordHasher
        except ImportError as exc:  # pragma: no cover - exercised in packaging, not unit tests
            raise SecurityConfigurationError(
                "Password hashing requires 'pwdlib[argon2]' (preferred) or 'argon2-cffi'."
            ) from exc
        return "argon2-cffi", PasswordHasher(
            time_cost=3,
            memory_cost=65536,
            parallelism=4,
            hash_len=32,
            salt_len=16,
        )


def hash_password(password: str) -> str:
    """Hash a password with Argon2id.

    Passwords are limited to a generous size to prevent accidental resource
    abuse before the intentionally expensive hash operation.
    """

    if not isinstance(password, str) or not password:
        raise ValueError("Password must be a non-empty string.")
    if len(password.encode("utf-8")) > 1024:
        raise ValueError("Password is too long.")

    _backend_name, backend = _password_backend()
    return backend.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Safely verify a password, returning ``False`` for invalid hash input."""

    if not isinstance(password, str) or not isinstance(password_hash, str):
        return False
    if not password or not password_hash or len(password.encode("utf-8")) > 1024:
        return False

    backend_name, backend = _password_backend()
    try:
        if backend_name == "pwdlib":
            return bool(backend.verify(password, password_hash))
        return bool(backend.verify(password_hash, password))
    except Exception as exc:
        if backend_name == "argon2-cffi":
            try:
                from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
            except ImportError:  # pragma: no cover
                return False
            if isinstance(exc, (InvalidHashError, VerificationError, VerifyMismatchError)):
                return False
        else:
            # pwdlib normalizes unsupported or malformed hashes to verification
            # failures.  Avoid leaking parsing detail into login responses.
            if isinstance(exc, (ValueError, TypeError)):
                return False
        raise


def password_needs_rehash(password_hash: str) -> bool:
    """Return whether a valid password hash should be upgraded after login."""

    if not isinstance(password_hash, str) or not password_hash:
        return False
    backend_name, backend = _password_backend()
    try:
        if backend_name == "pwdlib":
            return bool(backend.hash_needs_update(password_hash))
        return bool(backend.check_needs_rehash(password_hash))
    except (ValueError, TypeError):
        return False


def _jwt_package() -> Any:
    try:
        import jwt
    except ImportError as exc:  # pragma: no cover - exercised in packaging, not unit tests
        raise SecurityConfigurationError("JWT support requires 'PyJWT>=2.8'.") from exc
    return jwt


def _secret_bytes(secret: str | bytes) -> bytes:
    if isinstance(secret, str):
        value = secret.encode("utf-8")
    elif isinstance(secret, bytes):
        value = secret
    else:
        raise TypeError("JWT secret must be str or bytes.")
    if len(value) < _MINIMUM_HS256_SECRET_BYTES:
        raise SecurityConfigurationError("HS256 JWT secret must contain at least 32 bytes.")
    return value


def _utc(value: datetime | None = None) -> datetime:
    result = value or datetime.now(UTC)
    if result.tzinfo is None:
        raise ValueError("Datetime values must be timezone-aware.")
    return result.astimezone(UTC)


def create_access_token(
    subject: str,
    secret: str | bytes,
    *,
    expires_in: timedelta = DEFAULT_ACCESS_TOKEN_TTL,
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
    now: datetime | None = None,
    token_id: str | None = None,
    extra_claims: Mapping[str, Any] | None = None,
) -> str:
    """Create a signed, short-lived JWT access token."""

    if not isinstance(subject, str) or not subject.strip():
        raise ValueError("Token subject must be a non-empty string.")
    if not isinstance(expires_in, timedelta) or expires_in.total_seconds() <= 0:
        raise ValueError("Access-token lifetime must be positive.")
    if not issuer or not audience:
        raise ValueError("JWT issuer and audience are required.")

    extras = dict(extra_claims or {})
    collision = _RESERVED_CLAIMS.intersection(extras)
    if collision:
        raise ValueError(f"extra_claims cannot override reserved claims: {', '.join(sorted(collision))}")

    issued_at = _utc(now)
    payload: dict[str, Any] = {
        **extras,
        "sub": subject.strip(),
        "iat": issued_at,
        "nbf": issued_at,
        "exp": issued_at + expires_in,
        "iss": issuer,
        "aud": audience,
        "jti": token_id or uuid.uuid4().hex,
        "token_type": "access",
    }
    jwt = _jwt_package()
    return jwt.encode(payload, _secret_bytes(secret), algorithm=JWT_ALGORITHM, headers={"typ": "JWT"})


def decode_access_token(
    token: str,
    secret: str | bytes,
    *,
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
    leeway_seconds: int = 0,
) -> dict[str, Any]:
    """Validate and decode an access JWT.

    Only HS256 is accepted, and all security-relevant registered claims are
    required.  PyJWT performs signature, time, issuer, and audience checks.
    """

    if not isinstance(token, str) or not token:
        raise InvalidTokenError("Access token is missing.")
    if leeway_seconds < 0:
        raise ValueError("JWT leeway cannot be negative.")

    jwt = _jwt_package()
    try:
        payload = jwt.decode(
            token,
            _secret_bytes(secret),
            algorithms=[JWT_ALGORITHM],
            issuer=issuer,
            audience=audience,
            leeway=leeway_seconds,
            options={
                "require": ["sub", "iat", "nbf", "exp", "iss", "aud", "jti", "token_type"],
                "verify_signature": True,
                "verify_exp": True,
                "verify_nbf": True,
                "verify_iat": True,
                "verify_iss": True,
                "verify_aud": True,
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError("Access token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidTokenError("Access token is invalid.") from exc

    if payload.get("token_type") != "access":
        raise InvalidTokenError("Token is not an access token.")
    if not isinstance(payload.get("sub"), str) or not payload["sub"].strip():
        raise InvalidTokenError("Token subject is invalid.")
    if not isinstance(payload.get("jti"), str) or not payload["jti"]:
        raise InvalidTokenError("Token identifier is invalid.")
    return payload


def parse_access_token(
    token: str,
    secret: str | bytes,
    *,
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
    leeway_seconds: int = 0,
) -> AccessTokenClaims:
    """Validate a token and return its commonly used claims as typed data."""

    payload = decode_access_token(
        token,
        secret,
        issuer=issuer,
        audience=audience,
        leeway_seconds=leeway_seconds,
    )
    try:
        issued_at = datetime.fromtimestamp(float(payload["iat"]), tz=UTC)
        expires_at = datetime.fromtimestamp(float(payload["exp"]), tz=UTC)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise InvalidTokenError("Token timestamps are invalid.") from exc
    return AccessTokenClaims(
        subject=payload["sub"],
        token_id=payload["jti"],
        issued_at=issued_at,
        expires_at=expires_at,
        issuer=payload["iss"],
        audience=payload["aud"],
        raw=payload,
    )
