"""Authentication: accounts, sign-in sessions and role checks.

Accounts live in SQLite and are created with `python -m vajra.users`; there is
no self-registration and no built-in account. Passwords are stored only as
salted scrypt hashes, and session tokens only as SHA-256 hashes, so neither can
be recovered from the database.

A session lasts until sign-out, expiry, or the next backend start: the desktop
app starts the backend, so closing the app ends every session.

With `auth.required: false` in config.yaml every check here passes and the API
behaves as it did before authentication existed.
"""

import hashlib
import hmac
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request

from vajra import db
from vajra.config import Settings, get_settings

# Each role can do everything the roles before it can.
ROLES = ("viewer", "forecaster", "admin")
# Reachable without signing in: the health check and the sign-in itself.
OPEN_PATHS = {"/health", "/auth/login"}
MIN_PASSWORD_LENGTH = 10
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}
# Verified against when the account does not exist, so a wrong user ID takes as
# long to reject as a wrong password.
_DUMMY_SALT = b"\x00" * 16


@dataclass(frozen=True)
class User:
    username: str
    display_name: str
    role: str


class AuthError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _user(row: sqlite3.Row) -> User:
    return User(row["username"], row["display_name"], row["role"])


# ---- accounts -----------------------------------------------------------

def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(422, f"the password must be at least {MIN_PASSWORD_LENGTH} characters")


def create_user(settings: Settings, username: str, display_name: str, role: str, password: str) -> User:
    username = username.strip().lower()
    if not username or any(c.isspace() for c in username):
        raise AuthError(422, "the user ID must not be empty or contain spaces")
    if role not in ROLES:
        raise AuthError(422, f"the role must be one of {', '.join(ROLES)}")
    _check_password(password)
    salt = secrets.token_bytes(16)
    db.init_db(settings.db_path)
    with db.connect(settings.db_path) as conn:
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise AuthError(409, f"user '{username}' already exists")
        conn.execute(
            "INSERT INTO users (username, display_name, role, password_salt, password_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (username, display_name.strip() or username, role, salt, _hash_password(password, salt), db.utc_now()),
        )
    db.log_event(settings.db_path, "info", "auth", f"account '{username}' created with role {role}")
    return User(username, display_name.strip() or username, role)


def set_password(settings: Settings, username: str, password: str) -> None:
    """Change a password. Also unlocks the account and signs it out everywhere."""
    _check_password(password)
    salt = secrets.token_bytes(16)
    with db.connect(settings.db_path) as conn:
        changed = conn.execute(
            "UPDATE users SET password_salt = ?, password_hash = ?, failed_attempts = 0, locked_until = NULL "
            "WHERE username = ?",
            (salt, _hash_password(password, salt), username),
        ).rowcount
        if not changed:
            raise AuthError(404, f"user '{username}' does not exist")
        conn.execute("DELETE FROM sessions WHERE username = ?", (username,))
    db.log_event(settings.db_path, "info", "auth", f"password changed for '{username}'")


def unlock(settings: Settings, username: str) -> None:
    with db.connect(settings.db_path) as conn:
        changed = conn.execute(
            "UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE username = ?", (username,)
        ).rowcount
    if not changed:
        raise AuthError(404, f"user '{username}' does not exist")
    db.log_event(settings.db_path, "info", "auth", f"account '{username}' unlocked")


def remove_user(settings: Settings, username: str) -> None:
    with db.connect(settings.db_path) as conn:
        conn.execute("DELETE FROM sessions WHERE username = ?", (username,))
        changed = conn.execute("DELETE FROM users WHERE username = ?", (username,)).rowcount
    if not changed:
        raise AuthError(404, f"user '{username}' does not exist")
    db.log_event(settings.db_path, "info", "auth", f"account '{username}' removed")


def list_users(settings: Settings) -> list[sqlite3.Row]:
    db.init_db(settings.db_path)
    with db.connect(settings.db_path) as conn:
        return conn.execute(
            "SELECT username, display_name, role, locked_until, created_at, last_login_at "
            "FROM users ORDER BY username"
        ).fetchall()


# ---- sessions -----------------------------------------------------------

