#!/usr/bin/env python3
"""Try roost on a laptop: two local agents, one manager, no robots.

    pip install -e .
    python examples/minimal/demo.py
"""

import os
import secrets
import subprocess
import sys
import tempfile
import time

from roost.manager import AgentUnreachable, Fleet, HostRegistry, NoCapacity

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ['ROOST_TOKEN'] = secrets.token_hex(16)   # shared by both sides
log_dir = tempfile.mkdtemp(prefix='roost-demo-')


def show(fleet):
    for host in fleet.registry.all():
        units = sorted(u for u, h in fleet.units().items() if h == host.name)
        print(f'    {host.name}: {host.count}/{host.capacity} {units}')


registry = HostRegistry.from_yaml(os.path.join(HERE, 'hosts.yaml'))
agents = [
    subprocess.Popen(
        [sys.executable, '-m', 'roost.agent', '--config', 'units.yaml',
         '--port', str(host.port),
         '--log-dir', os.path.join(log_dir, host.name)],
        cwd=HERE)
    for host in registry.all()
]
try:
    fleet = Fleet(registry)
    for _ in range(50):                 # wait for both agents to come up
        fleet.sync()
        if all(host.online for host in registry.all()):
            break
        time.sleep(0.1)
    else:
        raise AgentUnreachable('the demo agents did not start')

    print('\n== deploy five units onto four slots')
    for name in ('robot-1', 'robot-2', 'robot-3', 'robot-4', 'robot-5'):
        try:
            placement = fleet.deploy('demo', name, {'interval': 0.5})
            print(f'    {name} -> {placement.host.name} '
                  f'(pid {placement.unit["pid"]})')
        except NoCapacity as exc:
            print(f'    {name} -> refused: {exc}')
    show(fleet)

    print('\n== remove robot-1, then robot-5 fits')
    fleet.remove('robot-1')
    print(f'    robot-5 -> {fleet.deploy("demo", "robot-5").host.name}')
    show(fleet)

    print('\n== a restarted manager recovers its state with sync()')
    restarted = Fleet(HostRegistry.from_yaml(os.path.join(HERE, 'hosts.yaml')))
    restarted.sync()
    show(restarted)

    print(f'\n== stopping the agents; each tears down its units '
          f'(logs: {log_dir})')
finally:
    for agent in agents:
        agent.terminate()
    for agent in agents:
        agent.wait()
