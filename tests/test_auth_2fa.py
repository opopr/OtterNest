from __future__ import annotations

import time

from otternest import auth as auth_mod
from tests.conftest import OWNER


def test_bootstrap_and_password_login_lockout(env):
    st = env.get("/api/v1/auth/status")
    assert st.status_code == 200
    assert st.json() == {"account_configured": False, "totp_enabled": False, "token_configured": True}

    bad_boot = env.post("/api/v1/auth/bootstrap", json={
        "owner_token": "wrong-token",
        "username": "alice",
        "password": "StrongPassword123!",
    })
    assert bad_boot.status_code == 401

    boot = env.post("/api/v1/auth/bootstrap", json={
        "owner_token": OWNER,
        "username": "Admin_User",
        "password": "StrongPassword123!",
        "display_name": "项目负责人",
    })
    assert boot.status_code == 201
    data = boot.json()
    assert data["status"] == "authenticated"
    assert data["token"].startswith("on_session_")
    assert data["user"]["username"] == "admin_user"
    assert data["user"]["totp_enabled"] is False

    # Session works immediately on protected APIs.
    me = env.get("/api/v1/me", headers={"Authorization": f"Bearer {data['token']}"})
    assert me.status_code == 200
    assert me.json()["kind"] == "owner"

    # Duplicate bootstrap rejected.
    dup = env.post("/api/v1/auth/bootstrap", json={
        "owner_token": OWNER,
        "username": "attacker",
        "password": "StrongPassword123!",
    })
    assert dup.status_code == 409

    # Wrong password increments failure counter and locks at 5 attempts.
    for _ in range(5):
        r = env.post("/api/v1/auth/login", json={"username": "admin_user", "password": "wrong-password"})
        assert r.status_code == 401
    locked = env.post("/api/v1/auth/login", json={"username": "admin_user", "password": "StrongPassword123!"})
    assert locked.status_code == 429


def test_totp_2fa_enrollment_login_replay_recovery_and_logout(env, monkeypatch):
    boot = env.post("/api/v1/auth/bootstrap", json={
        "owner_token": OWNER,
        "username": "alice",
        "password": "StrongPassword123!",
    }).json()
    pre_session_headers = {"Authorization": f"Bearer {boot['token']}"}

    # Step 1: setup secret
    setup = env.post("/api/v1/auth/2fa/setup", headers=pre_session_headers)
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    assert setup.json()["otpauth_uri"].startswith("otpauth://totp/OtterNest%3Aalice?secret=")

    # Wrong code fails
    assert env.post("/api/v1/auth/2fa/enable", headers=pre_session_headers, json={"code": "000000"}).status_code == 422

    # Valid code at t = 3000 (step 100) enables 2FA
    monkeypatch.setattr(time, "time", lambda: 3000.0)
    code_100 = auth_mod._totp_at_counter(secret, 100)
    enabled = env.post("/api/v1/auth/2fa/enable", headers=pre_session_headers, json={"code": code_100})
    assert enabled.status_code == 200
    recovery_codes = enabled.json()["recovery_codes"]
    assert len(recovery_codes) == 8
    elevated_token = enabled.json().get("session_token")
    assert elevated_token and elevated_token.startswith("on_session_")

    # Pre-2FA session token is no longer accepted once 2FA is enabled, while the elevated token works.
    assert env.get("/api/v1/projects", headers=pre_session_headers).status_code == 401
    assert env.get("/api/v1/projects", headers={"Authorization": f"Bearer {elevated_token}"}).status_code == 200

    # Password login returns mfa_required with non-privileged challenge token.
    login_resp = env.post("/api/v1/auth/login", json={"username": "alice", "password": "StrongPassword123!"})
    assert login_resp.status_code == 200
    body = login_resp.json()
    assert body["status"] == "mfa_required"
    challenge = body["challenge_token"]
    assert challenge.startswith("on_mfa_")
    assert env.get("/api/v1/projects", headers={"Authorization": f"Bearer {challenge}"}).status_code == 401

    # Replaying step 100 code must fail.
    replay = env.post("/api/v1/auth/2fa/verify", json={"challenge_token": challenge, "code": code_100})
    assert replay.status_code == 401

    # Next 30s step (t = 3030, step 101) succeeds and issues MFA-verified session.
    monkeypatch.setattr(time, "time", lambda: 3030.0)
    code_101 = auth_mod._totp_at_counter(secret, 101)
    verified = env.post("/api/v1/auth/2fa/verify", json={"challenge_token": challenge, "code": code_101})
    assert verified.status_code == 200
    vbody = verified.json()
    assert vbody["status"] == "authenticated"
    assert vbody["used_recovery_code"] is False
    sess_headers = {"Authorization": f"Bearer {vbody['token']}"}
    assert env.get("/api/v1/projects", headers=sess_headers).status_code == 200

    # Challenge token is single-use.
    assert env.post("/api/v1/auth/2fa/verify", json={"challenge_token": challenge, "code": code_101}).status_code == 401

    # Login again using a single-use backup recovery code.
    c2 = env.post("/api/v1/auth/login", json={"username": "alice", "password": "StrongPassword123!"}).json()["challenge_token"]
    rec_ok = env.post("/api/v1/auth/2fa/verify", json={"challenge_token": c2, "code": recovery_codes[0]})
    assert rec_ok.status_code == 200
    assert rec_ok.json()["used_recovery_code"] is True
    assert rec_ok.json()["user"]["recovery_codes_remaining"] == 7

    # Used recovery code cannot be reused.
    c3 = env.post("/api/v1/auth/login", json={"username": "alice", "password": "StrongPassword123!"}).json()["challenge_token"]
    assert env.post("/api/v1/auth/2fa/verify", json={"challenge_token": c3, "code": recovery_codes[0]}).status_code == 401

    # Logout invalidates active session token.
    assert env.post("/api/v1/auth/logout", headers=sess_headers).status_code == 200
    assert env.get("/api/v1/projects", headers=sess_headers).status_code == 401

    # Owner token bearer auth remains functional for automation/API scripts.
    assert env.get("/api/v1/auth/account", headers={"Authorization": f"Bearer {OWNER}"}).status_code == 200

    # Disable 2FA requires current password + valid code at next window.
    monkeypatch.setattr(time, "time", lambda: 3060.0)
    code_102 = auth_mod._totp_at_counter(secret, 102)
    sess2_headers = {"Authorization": f"Bearer {rec_ok.json()['token']}"}
    assert env.post("/api/v1/auth/2fa/disable", headers=sess2_headers, json={"current_password": "wrong", "code": code_102}).status_code == 401
    disabled = env.post("/api/v1/auth/2fa/disable", headers=sess2_headers, json={"current_password": "StrongPassword123!", "code": code_102})
    assert disabled.status_code == 200
    assert disabled.json()["totp_enabled"] is False

    ev = env.get("/api/v1/auth/events", headers={"Authorization": f"Bearer {OWNER}"})
    assert ev.status_code == 200
    kinds = [row["kind"] for row in ev.json()]
    assert "owner.2fa_enabled" in kinds
    assert "owner.2fa_recovery_used" in kinds
    assert "owner.2fa_disabled" in kinds
