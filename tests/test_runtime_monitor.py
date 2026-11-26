from __future__ import annotations


def test_global_runtime_list_returns_agent_metadata_and_server_heartbeat_state(env, owner_headers):
    created_agent = env.post(
        "/api/v1/agents",
        headers=owner_headers,
        json={"name": "runner-agent", "platform": "local", "execution_mode": "polling", "capabilities": []},
    )
    assert created_agent.status_code == 201, created_agent.text
    agent = created_agent.json()

    created_runtime = env.post(
        f"/api/v1/agents/{agent['id']}/runtimes",
        headers=owner_headers,
        json={"label": "runner-01", "environment": "test environment"},
    )
    assert created_runtime.status_code == 201, created_runtime.text
    runtime = created_runtime.json()

    initial = env.get("/api/v1/runtimes", headers=owner_headers)
    assert initial.status_code == 200, initial.text
    row = next(item for item in initial.json() if item["id"] == runtime["id"])
    assert row["agent_name"] == "runner-agent"
    assert row["agent_platform"] == "local"
    assert row["agent_enabled"] is True
    assert row["has_token"] is False
    assert row["online"] is False

    issued = env.post(
        f"/api/v1/agents/{agent['id']}/runtimes/{runtime['id']}/token",
        headers=owner_headers,
    )
    assert issued.status_code == 200, issued.text
    heartbeat = env.post(
        f"/api/v1/agent/runtimes/{runtime['id']}/heartbeat",
        headers={"Authorization": "Bearer " + issued.json()["token"]},
        json={"status": "busy", "health": {"cpu": "42%", "memory": "512MiB"}},
    )
    assert heartbeat.status_code == 200, heartbeat.text

    online = env.get("/api/v1/runtimes", headers=owner_headers)
    row = next(item for item in online.json() if item["id"] == runtime["id"])
    assert row["online"] is True
    assert row["status"] == "busy"
    assert row["has_token"] is True
    assert row["last_health"] == {"cpu": "42%", "memory": "512MiB"}

    disabled = env.post(
        f"/api/v1/agents/{agent['id']}/runtimes/{runtime['id']}/disable",
        headers=owner_headers,
    )
    assert disabled.status_code == 200, disabled.text
    final = env.get("/api/v1/runtimes", headers=owner_headers)
    row = next(item for item in final.json() if item["id"] == runtime["id"])
    assert row["enabled"] is False
    assert row["online"] is False


def test_global_runtime_list_is_owner_only(env, owner_headers):
    created_agent = env.post(
        "/api/v1/agents",
        headers=owner_headers,
        json={"name": "worker", "platform": "local", "execution_mode": "polling", "capabilities": []},
    )
    assert created_agent.status_code == 201, created_agent.text
    agent_token = created_agent.json()["token"]
    response = env.get("/api/v1/runtimes", headers={"Authorization": "Bearer " + agent_token})
    assert response.status_code == 403


def test_monitor_credential_only_allows_bound_monitoring_heartbeat(env, owner_headers):
    agent = env.post("/api/v1/agents", headers=owner_headers, json={
        "name": "monitor-agent", "platform": "local",
        "execution_mode": "polling", "capabilities": [],
    }).json()
    rt = env.post(
        f"/api/v1/agents/{agent['id']}/runtimes", headers=owner_headers,
        json={"label": "monitor-01", "environment": "linux"},
    ).json()
    other = env.post(
        f"/api/v1/agents/{agent['id']}/runtimes", headers=owner_headers,
        json={"label": "monitor-02", "environment": "linux"},
    ).json()
    old = env.post(
        f"/api/v1/agents/{agent['id']}/runtimes/{rt['id']}/token", headers=owner_headers,
    ).json()["token"]
    issued = env.post(
        f"/api/v1/agents/{agent['id']}/runtimes/{rt['id']}/monitor-token", headers=owner_headers,
    )
    assert issued.status_code == 200
    monitor = issued.json()["token"]
    assert monitor.startswith("on_monitor_")
    auth = {"Authorization": "Bearer " + monitor}
    assert env.post(
        f"/api/v1/agent/runtimes/{rt['id']}/heartbeat",
        headers={"Authorization": "Bearer " + old},
        json={"status": "monitoring"},
    ).status_code == 401
    assert env.post(
        f"/api/v1/agent/runtimes/{other['id']}/heartbeat", headers=auth,
        json={"status": "monitoring"},
    ).status_code == 403
    assert env.post(
        f"/api/v1/agent/runtimes/{rt['id']}/heartbeat", headers=auth,
        json={"status": "idle"},
    ).status_code == 403
    heartbeat = env.post(
        f"/api/v1/agent/runtimes/{rt['id']}/heartbeat", headers=auth,
        json={"status": "monitoring", "health": {"cpu_percent": "11"}},
    )
    assert heartbeat.status_code == 200, heartbeat.text
    for method, path, payload in [
        ("get", "/api/v1/agent/queue", None),
        ("post", "/api/v1/tasks/nonexistent/claim", {}),
        ("post", f"/api/v1/agents/{agent['id']}/runtimes/{rt['id']}/token", None),
        ("get", "/api/v1/runtimes", None),
    ]:
        response = env.request(method.upper(), path, headers=auth, json=payload)
        assert response.status_code == 403, (path, response.text)
    snapshot = env.get("/api/v1/runtimes", headers=owner_headers).json()
    row = next(item for item in snapshot if item["id"] == rt["id"])
    assert row["online"] is True and row["status"] == "monitoring"
    assert row["last_health"]["cpu_percent"] == "11"
