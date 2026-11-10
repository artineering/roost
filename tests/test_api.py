"""HTTP API: auth and status codes."""

import pytest

from conftest import TOKEN, auth
from roost.agent.app import create_app


@pytest.fixture
def client(supervisor):
    return create_app(supervisor, TOKEN).test_client()


def test_app_refuses_an_empty_token(supervisor):
    with pytest.raises(ValueError):
        create_app(supervisor, '')


@pytest.mark.parametrize('method, path', [
    ('get', '/health'), ('get', '/types'), ('get', '/units'),
    ('post', '/units'), ('delete', '/units/u1'), ('get', '/no-such-route'),
])
@pytest.mark.parametrize('headers', [
    {}, auth('wrong'), {'Authorization': TOKEN},
    {'Authorization': f'Bearer {TOKEN}x'}, {'Authorization': 'Bearer té'},
])
def test_every_route_requires_the_token(client, method, path, headers):
    resp = getattr(client, method)(path, headers=headers)
    assert resp.status_code == 401
    assert resp.get_json() == {'error': 'unauthorized'}


def test_health_and_types(client):
    assert client.get('/health', headers=auth()).get_json() == {'status': 'ok'}
    types = client.get('/types', headers=auth()).get_json()['types']
    assert types['sleeper'] == {
        'params': {'port': {'type': 'int', 'required': False, 'default': 0}}}
    assert 'command' not in types['sleeper']


def test_unit_lifecycle(client):
    body = {'type': 'sleeper', 'name': 'u1', 'params': {'port': 7}}
    resp = client.post('/units', json=body, headers=auth())
    assert resp.status_code == 201
    unit = resp.get_json()['unit']
    assert unit['name'] == 'u1' and unit['alive'] and unit['params'] == {'port': 7}

    listed = client.get('/units', headers=auth()).get_json()['units']
    assert [u['name'] for u in listed] == ['u1'] and listed[0]['alive']

    assert client.post('/units', json=body, headers=auth()).status_code == 409

    resp = client.delete('/units/u1', headers=auth())
    assert resp.status_code == 200 and resp.get_json() == {'stopped': 'u1'}
    assert client.get('/units', headers=auth()).get_json() == {'units': []}
    assert client.delete('/units/u1', headers=auth()).status_code == 404


@pytest.mark.parametrize('body', [
    None,
    [],
    {'name': 'u1'},
    {'type': 'sleeper'},
    {'type': 'sleeper', 'name': 'bad/name'},
    {'type': 'sleeper', 'name': 'u1', 'params': []},
    {'type': 'sleeper', 'name': 'u1', 'params': {'port': 'x'}},
    {'type': 'sleeper', 'name': 'u1', 'params': {'command': 'rm -rf /'}},
])
def test_invalid_requests_are_400(client, body):
    resp = client.post('/units', json=body, headers=auth())
    assert resp.status_code == 400 and 'error' in resp.get_json()
    assert client.get('/units', headers=auth()).get_json() == {'units': []}


def test_unknown_type_is_404(client):
    resp = client.post('/units', json={'type': 'nope', 'name': 'u1'},
                       headers=auth())
    assert resp.status_code == 404


def test_failed_start_is_500_with_log_path(client):
    resp = client.post('/units', json={'type': 'crasher', 'name': 'u1'},
                       headers=auth())
    assert resp.status_code == 500
    assert resp.get_json()['log_path'].endswith('u1.log')
