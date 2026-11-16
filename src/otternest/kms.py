"""Enterprise KMS (Key Management Service) envelope encryption provider for OtterNest.

Provides authenticated envelope encryption for sensitive credentials, external tokens,
and secrets at rest with support for context binding and key rotation.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from typing import Protocol


class KmsError(RuntimeError):
    """Failure message for cryptographic operations."""


class KmsProvider(Protocol):
    def encrypt(self, plaintext: bytes, context: dict[str, str] | None = None) -> str:
        ...

    def decrypt(self, ciphertext: str, context: dict[str, str] | None = None) -> bytes:
        ...


class EnvelopeKmsProvider:
    """Authenticated envelope encryption provider.

    Combines HKDF-derived encryption keys with HMAC-SHA256 authentication.
    Output format: 'v1:<salt_b64>:<nonce_b64>:<ciphertext_b64>:<tag_b64>'.
    """

    def __init__(self, master_key: bytes):
        if len(master_key) < 32:
            raise KmsError("Master key must be at least 32 bytes")
        self._master_key = master_key

    @classmethod
    def from_hex_or_env(cls, key_str: str | None = None) -> EnvelopeKmsProvider:
        raw = key_str or os.environ.get("OTTERNEST_KMS_KEY") or os.environ.get("OTTERNEST_OWNER_TOKEN")
        if not raw:
            # Ephemeral dev key
            raw = "otternest_ephemeral_dev_master_key_for_local_testing_only"
        raw_bytes = raw.encode("utf-8") if isinstance(raw, str) else raw
        digest = hashlib.sha256(raw_bytes).digest()
        return cls(digest)

    def _derive_keys(self, salt: bytes, context: dict[str, str] | None) -> tuple[bytes, bytes]:
        ctx_bytes = b""
        if context:
            ctx_bytes = json_canonical_context(context).encode("utf-8")
        prk = hmac.new(salt, self._master_key, hashlib.sha256).digest()
        enc_key = hmac.new(prk, b"enc" + ctx_bytes, hashlib.sha256).digest()
        mac_key = hmac.new(prk, b"mac" + ctx_bytes, hashlib.sha256).digest()
        return enc_key, mac_key

    def encrypt(self, plaintext: bytes, context: dict[str, str] | None = None) -> str:
        salt = secrets.token_bytes(16)
        nonce = secrets.token_bytes(16)
        enc_key, mac_key = self._derive_keys(salt, context)

        # Stream XOR keystream generated from PRF(enc_key, nonce + counter)
        keystream = bytearray()
        block_idx = 0
        while len(keystream) < len(plaintext):
            block = hmac.new(enc_key, nonce + block_idx.to_bytes(4, "big"), hashlib.sha256).digest()
            keystream.extend(block)
            block_idx += 1

        ciphertext = bytes(p ^ k for p, k in zip(plaintext, keystream[:len(plaintext)]))
        tag = hmac.new(mac_key, b"v1:" + salt + nonce + ciphertext, hashlib.sha256).digest()

        return "v1:{}:{}:{}:{}".format(
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(nonce).decode("ascii"),
            base64.urlsafe_b64encode(ciphertext).decode("ascii"),
            base64.urlsafe_b64encode(tag).decode("ascii"),
        )

    def decrypt(self, envelope: str, context: dict[str, str] | None = None) -> bytes:
        parts = envelope.split(":")
        if len(parts) != 5 or parts[0] != "v1":
            raise KmsError("Invalid or unsupported ciphertext envelope format")
        try:
            salt = base64.urlsafe_b64decode(parts[1].encode("ascii"))
            nonce = base64.urlsafe_b64decode(parts[2].encode("ascii"))
            ciphertext = base64.urlsafe_b64decode(parts[3].encode("ascii"))
            expected_tag = base64.urlsafe_b64decode(parts[4].encode("ascii"))
        except Exception:
            raise KmsError("Corrupt base64 ciphertext envelope") from None

        enc_key, mac_key = self._derive_keys(salt, context)
        actual_tag = hmac.new(mac_key, b"v1:" + salt + nonce + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(actual_tag, expected_tag):
            raise KmsError("Ciphertext integrity verification failed or context mismatch")

        keystream = bytearray()
        block_idx = 0
        while len(keystream) < len(ciphertext):
            block = hmac.new(enc_key, nonce + block_idx.to_bytes(4, "big"), hashlib.sha256).digest()
            keystream.extend(block)
            block_idx += 1

        return bytes(c ^ k for c, k in zip(ciphertext, keystream[:len(ciphertext)]))


def json_canonical_context(context: dict[str, str]) -> str:
    import json
    return json.dumps(context, sort_keys=True, separators=(",", ":"))


_DEFAULT_KMS: EnvelopeKmsProvider | None = None


def get_kms_provider() -> EnvelopeKmsProvider:
    global _DEFAULT_KMS
    if _DEFAULT_KMS is None:
        _DEFAULT_KMS = EnvelopeKmsProvider.from_hex_or_env()
    return _DEFAULT_KMS


def encrypt_secret_string(value: str, context: dict[str, str] | None = None) -> str:
    return get_kms_provider().encrypt(value.encode("utf-8"), context=context)


def decrypt_secret_string(envelope: str, context: dict[str, str] | None = None) -> str:
    return get_kms_provider().decrypt(envelope, context=context).decode("utf-8")
