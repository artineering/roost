"""Unit types: the declared, parameterised commands an agent may launch.

The manager never sends a command line. It names a unit type from the agent's
own config file and supplies typed parameters; the agent validates them and
substitutes them into the declared argv and environment.
"""

import re
from dataclasses import dataclass

import yaml

# Unit names end up in log file names and argv, so keep them boring.
NAME_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}')

_PLACEHOLDER = re.compile(r'\{(\w+)\}')
_PARAM_TYPES = {'str': str, 'int': int, 'float': (int, float), 'bool': bool}
_TYPE_KEYS = {'command', 'params', 'env', 'startup_grace_s', 'term_timeout_s'}


def _has_type(value, type_name: str) -> bool:
    # bool is a subclass of int in Python; do not let True pass as an int.
    if isinstance(value, bool) and type_name != 'bool':
        return False
    return isinstance(value, _PARAM_TYPES[type_name])


def _text(value) -> str:
    """Render a parameter value as an argv/env string (bools as true/false)."""
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)


@dataclass
class UnitType:
    name: str
    command: list
    params: dict            # param name -> {type, required[, default]}
    env: dict
    startup_grace_s: float = 2
    term_timeout_s: float = 10

    @classmethod
    def from_config(cls, name: str, cfg: dict) -> 'UnitType':
        """Build and validate one entry of the `units:` mapping."""
        def bad(msg):
            return ValueError(f'unit type {name!r}: {msg}')

        if not isinstance(cfg, dict):
            raise bad('must be a mapping')
        unknown = set(cfg) - _TYPE_KEYS
        if unknown:
            raise bad(f'unknown keys {sorted(unknown)}')

        command = cfg.get('command')
        if (not isinstance(command, list) or not command
                or not all(isinstance(arg, str) for arg in command)):
            raise bad('command must be a non-empty list of strings')
        env = cfg.get('env') or {}
        if not isinstance(env, dict) or not all(
                isinstance(v, str) for v in env.values()):
            raise bad('env must map names to strings')

        params = {}
        for pname, spec in (cfg.get('params') or {}).items():
            spec = spec or {}
            ptype = spec.get('type', 'str')
            if pname == 'name' or ptype not in _PARAM_TYPES:
                raise bad(f'param {pname!r}: reserved name or unknown type')
            required = bool(spec.get('required', False))
            if not required and 'default' not in spec:
                raise bad(f'param {pname!r} needs required: true or a default')
            params[pname] = {'type': ptype, 'required': required}
            if 'default' in spec:
                if not _has_type(spec['default'], ptype):
                    raise bad(f'param {pname!r}: default is not a {ptype}')
                params[pname]['default'] = spec['default']

        # Every placeholder must resolve, so a typo fails at load, not spawn.
        for text in list(command) + list(env.values()):
            for placeholder in _PLACEHOLDER.findall(text):
                if placeholder != 'name' and placeholder not in params:
                    raise bad(f'unknown placeholder {{{placeholder}}}')

        return cls(
            name=name, command=command, params=params, env=env,
            startup_grace_s=float(cfg.get('startup_grace_s', 2)),
            term_timeout_s=float(cfg.get('term_timeout_s', 10)),
        )

    def render(self, name, params: dict):
        """Validate a request and return (argv, extra_env) for it.

        Raises ValueError on a bad unit name or bad parameters.
        """
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            raise ValueError(
                'invalid unit name: use 1-64 characters from A-Z a-z 0-9 _ . -'
                ' starting with a letter or digit')
        unknown = set(params) - set(self.params)
        if unknown:
            raise ValueError(f'unknown params {sorted(unknown)}')

        values = {'name': name}
        for pname, spec in self.params.items():
            if pname in params:
                value = params[pname]
                if not _has_type(value, spec['type']):
                    raise ValueError(
                        f'param {pname!r} must be a {spec["type"]}')
            elif spec['required']:
                raise ValueError(f'missing required param {pname!r}')
            else:
                value = spec['default']
            values[pname] = _text(value)

        def fill(text):
            return _PLACEHOLDER.sub(lambda m: values[m.group(1)], text)

        return ([fill(arg) for arg in self.command],
                {key: fill(value) for key, value in self.env.items()})


def load_unit_types(path: str) -> dict:
    """Load the agent config file; returns {type name: UnitType}."""
    with open(path) as fh:
        data = yaml.safe_load(fh) or {}
    units = data.get('units')
    if not isinstance(units, dict) or not units:
        raise ValueError(f'{path}: expected a non-empty `units:` mapping')
    return {name: UnitType.from_config(name, cfg)
            for name, cfg in units.items()}
