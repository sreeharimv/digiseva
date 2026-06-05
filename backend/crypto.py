"""
Cryptographic helpers for DigiSeva data encryption.

Key hierarchy:
  User PIN  +  user_id (as KDF salt)
        ↓  Argon2id (raw/KDF mode)
  master_key (32 bytes, never stored)
        ↓  AES-256-GCM
  data_key (32 bytes, stored encrypted in users.encrypted_data_key)
        ↓  AES-256-GCM  (per-row random nonce)
  enc_data column in services / investments / paid_log

Scheduler access (no PIN available at cron time):
  SCHEDULER_SECRET (env var)  +  fixed salt
        ↓  Argon2id
  scheduler_master_key
        ↓  AES-256-GCM
  same data_key (stored in users.scheduler_encrypted_key)
"""

import base64
import json
import os
from typing import Optional

from argon2.low_level import hash_secret_raw, Type
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# ---------------------------------------------------------------------------
# Argon2id KDF parameters
# ---------------------------------------------------------------------------

_TIME  = 3
_MEM   = 65536   # 64 MB
_PAR   = 1
_LEN   = 32      # 256-bit output


def derive_master_key(pin: str, user_id: str) -> bytes:
    """Derive 32-byte master key from PIN + user_id as Argon2 salt."""
    # user_id is a UUID string (36 chars) — more than enough entropy for a salt
    salt = user_id.encode()
    return hash_secret_raw(
        secret=pin.encode(),
        salt=salt,
        time_cost=_TIME,
        memory_cost=_MEM,
        parallelism=_PAR,
        hash_len=_LEN,
        type=Type.ID,
    )


def derive_scheduler_master_key(scheduler_secret: str) -> bytes:
    """Derive scheduler master key from SCHEDULER_SECRET env var."""
    return hash_secret_raw(
        secret=scheduler_secret.encode(),
        salt=b"digiseva-sched-v1",   # fixed 17-byte salt
        time_cost=_TIME,
        memory_cost=_MEM,
        parallelism=_PAR,
        hash_len=_LEN,
        type=Type.ID,
    )


# ---------------------------------------------------------------------------
# Key wrapping (encrypting data_key with a master key)
# ---------------------------------------------------------------------------

def wrap_key(master_key: bytes, data_key: bytes) -> tuple[str, str]:
    """Encrypt data_key with master_key. Returns (enc_b64, nonce_b64)."""
    nonce = os.urandom(12)
    ct    = AESGCM(master_key).encrypt(nonce, data_key, None)
    return base64.b64encode(ct).decode(), base64.b64encode(nonce).decode()


def unwrap_key(master_key: bytes, enc_b64: str, nonce_b64: str) -> bytes:
    """Decrypt data_key using master_key. Raises on bad key."""
    ct    = base64.b64decode(enc_b64)
    nonce = base64.b64decode(nonce_b64)
    return AESGCM(master_key).decrypt(nonce, ct, None)


# ---------------------------------------------------------------------------
# Data encryption (per-row)
# ---------------------------------------------------------------------------

def encrypt_fields(data_key: bytes, payload: dict) -> tuple[str, str]:
    """Encrypt a dict of sensitive fields. Returns (enc_b64, nonce_b64)."""
    nonce = os.urandom(12)
    pt    = json.dumps(payload, default=str).encode()
    ct    = AESGCM(data_key).encrypt(nonce, pt, None)
    return base64.b64encode(ct).decode(), base64.b64encode(nonce).decode()


def decrypt_fields(data_key: bytes, enc_b64: str, nonce_b64: str) -> dict:
    """Decrypt an enc_data blob. Returns the original dict."""
    ct    = base64.b64decode(enc_b64)
    nonce = base64.b64decode(nonce_b64)
    pt    = AESGCM(data_key).decrypt(nonce, ct, None)
    return json.loads(pt)
