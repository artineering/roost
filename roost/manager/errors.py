"""Exceptions raised by the manager library."""


class RoostError(Exception):
    """Base class for manager-side errors."""


class NoCapacity(RoostError):
    """No enabled, online host has a free slot for the requested unit type."""


class AgentError(RoostError):
    """An agent call failed.

    `status` is the HTTP status when the agent answered (it rejected the
    request), or None when the outcome is unknown (e.g. a read timeout).
    """

    def __init__(self, message: str, status=None):
        super().__init__(message)
        self.status = status


class AgentUnreachable(AgentError):
    """Could not connect to the agent at all; the request never ran."""
