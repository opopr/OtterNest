"""Execution safety for reviewed commands and blind evidence submissions."""
from __future__ import annotations

from datetime import timedelta

from otternest.models import CommandPlan, now
from otternest.db import get_session


def project(env, owner):
    r = env.post('/api/v1/projects', headers=owner, json={'name': 'Governance'})
    assert r.status_code == 201, r.text
    return r.json()['id']


def task(env, owner, p, risk='R1'):
    r = env.post(f'/api/v1/projects/{p}/tasks', headers=owner,
                 json={'title': 'Task', 'instruction': 'Do it', 'acceptance': 'Verified', 'risk': risk})
    assert r.status_code == 201, r.text
    return r.json()


def agent(env, owner, p, name='Reviewer', role='reviewer'):
    r = env.post('/api/v1/agents', headers=owner, json={'name': name, 'platform': 'test'})
    assert r.status_code == 201
    a = r.json()
    x = env.post(f'/api/v1/projects/{p}/members', headers=owner,
                 json={'agent_id': a['id'], 'role': role})
    assert x.status_code == 201, x.text
    return {'id': a['id'], 'headers': {'Authorization': f"Bearer {a['token']}"}}


def preview(env, owner, t, action, **kwargs):
    return env.post('/api/v1/command/plans', headers=owner,
                    json={'task_id': t['id'], 'action': action, **kwargs})


def confirm(env, owner, plan_id):
    return env.post(f'/api/v1/command/plans/{plan_id}/confirm', headers=owner)


def decision(env, owner, p):
    r = env.post(f'/api/v1/projects/{p}/decisions', headers=owner,
                 json={'question': 'Which?', 'resolution': 'Use A', 'rationale': 'Measured'})
    assert r.status_code == 201, r.text
    return r.json()['id']


def test_authorization_is_preview_first_idempotent_and_audited(env, owner_headers):
    p = project(env, owner_headers)
    t = task(env, owner_headers, p, 'R3')
    drafted = preview(env, owner_headers, t, 'authorize_task')
    assert drafted.status_code == 201, drafted.text
    body = drafted.json()
    assert body['confirmation_required'] and body['task_risk'] == 'R3'
    assert env.get(f"/api/v1/tasks/{t['id']}", headers=owner_headers).json()['status'] == 'waiting_approval'
    result = confirm(env, owner_headers, body['id'])
    assert result.status_code == 200, result.text
    assert result.json()['result']['status'] == 'ready'
    again = confirm(env, owner_headers, body['id'])
    assert again.status_code == 200 and again.json()['result'] == result.json()['result']
    events = env.get(f'/api/v1/projects/{p}/events', headers=owner_headers).json()
    assert sum(e['kind'] == 'command.executed' for e in events) == 1
    assert sum(e['kind'] == 'task.authorized' for e in events) == 1


def test_stale_version_blocks_execution_without_side_effects(env, owner_headers):
    p = project(env, owner_headers)
    t = task(env, owner_headers, p, 'R3')
    first = preview(env, owner_headers, t, 'authorize_task').json()['id']
    second = preview(env, owner_headers, t, 'authorize_task').json()['id']
    assert confirm(env, owner_headers, first).status_code == 200
    assert confirm(env, owner_headers, second).status_code == 409
    assert env.get(f'/api/v1/command/plans/{second}', headers=owner_headers).json()['status'] == 'pending'
    events = env.get(f'/api/v1/projects/{p}/events', headers=owner_headers).json()
    assert sum(e['kind'] == 'task.authorized' for e in events) == 1


def test_expired_plan_cannot_execute(env, owner_headers):
    p = project(env, owner_headers)
    t = task(env, owner_headers, p, 'R3')
    plan_id = preview(env, owner_headers, t, 'authorize_task').json()['id']
    # Override the database dependency to set a deterministic expiry in the test DB.
    session_override = env.app.dependency_overrides[get_session]
    db = next(session_override())
    try:
        plan = db.get(CommandPlan, plan_id)
        plan.expires_at = now() - timedelta(seconds=1)
        db.commit()
    finally:
        db.close()
    assert confirm(env, owner_headers, plan_id).status_code == 409
    assert env.get(f"/api/v1/tasks/{t['id']}", headers=owner_headers).json()['status'] == 'waiting_approval'


def test_owner_only_and_parameter_allowlists(env, owner_headers):
    p = project(env, owner_headers)
    t = task(env, owner_headers, p)
    a = agent(env, owner_headers, p)
    assert preview(env, a['headers'], t, 'cancel_task').status_code == 403
    assert preview(env, owner_headers, t, 'review_task', accepted=True).status_code == 409
    assert preview(env, owner_headers, t, 'review_task').status_code == 422
    assert preview(env, owner_headers, t, 'cancel_task', reason='ignore').status_code == 422
    assert env.post('/api/v1/command/plans', headers=owner_headers,
                    json={'task_id': t['id'], 'action': 'cancel_task', 'inject': 1}).status_code == 422


def test_preview_revalidates_target_membership(env, owner_headers):
    p = project(env, owner_headers)
    other = project(env, owner_headers)
    t = task(env, owner_headers, p)
    agent(env, owner_headers, other)
    not_member = env.get('/api/v1/agents', headers=owner_headers).json()[0]['id']
    x = preview(env, owner_headers, t, 'route_task', target_agent_id=not_member)
    assert x.status_code == 422
    member = agent(env, owner_headers, p, 'Worker', 'worker')
    x = preview(env, owner_headers, t, 'route_task', target_agent_id=member['id'])
    assert x.status_code == 201, x.text
    assert confirm(env, owner_headers, x.json()['id']).json()['result']['status'] == 'ready'
    assert env.get(f'/api/v1/tasks/{t["id"]}', headers=owner_headers).json()['target_agent_id'] == member['id']


