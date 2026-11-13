"""Protocol and security tests for the A2A v0.3 JSON-RPC gateway."""
import uuid

from test_workflow import create_agent, create_project, enroll


def rpc(env, headers, method, params, *, req_id=1):
    response = env.post('/a2a', headers=headers, json={
        'jsonrpc': '2.0', 'id': req_id, 'method': method, 'params': params
    })
    assert response.status_code == 200, response.text
    obj = response.json()
    assert obj['jsonrpc'] == '2.0' and obj['id'] == req_id
    return obj


def send(project, *, mid=None, risk='R1', text='Build the report', **metadata):
    return {
        'message': {
            'kind': 'message', 'role': 'user', 'messageId': mid or str(uuid.uuid4()),
            'parts': [{'kind': 'text', 'text': text}],
        },
        'metadata': {'projectId': project, 'title': 'Report', 'acceptance': 'Evidence included', 'risk': risk, **metadata},
    }


def test_agent_card_is_explicitly_configured_and_not_host_header_reflected(env, monkeypatch):
    monkeypatch.delenv('OTTERNEST_A2A_BASE_URL', raising=False)
    assert env.get('/.well-known/agent-card.json').status_code == 503
    monkeypatch.setenv('OTTERNEST_A2A_BASE_URL', 'http://localhost:8000')
    response = env.get('/.well-known/agent-card.json', headers={'Host': 'evil.example'})
    assert response.status_code == 200
    card = response.json()
    assert card['url'] == 'http://localhost:8000/a2a'
    assert card['protocolVersion'] == '0.3.0'
    assert card['capabilities']['streaming'] is False
    assert card['capabilities']['pushNotifications'] is False
    assert card['securitySchemes']['bearer']['scheme'] == 'bearer'
    assert 'evil.example' not in str(card)
    monkeypatch.setenv('OTTERNEST_A2A_BASE_URL', 'http://public.example')
    assert env.get('/.well-known/agent-card.json').status_code == 503
    monkeypatch.setenv('OTTERNEST_A2A_BASE_URL', 'https://public.example/?token=secret')
    assert env.get('/.well-known/agent-card.json').status_code == 503


def test_a2a_send_replay_get_and_end_to_end_acceptance(env, owner_headers):
    p = create_project(env, owner_headers)
    work = send(p, mid='external-msg-1')
    assert env.post('/a2a', json={'jsonrpc': '2.0', 'id': 1, 'method': 'message/send', 'params': work}).status_code == 401
    first = rpc(env, owner_headers, 'message/send', work, req_id='hello')['result']
    assert first['kind'] == 'task' and first['contextId'] == p
    assert first['status']['state'] == 'submitted'
    second = rpc(env, owner_headers, 'message/send', work)['result']
    assert second['id'] == first['id']  # dedup, not just repeat idempotent result text
    assert len(env.get(f'/api/v1/projects/{p}/tasks', headers=owner_headers).json()) == 1
    get = rpc(env, owner_headers, 'tasks/get', {'id': first['id'], 'historyLength': 1})['result']
    assert get['history'][0]['messageId'] == 'external-msg-1'
    assert get['history'][0]['parts'][0]['text'] == 'Build the report'

    a = create_agent(env, owner_headers)
    enroll(env, owner_headers, p, a['id'])
    ah = {'Authorization': f"Bearer {a['token']}"}
    claimed = env.post(f"/api/v1/tasks/{first['id']}/claim", headers=ah, json={}).json()
    assert rpc(env, owner_headers, 'tasks/get', {'id': first['id']})['result']['status']['state'] == 'working'
    assert env.post(f"/api/v1/tasks/{first['id']}/submit", headers=ah,
                    json={'epoch': claimed['lease_epoch'], 'summary': 'Validated work'}).status_code == 200
    assert rpc(env, owner_headers, 'tasks/get', {'id': first['id']})['result']['status']['state'] == 'input-required'
    assert env.post(f"/api/v1/tasks/{first['id']}/review", headers=owner_headers,
                    json={'accepted': True}).status_code == 200
    done = rpc(env, owner_headers, 'tasks/get', {'id': first['id']})['result']
    assert done['status']['state'] == 'completed'
    assert done['artifacts'][0]['parts'][0]['text'] == 'Validated work'
    assert rpc(env, owner_headers, 'tasks/cancel', {'id': first['id']})['error']['code'] == -32002
    events = env.get(f'/api/v1/projects/{p}/events', headers=owner_headers).json()
    assert [e['kind'] for e in events].count('a2a.task_created') == 1


def test_a2a_idempotency_rejects_changed_payload(env, owner_headers):
    p = create_project(env, owner_headers)
    body = send(p, mid='one-id')
    first = rpc(env, owner_headers, 'message/send', body)['result']
    body['message']['parts'][0]['text'] = 'Execute another operation'
    assert rpc(env, owner_headers, 'message/send', body)['error']['code'] == -32602
    assert len(env.get(f'/api/v1/projects/{p}/tasks', headers=owner_headers).json()) == 1
    assert rpc(env, owner_headers, 'tasks/get', {'id': first['id']})['result']['status']['state'] == 'submitted'


