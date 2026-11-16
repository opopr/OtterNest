"""Tests for enterprise KMS envelope encryption and security audit engine."""
from __future__ import annotations

import os
import pytest

from otternest.kms import (
    EnvelopeKmsProvider,
    KmsError,
    decrypt_secret_string,
    encrypt_secret_string,
)
from otternest.security_audit import audit_configuration, run_security_audit


def test_kms_envelope_encryption_roundtrip():
    provider = EnvelopeKmsProvider(b"master_secret_key_at_least_32_bytes_long!")
    plaintext = b"super-sensitive-secret-token-123456"
    context = {"project_id": "proj-abc", "field": "a2a_remote_token"}

    ciphertext = provider.encrypt(plaintext, context=context)
    assert ciphertext.startswith("v1:")
    assert ciphertext != plaintext.decode("ascii")

    decrypted = provider.decrypt(ciphertext, context=context)
    assert decrypted == plaintext


def test_kms_envelope_context_mismatch_fails():
    provider = EnvelopeKmsProvider(b"master_secret_key_at_least_32_bytes_long!")
    plaintext = b"critical-credential"
    ciphertext = provider.encrypt(plaintext, context={"tenant": "project-1"})

    with pytest.raises(KmsError, match="integrity verification failed"):
        provider.decrypt(ciphertext, context={"tenant": "project-2"})


def test_kms_tampered_ciphertext_fails():
    provider = EnvelopeKmsProvider(b"master_secret_key_at_least_32_bytes_long!")
    ciphertext = provider.encrypt(b"my-data")
    parts = ciphertext.split(":")
    # Tamper with the tag
    parts[4] = "AAAA" + parts[4][4:]
    tampered = ":".join(parts)

    with pytest.raises(KmsError):
        provider.decrypt(tampered)


def test_kms_string_helpers():
    text = "secret_string_value_测试"
    cipher = encrypt_secret_string(text, context={"type": "test"})
    assert decrypt_secret_string(cipher, context={"type": "test"}) == text


def test_security_audit_detects_weak_token(monkeypatch):
    monkeypatch.setenv("OTTERNEST_OWNER_TOKEN", "short")
    monkeypatch.setenv("OTTERNEST_DATABASE_URL", "sqlite:///test.db")
    findings = audit_configuration()
    owner_check = next(f for f in findings if f["check"] == "owner_token")
    assert owner_check["level"] == "critical"
    assert "minimum 32" in owner_check["message"]


def test_security_audit_passes_valid_token(monkeypatch):
    monkeypatch.setenv("OTTERNEST_OWNER_TOKEN", "a_very_long_and_secure_owner_token_over_32_chars!")
    monkeypatch.setenv("OTTERNEST_DATABASE_URL", "postgresql+psycopg://user:pass@127.0.0.1:5432/db")
    findings = audit_configuration()
    owner_check = next(f for f in findings if f["check"] == "owner_token")
    assert owner_check["level"] == "pass"
