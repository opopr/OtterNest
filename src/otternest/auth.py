"""Owner Web login, session lifecycle, and RFC 6238 TOTP two-factor authentication."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import struct
import time
from datetime import timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .api import as_dict
from .db import get_session
from .models import Event, OwnerAccount, OwnerSession, now
from .security import Actor, as_utc, authenticate, hash_token, owner_only
from .services import Conflict, emit

router = APIRouter(prefix="/api/v1/auth")

SESSION_TTL = timedelta(hours=12)
CHALLENGE_TTL = timedelta(minutes=5)
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION = timedelta(seconds=60)
RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _configured_owner_token() -> str:
    owner = os.environ.get("OTTERNEST_OWNER_TOKEN", "")
    return owner if len(owner) >= 32 and not owner.startswith("REPLACE_") else ""


def _require_valid_owner_token(candidate: str) -> None:
    expected = _configured_owner_token()
    if not expected:
        raise HTTPException(503, "Owner token is not configured")
    if not hmac.compare_digest(candidate, expected):
        raise HTTPException(401, "Invalid owner setup token")


def _normalize_username(username: str) -> str:
    cleaned = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,62}[a-z0-9]", cleaned):
        raise HTTPException(422, "Username must be 3-64 characters using lowercase letters, digits, ., _, or -")
    return cleaned


def hash_password(password: str) -> str:
    """Hash a password with a per-user random salt using stdlib scrypt (or PBKDF2 fallback)."""
    salt = secrets.token_bytes(16)
    salt_b64 = base64.b64encode(salt).decode("ascii")
    if hasattr(hashlib, "scrypt"):
        n, r, p = 16384, 8, 1
        dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
        return f"scrypt${n}${r}${p}${salt_b64}${base64.b64encode(dk).decode('ascii')}"
    iters = 310_000
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters, dklen=32)
    return f"pbkdf2${iters}${salt_b64}${base64.b64encode(dk).decode('ascii')}"


def verify_password(password: str, encoded: str) -> bool:
    """Verify a plaintext password against a stored salted digest in constant time."""
    parts = encoded.split("$")
    try:
        if parts[0] == "scrypt" and len(parts) == 6 and hasattr(hashlib, "scrypt"):
            _, n_s, r_s, p_s, salt_b64, hash_b64 = parts
            salt = base64.b64decode(salt_b64)
            expected = base64.b64decode(hash_b64)
            actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n_s), r=int(r_s), p=int(p_s), dklen=len(expected))
            return hmac.compare_digest(actual, expected)
        if parts[0] == "pbkdf2" and len(parts) == 4:
            _, iters_s, salt_b64, hash_b64 = parts
            salt = base64.b64decode(salt_b64)
            expected = base64.b64decode(hash_b64)
            actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iters_s), dklen=len(expected))
            return hmac.compare_digest(actual, expected)
    except Exception:
        return False
    return False


def generate_totp_secret() -> str:
    """Return a 160-bit Base32 RFC 6238 secret."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def format_totp_secret(secret: str) -> str:
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))


