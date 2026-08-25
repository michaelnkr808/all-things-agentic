"""Auth module tests (provisional — Malik).

No network, no JWT server — pure function coverage: hashing, the user
store's fail-closed loading, token issue/verify, and authenticate()'s
role gating. Every failure mode must fail closed.
"""

import time

import pytest

from agentic.contracts.config import ConfigError
from agentic.server import auth


@pytest.fixture
def users_file(tmp_path):
    p = tmp_path / "users.yaml"
    p.write_text(
        "users:\n"
        "  alice:\n"
        "    role: analyst\n"
        f"    password_hash: {auth.hash_password('wonderland')}\n"
        "  root:\n"
        "    role: admin\n"
        f"    password_hash: {auth.hash_password('toor')}\n"
    )
    return auth.load_users(p)


# ---- password hashing ----

def test_hash_and_verify_roundtrip():
    encoded = auth.hash_password("hunter2")
    assert encoded.startswith("pbkdf2_sha256$")
    assert auth.verify_password("hunter2", encoded) is True


def test_wrong_password_fails():
    assert auth.verify_password("hunter3", auth.hash_password("hunter2")) is False


def test_hashes_are_salted():
    assert auth.hash_password("same") != auth.hash_password("same")


def test_malformed_stored_hash_fails_closed():
    assert auth.verify_password("x", "not-a-hash") is False
    assert auth.verify_password("x", "") is False
    assert auth.verify_password("x", "scrypt$1$salt$abcd") is False
    assert auth.verify_password("x", "pbkdf2_sha256$abc$salt$zzzz") is False


# ---- user store ----

def test_load_users(users_file):
    assert set(users_file.users) == {"alice", "root"}
    assert users_file.users["alice"].role == "analyst"


def test_load_users_missing_raises(tmp_path):
    with pytest.raises(ConfigError):
        auth.load_users(tmp_path / "nope.yaml")


def test_load_users_invalid_yaml_raises(tmp_path):
    p = tmp_path / "users.yaml"
    p.write_text("users: [unclosed")
    with pytest.raises(ConfigError, match="Invalid YAML"):
        auth.load_users(p)


def test_load_users_bad_schema_raises(tmp_path):
    p = tmp_path / "users.yaml"
    p.write_text("users:\n  alice:\n    role: analyst\n")  # password_hash missing
    with pytest.raises(ConfigError, match="password_hash"):
        auth.load_users(p)


# ---- tokens ----

SECRET = "test-secret"


def _principal(role="analyst", username="alice"):
    return auth.TokenPrincipal(username=username, role=role)


def test_token_roundtrip():
    token = auth.issue_token(_principal(), SECRET)
    assert auth.verify_token(token, SECRET) == _principal()


def test_expired_token_rejected():
    token = auth.issue_token(_principal(), SECRET, expires_hours=-0.001)
    with pytest.raises(auth.InvalidToken):
        auth.verify_token(token, SECRET)


def test_wrong_secret_rejected():
    token = auth.issue_token(_principal(), SECRET)
    with pytest.raises(auth.InvalidToken):
        auth.verify_token(token, "other-secret")


def test_garbage_token_rejected():
    with pytest.raises(auth.InvalidToken):
        auth.verify_token("garbage.token.here", SECRET)


def test_token_missing_claims_rejected():
    import jwt as pyjwt

    bare = pyjwt.encode({"sub": "alice"}, SECRET, algorithm="HS256")
    with pytest.raises(auth.InvalidToken):
        auth.verify_token(bare, SECRET)


def test_load_secret_required(monkeypatch):
    monkeypatch.delenv("AGENTIC_JWT_SECRET", raising=False)
    with pytest.raises(ConfigError, match="AGENTIC_JWT_SECRET"):
        auth.load_secret()
    monkeypatch.setenv("AGENTIC_JWT_SECRET", "s")
    assert auth.load_secret() == "s"


# ---- authenticate ----

KNOWN = {"analyst", "admin"}


def test_authenticate_ok(users_file):
    assert auth.authenticate(users_file, "alice", "wonderland", KNOWN) == _principal()


def test_authenticate_unknown_user_indistinguishable_from_bad_password(users_file):
    with pytest.raises(auth.InvalidCredentials):
        auth.authenticate(users_file, "mallory", "wonderland", KNOWN)
    with pytest.raises(auth.InvalidCredentials):
        auth.authenticate(users_file, "alice", "wrong", KNOWN)


def test_authenticate_unconfigured_role_denied(users_file):
    users_file.users["bob"] = auth.UserEntry(
        role="ghost-role", password_hash=auth.hash_password("pw")
    )
    with pytest.raises(auth.UnknownRole):
        auth.authenticate(users_file, "bob", "pw", KNOWN)


def test_authenticate_without_known_roles_skips_role_check(users_file):
    users_file.users["bob"] = auth.UserEntry(
        role="ghost-role", password_hash=auth.hash_password("pw")
    )
    assert auth.authenticate(users_file, "bob", "pw").role == "ghost-role"
