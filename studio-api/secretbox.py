"""At-rest encryption for stored secrets (S7).

LLM API keys and the container-registry password are persisted in the SQLite
DB on the user's NFS-backed PVC. Storing them in plaintext means a leak of the
DB file alone — an NFS snapshot/backup, a support bundle, or another pod that
can mount the same export — exposes every key.

This module encrypts those values with a key **derived from an operator-provided
master secret** (``STUDIO_SECRET_KEY`` or a file named by
``STUDIO_SECRET_KEY_FILE``). The master secret must live somewhere the DB file
does NOT — an env var injected from a sealed secret, not on the PVC — otherwise
encrypting next to the key it protects buys nothing.

Fail-open by design: when no secret is configured (local dev, or a deployment
where the platform hasn't injected one yet) values are stored in plaintext and a
one-time warning is logged. Encryption switches on automatically once a secret
is present. Encrypted values are tagged ``enc:v1:`` so encrypted and legacy
plaintext coexist and migrate transparently on the next write.
"""

from __future__ import annotations

import base64
import hashlib
import os

import structlog

log = structlog.get_logger()

_PREFIX = "enc:v1:"
_warned = False


def _master_secret() -> str | None:
    secret = os.environ.get("STUDIO_SECRET_KEY")
    if not secret:
        path = os.environ.get("STUDIO_SECRET_KEY_FILE")
        if path and os.path.isfile(path):
            try:
                secret = open(path, encoding="utf-8").read().strip()
            except OSError as e:
                log.warning("secret_key_file_unreadable", path=path, error=str(e))
                return None
    return secret or None


def _fernet():
    """A Fernet built from the master secret, or None when unavailable.

    Any string works as the master secret — it's hashed to Fernet's required
    32-byte urlsafe-base64 key. Returns None if no secret is set OR the
    ``cryptography`` package isn't importable (both → plaintext fallback).
    """
    secret = _master_secret()
    if not secret:
        return None
    try:
        from cryptography.fernet import Fernet
    except Exception as e:  # pragma: no cover - cryptography is a hard dep in prod
        log.warning("cryptography_unavailable", error=str(e))
        return None
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def is_active() -> bool:
    """True when secrets will actually be encrypted at rest."""
    return _fernet() is not None


def encrypt(value: str | None) -> str | None:
    """Encrypt a secret for storage. Empty/None pass through unchanged; with no
    master secret the plaintext is returned (with a one-time warning)."""
    if not value:
        return value
    if value.startswith(_PREFIX):
        return value  # already encrypted (idempotent)
    f = _fernet()
    if f is None:
        global _warned
        if not _warned:
            log.warning(
                "secrets_stored_plaintext",
                detail="STUDIO_SECRET_KEY not set — API keys are stored "
                "unencrypted at rest. Inject a secret to enable encryption.",
            )
            _warned = True
        return value
    return _PREFIX + f.encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str | None:
    """Decrypt a stored secret. Untagged (legacy plaintext) values pass through.
    A tagged value that can't be decrypted (missing/rotated secret, corruption)
    returns "" and logs — never the raw ciphertext."""
    if not value or not value.startswith(_PREFIX):
        return value  # legacy plaintext or empty
    token = value[len(_PREFIX):]
    f = _fernet()
    if f is None:
        log.error("encrypted_secret_but_no_key", detail="cannot decrypt at-rest secret")
        return ""
    try:
        return f.decrypt(token.encode()).decode()
    except Exception as e:
        log.error("secret_decrypt_failed", error=str(e))
        return ""
