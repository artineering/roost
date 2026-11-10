"""Host registry: loading and least-loaded placement."""

import pytest

from roost.manager import Host, HostRegistry, NoCapacity

CONFIG = {
    'agent': {'token': 'from-file'},
    'hosts': [
        {'name': 'a', 'host': '10.0.0.1', 'capacity': 2},
        {'name': 'b', 'host': '10.0.0.2', 'port': 9000, 'capacity': 2},
        {'name': 'c', 'host': '10.0.0.3', 'capacity': 4, 'types': ['arm']},
        {'name': 'd', 'host': '10.0.0.4', 'capacity': 9, 'online': False},
    ],
}


def test_from_yaml(tmp_path, monkeypatch):
    path = tmp_path / 'hosts.yaml'
    path.write_text(
        'agent: {token: from-file}\n'
        'hosts:\n'
        '  - {name: a, host: 10.0.0.1, capacity: 2}\n'
        '  - {name: b, host: 10.0.0.2, port: 9000, capacity: 3}\n')
    registry = HostRegistry.from_yaml(str(path))
    assert [h.name for h in registry.all()] == ['a', 'b']
    assert registry.get('a').base_url == 'http://10.0.0.1:8181'
    assert registry.get('b').base_url == 'http://10.0.0.2:9000'
    assert registry.get('b').free == 3
    assert registry.token == 'from-file'

    monkeypatch.setenv('ROOST_TOKEN', 'from-env')
    assert HostRegistry.from_yaml(str(path)).token == 'from-env'


def test_duplicate_host_names_are_rejected():
    with pytest.raises(ValueError):
        HostRegistry([Host('a', 'x', 1), Host('a', 'y', 1)])


def test_least_loaded_with_config_order_tie_break():
    registry = HostRegistry.from_config(CONFIG)
    order = []
    for _ in range(4):
        host = registry.select('wheel')
        host.count += 1
        order.append(host.name)
    assert order == ['a', 'b', 'a', 'b']
    with pytest.raises(NoCapacity):
        registry.select('wheel')


def test_type_filter_and_any_type():
    registry = HostRegistry.from_config(CONFIG)
    assert registry.select('arm').name == 'c'       # most free, accepts arm
    assert registry.select().name == 'c'            # no type: any host
    assert registry.select('wheel').name == 'a'     # c does not accept wheel


def test_disabled_offline_and_full_hosts_are_skipped():
    registry = HostRegistry.from_config(CONFIG)
    assert registry.get('d').enabled is False
    registry.get('c').online = False
    registry.get('a').count = 2
    assert registry.select().name == 'b'
    registry.get('b').online = False
    with pytest.raises(NoCapacity):
        registry.select()
