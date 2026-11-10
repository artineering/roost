"""Spawns and tears down unit process trees on this host."""

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass


class AlreadyRunning(Exception):
    """A live unit with this name already exists on this agent."""


class StartFailed(Exception):
    """The unit could not be started or died during its startup grace."""

    def __init__(self, message: str, log_path: str):
        super().__init__(message)
        self.log_path = log_path


@dataclass
class _Unit:
    name: str
    type: str
    params: dict
    process: subprocess.Popen
    log: object
    log_path: str
    term_timeout_s: float
    started_at: float

    def info(self) -> dict:
        return {
            'name': self.name,
            'type': self.type,
            'params': self.params,
            'pid': self.process.pid,
            'alive': self.process.poll() is None,
            'started_at': self.started_at,
            'log_path': self.log_path,
        }


def _signal_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass


class Supervisor:
    """Tracks the units running on this host, one process group per unit."""

    def __init__(self, unit_types: dict, log_dir: str):
        self.unit_types = unit_types
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self._units = {}
        self._lock = threading.Lock()

    def spawn(self, type_name: str, name: str, params: dict) -> dict:
        """Start a unit and return its info dict.

        Raises KeyError (unknown type), ValueError (bad name/params),
        AlreadyRunning, or StartFailed.
        """
        unit_type = self.unit_types[type_name]
        argv, env = unit_type.render(name, params)
        log_path = os.path.join(self.log_dir, f'{name}.log')

        with self._lock:
            old = self._units.get(name)
            if old is not None:
                if old.process.poll() is None:
                    raise AlreadyRunning(f'{name} is already running')
                old.log.close()             # dead leftover: replace it
                del self._units[name]
            log = open(log_path, 'ab', buffering=0)
            try:
                # Own session => own process group, so teardown can signal the
                # whole tree (the unit and everything it started) at once.
                process = subprocess.Popen(
                    argv, stdin=subprocess.DEVNULL, stdout=log,
                    stderr=subprocess.STDOUT, env={**os.environ, **env},
                    start_new_session=True)
            except OSError as exc:
                log.close()
                raise StartFailed(f'{name} failed to start: {exc}', log_path)
            unit = _Unit(name, type_name, dict(params), process, log,
                         log_path, unit_type.term_timeout_s, time.time())
            self._units[name] = unit

        # Startup check outside the lock so concurrent spawns do not queue
        # behind each other's grace period.
        time.sleep(unit_type.startup_grace_s)
        if process.poll() is not None:
            self._forget(unit)
            raise StartFailed(
                f'{name} exited during startup (code {process.returncode})',
                log_path)
        return unit.info()

    def teardown(self, name: str) -> bool:
        """Stop a unit's whole process group. False if there is no such unit."""
        with self._lock:
            unit = self._units.get(name)
        if unit is None:
            return False
        # start_new_session made the unit its own group leader: pgid == pid.
        pgid = unit.process.pid
        _signal_group(pgid, signal.SIGTERM)
        try:
            unit.process.wait(timeout=unit.term_timeout_s)
        except subprocess.TimeoutExpired:
            _signal_group(pgid, signal.SIGKILL)
            unit.process.wait()
        self._forget(unit)
        return True

    def teardown_all(self) -> None:
        """Stop every unit in parallel (agent shutdown)."""
        with self._lock:
            names = list(self._units)
        threads = [threading.Thread(target=self.teardown, args=(name,))
                   for name in names]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    def units(self) -> list:
        with self._lock:
            return [unit.info() for unit in self._units.values()]

    def _forget(self, unit: _Unit) -> None:
        with self._lock:
            if self._units.get(unit.name) is unit:
                del self._units[unit.name]
        unit.log.close()