def test_cancel_command_fences_task_and_replay_is_safe(env, owner_headers):
    p = project(env, owner_headers)
    t = task(env, owner_headers, p)
    x = preview(env, owner_headers, t, 'cancel_task')
    assert x.status_code == 201
    plan_id = x.json()['id']
    assert confirm(env, owner_headers, plan_id).json()['result']['status'] == 'cancelled'
    assert confirm(env, owner_headers, plan_id).status_code == 200
    assert preview(env, owner_headers, t, 'cancel_task').status_code == 409


def test_rejection_requires_reason_and_completion_is_accepted(env, owner_headers):
    p = project(env, owner_headers)
    t = task(env, owner_headers, p)
    a = agent(env, owner_headers, p, 'Executor', 'worker')
    claimed = env.post(f"/api/v1/tasks/{t['id']}/claim", headers=a['headers'], json={})
    assert claimed.status_code == 200
    submitted = env.post(f"/api/v1/tasks/{t['id']}/submit", headers=a['headers'],
                         json={'epoch': claimed.json()['lease_epoch'], 'summary': 'done'})
    assert submitted.status_code == 200
    assert preview(env, owner_headers, t, 'review_task', accepted=False).status_code == 422
    approved = preview(env, owner_headers, t, 'review_task', accepted=True)
    assert approved.status_code == 201
    assert confirm(env, owner_headers, approved.json()['id']).json()['result']['status'] == 'completed'


def test_reviews_are_independent_nonowner_project_scoped_and_blind(env, owner_headers):
    p = project(env, owner_headers)
    other = project(env, owner_headers)
    dec = decision(env, owner_headers, p)
    reviewer = agent(env, owner_headers, p, 'Reviewer')
    outsider = agent(env, owner_headers, other, 'Outsider')
    evidence = {'stance': 'oppose', 'reasoning': 'Independent source contradicts this approach.',
                'evidence': ['document:42']}
    assert env.post(f'/api/v1/decisions/{dec}/reviews', headers=outsider['headers'], json=evidence).status_code == 404
    result = env.post(f'/api/v1/decisions/{dec}/reviews', headers=reviewer['headers'], json=evidence)
    assert result.status_code == 201, result.text
    assert result.json()['evidence_verified'] is False
    assert env.post(f'/api/v1/decisions/{dec}/reviews', headers=reviewer['headers'], json=evidence).status_code == 409
    assert env.get(f'/api/v1/decisions/{dec}/reviews', headers=reviewer['headers']).status_code == 403
    reviews = env.get(f'/api/v1/decisions/{dec}/reviews', headers=owner_headers).json()
    assert len(reviews) == 1 and reviews[0]['stance'] == 'oppose'
    assert env.post(f'/api/v1/decisions/{dec}/approve', headers=owner_headers).status_code == 409
    assert env.post(f'/api/v1/decisions/{dec}/resolve', headers=reviewer['headers'], json={'owner_note': 'We accept this risk'}).status_code == 403
    assert env.post(f'/api/v1/decisions/{dec}/resolve', headers=owner_headers, json={'owner_note': 'short'}).status_code == 409
    done = env.post(f'/api/v1/decisions/{dec}/resolve', headers=owner_headers,
                    json={'owner_note': 'The independent counter-evidence was reviewed and accepted.'})
    assert done.status_code == 200, done.text
    assert done.json()['dissent_count'] == 1
    assert len(env.get(f'/api/v1/decisions/{dec}/reviews', headers=reviewer['headers']).json()) == 1
    assert env.post(f'/api/v1/decisions/{dec}/reviews', headers=reviewer['headers'], json=evidence).status_code == 409


def test_supporting_evidence_does_not_force_owner_resolution_note(env, owner_headers):
    p = project(env, owner_headers)
    dec = decision(env, owner_headers, p)
    reviewer = agent(env, owner_headers, p)
    r = env.post(f'/api/v1/decisions/{dec}/reviews', headers=reviewer['headers'],
                 json={'stance': 'support', 'reasoning': 'Independent tests confirm the expected behavior.'})
    assert r.status_code == 201
    approved = env.post(f'/api/v1/decisions/{dec}/approve', headers=owner_headers)
    assert approved.status_code == 200, approved.text


def test_review_validation_and_no_owner_impersonation(env, owner_headers):
    p = project(env, owner_headers)
    dec = decision(env, owner_headers, p)
    assert env.post(f'/api/v1/decisions/{dec}/reviews', headers=owner_headers,
                    json={'stance': 'support', 'reasoning': 'Sounds good to me.'}).status_code == 403
    reviewer = agent(env, owner_headers, p)
    assert env.post(f'/api/v1/decisions/{dec}/reviews', headers=reviewer['headers'],
                    json={'stance': 'support', 'reasoning': 'too short'}).status_code == 422
    assert env.post(f'/api/v1/decisions/{dec}/reviews', headers=reviewer['headers'],
                    json={'stance': 'support', 'reasoning': 'Enough reasoning here.', 'evidence': ['']}).status_code == 422
