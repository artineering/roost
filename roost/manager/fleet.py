"""Fleet: place units on hosts, and keep the books honest."""

import threading
from dataclasses import dataclass

from roost.manager.client import DEFAULT_TIMEOUT, AgentClient
from roost.manager.errors import (
    AgentError, AgentUnreachable, NoCapacity, RoostError)
from roost.manager.registry import Host, HostRegistry


@dataclass
class Placement:
    name: str       # unit name
    type: str       # unit type
    host: Host      # where it runs
    unit: dict      # the agent's info dict (pid, log_path, ...)


class Fleet:
    """Thread-safe deploy/remove/sync over a HostRegistry."""

    def __init__(self, registry: HostRegistry, timeout=DEFAULT_TIMEOUT):
        self.registry = registry
        self._clients = {
            host.name: AgentClient(host.base_url, registry.token, timeout)
            for host in registry.all()}
        self._units = {}        # unit name -> host name
        self._deploying = set()  # unit names reserved but not yet confirmed
        self._lock = threading.Lock()

    def deploy(self, unit_type: str, name: str, params: dict = None
               ) -> Placement:
        """Start a unit on the least-loaded suitable host.

        A slot is reserved before the agent is called and released again on
        any failure. If the chosen host cannot be reached it is marked offline
        and the next candidate is tried; if it answers with a rejection the
        error is raised as-is (AgentError), because another host would only
        reject it too -- or worse, start a second copy.

        Raises NoCapacity, AgentError, or RoostError (name already in use).
        """
        unreachable = None
        while True:
            with self._lock:
                if name in self._units:
                    raise RoostError(
                        f'{name} is already deployed on {self._units[name]}')
                try:
                    host = self.registry.select(unit_type)
                except NoCapacity as exc:
                    raise exc from unreachable
                host.count += 1
                self._units[name] = host.name
                self._deploying.add(name)
            client = self._clients[host.name]
            try:
                unit = client.spawn(unit_type, name, params)
            except AgentUnreachable as exc:
                self._release(name, host, online=False)
                unreachable = exc
                continue
            except AgentError as exc:
                if exc.status is None:
                    # Outcome unknown (e.g. timeout): the unit may be running.
                    try:
                        client.remove(name)
                    except AgentError:
                        pass
                self._release(name, host)
                raise
            with self._lock:
                self._deploying.discard(name)
            return Placement(name, unit_type, host, unit)

    def remove(self, name: str) -> None:
        """Stop a unit and free its slot.

        Raises KeyError for an unknown unit. If the agent cannot be reached,
        raises AgentError and keeps the unit on the books.
        """
        with self._lock:
            if name in self._deploying:
                raise RoostError(f'{name} is still being deployed')
            host = self.registry.get(self._units[name])
        self._clients[host.name].remove(name)   # an already-gone unit is fine
        self._release(name, host)

    def sync(self) -> None:
        """Rebuild counts, unit placement and online flags from the agents.

        The agents are the source of truth. Call this at startup (a restarted
        manager recovers its state) and whenever drift is possible. Hosts that
        cannot be reached are marked offline and keep their last known units.
        """
        listings = {}
        for host in self.registry.all():
            try:
                listings[host.name] = self._clients[host.name].units()
            except AgentError:
                listings[host.name] = None
        with self._lock:
            units = {
                name: host_name for name, host_name in self._units.items()
                if name in self._deploying or listings[host_name] is None}
            for host in self.registry.all():
                listing = listings[host.name]
                host.online = listing is not None
                for unit in listing or []:
                    units[unit['name']] = host.name
            self._units = units
            placed = list(units.values())
            for host in self.registry.all():
                host.count = placed.count(host.name)

    def units(self) -> dict:
        """Snapshot of {unit name: host name}."""
        with self._lock:
            return dict(self._units)

    def _release(self, name: str, host: Host, online: bool = True) -> None:
        with self._lock:
            self._deploying.discard(name)
            if self._units.pop(name, None) is not None:
                host.count -= 1
            if not online:
                host.online = False