def sign_in(settings: Settings, username: str, password: str) -> tuple[str, datetime, User]:
    """Check a user ID and password and start a session. Returns (token, expiry, user)."""
    username = username.strip().lower()
    if settings.auth.open_access:
        return _open_sign_in(settings, username)
    refused = AuthError(401, "Invalid user ID or password")
    with db.connect(settings.db_path) as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if row is None:
            _hash_password(password, _DUMMY_SALT)
            raise refused
        if row["locked_until"] and datetime.fromisoformat(row["locked_until"]) > _now():
            raise AuthError(423, "This account is locked after too many failed attempts. Try again later.")
        correct = hmac.compare_digest(_hash_password(password, row["password_salt"]), row["password_hash"])
        if correct:
            token = secrets.token_urlsafe(32)
            expires = _now() + timedelta(hours=settings.auth.session_hours)
            conn.execute(
                "UPDATE users SET failed_attempts = 0, locked_until = NULL, last_login_at = ? WHERE username = ?",
                (db.utc_now(), username),
            )
            conn.execute(
                "INSERT INTO sessions (token_hash, username, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (_hash_token(token), username, db.utc_now(), expires.isoformat()),
            )
        else:
            attempts = row["failed_attempts"] + 1
            locked = attempts >= settings.auth.max_failed_attempts
            until = (_now() + timedelta(minutes=settings.auth.lockout_min)).isoformat() if locked else None
            conn.execute(
                "UPDATE users SET failed_attempts = ?, locked_until = ? WHERE username = ?",
                (0 if locked else attempts, until, username),
            )
    # Logged after the transaction above has been committed.
    if correct:
        db.log_event(settings.db_path, "info", "auth", f"'{username}' signed in")
        return token, expires, _user(row)
    db.log_event(
        settings.db_path, "warning", "auth",
        f"failed sign-in for '{username}'" + ("; account locked" if locked else ""),
    )
    raise refused


def _open_sign_in(settings: Settings, username: str) -> tuple[str, datetime, User]:
    """Open access (`auth.open_access`): start a session for whatever user ID was
    typed, without checking a password. An ID with no account gets one as a forecaster."""
    username = "-".join(username.split()) or "user"
    with db.connect(settings.db_path) as conn:
        known = conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
    if not known:
        create_user(settings, username, username, "forecaster", secrets.token_urlsafe(24))
    token = secrets.token_urlsafe(32)
    expires = _now() + timedelta(hours=settings.auth.session_hours)
    with db.connect(settings.db_path) as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        conn.execute("UPDATE users SET last_login_at = ? WHERE username = ?", (db.utc_now(), username))
        conn.execute(
            "INSERT INTO sessions (token_hash, username, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (_hash_token(token), username, db.utc_now(), expires.isoformat()),
        )
    db.log_event(settings.db_path, "warning", "auth", f"'{username}' signed in with open access (no password check)")
    return token, expires, _user(row)


def sign_out(settings: Settings, token: str) -> None:
    with db.connect(settings.db_path) as conn:
        row = conn.execute("SELECT username FROM sessions WHERE token_hash = ?", (_hash_token(token),)).fetchone()
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))
    if row:
        db.log_event(settings.db_path, "info", "auth", f"'{row['username']}' signed out")


def end_all_sessions(settings: Settings) -> None:
    """Called when the backend starts: a session never outlives the app."""
    with db.connect(settings.db_path) as conn:
        conn.execute("DELETE FROM sessions")


def _session_user(settings: Settings, token: str) -> User | None:
    with db.connect(settings.db_path) as conn:
        row = conn.execute(
            "SELECT u.username, u.display_name, u.role, s.expires_at FROM sessions s "
            "JOIN users u ON u.username = s.username WHERE s.token_hash = ?",
            (_hash_token(token),),
        ).fetchone()
        if row is None:
            return None
        if datetime.fromisoformat(row["expires_at"]) <= _now():
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))
            return None
    return _user(row)


# ---- request checks (FastAPI dependencies) ------------------------------

def bearer_token(request: Request) -> str | None:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def current_user(request: Request) -> User | None:
    """The signed-in user, or None when authentication is switched off. Rejects the
    request if authentication is on and it carries no valid session."""
    settings = get_settings()
    if not settings.auth.required:
        return None
    if hasattr(request.state, "user"):
        return request.state.user
    token = bearer_token(request)
    user = _session_user(settings, token) if token else None
    if user is None and request.url.path not in OPEN_PATHS:
        raise HTTPException(401, "Sign in to continue", headers={"WWW-Authenticate": "Bearer"})
    request.state.user = user
    return user


def require_role(minimum: str):
    """Dependency for actions that need at least the given role."""

    def check(request: Request) -> User | None:
        user = current_user(request)
        if user is not None and ROLES.index(user.role) < ROLES.index(minimum):
            raise HTTPException(403, f"This action needs the {minimum} role")
        return user

    return check
