"""Thin HTTP client for one agent."""

import requests

from roost.manager.errors import AgentError, AgentUnreachable

# (connect, read) seconds. The read timeout must exceed the agent's
# startup_grace_s (POST /units) and term_timeout_s (DELETE /units/<name>),
# because the agent answers only after those waits.
DEFAULT_TIMEOUT = (3, 30)


class AgentClient:

    def __init__(self, base_url: str, token: str, timeout=DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self._headers = {'Authorization': f'Bearer {token}'}

    def health(self) -> dict:
        return self._request('GET', '/health')

    def types(self) -> dict:
        """Declared unit types: {type name: {'params': {...}}}."""
        return self._request('GET', '/types')['types']

    def units(self) -> list:
        """Info dicts of the units on this agent."""
        return self._request('GET', '/units')['units']

    def spawn(self, unit_type: str, name: str, params: dict = None) -> dict:
        """Start a unit; returns its info dict."""
        body = {'type': unit_type, 'name': name, 'params': params or {}}
        return self._request('POST', '/units', json=body)['unit']

    def remove(self, name: str) -> bool:
        """Stop a unit. False if the agent had no such unit (already gone)."""
        try:
            self._request('DELETE', f'/units/{name}')
        except AgentError as exc:
            if exc.status == 404:
                return False
            raise
        return True

    def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            resp = requests.request(
                method, self.base_url + path, headers=self._headers,
                timeout=self.timeout, **kwargs)
        except requests.ConnectionError as exc:
            raise AgentUnreachable(
                f'agent {self.base_url} unreachable: {exc}') from exc
        except requests.RequestException as exc:
            raise AgentError(
                f'agent {self.base_url} request failed: {exc}') from exc
        try:
            body = resp.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            body = {}
        if not resp.ok:
            raise AgentError(
                body.get('error') or f'agent {self.base_url} returned '
                                     f'HTTP {resp.status_code}',
                status=resp.status_code)
        return body
