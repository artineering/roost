"""Unit type config validation and command rendering."""

import pytest

from roost.agent.units import UnitType, load_unit_types

ROBOT = {
    'command': ['run', '{name}', '{port}', '{external_localization}',
                '--rate={rate}'],
    'params': {
        'port': {'type': 'int', 'required': True},
        'external_localization': {'type': 'bool', 'default': False},
        'rate': {'type': 'float', 'default': 1.5},
        'label': {'type': 'str', 'default': 'x'},
    },
    'env': {'UNIT': '{name}-{port}', 'STATIC': 'yes'},
}


def test_render_substitutes_name_params_and_defaults():
    unit_type = UnitType.from_config('robot', ROBOT)
    argv, env = unit_type.render('robot-1', {'port': 5001})
    assert argv == ['run', 'robot-1', '5001', 'false', '--rate=1.5']
    assert env == {'UNIT': 'robot-1-5001', 'STATIC': 'yes'}

    argv, _ = unit_type.render(
        'robot-1', {'port': 5001, 'external_localization': True, 'rate': 2})
    assert argv == ['run', 'robot-1', '5001', 'true', '--rate=2']


@pytest.mark.parametrize('params, message', [
    ({}, 'missing required'),
    ({'port': '5001'}, 'must be a int'),
    ({'port': True}, 'must be a int'),
    ({'port': 1, 'external_localization': 'yes'}, 'must be a bool'),
    ({'port': 1, 'rate': 'fast'}, 'must be a float'),
    ({'port': 1, 'label': 7}, 'must be a str'),
    ({'port': 1, 'bogus': 1}, 'unknown params'),
])
def test_render_rejects_bad_params(params, message):
    unit_type = UnitType.from_config('robot', ROBOT)
    with pytest.raises(ValueError, match=message):
        unit_type.render('robot-1', params)


@pytest.mark.parametrize('name', [
    '', '../etc/passwd', 'a/b', '.hidden', '-flag', 'has space', 'x' * 65,
    None, 5,
])
def test_render_rejects_unsafe_names(name):
    unit_type = UnitType.from_config('robot', ROBOT)
    with pytest.raises(ValueError, match='invalid unit name'):
        unit_type.render(name, {'port': 1})


@pytest.mark.parametrize('cfg, message', [
    ({}, 'command'),
    ({'command': 'ros2 run'}, 'command'),
    ({'command': ['x'], 'shell': True}, 'unknown keys'),
    ({'command': ['{nope}']}, 'unknown placeholder'),
    ({'command': ['x'], 'env': {'A': '{nope}'}}, 'unknown placeholder'),
    ({'command': ['x'], 'params': {'p': {'type': 'list'}}}, 'unknown type'),
    ({'command': ['x'], 'params': {'name': {'type': 'str'}}}, 'reserved'),
    ({'command': ['x'], 'params': {'p': {'type': 'int'}}}, 'default'),
    ({'command': ['x'], 'params': {'p': {'type': 'int', 'default': 'a'}}},
     'not a int'),
])
def test_bad_config_fails_at_load(cfg, message):
    with pytest.raises(ValueError, match=message):
        UnitType.from_config('t', cfg)


def test_load_unit_types(tmp_path):
    path = tmp_path / 'units.yaml'
    path.write_text(
        'units:\n'
        '  demo:\n'
        '    command: [echo, "{name}"]\n'
        '    term_timeout_s: 3\n')
    types = load_unit_types(str(path))
    assert types['demo'].term_timeout_s == 3
    assert types['demo'].startup_grace_s == 2

    path.write_text('units: {}\n')
    with pytest.raises(ValueError, match='units'):
        load_unit_types(str(path))
