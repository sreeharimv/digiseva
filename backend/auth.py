"""
Authentication helpers — PIN hashing, key derivation, JWT, rate limiting.
"""

import base64
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from crypto import derive_master_key, wrap_key, unwrap_key

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

JWT_SECRET       = os.environ.get("JWT_SECRET", "digiseva-change-me-in-production")
JWT_ALGORITHM    = "HS256"
JWT_EXPIRY_HOURS = 2
SCHEDULER_SECRET = os.environ.get("SCHEDULER_SECRET", "")

# Argon2id — 64 MB, 3 iterations (~0.5s): used for PIN verification only
_ph = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=1,
    hash_len=32,
    salt_len=16,
)

# ---------------------------------------------------------------------------
# PIN hashing (verification)
# ---------------------------------------------------------------------------

def hash_pin(pin: str) -> str:
    return _ph.hash(pin)


def verify_pin(pin: str, pin_hash: str) -> bool:
    try:
        _ph.verify(pin_hash, pin)
        return True
    except VerifyMismatchError:
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Data key management
# ---------------------------------------------------------------------------

def create_data_key(pin: str, user_id: str) -> tuple[bytes, str, str, str, str]:
    """Generate a new data_key for a new user.

    Returns:
        (data_key, encrypted_data_key, key_nonce,
         scheduler_encrypted_key, scheduler_key_nonce)
    """
    import os as _os
    from crypto import derive_scheduler_master_key

    data_key    = _os.urandom(32)
    master_key  = derive_master_key(pin, user_id)
    enc_dk, nonce_dk = wrap_key(master_key, data_key)

    sched_enc, sched_nonce = ("", "")
    if SCHEDULER_SECRET:
        sched_master = derive_scheduler_master_key(SCHEDULER_SECRET)
        sched_enc, sched_nonce = wrap_key(sched_master, data_key)

    return data_key, enc_dk, nonce_dk, sched_enc, sched_nonce


def unlock_data_key(pin: str, user: dict) -> Optional[bytes]:
    """Derive master_key from PIN and decrypt the user's data_key.

    Returns None if decryption fails (wrong PIN or no key stored yet).
    """
    if not user.get("encrypted_data_key"):
        return None
    try:
        master_key = derive_master_key(pin, user["id"])
        return unwrap_key(master_key, user["encrypted_data_key"], user["key_nonce"])
    except Exception:
        return None


def rewrap_data_key(new_pin: str, user_id: str, data_key: bytes) -> tuple:
    """Re-encrypt an existing data_key under a new PIN (for PIN change).

    Returns (enc_dk, nonce_dk, sched_enc, sched_nonce) — same shape as
    the last 4 elements of create_data_key().
    """
    from crypto import derive_scheduler_master_key
    new_master = derive_master_key(new_pin, user_id)
    enc_dk, nonce_dk = wrap_key(new_master, data_key)

    sched_enc, sched_nonce = ("", "")
    if SCHEDULER_SECRET:
        sched_master = derive_scheduler_master_key(SCHEDULER_SECRET)
        sched_enc, sched_nonce = wrap_key(sched_master, data_key)

    return enc_dk, nonce_dk, sched_enc, sched_nonce


def get_scheduler_data_key(user: dict) -> Optional[bytes]:
    """Decrypt user's data_key using the server-side SCHEDULER_SECRET.

    Returns None if SCHEDULER_SECRET is not configured or key not stored.
    """
    from crypto import derive_scheduler_master_key
    if not SCHEDULER_SECRET or not user.get("scheduler_encrypted_key"):
        return None
    try:
        sched_master = derive_scheduler_master_key(SCHEDULER_SECRET)
        return unwrap_key(sched_master, user["scheduler_encrypted_key"], user["scheduler_key_nonce"])
    except Exception:
        return None


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def create_access_token(user_id: str, username: str, data_key: Optional[bytes] = None) -> str:
    payload: dict = {
        "user_id":  user_id,
        "username": username,
        "exp":      datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    if data_key:
        payload["dk"] = base64.b64encode(data_key).decode()
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> dict:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Not authenticated")
    payload = decode_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid or expired token")

    result: dict = {"user_id": payload["user_id"], "username": payload["username"]}
    if "dk" in payload:
        result["data_key"] = base64.b64decode(payload["dk"])
    else:
        result["data_key"] = None   # old token — no encryption
    return result


# ---------------------------------------------------------------------------
# Rate limiting — 5 failed attempts per IP per 15 minutes
# ---------------------------------------------------------------------------

_failed_attempts: dict = {}
_WINDOW = 15 * 60
_MAX    = 5


def is_rate_limited(ip: str) -> bool:
    now    = time.time()
    recent = [t for t in _failed_attempts.get(ip, []) if now - t < _WINDOW]
    _failed_attempts[ip] = recent
    return len(recent) >= _MAX


def record_failed_attempt(ip: str) -> None:
    _failed_attempts.setdefault(ip, []).append(time.time())