def build_otpauth_uri(secret: str, username: str, issuer: str = "OtterNest") -> str:
    label = quote(f"{issuer}:{username}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"


def _totp_at_counter(secret_b32: str, counter: int) -> str:
    padded = secret_b32 + "=" * ((-len(secret_b32)) % 8)
    key = base64.b32decode(padded.upper(), casefold=True)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{binary % 1_000_000:06d}"


def verify_totp_step(
    secret_b32: str,
    code: str,
    last_used_step: int | None = None,
    now_ts: float | None = None,
    window: int = 1,
) -> int | None:
    """Return matched TOTP counter step if valid inside [-window, +window] and not replayed."""
    cleaned = re.sub(r"[\s-]", "", code)
    if not re.fullmatch(r"\d{6}", cleaned):
        return None
    counter = int((time.time() if now_ts is None else now_ts) // 30)
    for delta in range(-window, window + 1):
        step = counter + delta
        if step < 0:
            continue
        if last_used_step is not None and step <= last_used_step:
            continue
        candidate = _totp_at_counter(secret_b32, step)
        if hmac.compare_digest(candidate, cleaned):
            return step
    return None


def generate_recovery_codes(count: int = 8) -> tuple[list[str], list[str]]:
    """Generate single-use 8-char backup codes and their SHA-256 digests."""
    codes: list[str] = []
    hashes: list[str] = []
    for _ in range(count):
        raw = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(8))
        formatted = f"{raw[:4]}-{raw[4:]}"
        codes.append(formatted)
        hashes.append(hash_token(raw))
    return codes, hashes


def _normalize_recovery_input(code: str) -> str:
    return re.sub(r"[\s-]", "", code).upper()


def _create_session(db: Session, owner_id: str, *, kind: str, mfa_verified: bool, ttl: timedelta) -> tuple[str, OwnerSession]:
    prefix = "on_mfa_" if kind == "mfa_challenge" else "on_session_"
    raw = prefix + secrets.token_urlsafe(36)
    row = OwnerSession(
        owner_id=owner_id,
        token_hash=hash_token(raw),
        kind=kind,
        mfa_verified=mfa_verified,
        revoked=False,
        expires_at=now() + ttl,
    )
    db.add(row)
    db.flush()
    return raw, row


def _check_lockout(account: OwnerAccount) -> None:
    locked = as_utc(account.locked_until)
    if locked and locked > now():
        remaining = max(1, int((locked - now()).total_seconds()))
        raise HTTPException(429, f"Too many failed attempts; retry in {remaining}s")


def _record_failure(account: OwnerAccount) -> None:
    account.failed_attempts += 1
    if account.failed_attempts >= MAX_FAILED_ATTEMPTS:
        account.locked_until = now() + LOCKOUT_DURATION
    account.updated_at = now()


def _account_summary(account: OwnerAccount) -> dict:
    return {
        "username": account.username,
        "display_name": account.display_name,
        "totp_enabled": account.totp_enabled,
        "recovery_codes_remaining": len(account.recovery_code_hashes or []),
    }


class BootstrapIn(BaseModel):
    owner_token: str = Field(min_length=1, max_length=512)
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="管理员", min_length=1, max_length=120)


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class Verify2FAIn(BaseModel):
    challenge_token: str = Field(min_length=10, max_length=256)
    code: str = Field(min_length=4, max_length=32)


class PasswordUpdateIn(BaseModel):
    username: str | None = Field(default=None, min_length=3, max_length=64)
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    current_password: str | None = Field(default=None, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class CodeIn(BaseModel):
    code: str = Field(min_length=4, max_length=32)


class Disable2FAIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=4, max_length=32)


@router.get("/status")
def auth_status(db: Session = Depends(get_session)):
    account = db.get(OwnerAccount, "owner")
    return {
        "account_configured": account is not None,
        "totp_enabled": bool(account and account.totp_enabled),
        "token_configured": bool(_configured_owner_token()),
    }


@router.post("/bootstrap", status_code=201)
def bootstrap_owner(body: BootstrapIn, db: Session = Depends(get_session)):
    _require_valid_owner_token(body.owner_token)
    existing = db.scalar(select(OwnerAccount).where(OwnerAccount.id == "owner").with_for_update())
    if existing is not None:
        raise Conflict("Owner account is already initialized")
    username = _normalize_username(body.username)
    account = OwnerAccount(
        id="owner",
        username=username,
        display_name=body.display_name.strip() or "管理员",
        password_hash=hash_password(body.password),
        totp_enabled=False,
        recovery_code_hashes=[],
    )
    db.add(account)
    db.flush()
    raw_token, session = _create_session(db, account.id, kind="session", mfa_verified=False, ttl=SESSION_TTL)
    emit(db, Actor("owner", "owner"), "owner.bootstrapped", account.id, payload={"username": username})
    db.commit()
    return {
        "status": "authenticated",
        "token": raw_token,
        "expires_at": session.expires_at,
        "user": _account_summary(account),
    }


