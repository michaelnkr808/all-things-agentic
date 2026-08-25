"""User authentication for the SSE backend (provisional — Malik).

PROVISIONAL CODE: auth surface only; the rest of the team should treat
schemas/behaviour here as stable-ish but negotiable — ping me before
building hard dependencies on it.

Design:
- config/users.yaml maps username -> {role, password_hash}. Roles must
  exist in config/permissions.yaml 'roles'; login fails closed otherwise.
- Passwords are PBKDF2-HMAC-SHA256 (stdlib), stored as
  ``pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>``. No new crypto deps.
- Tokens are compact JWTs (HS256, pyjwt) carrying {sub, role, exp}.
  Stateless by design — no server-side session store, matching the
  project's Cloud Run-ready statelessness invariant.

The authenticated role is what threads through plan_and_gather as
``requester_role``; it never comes from LLM output or request bodies.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

import jwt
import yaml
from pydantic import BaseModel, ValidationError

from agentic.contracts.config import ConfigError

DEFAULT_USERS_CONFIG = "config/users.yaml"
DEFAULT_TTL_HOURS = 12.0

_PBKDF2_ITERATIONS = 390_000
_HASH_NAME = "pbkdf2_sha256"
_SECRET_ENV_VAR = "AGENTIC_JWT_SECRET"
_TTL_HOURS_ENV_VAR = "AGENTIC_JWT_TTL_HOURS"


class UserEntry(BaseModel):
    """One user in config/users.yaml."""

    role: str
    password_hash: str


class UsersFile(BaseModel):
    users: dict[str, UserEntry]


class TokenPrincipal(BaseModel):
    """The identity a verified token asserts. Feeds requester_role only."""

    username: str
    role: str


class InvalidCredentials(Exception):
    """Username unknown or password mismatch."""


class UnknownRole(Exception):
    """The user's role is not defined in the permissions config."""


class InvalidToken(Exception):
    """Token missing, malformed, expired, or signed by someone else."""


def load_users(path: str | Path | None = None) -> UsersFile:
    """Parse and validate config/users.yaml.

    Fails closed (ConfigError) when missing, invalid YAML, or bad schema —
    a broken user store must never authenticate anyone.
    """
    if path is None:
        path = DEFAULT_USERS_CONFIG
    try:
        with open(path, "r") as f:
            raw = yaml.safe_load(f)
    except FileNotFoundError:
        raise ConfigError(
            f"Users config not found at: {path} "
            f"(copy users.example.yaml and generate hashes via "
            f"scripts/hash_password.py)"
        ) from None
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {path}: {e}") from e

    try:
        return UsersFile.model_validate(raw)
    except ValidationError as e:
        lines = [
            f"{' -> '.join(str(p) for p in err['loc'])}: {err['msg']}"
            for err in e.errors()
        ]
        raise ConfigError(f"Invalid users config in {path}:\n" + "\n".join(lines)) from e


def hash_password(password: str) -> str:
    """Encode a password for pasting into config/users.yaml."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), _PBKDF2_ITERATIONS
    )
    return f"{_HASH_NAME}${_PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time check of `password` against an encoded hash.

    Malformed stored hashes fail closed (False), never raise.
    """
    try:
        name, iterations, salt, hex_digest = encoded.split("$")
        if name != _HASH_NAME:
            return False
        expected = bytes.fromhex(hex_digest)
    except (ValueError, TypeError):
        return False

    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), int(iterations)
    )
    return hmac.compare_digest(candidate, expected)


def load_secret() -> str:
    """Read AGENTIC_JWT_SECRET from the environment.

    Required — no silent default secret. A missing secret fails closed so
    nobody accidentally runs a publicly-trust-anything server.
    """
    secret = os.environ.get(_SECRET_ENV_VAR, "")
    if not secret:
        raise ConfigError(
            f"{_SECRET_ENV_VAR} is not set — refusing to issue or accept "
            f"tokens with a default secret."
        )
    return secret


def ttl_hours() -> float:
    raw = os.environ.get(_TTL_HOURS_ENV_VAR, "")
    try:
        return float(raw) if raw else DEFAULT_TTL_HOURS
    except ValueError:
        return DEFAULT_TTL_HOURS


def issue_token(principal: TokenPrincipal, secret: str, expires_hours: float | None = None) -> str:
    """Mint a signed token for an already-authenticated principal."""
    hours = ttl_hours() if expires_hours is None else expires_hours
    now = int(time.time())
    payload = {
        "sub": principal.username,
        "role": principal.role,
        "iat": now,
        "exp": now + int(hours * 3600),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def verify_token(token: str, secret: str) -> TokenPrincipal:
    """Verify signature + expiry; raises InvalidToken on any failure."""
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise InvalidToken(f"{type(e).__name__}") from None

    username = payload.get("sub")
    role = payload.get("role")
    if not isinstance(username, str) or not isinstance(role, str) or not username or not role:
        raise InvalidToken("token claims missing or malformed")
    return TokenPrincipal(username=username, role=role)


def authenticate(
    users_cfg: UsersFile,
    username: str,
    password: str,
    known_roles: set[str] | None = None,
) -> TokenPrincipal:
    """Check credentials and (optionally) that the user's role is grantable.

    `known_roles` should be the permissions config's defined roles; passing
    it makes an unconfigured role a hard login failure instead of a later
    surprise at the gather gate. Both failure modes are deliberately
    indistinguishable to the caller's client (map both to 401/403 upstream).
    """
    user = users_cfg.users.get(username)
    if user is None or not verify_password(password, user.password_hash):
        raise InvalidCredentials()
    if known_roles is not None and user.role not in known_roles:
        raise UnknownRole(f"role {user.role!r} is not defined in the permissions config")
    return TokenPrincipal(username=username, role=user.role)
