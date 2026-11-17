"""Tests for web one-click self-update check, status, and apply endpoints."""
from __future__ import annotations

import otternest.updater as updater


def test_update_check_and_disabled_apply(env, owner_headers, monkeypatch, tmp_path):
    monkeypatch.delenv("OTTERNEST_ENABLE_WEB_UPDATE", raising=False)
    monkeypatch.setenv("OTTERNEST_UPDATE_STATUS_FILE", str(tmp_path / "update_status.json"))

    check_res = env.get("/api/v1/system/update/check", params={"fetch": "false"}, headers=owner_headers)
    assert check_res.status_code == 200
    data = check_res.json()
    assert data["enabled"] is False
    assert data["current_commit"]
    assert isinstance(data["behind_count"], int)

    status_res = env.get("/api/v1/system/update/status", headers=owner_headers)
    assert status_res.status_code == 200
    assert status_res.json()["state"] == "idle"

    apply_res = env.post("/api/v1/system/update/apply", headers=owner_headers)
    assert apply_res.status_code == 400


def test_update_apply_spawns_detached_worker_when_enabled(env, owner_headers, monkeypatch, tmp_path):
    monkeypatch.setenv("OTTERNEST_ENABLE_WEB_UPDATE", "true")
    status_file = tmp_path / "update_status.json"
    monkeypatch.setenv("OTTERNEST_UPDATE_STATUS_FILE", str(status_file))

    spawned = {}

    def fake_popen(cmd, **kwargs):
        spawned["cmd"] = cmd
        spawned["kwargs"] = kwargs
        return None

    monkeypatch.setattr(updater.subprocess, "Popen", fake_popen)

    apply_res = env.post("/api/v1/system/update/apply", headers=owner_headers)
    assert apply_res.status_code == 200
    assert apply_res.json()["state"] == "running"
    assert "otternest.updater" in spawned["cmd"]
    assert "--apply" in spawned["cmd"]

    status_res = env.get("/api/v1/system/update/status", headers=owner_headers)
    assert status_res.status_code == 200
    assert status_res.json()["state"] == "running"