@router.post("/login")
def login_owner(body: LoginIn, db: Session = Depends(get_session)):
    if not _configured_owner_token():
        raise HTTPException(503, "Owner token is not configured")
    account = db.scalar(select(OwnerAccount).where(OwnerAccount.id == "owner").with_for_update())
    if account is None:
        raise HTTPException(401, "Owner login account is not initialized")
    _check_lockout(account)

    uname = body.username.strip().lower()
    password_ok = verify_password(body.password, account.password_hash)
    username_ok = hmac.compare_digest(uname, account.username)
    if not (username_ok and password_ok):
        _record_failure(account)
        emit(db, Actor("owner", "owner"), "owner.login_failed", account.id, payload={"reason": "invalid_credentials"})
        db.commit()
        raise HTTPException(401, "Invalid username or password")

    account.failed_attempts = 0
    account.locked_until = None
    account.updated_at = now()

    if account.totp_enabled and account.totp_secret:
        challenge_token, challenge = _create_session(
            db, account.id, kind="mfa_challenge", mfa_verified=False, ttl=CHALLENGE_TTL
        )
        emit(db, Actor("owner", "owner"), "owner.login_challenge", account.id)
        db.commit()
        return {
            "status": "mfa_required",
            "challenge_token": challenge_token,
            "expires_at": challenge.expires_at,
            "user": _account_summary(account),
        }

    raw_token, session = _create_session(db, account.id, kind="session", mfa_verified=False, ttl=SESSION_TTL)
    emit(db, Actor("owner", "owner"), "owner.login_succeeded", account.id, payload={"method": "password"})
    db.commit()
    return {
        "status": "authenticated",
        "token": raw_token,
        "expires_at": session.expires_at,
        "user": _account_summary(account),
    }


@router.post("/2fa/verify")
def verify_login_2fa(body: Verify2FAIn, db: Session = Depends(get_session)):
    challenge = db.scalar(
        select(OwnerSession)
        .where(
            OwnerSession.token_hash == hash_token(body.challenge_token),
            OwnerSession.kind == "mfa_challenge",
            OwnerSession.revoked.is_(False),
        )
        .with_for_update()
    )
    now_dt = now()
    expires = as_utc(challenge.expires_at) if challenge else None
    if challenge is None or not expires or expires <= now_dt:
        raise HTTPException(401, "Invalid or expired 2FA challenge")

    account = db.scalar(select(OwnerAccount).where(OwnerAccount.id == challenge.owner_id).with_for_update())
    if account is None or not account.totp_enabled or not account.totp_secret:
        raise HTTPException(401, "2FA is not enabled")
    _check_lockout(account)

    step = verify_totp_step(account.totp_secret, body.code, account.totp_last_step)
    used_recovery = False
    if step is not None:
        account.totp_last_step = step
    else:
        norm_code = _normalize_recovery_input(body.code)
        code_hash = hash_token(norm_code) if len(norm_code) == 8 else ""
        hashes = list(account.recovery_code_hashes or [])
        match_idx = next((i for i, h in enumerate(hashes) if code_hash and hmac.compare_digest(h, code_hash)), None)
        if match_idx is None:
            _record_failure(account)
            emit(db, Actor("owner", "owner"), "owner.2fa_failed", account.id)
            db.commit()
            raise HTTPException(401, "Invalid verification or recovery code")
        hashes.pop(match_idx)
        account.recovery_code_hashes = hashes
        used_recovery = True

    account.failed_attempts = 0
    account.locked_until = None
    account.updated_at = now_dt
    challenge.revoked = True
    raw_token, session = _create_session(db, account.id, kind="session", mfa_verified=True, ttl=SESSION_TTL)
    event_kind = "owner.2fa_recovery_used" if used_recovery else "owner.login_succeeded"
    emit(db, Actor("owner", "owner"), event_kind, account.id, payload={"method": "recovery" if used_recovery else "totp"})
    db.commit()
    return {
        "status": "authenticated",
        "token": raw_token,
        "expires_at": session.expires_at,
        "used_recovery_code": used_recovery,
        "user": _account_summary(account),
    }


