"""Encrypts stored connection credentials at rest (Fernet)."""
import base64
import hashlib
import json
import os
import secrets

from cryptography.fernet import Fernet, InvalidToken

SECRET_FIELDS = {"password", "api_key", "app_key"}
_KEY_FILE = os.path.join(os.path.dirname(os.getenv("DMA_DB_PATH", "/data/dma.sqlite3")), ".dma_secret")


def _secret() -> str:
    env = os.getenv("DMA_SECRET_KEY")
    if env:
        return env
    if os.path.exists(_KEY_FILE):
        with open(_KEY_FILE) as fh:
            return fh.read().strip()
    os.makedirs(os.path.dirname(_KEY_FILE), exist_ok=True)
    value = secrets.token_urlsafe(48)
    with open(_KEY_FILE, "w") as fh:
        fh.write(value)
    os.chmod(_KEY_FILE, 0o600)
    return value


def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(_secret().encode()).digest()))


def encrypt(data: dict) -> str:
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt(token: str) -> dict:
    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except (InvalidToken, ValueError):
        return {}


def mask(data: dict) -> dict:
    """Return a copy safe to send to the browser."""
    out = {}
    for k, v in data.items():
        if k in SECRET_FIELDS:
            out[k] = ""
            out[f"{k}_set"] = bool(v)
        else:
            out[k] = v
    return out


def merge_secrets(new: dict, old: dict) -> dict:
    """Blank secret fields in an update mean 'keep the stored value'."""
    merged = dict(new)
    for k in SECRET_FIELDS:
        if not merged.get(k) and old.get(k):
            merged[k] = old[k]
    for k in list(merged):
        if k.endswith("_set"):
            merged.pop(k)
    return merged
