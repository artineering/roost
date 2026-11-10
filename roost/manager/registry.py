"""Hosts that run an agent, and least-loaded placement across them."""

import os
from dataclasses import dataclass
from typing import List, Optional

import yaml

from roost.manager.errors import NoCapacity


@dataclass
class Host:
    name: str
    host: str                           # hostname or IP of the agent
    capacity: int                       # max simultaneous units (slots)
    port: int = 8181
    enabled: bool = True                # config `online:`; false = no new units
    types: Optional[List[str]] = None   # unit types accepted; None = any
    online: bool = True                 # runtime: was the agent reachable?
    count: int = 0                      # units currently placed here

    @property
    def free(self) -> int:
        return self.capacity - self.count

    @property
    def base_url(self) -> str:
        return f'http://{self.host}:{self.port}'


class HostRegistry:
    """The configured hosts, in config order, plus the shared agent token.

    Plain data: not locked. `Fleet` serialises access to it.
    """

    def __init__(self, hosts: List[Host], token: str = ''):
        self._hosts = {host.name: host for host in hosts}
        if len(self._hosts) != len(hosts):
            raise ValueError('host names must be unique')
        # The environment wins so the secret can stay out of the config file.
        self.token = os.environ.get('ROOST_TOKEN') or token

    @classmethod
    def from_yaml(cls, path: str) -> 'HostRegistry':
        with open(path) as fh:
            return cls.from_config(yaml.safe_load(fh) or {})

    @classmethod
    def from_config(cls, data: dict) -> 'HostRegistry':
        hosts = [
            Host(name=entry['name'], host=entry['host'],
                 capacity=int(entry['capacity']),
                 port=int(entry.get('port', 8181)),
                 enabled=bool(entry.get('online', True)),
                 types=entry.get('types'))
            for entry in data.get('hosts') or []
        ]
        token = (data.get('agent') or {}).get('token') or ''
        return cls(hosts, token=str(token))

    def get(self, name: str) -> Optional[Host]:
        return self._hosts.get(name)

    def all(self) -> List[Host]:
        return list(self._hosts.values())

    def select(self, unit_type: Optional[str] = None) -> Host:
        """The usable host with the most free slots; ties go to config order.

        Usable = enabled, online, below capacity and, when `unit_type` is
        given, accepting that type. Raises NoCapacity if there is none.
        """
        candidates = [
            host for host in self._hosts.values()
            if host.enabled and host.online and host.free > 0
            and (unit_type is None or host.types is None
                 or unit_type in host.types)
        ]
        if not candidates:
            what = f' for unit type {unit_type!r}' if unit_type else ''
            raise NoCapacity(f'no online host with a free slot{what}')
        return max(candidates, key=lambda host: host.free)  # first max wins
