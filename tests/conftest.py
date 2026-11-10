"""Shared helpers: fake controller scripts and in-process agents."""

import socket
import sys
import threading
import time

import pytest
from werkzeug.serving import make_server

from roost.agent.app import create_app
from roost.agent.supervisor import Supervisor
from roost.agent.units import UnitType

TOKEN = 'test-token'

# argv: <pidfile>. Forks a grandchild that records its pid; both sleep.
TREE = '''
import os, sys, time
if os.fork() == 0:
    with open(sys.argv[1], 'w') as fh:
        fh.write(str(os.getpid()))
time.sleep(300)
'''

# argv: <readyfile>. Ignores SIGTERM, then says so.
STUBBORN = '''
import signal, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
open(sys.argv[1], 'w').close()
time.sleep(300)
'''


def make_types(tmp_path) -> dict:
    """Unit types backed by tiny real scripts, with short timeouts."""
    (tmp_path / 'tree.py').write_text(TREE)
    (tmp_path / 'stubborn.py').write_text(STUBBORN)

    def unit_type(name, command, params=None):
        return UnitType.from_config(name, {
            'command': command, 'params': params or {},
            'startup_grace_s': 0.2, 'term_timeout_s': 1})

    py = sys.executable
    path = {'path': {'type': 'str', 'required': True}}
    return {
        'sleeper': unit_type(
            'sleeper', [py, '-c', 'import time; time.sleep(300)', '{name}'],
            {'port': {'type': 'int', 'default': 0}}),
        'tree': unit_type('tree', [py, str(tmp_path / 'tree.py'), '{path}'],
                          path),
        'stubborn': unit_type(
            'stubborn', [py, str(tmp_path / 'stubborn.py'), '{path}'], path),
        'crasher': unit_type('crasher', [py, '-c', 'raise SystemExit(3)']),
        'missing': unit_type('missing', ['/nonexistent/roost-test-binary']),
    }


def alive(pid: int) -> bool:
    """True if the pid is a running (not zombie) process."""
    try:
        with open(f'/proc/{pid}/stat') as fh:
            return fh.read().rsplit(')', 1)[1].split()[0] != 'Z'
    except OSError:
        return False


def wait_until(predicate, timeout=5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def read_pid(pidfile) -> int:
    """Wait for a fake controller to record a pid, then return it."""
    assert wait_until(lambda: pidfile.exists() and pidfile.read_text())
    return int(pidfile.read_text())


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def auth(token=TOKEN) -> dict:
    return {'Authorization': f'Bearer {token}'}


@pytest.fixture
def supervisor(tmp_path):
    sup = Supervisor(make_types(tmp_path), str(tmp_path / 'logs'))
    yield sup
    sup.teardown_all()


@pytest.fixture
def start_agent(tmp_path):
    """Factory: start a real HTTP agent on an ephemeral localhost port."""
    started = []

    def start(label: str):
        sup = Supervisor(make_types(tmp_path), str(tmp_path / f'logs-{label}'))
        server = make_server('127.0.0.1', 0, create_app(sup, TOKEN),
                             threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        started.append((server, sup))
        return server.server_port

    yield start
    for server, sup in started:
        server.shutdown()
        sup.teardown_all()


@pytest.fixture(autouse=True)
def no_ambient_token(monkeypatch):
    monkeypatch.delenv('ROOST_TOKEN', raising=False)
