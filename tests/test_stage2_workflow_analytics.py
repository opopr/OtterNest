from __future__ import annotations


def test_goal_dependencies_cycle_protection(env, owner_headers):
    p = env.post("/api/v1/projects", headers=owner_headers, json={"name": "GoalsProj", "priority": 80}).json()
    g1 = env.post(f"/api/v1/projects/{p['id']}/goals", headers=owner_headers, json={"title": "G1", "success_criteria": "done", "priority": 90}).json()
    g2 = env.post(f"/api/v1/projects/{p['id']}/goals", headers=owner_headers, json={"title": "G2", "success_criteria": "done", "priority": 40}).json()

    assert env.post(f"/api/v1/goals/{g2['id']}/dependencies", headers=owner_headers, json={"predecessor_id": g1["id"]}).status_code == 201
    # Cycle g1 -> g2 must be rejected
    assert env.post(f"/api/v1/goals/{g1['id']}/dependencies", headers=owner_headers, json={"predecessor_id": g2["id"]}).status_code == 409
    deps = env.get(f"/api/v1/goals/{g2['id']}/dependencies", headers=owner_headers).json()
    assert deps["depends_on"] == [g1["id"]]
    assert deps["satisfied"] is False


def test_workflow_rules_and_circuit_breaker(env, owner_headers):
    p = env.post("/api/v1/projects", headers=owner_headers, json={"name": "WFProj"}).json()
    rule = env.post(
        f"/api/v1/projects/{p['id']}/workflow-rules",
        headers=owner_headers,
        json={
            "name": "Auto follow-up on task creation",
            "event_prefix": "task.created",
            "action_kind": "create_task",
            "action_config": {"title": "Verify task", "risk": "R1", "priority": 70},
            "max_chain_depth": 1,
            "cooldown_seconds": 0,
        },
    ).json()
    assert rule["enabled"] is True

    # Trigger initial event
    env.post(
        f"/api/v1/projects/{p['id']}/tasks",
        headers=owner_headers,
        json={"title": "Seed Task", "instruction": "run", "acceptance": "ok"},
    )

    # First evaluation creates 1 follow-up task with workflow_chain_depth=1
    r1 = env.post(f"/api/v1/projects/{p['id']}/workflow-rules/evaluate", headers=owner_headers).json()
    assert r1["executed"] == 1
    assert len(r1["runs"]) == 1
    assert r1["runs"][0]["status"] == "executed"

    # Second evaluation sees the newly created task.created (depth=1 -> next_depth=2 > max_chain_depth=1) and trips circuit breaker!
    r2 = env.post(f"/api/v1/projects/{p['id']}/workflow-rules/evaluate", headers=owner_headers).json()
    assert r2["executed"] == 0
    assert r2["skipped"] == 1
    assert r2["runs"][0]["status"] == "skipped_circuit_breaker"

    # Circuit breaker generates an urgent notification
    notifs = env.get("/api/v1/notifications?level=urgent&unread_only=true", headers=owner_headers).json()
    assert any(n["source_kind"] == "workflow_rule" for n in notifs)


def test_context_broker_ranking_and_memory_search(env, owner_headers):
    p = env.post("/api/v1/projects", headers=owner_headers, json={"name": "CtxProj"}).json()
    m1 = env.post(
        f"/api/v1/projects/{p['id']}/memories",
        headers=owner_headers,
        json={"kind": "fact", "title": "PostgreSQL Lock Rule", "content": "Use row-level FOR UPDATE on claim", "source": "adr"},
    ).json()
    m2 = env.post(
        f"/api/v1/projects/{p['id']}/memories",
        headers=owner_headers,
        json={"kind": "fact", "title": "UI Color Guide", "content": "Blue and purple palette for mascot", "source": "design"},
    ).json()
    env.post(f"/api/v1/memories/{m1['id']}/publish", headers=owner_headers)
    env.post(f"/api/v1/memories/{m2['id']}/publish", headers=owner_headers)

    hits = env.get(f"/api/v1/projects/{p['id']}/memories/search?q=PostgreSQL+claim", headers=owner_headers).json()
    assert hits[0]["id"] == m1["id"]

    t = env.post(
        f"/api/v1/projects/{p['id']}/tasks",
        headers=owner_headers,
        json={"title": "Fix PostgreSQL claim lock", "instruction": "Check FOR UPDATE lock", "acceptance": "tests pass"},
    ).json()
    ctx = env.get(f"/api/v1/tasks/{t['id']}/context", headers=owner_headers).json()
    assert ctx["memories"][0]["id"] == m1["id"]
    assert ctx["memories"][0]["relevance_score"] >= ctx["memories"][1]["relevance_score"]
    assert "remaining_char_budget" in ctx


