"""End to end: a Fleet driving real agents over HTTP on localhost."""

import os
import signal
import subprocess
import sys
import threading

import pytest
import requests

from conftest import (
    TOKEN, TREE, alive, auth, free_port, read_pid, wait_until)
from roost.manager import (
    AgentClient, AgentError, AgentUnreachable, Fleet, Host, HostRegistry,
    NoCapacity, RoostError)


def make_registry(port_a, port_b, dead_port):
    # `dead` has the most free slots, so it is always tried first.
    return HostRegistry([
        Host('dead', '127.0.0.1', capacity=5, port=dead_port),
        Host('a', '127.0.0.1', capacity=2, port=port_a),
        Host('b', '127.0.0.1', capacity=2, port=port_b),
    ], token=TOKEN)


def counts(fleet):
    return {host.name: host.count for host in fleet.registry.all()}


def test_fleet_end_to_end(start_agent):
    port_a, port_b, dead_port = start_agent('a'), start_agent('b'), free_port()
    agent_a = AgentClient(f'http://127.0.0.1:{port_a}', TOKEN)
    agent_b = AgentClient(f'http://127.0.0.1:{port_b}', TOKEN)
    fleet = Fleet(make_registry(port_a, port_b, dead_port))

    # Fail over from the unreachable host, which is then marked offline.
    placement = fleet.deploy('sleeper', 'u1', {'port': 5001})
    assert placement.host.name == 'a'
    assert placement.unit['alive'] and placement.unit['params'] == {'port': 5001}
    assert fleet.registry.get('dead').online is False

    # Least-loaded placement, then capacity.
    assert [fleet.deploy('sleeper', name).host.name
            for name in ('u2', 'u3', 'u4')] == ['b', 'a', 'b']
    assert counts(fleet) == {'dead': 0, 'a': 2, 'b': 2}
    with pytest.raises(NoCapacity):
        fleet.deploy('sleeper', 'u5')
    assert sorted(u['name'] for u in agent_a.units()) == ['u1', 'u3']
    assert sorted(u['name'] for u in agent_b.units()) == ['u2', 'u4']

    # Removing frees the slot, on the fleet's books and on the agent.
    fleet.remove('u1')
    assert counts(fleet)['a'] == 1 and 'u1' not in fleet.units()
    assert [u['name'] for u in agent_a.units()] == ['u3']
    with pytest.raises(KeyError):
        fleet.remove('u1')

    # A rejected request rolls back and is not retried on another host.
    with pytest.raises(RoostError, match='already deployed'):
        fleet.deploy('sleeper', 'u3')
    for unit_type, params, status in [('sleeper', {'port': 'x'}, 400),
                                      ('nope', {}, 404),
                                      ('crasher', {}, 500)]:
        with pytest.raises(AgentError) as excinfo:
            fleet.deploy(unit_type, 'u5', params)
        assert excinfo.value.status == status
    assert counts(fleet) == {'dead': 0, 'a': 1, 'b': 2}
    assert 'u5' not in fleet.units()
    assert fleet.deploy('sleeper', 'u5').host.name == 'a'

    # A fresh manager knows nothing...
    fresh = Fleet(make_registry(port_a, port_b, dead_port))
    assert fresh.units() == {} and counts(fresh)['a'] == 0
    # ...so it trips over a name the agent already runs (409, no failover)...
    with pytest.raises(AgentError) as excinfo:
        fresh.deploy('sleeper', 'u3')
    assert excinfo.value.status == 409
    assert fresh.units() == {} and len(agent_b.units()) == 2
    # ...until sync() recovers placement, counts and online flags.
    fresh.sync()
    assert fresh.units() == fleet.units() == {
        'u2': 'b', 'u3': 'a', 'u4': 'b', 'u5': 'a'}
    assert counts(fresh) == {'dead': 0, 'a': 2, 'b': 2}
    assert [h.online for h in fresh.registry.all()] == [False, True, True]
    with pytest.raises(NoCapacity):
        fresh.deploy('sleeper', 'u6')
    fresh.remove('u2')
    assert [u['name'] for u in agent_b.units()] == ['u4']
    assert fresh.deploy('sleeper', 'u6').host.name == 'b'


