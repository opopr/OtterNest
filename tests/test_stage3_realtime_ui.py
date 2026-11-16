from __future__ import annotations

from tests.conftest import OWNER


def test_websocket_and_ag_ui_projection(env, owner_headers):
    p = env.post("/api/v1/projects", headers=owner_headers, json={"name": "WSProj"}).json()
    t = env.post(
        f"/api/v1/projects/{p['id']}/tasks",
        headers=owner_headers,
        json={"title": "Streamed Task", "instruction": "do", "acceptance": "ok"},
    ).json()

    ag_ui = env.get(f"/api/v1/projects/{p['id']}/ag-ui/events", headers=owner_headers)
    assert ag_ui.status_code == 200
    body = ag_ui.json()
    assert body["protocol"] == "ag-ui-projection-v1"
    assert body["state_snapshot"]["task_counts"]["ready"] == 1
    assert any(e["subject_id"] == t["id"] for e in body["events"])

    with env.websocket_connect(f"/api/v1/projects/{p['id']}/events/ws") as ws:
        ws.send_json({"type": "auth", "token": OWNER, "after": 0})
        conn = ws.receive_json()
        assert conn["type"] == "connected"
        ev1 = ws.receive_json()
        assert ev1["type"] == "event"
        ws.send_text("ping")
        # Drain remaining buffered event(s) until pong
        seen_pong = False
        for _ in range(5):
            msg = ws.receive_json()
            if msg.get("type") == "pong":
                seen_pong = True
                break
        assert seen_pong


def test_a2ui_whitelist_and_sanitization(env, owner_headers):
    res = env.post(
        "/api/v1/a2ui/validate",
        headers=owner_headers,
        json={
            "components": [
                {
                    "type": "card",
                    "title": "Safe Card",
                    "href": "javascript:alert(1)",
                    "children": [
                        {"type": "metric", "label": "Pass Rate", "value": "95%"},
                        {"type": "script", "text": "evil()"},
                    ],
                }
            ]
        },
    ).json()
    assert res["valid"] is False
    assert "unsafe_href" in res["stripped"]
    assert "disallowed_type:script" in res["stripped"]
    assert len(res["components"]) == 1
    assert len(res["components"][0]["children"]) == 1
    assert res["components"][0]["children"][0]["type"] == "metric"


def test_command_chat_multiturn_clarification_and_plan(env, owner_headers):
    p = env.post("/api/v1/projects", headers=owner_headers, json={"name": "ChatProj"}).json()
    t1 = env.post(
        f"/api/v1/projects/{p['id']}/tasks",
        headers=owner_headers,
        json={"title": "Deploy API", "instruction": "release", "acceptance": "ok", "risk": "R3"},
    ).json()
    t2 = env.post(
        f"/api/v1/projects/{p['id']}/tasks",
        headers=owner_headers,
        json={"title": "Write Docs", "instruction": "docs", "acceptance": "ok", "risk": "R1"},
    ).json()

    # Status query returns A2UI summary card
    q_res = env.post(
        "/api/v1/command/chat",
        headers=owner_headers,
        json={"project_id": p["id"], "text": "查看项目状态和指标"},
    ).json()
    assert q_res["status"] == "answered"
    assert q_res["a2ui"]["components"][0]["type"] == "card"

    # Ambiguous command without focus returns clarification candidates (does not guess!)
    amb = env.post(
        "/api/v1/command/chat",
        headers=owner_headers,
        json={"project_id": p["id"], "text": "请批准它"},
    ).json()
    assert amb["status"] == "needs_clarification"
    assert len(amb["candidates"]) == 2

    # With focus_task_id, creates confirmable CommandPlan preview and confirms it
    focused = env.post(
        "/api/v1/command/chat",
        headers=owner_headers,
        json={"project_id": p["id"], "focus_task_id": t1["id"], "text": "继续批准它"},
    ).json()
    assert focused["status"] == "plan_ready"
    plan_id = focused["plan"]["id"]
    confirmed = env.post(f"/api/v1/command/plans/{plan_id}/confirm", headers=owner_headers)
    assert confirmed.status_code == 200
    assert env.get(f"/api/v1/tasks/{t1['id']}", headers=owner_headers).json()["status"] == "ready"
    assert env.get(f"/api/v1/tasks/{t2['id']}", headers=owner_headers).json()["status"] == "ready"


def test_web_console_renders_new_capabilities(env):
    html = env.get("/").text
    assert "renderTaskDagSvg" in html
    assert "connectProjectWs" in html
    assert "renderNotifDrawer" in html
    assert "sendCommandChat" in html
    assert "exportArtifactArchive" in html
