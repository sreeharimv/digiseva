"""
Authentication helpers — PIN hashing, JWT creation/verification, rate limiting.
"""

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerifyMismatchError
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

JWT_SECRET    = os.environ.get("JWT_SECRET", "digiseva-change-me-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 2

# Argon2id — 64 MB memory, 3 iterations: ~0.5s on modern hardware
# Makes brute-forcing all 1,000,000 six-digit PINs take ~6 days on a single core
_ph = PasswordHasher(
    time_cost=3,
    memory_cost=65536,  # 64 MB
    parallelism=1,
    hash_len=32,
    salt_len=16,
)

# ---------------------------------------------------------------------------
# PIN hashing
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
# JWT
# ---------------------------------------------------------------------------

def create_access_token(user_id: str, username: str) -> str:
    payload = {
        "user_id":  user_id,
        "username": username,
        "exp":      datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
    }
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
    return payload  # {"user_id": ..., "username": ...}


# ---------------------------------------------------------------------------
# Rate limiting — 5 failed attempts per IP per 15 minutes
# ---------------------------------------------------------------------------

_failed_attempts: dict = {}   # ip → [timestamp, ...]
_WINDOW = 15 * 60             # 15 minutes
_MAX    = 5


def is_rate_limited(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _failed_attempts.get(ip, []) if now - t < _WINDOW]
    _failed_attempts[ip] = recent
    return len(recent) >= _MAX


def record_failed_attempt(ip: str) -> None:
    _failed_attempts.setdefault(ip, []).append(time.time())
