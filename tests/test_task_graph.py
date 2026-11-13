"""Dependency DAG invariants, task routing and approval must hold server-side."""
from otternest.models import Task, TaskDependency
from otternest.db import get_session
from otternest.app import app


def project(client, owner, name="Project"):
    response = client.post("/api/v1/projects", headers=owner, json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def agent(client, owner, project_id, name="worker"):
    bot = client.post("/api/v1/agents", headers=owner, json={"name": name, "platform": "local", "execution_mode": "polling"})
    assert bot.status_code == 201
    bot = bot.json()
    member = client.post(f"/api/v1/projects/{project_id}/members", headers=owner,
                         json={"agent_id": bot["id"], "role": "worker"})
    assert member.status_code == 201
    return bot, {"Authorization": "Bearer " + bot["token"]}


def task(client, owner, p, title="task", **kwargs):
    response = client.post(f"/api/v1/projects/{p}/tasks", headers=owner,
                           json={"title": title, "instruction": "Do work", "acceptance": "Review", **kwargs})
    assert response.status_code == 201, response.text
    return response.json()


def accept(client, owner, worker, t):
    claimed = client.post(f"/api/v1/tasks/{t['id']}/claim", headers=worker, json={})
    assert claimed.status_code == 200, claimed.text
    submitted = client.post(f"/api/v1/tasks/{t['id']}/submit", headers=worker,
                            json={"epoch": claimed.json()["lease_epoch"], "summary": "done"})
    assert submitted.status_code == 200, submitted.text
    reviewed = client.post(f"/api/v1/tasks/{t['id']}/review", headers=owner, json={"accepted": True})
    assert reviewed.status_code == 200, reviewed.text
    return reviewed.json()


def test_waits_for_independent_acceptance_and_releases_on_last_dependency(env, owner_headers):
    p = project(env, owner_headers)
    bot, auth = agent(env, owner_headers, p)
    first = task(env, owner_headers, p, "first")
    second = task(env, owner_headers, p, "second")
    third = task(env, owner_headers, p, "third", dependency_ids=[first["id"], second["id"]])
    assert third["status"] == "waiting_dependencies"
    assert {t["id"] for t in env.get("/api/v1/agent/queue", headers=auth).json()} == {first["id"], second["id"]}
    assert env.post(f"/api/v1/tasks/{third['id']}/claim", headers=auth, json={}).status_code == 409
    accept(env, owner_headers, auth, first)
    assert env.get(f"/api/v1/tasks/{third['id']}", headers=auth).json()["status"] == "waiting_dependencies"
    accept(env, owner_headers, auth, second)
    assert env.get(f"/api/v1/tasks/{third['id']}", headers=auth).json()["status"] == "ready"
    assert [t["id"] for t in env.get("/api/v1/agent/queue", headers=auth).json()] == [third["id"]]
    assert env.get(f"/api/v1/tasks/{third['id']}/dependencies", headers=auth).json()["satisfied"] is True
    events = env.get(f"/api/v1/projects/{p}/events", headers=owner_headers).json()
    assert [e["kind"] for e in events].count("task.dependencies_satisfied") == 1


def test_cycle_self_duplicate_and_cross_project_are_rejected(env, owner_headers):
    p = project(env, owner_headers)
    other = project(env, owner_headers, "other")
    a = task(env, owner_headers, p, "a")
    b = task(env, owner_headers, p, "b")
    c = task(env, owner_headers, p, "c")
    foreign = task(env, owner_headers, other, "foreign")
    add = lambda t, dep: env.post(f"/api/v1/tasks/{t['id']}/dependencies", headers=owner_headers,
                                   json={"predecessor_id": dep["id"]})
    assert add(b, a).status_code == 201
    assert add(c, b).status_code == 201
    assert add(a, c).status_code == 409  # transitive cycle
    assert add(a, a).status_code == 409
    assert add(b, a).status_code == 409
    assert add(b, foreign).status_code == 422
    bad = env.post(f"/api/v1/projects/{p}/tasks", headers=owner_headers,
                   json={"title": "unsafe", "instruction": "x", "acceptance": "y", "dependency_ids": [foreign["id"]]})
    assert bad.status_code == 422
    # Graph unchanged after failed operations.
    assert env.get(f"/api/v1/tasks/{a['id']}/dependencies", headers=owner_headers).json()["depends_on"] == []
    assert env.get(f"/api/v1/tasks/{b['id']}/dependencies", headers=owner_headers).json()["depends_on"] == [a["id"]]


def test_removing_last_dependency_releases_and_freezes_graph_after_claim(env, owner_headers):
    p = project(env, owner_headers)
    _, auth = agent(env, owner_headers, p)
    a = task(env, owner_headers, p, "a")
    b = task(env, owner_headers, p, "b", dependency_ids=[a["id"]])
    delete = env.delete(f"/api/v1/tasks/{b['id']}/dependencies/{a['id']}", headers=owner_headers)
    assert delete.status_code == 200 and delete.json()["task"]["status"] == "ready"
    claimed = env.post(f"/api/v1/tasks/{b['id']}/claim", headers=auth, json={})
    assert claimed.status_code == 200
    assert env.post(f"/api/v1/tasks/{b['id']}/dependencies", headers=owner_headers,
                    json={"predecessor_id": a["id"]}).status_code == 409
    assert env.delete(f"/api/v1/tasks/{b['id']}/dependencies/{a['id']}", headers=owner_headers).status_code == 409


def test_r3_approval_while_waiting_does_not_bypass_dependencies(env, owner_headers):
    p = project(env, owner_headers)
    _, auth = agent(env, owner_headers, p)
    a = task(env, owner_headers, p)
    b = task(env, owner_headers, p, risk="R3", dependency_ids=[a["id"]])
    assert b["status"] == "waiting_approval"
    approved = env.post(f"/api/v1/tasks/{b['id']}/authorize", headers=owner_headers)
    assert approved.status_code == 200
    assert approved.json()["status"] == "waiting_dependencies"
    assert env.post(f"/api/v1/tasks/{b['id']}/claim", headers=auth, json={}).status_code == 409
    accept(env, owner_headers, auth, a)
    assert env.get(f"/api/v1/tasks/{b['id']}", headers=owner_headers).json()["status"] == "ready"
    assert env.post(f"/api/v1/tasks/{b['id']}/claim", headers=auth, json={}).status_code == 200


def test_owner_targeting_priority_and_agent_enforcement(env, owner_headers):
    p = project(env, owner_headers)
    a, aauth = agent(env, owner_headers, p, "a")
    b, bauth = agent(env, owner_headers, p, "b")
    low = task(env, owner_headers, p, "low", priority=4)
    high = task(env, owner_headers, p, "high", priority=96)
    routed = env.put(f"/api/v1/tasks/{high['id']}/target", headers=owner_headers, json={"agent_id": b["id"]})
    assert routed.status_code == 200 and routed.json()["target_agent_id"] == b["id"]
    assert [x["id"] for x in env.get("/api/v1/agent/queue", headers=aauth).json()] == [low["id"]]
    assert [x["id"] for x in env.get("/api/v1/agent/queue", headers=bauth).json()] == [high["id"], low["id"]]
    assert env.post(f"/api/v1/tasks/{high['id']}/claim", headers=aauth, json={}).status_code == 409
    assert env.post(f"/api/v1/tasks/{high['id']}/claim", headers=bauth, json={}).status_code == 200
    assert env.put(f"/api/v1/tasks/{high['id']}/target", headers=owner_headers,
                   json={"agent_id": a["id"]}).status_code == 409
    assert env.put(f"/api/v1/tasks/{low['id']}/target", headers=aauth,
                   json={"agent_id": a["id"]}).status_code == 403


def test_agent_cannot_modify_graph_even_in_own_project(env, owner_headers):
    p = project(env, owner_headers)
    _, auth = agent(env, owner_headers, p)
    a = task(env, owner_headers, p, "a")
    b = task(env, owner_headers, p, "b")
    assert env.post(f"/api/v1/tasks/{b['id']}/dependencies", headers=auth,
                    json={"predecessor_id": a["id"]}).status_code == 403


def test_project_graph_endpoint_enforces_project_isolation(env, owner_headers):
    p = project(env, owner_headers)
    other = project(env, owner_headers, "private")
    _, auth = agent(env, owner_headers, p)
    a = task(env, owner_headers, p, "plan")
    b = task(env, owner_headers, p, "implement", dependency_ids=[a["id"]])
    graph = env.get(f"/api/v1/projects/{p}/task-graph", headers=auth)
    assert graph.status_code == 200
    assert {row["id"] for row in graph.json()["tasks"]} == {a["id"], b["id"]}
    assert graph.json()["dependencies"] == [{"task_id": b["id"], "depends_on_id": a["id"]}]
    assert env.get(f"/api/v1/projects/{other}/task-graph", headers=auth).status_code == 404


def test_r3_rejected_review_requires_fresh_approval(env, owner_headers):
    p = project(env, owner_headers)
    _, auth = agent(env, owner_headers, p)
    dangerous = task(env, owner_headers, p, risk="R3")
    assert env.post(f"/api/v1/tasks/{dangerous['id']}/authorize", headers=owner_headers).status_code == 200
    claimed = env.post(f"/api/v1/tasks/{dangerous['id']}/claim", headers=auth, json={}).json()
    assert env.post(f"/api/v1/tasks/{dangerous['id']}/submit", headers=auth,
                    json={"epoch": claimed["lease_epoch"], "summary": "draft"}).status_code == 200
    rejected = env.post(f"/api/v1/tasks/{dangerous['id']}/review", headers=owner_headers,
                        json={"accepted": False, "reason": "needs revision"})
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "waiting_approval"
    assert rejected.json()["approval_granted"] is False
    assert env.post(f"/api/v1/tasks/{dangerous['id']}/claim", headers=auth, json={}).status_code == 409