@router.get("/account")
def get_account(actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    account = db.get(OwnerAccount, "owner")
    active_sessions = 0
    if account:
        now_dt = now()
        rows = db.scalars(
            select(OwnerSession).where(
                OwnerSession.owner_id == account.id,
                OwnerSession.kind == "session",
                OwnerSession.revoked.is_(False),
            )
        ).all()
        active_sessions = sum(1 for r in rows if (as_utc(r.expires_at) or now_dt) > now_dt)
    return {
        "account_configured": account is not None,
        "auth_method": actor.auth_method,
        "active_sessions": active_sessions,
        "user": _account_summary(account) if account else None,
    }


@router.post("/password")
def set_or_update_password(body: PasswordUpdateIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    account = db.scalar(select(OwnerAccount).where(OwnerAccount.id == "owner").with_for_update())
    if account is None:
        if not body.username:
            raise HTTPException(422, "Username is required for initial account setup")
        username = _normalize_username(body.username)
        account = OwnerAccount(
            id="owner",
            username=username,
            display_name=(body.display_name or "管理员").strip() or "管理员",
            password_hash=hash_password(body.new_password),
            totp_enabled=False,
            recovery_code_hashes=[],
        )
        db.add(account)
        emit(db, actor, "owner.bootstrapped", account.id, payload={"username": username})
    else:
        if actor.auth_method != "owner_token":
            if not body.current_password or not verify_password(body.current_password, account.password_hash):
                raise HTTPException(401, "Current password is required")
        if body.username:
            account.username = _normalize_username(body.username)
        if body.display_name:
            account.display_name = body.display_name.strip() or account.display_name
        account.password_hash = hash_password(body.new_password)
        account.updated_at = now()
        db.execute(update(OwnerSession).where(OwnerSession.owner_id == account.id).values(revoked=True))
        emit(db, actor, "owner.password_changed", account.id)
    db.commit()
    return {"status": "updated", "user": _account_summary(account)}


@router.post("/2fa/setup")
def setup_2fa(actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    account = db.scalar(select(OwnerAccount).where(OwnerAccount.id == "owner").with_for_update())
    if account is None:
        raise Conflict("Initialize your owner login username and password before enabling 2FA")
    if account.totp_enabled:
        raise Conflict("2FA is already enabled; disable it first to rotate secret")
    secret = generate_totp_secret()
    account.totp_pending_secret = secret
    account.updated_at = now()
    db.commit()
    return {
        "username": account.username,
        "secret": secret,
        "formatted_secret": format_totp_secret(secret),
        "otpauth_uri": build_otpauth_uri(secret, account.username),
        "period_seconds": 30,
        "digits": 6,
    }


@router.post("/2fa/enable")
def enable_2fa(
    body: CodeIn,
    authorization: str | None = Header(default=None),
    actor: Actor = Depends(authenticate),
    db: Session = Depends(get_session),
):
    owner_only(actor)
    account = db.scalar(select(OwnerAccount).where(OwnerAccount.id == "owner").with_for_update())
    if account is None or not account.totp_pending_secret:
        raise Conflict("Run 2FA setup before enabling")
    step = verify_totp_step(account.totp_pending_secret, body.code, None)
    if step is None:
        raise HTTPException(422, "Invalid 6-digit authenticator verification code")
    recovery_codes, recovery_hashes = generate_recovery_codes()
    account.totp_secret = account.totp_pending_secret
    account.totp_pending_secret = None
    account.totp_enabled = True
    account.totp_last_step = step
    account.recovery_code_hashes = recovery_hashes
    account.updated_at = now()
    # Revoke pre-2FA sessions and mint a fresh MFA-verified token if enabled from a password session.
    db.execute(update(OwnerSession).where(OwnerSession.owner_id == account.id).values(revoked=True))
    elevated_token: str | None = None
    if authorization and authorization.startswith("Bearer on_session_"):
        elevated_token, _ = _create_session(db, account.id, kind="session", mfa_verified=True, ttl=SESSION_TTL)
    emit(db, actor, "owner.2fa_enabled", account.id)
    db.commit()
    resp = {
        "totp_enabled": True,
        "recovery_codes": recovery_codes,
        "user": _account_summary(account),
    }
    if elevated_token:
        resp["session_token"] = elevated_token
    return resp


@router.post("/2fa/recovery-codes")
def regenerate_recovery_codes(body: CodeIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    account = db.scalar(select(OwnerAccount).where(OwnerAccount.id == "owner").with_for_update())
    if account is None or not account.totp_enabled or not account.totp_secret:
        raise Conflict("2FA is not enabled")
    step = verify_totp_step(account.totp_secret, body.code, account.totp_last_step)
    if step is None:
        raise HTTPException(401, "Invalid 6-digit authenticator verification code")
    account.totp_last_step = step
    recovery_codes, recovery_hashes = generate_recovery_codes()
    account.recovery_code_hashes = recovery_hashes
    account.updated_at = now()
    emit(db, actor, "owner.2fa_recovery_regenerated", account.id)
    db.commit()
    return {"recovery_codes": recovery_codes, "user": _account_summary(account)}


@router.post("/2fa/disable")
def disable_2fa(body: Disable2FAIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    account = db.scalar(select(OwnerAccount).where(OwnerAccount.id == "owner").with_for_update())
    if account is None or not account.totp_enabled or not account.totp_secret:
        raise Conflict("2FA is not enabled")
    if not verify_password(body.current_password, account.password_hash):
        raise HTTPException(401, "Invalid current password")
    step = verify_totp_step(account.totp_secret, body.code, account.totp_last_step)
    code_matched = step is not None
    if not code_matched:
        norm = _normalize_recovery_input(body.code)
        code_hash = hash_token(norm) if len(norm) == 8 else ""
        code_matched = any(code_hash and hmac.compare_digest(h, code_hash) for h in (account.recovery_code_hashes or []))
    if not code_matched:
        raise HTTPException(401, "Invalid 2FA or recovery code")
    account.totp_enabled = False
    account.totp_secret = None
    account.totp_pending_secret = None
    account.totp_last_step = None
    account.recovery_code_hashes = []
    account.updated_at = now()
    emit(db, actor, "owner.2fa_disabled", account.id)
    db.commit()
    return {"totp_enabled": False, "user": _account_summary(account)}


@router.post("/logout")
def logout_current_session(
    authorization: str | None = Header(default=None),
    actor: Actor = Depends(authenticate),
    db: Session = Depends(get_session),
):
    owner_only(actor)
    if authorization and authorization.startswith("Bearer on_session_"):
        tok = authorization[7:]
        db.execute(update(OwnerSession).where(OwnerSession.token_hash == hash_token(tok)).values(revoked=True))
        emit(db, actor, "owner.session_revoked", actor.id)
        db.commit()
    return {"status": "logged_out"}


@router.post("/logout-all")
def logout_all_sessions(actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    db.execute(update(OwnerSession).where(OwnerSession.owner_id == "owner").values(revoked=True))
    emit(db, actor, "owner.sessions_revoked_all", actor.id)
    db.commit()
    return {"status": "logged_out_all"}


@router.get("/events")
def auth_events(limit: int = 50, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    if limit < 1 or limit > 200:
        raise HTTPException(422, "limit must be 1..200")
    items = db.scalars(
        select(Event)
        .where(Event.kind.startswith("owner."))
        .order_by(Event.id.desc())
        .limit(limit)
    ).all()
    return [as_dict(e, "id", "kind", "subject_id", "payload", "created_at") for e in items]
