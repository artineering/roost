"""Process lifecycle, using real subprocesses."""

import os
import time

import pytest

from conftest import alive, read_pid, wait_until
from roost.agent.supervisor import AlreadyRunning, StartFailed


def test_spawn_lists_unit_and_writes_log(supervisor, tmp_path):
    info = supervisor.spawn('sleeper', 'u1', {'port': 5001})
    assert info['alive'] and alive(info['pid'])
    assert info['type'] == 'sleeper' and info['params'] == {'port': 5001}
    assert info['log_path'] == str(tmp_path / 'logs' / 'u1.log')
    assert os.path.exists(info['log_path'])
    assert [u['name'] for u in supervisor.units()] == ['u1']


def test_unit_leads_its_own_session(supervisor):
    pid = supervisor.spawn('sleeper', 'u1', {})['pid']
    assert os.getpgid(pid) == pid and os.getsid(pid) == pid
    assert os.getpgid(pid) != os.getpgid(0)


def test_duplicate_name_is_refused_while_alive(supervisor):
    supervisor.spawn('sleeper', 'u1', {})
    with pytest.raises(AlreadyRunning):
        supervisor.spawn('sleeper', 'u1', {})
    assert len(supervisor.units()) == 1


def test_invalid_request_spawns_nothing(supervisor):
    with pytest.raises(KeyError):
        supervisor.spawn('nope', 'u1', {})
    with pytest.raises(ValueError):
        supervisor.spawn('sleeper', '../u1', {})
    with pytest.raises(ValueError):
        supervisor.spawn('sleeper', 'u1', {'port': 'x'})
    assert supervisor.units() == []


@pytest.mark.parametrize('type_name', ['crasher', 'missing'])
def test_startup_failure_is_reported_and_forgotten(supervisor, type_name):
    with pytest.raises(StartFailed) as excinfo:
        supervisor.spawn(type_name, 'u1', {})
    assert excinfo.value.log_path.endswith('u1.log')
    assert supervisor.units() == []
    # The name is free again.
    assert supervisor.spawn('sleeper', 'u1', {})['alive']


def test_teardown_kills_the_whole_process_group(supervisor, tmp_path):
    pidfile = tmp_path / 'grandchild.pid'
    root = supervisor.spawn('tree', 'u1', {'path': str(pidfile)})['pid']
    grandchild = read_pid(pidfile)
    assert alive(grandchild) and os.getpgid(grandchild) == root

    assert supervisor.teardown('u1') is True
    assert supervisor.units() == []
    assert not alive(root)
    assert wait_until(lambda: not alive(grandchild))


def test_teardown_escalates_to_sigkill(supervisor, tmp_path):
    ready = tmp_path / 'ready'
    pid = supervisor.spawn('stubborn', 'u1', {'path': str(ready)})['pid']
    assert wait_until(ready.exists)     # SIGTERM is now being ignored

    started = time.monotonic()
    assert supervisor.teardown('u1') is True
    elapsed = time.monotonic() - started
    assert elapsed >= 1.0               # waited out term_timeout_s first
    assert not alive(pid)


def test_teardown_unknown_unit(supervisor):
    assert supervisor.teardown('nope') is False


def test_dead_unit_stays_listed_until_replaced(supervisor):
    pid = supervisor.spawn('sleeper', 'u1', {})['pid']
    os.kill(pid, 9)
    assert wait_until(lambda: not supervisor.units()[0]['alive'])
    assert supervisor.spawn('sleeper', 'u1', {})['pid'] != pid


def test_teardown_all(supervisor):
    pids = [supervisor.spawn('sleeper', f'u{i}', {})['pid'] for i in range(3)]
    supervisor.teardown_all()
    assert supervisor.units() == []
    assert not any(alive(pid) for pid in pids)
