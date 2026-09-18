"""Authentication: local accounts, PBKDF2 password hashing, server-side sessions."""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from . import db

log = logging.getLogger("dma.auth")

ITERATIONS = 600_000
MIN_PASSWORD = 10
SESSION_HOURS = max(1, int(os.getenv("DMA_SESSION_HOURS", "12") or 12))
COOKIE_SESSION = "dma_session"
COOKIE_CSRF = "dma_csrf"

_FAIL_WINDOW = 900        # 15 minutes
_FAIL_LIMIT = 10
_fails: dict[str, list[float]] = {}
_lock = threading.Lock()


class AuthError(Exception):
    pass


# ------------------------------------------------------------------ passwords
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_hex, hash_hex = (stored or "").split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)


def check_password_policy(password: str) -> None:
    if len(password or "") < MIN_PASSWORD:
        raise AuthError(f"Password must be at least {MIN_PASSWORD} characters")


# ---------------------------------------------------------------------- users
def user_count() -> int:
    return db.one("SELECT COUNT(*) AS c FROM users")["c"]


def get_user(username: str) -> dict | None:
    return db.one("SELECT * FROM users WHERE username = ? COLLATE NOCASE", ((username or "").strip(),))


def list_users() -> list[dict]:
    return db.query("SELECT id, username, created_at, last_login FROM users ORDER BY username")


def create_user(username: str, password: str) -> dict:
    username = (username or "").strip()
    if not 3 <= len(username) <= 64 or not username.replace("_", "").replace("-", "").replace(".", "").isalnum():
        raise AuthError("Username must be 3–64 characters: letters, digits, dot, dash or underscore")
    check_password_policy(password)
    if get_user(username):
        raise AuthError("That username already exists")
    uid = db.execute("INSERT INTO users(username, password_hash, created_at) VALUES (?,?,?)",
                     (username, hash_password(password), db.now()))
    log.info("created user %s", username)
    return {"id": uid, "username": username}


def set_password(user_id: int, password: str) -> None:
    check_password_policy(password)
    db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user_id))
    db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def delete_user(user_id: int) -> None:
    if user_count() <= 1:
        raise AuthError("The last account cannot be deleted")
    db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))


def bootstrap_from_env() -> None:
    """Create the first account from DMA_ADMIN_USER / DMA_ADMIN_PASSWORD, if set."""
    user, password = os.getenv("DMA_ADMIN_USER"), os.getenv("DMA_ADMIN_PASSWORD")
    if not user or not password or user_count():
        return
    try:
        create_user(user, password)
        log.info("bootstrapped first account from environment: %s", user)
    except AuthError as e:
        log.error("DMA_ADMIN_USER/DMA_ADMIN_PASSWORD ignored: %s", e)


# ------------------------------------------------------------------ throttling
def _throttle_key(username: str, ip: str) -> str:
    return f"{(username or '').lower()}|{ip}"


def throttled(username: str, ip: str) -> int:
    """Seconds remaining before another attempt is allowed, or 0."""
    with _lock:
        hits = [t for t in _fails.get(_throttle_key(username, ip), []) if time.time() - t < _FAIL_WINDOW]
        _fails[_throttle_key(username, ip)] = hits
        if len(hits) >= _FAIL_LIMIT:
            return int(_FAIL_WINDOW - (time.time() - hits[0])) + 1
    return 0


def record_failure(username: str, ip: str) -> None:
    with _lock:
        _fails.setdefault(_throttle_key(username, ip), []).append(time.time())


def clear_failures(username: str, ip: str) -> None:
    with _lock:
        _fails.pop(_throttle_key(username, ip), None)


# -------------------------------------------------------------------- sessions
def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def login(username: str, password: str, ip: str) -> dict:
    wait = throttled(username, ip)
    if wait:
        raise AuthError(f"Too many failed attempts. Try again in {wait // 60 + 1} minute(s).")
    user = get_user(username)
    if not user or not verify_password(password, user["password_hash"]):
        record_failure(username, ip)
        raise AuthError("Incorrect username or password")
    clear_failures(username, ip)
    db.execute("UPDATE users SET last_login = ? WHERE id = ?", (db.now(), user["id"]))
    return create_session(user["id"], user["username"])


def create_session(user_id: int, username: str) -> dict:
    purge_expired()
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)
    db.execute("INSERT INTO sessions(user_id, token_hash, created_at, expires_at) VALUES (?,?,?,?)",
               (user_id, _token_hash(token), db.now(), expires.isoformat(timespec="seconds")))
    return {"token": token, "username": username, "user_id": user_id,
            "expires_at": expires.isoformat(timespec="seconds"), "csrf": secrets.token_urlsafe(24)}


def session_user(token: str) -> dict | None:
    if not token:
        return None
    row = db.one("SELECT s.id, s.expires_at, u.id AS user_id, u.username FROM sessions s "
                 "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?", (_token_hash(token),))
    if not row:
        return None
    if row["expires_at"] < db.now():
        db.execute("DELETE FROM sessions WHERE id = ?", (row["id"],))
        return None
    return {"id": row["user_id"], "username": row["username"]}


def logout(token: str) -> None:
    if token:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def purge_expired() -> None:
    db.execute("DELETE FROM sessions WHERE expires_at < ?", (db.now(),))