def test_all_hosts_unreachable(start_agent):
    fleet = Fleet(HostRegistry(
        [Host('x', '127.0.0.1', 1, port=free_port()),
         Host('y', '127.0.0.1', 1, port=free_port())], token=TOKEN))
    with pytest.raises(NoCapacity) as excinfo:
        fleet.deploy('sleeper', 'u1')
    assert isinstance(excinfo.value.__cause__, AgentUnreachable)
    assert fleet.units() == {}
    assert [h.count for h in fleet.registry.all()] == [0, 0]


def test_sync_keeps_units_of_an_unreachable_host_and_brings_it_back(start_agent):
    port = start_agent('a')
    registry = HostRegistry([Host('a', '127.0.0.1', 2, port=port)], token=TOKEN)
    fleet = Fleet(registry)
    fleet.deploy('sleeper', 'u1')

    registry.get('a').port = free_port()        # "unplug" the host
    fleet._clients['a'] = AgentClient(registry.get('a').base_url, TOKEN)
    fleet.sync()
    assert registry.get('a').online is False
    assert fleet.units() == {'u1': 'a'} and registry.get('a').count == 1
    with pytest.raises(AgentUnreachable):
        fleet.remove('u1')
    assert fleet.units() == {'u1': 'a'}

    fleet._clients['a'] = AgentClient(f'http://127.0.0.1:{port}', TOKEN)
    fleet.sync()
    assert registry.get('a').online is True
    fleet.remove('u1')
    assert registry.get('a').count == 0


def test_concurrent_deploys_never_exceed_capacity(start_agent):
    port_a, port_b = start_agent('a'), start_agent('b')
    fleet = Fleet(HostRegistry([Host('a', '127.0.0.1', 2, port=port_a),
                                Host('b', '127.0.0.1', 2, port=port_b)],
                               token=TOKEN))
    results = []

    def deploy(i):
        try:
            results.append(fleet.deploy('sleeper', f'u{i}').host.name)
        except NoCapacity:
            results.append(None)

    threads = [threading.Thread(target=deploy, args=(i,)) for i in range(7)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(r for r in results if r) == ['a', 'a', 'b', 'b']
    assert results.count(None) == 3
    assert len(fleet.units()) == 4


def test_wrong_token_is_a_rejection_not_a_failover(start_agent):
    port = start_agent('a')
    fleet = Fleet(HostRegistry([Host('a', '127.0.0.1', 1, port=port)],
                               token='wrong'))
    with pytest.raises(AgentError) as excinfo:
        fleet.deploy('sleeper', 'u1')
    assert excinfo.value.status == 401
    assert fleet.registry.get('a').online is True


# --- the real `roost-agent` process ----------------------------------------

def run_agent(tmp_path, port, token=TOKEN):
    config = tmp_path / 'units.yaml'
    config.write_text(
        'units:\n'
        '  tree:\n'
        f'    command: ["{sys.executable}", "{tmp_path}/tree.py", "{{path}}"]\n'
        '    params: {path: {type: str, required: true}}\n'
        '    startup_grace_s: 0.2\n'
        '    term_timeout_s: 2\n')
    env = {**os.environ, 'ROOST_TOKEN': token, 'ROOST_PORT': str(port),
           'ROOST_LOG_DIR': str(tmp_path / 'agent-logs')}
    return subprocess.Popen(
        [sys.executable, '-m', 'roost.agent', '--config', str(config)], env=env)


def test_agent_refuses_to_start_without_a_token(tmp_path):
    agent = run_agent(tmp_path, free_port(), token='')
    assert agent.wait(timeout=10) != 0


def test_sigterm_tears_down_every_unit(tmp_path):
    (tmp_path / 'tree.py').write_text(TREE)
    port = free_port()
    agent = run_agent(tmp_path, port)
    try:
        client = AgentClient(f'http://127.0.0.1:{port}', TOKEN)

        def up():
            try:
                return client.health() == {'status': 'ok'}
            except AgentUnreachable:
                return False
        assert wait_until(up, timeout=10)
        assert requests.get(f'http://127.0.0.1:{port}/units',
                            headers=auth('wrong')).status_code == 401

        pids = []
        for name in ('u1', 'u2'):
            pidfile = tmp_path / f'{name}.pid'
            pids.append(client.spawn('tree', name, {'path': str(pidfile)})['pid'])
            pids.append(read_pid(pidfile))
        assert all(alive(pid) for pid in pids)

        agent.send_signal(signal.SIGTERM)
        assert agent.wait(timeout=10) == 0
        assert wait_until(lambda: not any(alive(pid) for pid in pids))
    finally:
        agent.kill()