def test_a2a_manager_only_and_cross_project_hidden(env, owner_headers):
    a = create_project(env, owner_headers, 'A')
    b = create_project(env, owner_headers, 'B')
    worker = create_agent(env, owner_headers, 'Worker')
    manager = create_agent(env, owner_headers, 'Manager')
    outside = create_agent(env, owner_headers, 'Outside')
    enroll(env, owner_headers, a, worker['id'])
    for bot, role in [(manager, 'manager'), (outside, 'worker')]:
        r = env.post(f'/api/v1/projects/{a if bot is manager else b}/members', headers=owner_headers,
                     json={'agent_id': bot['id'], 'role': role})
        assert r.status_code == 201
    worker_h = {'Authorization': 'Bearer ' + worker['token']}
    manager_h = {'Authorization': 'Bearer ' + manager['token']}
    outside_h = {'Authorization': 'Bearer ' + outside['token']}
    payload = send(a, mid='scoped')
    assert rpc(env, worker_h, 'message/send', payload)['error']['code'] == -32001
    assert rpc(env, outside_h, 'message/send', payload)['error']['code'] == -32001
    assert rpc(env, manager_h, 'message/send', send(a, risk='R3'))['error']['code'] == -32004
    result = rpc(env, manager_h, 'message/send', payload)['result']
    task_id = result['id']
    assert rpc(env, worker_h, 'tasks/get', {'id': task_id})['error']['code'] == -32001
    assert rpc(env, outside_h, 'tasks/get', {'id': task_id})['error']['code'] == -32001
    assert rpc(env, worker_h, 'tasks/cancel', {'id': task_id})['error']['code'] == -32001
    assert rpc(env, manager_h, 'tasks/cancel', {'id': task_id})['result']['status']['state'] == 'canceled'


def test_a2a_cancel_does_not_pretend_remote_worker_stopped(env, owner_headers):
    p = create_project(env, owner_headers)
    task_id = rpc(env, owner_headers, 'message/send', send(p))['result']['id']
    agent = create_agent(env, owner_headers)
    enroll(env, owner_headers, p, agent['id'])
    ah = {'Authorization': 'Bearer ' + agent['token']}
    assert env.post(f'/api/v1/tasks/{task_id}/claim', headers=ah, json={}).status_code == 200
    fail = rpc(env, owner_headers, 'tasks/cancel', {'id': task_id})
    assert fail['error']['code'] == -32002
    assert env.get(f'/api/v1/tasks/{task_id}', headers=owner_headers).json()['status'] == 'running'


def test_a2a_high_risk_owner_requires_explicit_approval(env, owner_headers):
    p = create_project(env, owner_headers)
    task = rpc(env, owner_headers, 'message/send', send(p, risk='R3'))['result']
    assert task['status']['state'] == 'input-required'
    assert env.get(f"/api/v1/tasks/{task['id']}", headers=owner_headers).json()['approval_granted'] is False


def test_a2a_invalid_jsonrpc_inputs_fail_closed(env, owner_headers):
    p = create_project(env, owner_headers)
    for value, code in [
        ({'jsonrpc': '2.0', 'method': 'message/send', 'params': send(p)}, -32600),
        ({'jsonrpc': '2.0', 'id': True, 'method': 'tasks/get'}, -32600),
        ({'jsonrpc': '1.0', 'id': 1, 'method': 'tasks/get'}, -32600),
        ({'jsonrpc': '2.0', 'id': 1, 'method': 'evil'}, -32601),
        ({'jsonrpc': '2.0', 'id': 1, 'method': 'tasks/get', 'params': []}, -32602),
        ({'jsonrpc': '2.0', 'id': 1, 'method': 'message/send', 'params': send(p, text='')}, -32602),
    ]:
        resp = env.post('/a2a', headers=owner_headers, json=value)
        assert resp.status_code == 200 and resp.json()['error']['code'] == code, resp.text
    assert env.post('/a2a', headers=owner_headers, content=b'{nonsense').json()['error']['code'] == -32700
    assert env.post('/a2a', headers=owner_headers, content=b'x' * 33000).json()['error']['code'] == -32600
    assert len(env.get(f'/api/v1/projects/{p}/tasks', headers=owner_headers).json()) == 0


def test_a2a_unsupported_modalities_and_features(env, owner_headers):
    p = create_project(env, owner_headers)
    body = send(p)
    body['message']['parts'] = [{'kind': 'file', 'file': {'uri': 'https://example.com/secret'}}]
    assert rpc(env, owner_headers, 'message/send', body)['error']['code'] == -32005
    body = send(p)
    body['configuration'] = {'blocking': True}
    assert rpc(env, owner_headers, 'message/send', body)['error']['code'] == -32004
    body['configuration'] = {'pushNotificationConfig': {}}
    assert rpc(env, owner_headers, 'message/send', body)['error']['code'] == -32003
    assert rpc(env, owner_headers, 'message/stream', body)['error']['code'] == -32601


def test_a2a_manager_queue_is_bounded(env, owner_headers):
    p = create_project(env, owner_headers)
    mgr = create_agent(env, owner_headers)
    r = env.post(f'/api/v1/projects/{p}/members', headers=owner_headers,
                 json={'agent_id': mgr['id'], 'role': 'manager'})
    assert r.status_code == 201
    headers = {'Authorization': 'Bearer ' + mgr['token']}
    for i in range(25):
        assert 'result' in rpc(env, headers, 'message/send', send(p, mid=f'msg-{i}'))
    assert rpc(env, headers, 'message/send', send(p, mid='msg-over-cap'))['error']['code'] == -32004
    assert 'result' in rpc(env, headers, 'message/send', send(p, mid='msg-0'))  # retries must still work
    assert len(env.get(f'/api/v1/projects/{p}/tasks', headers=owner_headers).json()) == 25