def test_portfolio_schedule_metrics_briefing_and_experiments(env, owner_headers):
    p_low = env.post("/api/v1/projects", headers=owner_headers, json={"name": "LowProj", "priority": 20}).json()
    p_high = env.post("/api/v1/projects", headers=owner_headers, json={"name": "HighProj", "priority": 90}).json()
    agent = env.post(
        "/api/v1/agents",
        headers=owner_headers,
        json={"name": "poly-worker", "platform": "local", "execution_mode": "polling", "capabilities": []},
    ).json()
    ah = {"Authorization": f"Bearer {agent['token']}"}
    rt = env.post(f"/api/v1/agents/{agent['id']}/runtimes", headers=owner_headers, json={"label": "main", "environment": "local"}).json()
    env.post(f"/api/v1/agent/runtimes/{rt['id']}/heartbeat", headers=ah, json={"status": "idle"})

    env.post(f"/api/v1/projects/{p_low['id']}/members", headers=owner_headers, json={"agent_id": agent["id"], "role": "worker"})
    env.post(f"/api/v1/projects/{p_high['id']}/members", headers=owner_headers, json={"agent_id": agent["id"], "role": "worker"})

    t_high = env.post(f"/api/v1/projects/{p_high['id']}/tasks", headers=owner_headers, json={"title": "High Task", "instruction": "do", "acceptance": "ok"}).json()
    t_r3 = env.post(f"/api/v1/projects/{p_high['id']}/tasks", headers=owner_headers, json={"title": "R3 Task", "instruction": "deploy", "acceptance": "ok", "risk": "R3"}).json()

    sched = env.post("/api/v1/portfolio/schedule", headers=owner_headers, json={"limit_per_project": 5}).json()
    assert sched["projects_evaluated"] == 2
    assert sched["total_routes"] == 1

    # Complete with 1 rework and 1 blocked failure attribution
    claimed = env.post(f"/api/v1/tasks/{t_high['id']}/claim", headers=ah, json={"lease_seconds": 60}).json()
    env.post(f"/api/v1/tasks/{t_high['id']}/submit", headers=ah, json={"epoch": claimed["lease_epoch"], "summary": "draft 1"})
    env.post(f"/api/v1/tasks/{t_high['id']}/review", headers=owner_headers, json={"accepted": False, "reason": "needs better tests"})
    claimed2 = env.post(f"/api/v1/tasks/{t_high['id']}/claim", headers=ah, json={"lease_seconds": 60}).json()
    env.post(f"/api/v1/tasks/{t_high['id']}/fail", headers=ah, json={"epoch": claimed2["lease_epoch"], "summary": "timeout", "failure_reason": "external_service_failure"})

    metrics = env.get(f"/api/v1/projects/{p_high['id']}/metrics", headers=owner_headers).json()
    assert metrics["rework_task_count"] == 1
    assert metrics["blocked_count"] == 1
    assert metrics["failure_attribution"]["external_service_failure"] == 1

    briefing = env.get("/api/v1/briefing/daily", headers=owner_headers).json()
    assert any(r["task_id"] == t_r3["id"] for r in briefing["pending_r3_tasks"])
    assert any(r["task_id"] == t_high["id"] for r in briefing["blocked_tasks"])

    # Controlled experiment lifecycle & regression gate
    sk = env.post(f"/api/v1/projects/{p_high['id']}/skills", headers=owner_headers, json={"name": "triage", "version": "1.0", "content": "steps", "source": "local"}).json()
    exp = env.post(f"/api/v1/projects/{p_high['id']}/experiments", headers=owner_headers, json={"target_kind": "skill", "target_id": sk["id"], "hypothesis": "Improves pass rate"}).json()
    # Regression gate rejects promotion if score regressed
    env.post(f"/api/v1/experiments/{exp['id']}/record-run", headers=owner_headers, json={"baseline_score": 80, "candidate_score": 70, "sample_count": 5})
    assert env.post(f"/api/v1/experiments/{exp['id']}/promote", headers=owner_headers, json={"note": "try anyway"}).status_code == 409
    env.post(f"/api/v1/experiments/{exp['id']}/record-run", headers=owner_headers, json={"baseline_score": 80, "candidate_score": 92, "sample_count": 5})
    promoted = env.post(f"/api/v1/experiments/{exp['id']}/promote", headers=owner_headers, json={"note": "verified improvement"})
    assert promoted.status_code == 200
    assert promoted.json()["status"] == "promoted"
