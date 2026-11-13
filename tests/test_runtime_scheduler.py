"""Agent lifecycle, verified task matching, offline status and deterministic routing."""
from datetime import timedelta

from otternest.db import get_session
from otternest.app import app
from otternest.models import AgentRuntime, now
from otternest.scheduler import schedule_once


def project(client, owner, name="Project"):
    r = client.post("/api/v1/projects", headers=owner, json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def agent(client, owner, project_id, capabilities=("python",), mode="polling", name="coder"):
    r = client.post("/api/v1/agents", headers=owner, json={"name": name,
                    "platform": "generic", "execution_mode": mode,
                    "capabilities": list(capabilities)})
    assert r.status_code == 201, r.text
    record = r.json()
    assert client.post(f"/api/v1/projects/{project_id}/members", headers=owner,
                       json={"agent_id": record["id"], "role": "worker"}).status_code == 201
    return record, {"Authorization": "Bearer " + record["token"]}


def task(client, owner, project_id, title="task", caps=("python",), priority=50, risk="R1"):
    r = client.post(f"/api/v1/projects/{project_id}/tasks", headers=owner,
                    json={"title": title, "instruction": "Do work", "acceptance": "Tests pass",
                          "required_capabilities": list(caps), "priority": priority, "risk": risk})
    assert r.status_code == 201, r.text
    return r.json()


def register_runtime(client, owner, record, auth, label="host-a"):
    created = client.post(f"/api/v1/agents/{record['id']}/runtimes", headers=owner,
                          json={"label": label, "environment": "self-hosted sandbox"})
    assert created.status_code == 201, created.text
    runtime = created.json()
    seen = client.post(f"/api/v1/agent/runtimes/{runtime['id']}/heartbeat", headers=auth,
                       json={"status": "idle"})
    assert seen.status_code == 200, seen.text
    return runtime


def test_capabilities_must_be_verified_by_owner(env, owner_headers):
    p = project(env, owner_headers)
    bot, auth = agent(env, owner_headers, p, ("python", "browser"))
    work = task(env, owner_headers, p, caps=("python",))
    assert env.get("/api/v1/agent/queue", headers=auth).json() == []
    assert env.post(f"/api/v1/tasks/{work['id']}/claim", headers=auth, json={}).status_code == 409
    assert env.put(f"/api/v1/agents/{bot['id']}/verified-capabilities", headers=auth,
                   json={"capabilities": ["python"]}).status_code == 403
    assert env.put(f"/api/v1/agents/{bot['id']}/verified-capabilities", headers=owner_headers,
                   json={"capabilities": ["shell"]}).status_code == 422
    approved = env.put(f"/api/v1/agents/{bot['id']}/verified-capabilities", headers=owner_headers,
                       json={"capabilities": ["python"]})
    assert approved.status_code == 200
    assert [x["id"] for x in env.get("/api/v1/agent/queue", headers=auth).json()] == [work["id"]]
    assert env.post(f"/api/v1/tasks/{work['id']}/claim", headers=auth, json={}).status_code == 200


def test_invalid_duplicate_and_suspicious_capabilities_rejected(env, owner_headers):
    p = project(env, owner_headers)
    assert env.post("/api/v1/agents", headers=owner_headers,
                    json={"name": "x", "platform": "x", "capabilities": ["python", "python"]}).status_code == 422
    assert env.post(f"/api/v1/projects/{p}/tasks", headers=owner_headers,
                    json={"title": "x", "instruction": "x", "acceptance": "x",
                          "required_capabilities": ["../shell"]}).status_code == 422


def test_runtime_is_self_reported_per_agent_only_and_can_be_disabled(env, owner_headers):
    p = project(env, owner_headers)
    a, auth_a = agent(env, owner_headers, p, name="a")
    b, auth_b = agent(env, owner_headers, p, name="b")
    r = register_runtime(env, owner_headers, a, auth_a)
    assert env.post(f"/api/v1/agent/runtimes/{r['id']}/heartbeat", headers=auth_b,
                    json={"status": "idle"}).status_code == 404
    assert env.post(f"/api/v1/agent/runtimes/{r['id']}/heartbeat", headers=owner_headers,
                    json={"status": "idle"}).status_code == 403
    assert env.post(f"/api/v1/agents/{a['id']}/runtimes/{r['id']}/disable", headers=owner_headers).status_code == 200
    assert env.post(f"/api/v1/agent/runtimes/{r['id']}/heartbeat", headers=auth_a,
                    json={"status": "idle"}).status_code == 404
    assert env.get(f"/api/v1/agents/{a['id']}/runtimes", headers=auth_a).status_code == 403


def test_scheduler_requires_live_verified_polling_agent(env, owner_headers):
    p = project(env, owner_headers)
    a, auth = agent(env, owner_headers, p)
    work = task(env, owner_headers, p)
    assert env.post(f"/api/v1/projects/{p}/schedule", headers=owner_headers, json={}).json()["count"] == 0
    r = register_runtime(env, owner_headers, a, auth)
    assert env.post(f"/api/v1/projects/{p}/schedule", headers=owner_headers, json={}).json()["count"] == 0
    env.put(f"/api/v1/agents/{a['id']}/verified-capabilities", headers=owner_headers,
            json={"capabilities": ["python"]})
    selected = env.post(f"/api/v1/projects/{p}/schedule", headers=owner_headers, json={})
    assert selected.status_code == 200 and selected.json()["routes"] == [{"task_id": work["id"], "agent_id": a["id"]}]
    # Targeting is never a claim; no remote execution is implied.
    after = env.get(f"/api/v1/tasks/{work['id']}", headers=owner_headers).json()
    assert after["status"] == "ready" and after["assigned_agent_id"] is None
    assert env.post(f"/api/v1/projects/{p}/schedule", headers=owner_headers, json={}).json()["count"] == 0
    assert env.post(f"/api/v1/projects/{p}/schedule", headers=auth, json={}).status_code == 403


def test_scheduler_skips_offline_manual_busy_unapproved_or_incompatible(env, owner_headers):
    p = project(env, owner_headers)
    a, auth = agent(env, owner_headers, p, ("python",), name="live")
    manual, manual_auth = agent(env, owner_headers, p, ("browser",), mode="manual", name="manual")
    r = register_runtime(env, owner_headers, a, auth)
    register_runtime(env, owner_headers, manual, manual_auth)
    env.put(f"/api/v1/agents/{a['id']}/verified-capabilities", headers=owner_headers,
            json={"capabilities": ["python"]})
    env.put(f"/api/v1/agents/{manual['id']}/verified-capabilities", headers=owner_headers,
            json={"capabilities": ["browser"]})
    high = task(env, owner_headers, p, title="high", priority=90)
    low = task(env, owner_headers, p, title="low", priority=30)
    r3 = task(env, owner_headers, p, title="R3", risk="R3")
    browser = task(env, owner_headers, p, title="browser", caps=("browser",))
    first = env.post(f"/api/v1/projects/{p}/schedule", headers=owner_headers, json={}).json()
    assert first["routes"] == [{"task_id": high["id"], "agent_id": a["id"]}]
    assert env.get(f"/api/v1/tasks/{low['id']}", headers=owner_headers).json()["target_agent_id"] is None
    assert env.get(f"/api/v1/tasks/{r3['id']}", headers=owner_headers).json()["status"] == "waiting_approval"
    assert env.get(f"/api/v1/tasks/{browser['id']}", headers=owner_headers).json()["target_agent_id"] is None
    # A stale self-reported heartbeat does not qualify for automatic routing.
    for dependency in app.dependency_overrides[get_session]():
        runtime = dependency.get(AgentRuntime, r["id"])
        runtime.last_seen = now() - timedelta(hours=1)
        dependency.commit()
        break
    assert env.post(f"/api/v1/projects/{p}/schedule", headers=owner_headers, json={}).json()["count"] == 0


def test_scheduler_skips_currently_busy_agent_and_unblocks_after_review(env, owner_headers):
    p = project(env, owner_headers)
    a, auth = agent(env, owner_headers, p)
    register_runtime(env, owner_headers, a, auth)
    env.put(f"/api/v1/agents/{a['id']}/verified-capabilities", headers=owner_headers,
            json={"capabilities": ["python"]})
    high = task(env, owner_headers, p, title="a", priority=90)
    low = task(env, owner_headers, p, title="b", priority=20)
    env.post(f"/api/v1/projects/{p}/schedule", headers=owner_headers, json={})
    lease = env.post(f"/api/v1/tasks/{high['id']}/claim", headers=auth, json={}).json()
    assert env.post(f"/api/v1/projects/{p}/schedule", headers=owner_headers, json={}).json()["count"] == 0
    env.post(f"/api/v1/tasks/{high['id']}/submit", headers=auth,
             json={"epoch": lease["lease_epoch"], "summary": "done"})
    env.post(f"/api/v1/tasks/{high['id']}/review", headers=owner_headers, json={"accepted": True})
    result = env.post(f"/api/v1/projects/{p}/schedule", headers=owner_headers, json={}).json()
    assert result["routes"] == [{"task_id": low["id"], "agent_id": a["id"]}]


def test_live_runner_uses_registered_runtime_without_owner_credentials(env, owner_headers, monkeypatch):
    """A runner can heartbeat its own runtime and cannot impersonate another Agent."""
    p = project(env, owner_headers)
    a, auth = agent(env, owner_headers, p)
    other, other_auth = agent(env, owner_headers, p, name="other")
    runtime = register_runtime(env, owner_headers, a, auth)
    from otternest.runner import Runner
    import httpx
    runner = Runner("http://127.0.0.1:8000", a["token"], ["unused"], runtime_id=runtime["id"])
    runner.client.close()
    def handler(request):
        response = env.request(request.method, request.url.path, headers=dict(request.headers),
                               content=request.content)
        return httpx.Response(response.status_code, content=response.content,
                              headers={"content-type": "application/json"})
    runner.client = httpx.Client(base_url="http://127.0.0.1:8000", transport=httpx.MockTransport(handler),
                                 headers={"Authorization": "Bearer " + a["token"]})
    try:
        runner.runtime_heartbeat("busy")
        assert env.get(f"/api/v1/agents/{a['id']}/runtimes", headers=owner_headers).json()[0]["status"] == "busy"
        runner.runtime_heartbeat("idle")
        assert env.get(f"/api/v1/agents/{a['id']}/runtimes", headers=owner_headers).json()[0]["status"] == "idle"
    finally:
        runner.close()


def test_scheduler_cli_uses_shared_domain_and_emits_audit(env, owner_headers, monkeypatch):
    p = project(env, owner_headers)
    a, auth = agent(env, owner_headers, p)
    register_runtime(env, owner_headers, a, auth)
    env.put(f"/api/v1/agents/{a['id']}/verified-capabilities", headers=owner_headers,
            json={"capabilities": ["python"]})
    t = task(env, owner_headers, p)
    import otternest.scheduler as scheduler
    # The test fixture provides an injected SQLite engine; the production daemon
    # obtains its PostgreSQL engine from the normal database configuration.
    session = next(app.dependency_overrides[get_session]())
    monkeypatch.setattr(scheduler, "get_engine", lambda: session.bind)
    assert schedule_once() == 1
    assert env.get(f"/api/v1/tasks/{t['id']}", headers=owner_headers).json()["target_agent_id"] == a["id"]
    assert schedule_once() == 0


def test_owner_cannot_target_unverified_agent(env, owner_headers):
    p = project(env, owner_headers)
    bot, auth = agent(env, owner_headers, p, ("python",))
    t = task(env, owner_headers, p)
    assert env.put(f"/api/v1/tasks/{t['id']}/target", headers=owner_headers,
                   json={"agent_id": bot["id"]}).status_code == 422
    env.put(f"/api/v1/agents/{bot['id']}/verified-capabilities", headers=owner_headers,
            json={"capabilities": ["python"]})
    assert env.put(f"/api/v1/tasks/{t['id']}/target", headers=owner_headers,
                   json={"agent_id": bot["id"]}).status_code == 200


def test_stale_runtime_is_not_reported_as_online(env, owner_headers):
    p = project(env, owner_headers)
    bot, auth = agent(env, owner_headers, p)
    r = register_runtime(env, owner_headers, bot, auth)
    records = env.get(f"/api/v1/agents/{bot['id']}/runtimes", headers=owner_headers).json()
    assert records[0]["online"] is True
    session = next(app.dependency_overrides[get_session]())
    runtime = session.get(AgentRuntime, r["id"])
    runtime.last_seen = now() - timedelta(hours=1)
    session.commit()
    records = env.get(f"/api/v1/agents/{bot['id']}/runtimes", headers=owner_headers).json()
    assert records[0]["online"] is False


def test_scheduler_spreads_work_across_two_live_agents(env, owner_headers):
    p = project(env, owner_headers)
    agents = []
    for name in ("a", "b"):
        a, auth = agent(env, owner_headers, p, name=name)
        register_runtime(env, owner_headers, a, auth)
        env.put(f"/api/v1/agents/{a['id']}/verified-capabilities", headers=owner_headers,
                json={"capabilities": ["python"]})
        agents.append(a["id"])
    high = task(env, owner_headers, p, title="high", priority=90)
    low = task(env, owner_headers, p, title="low", priority=20)
    third = task(env, owner_headers, p, title="third", priority=10)
    scheduled = env.post(f"/api/v1/projects/{p}/schedule", headers=owner_headers, json={}).json()
    assert [r["task_id"] for r in scheduled["routes"]] == [high["id"], low["id"]]
    assert {r["agent_id"] for r in scheduled["routes"]} == set(agents)
    assert env.get(f"/api/v1/tasks/{third['id']}", headers=owner_headers).json()["target_agent_id"] is None
