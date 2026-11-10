"""Manager library: host registry, agent client and fleet orchestration."""

from roost.manager.client import AgentClient
from roost.manager.errors import (
    AgentError, AgentUnreachable, NoCapacity, RoostError)
from roost.manager.fleet import Fleet, Placement
from roost.manager.registry import Host, HostRegistry

__all__ = [
    'AgentClient', 'AgentError', 'AgentUnreachable', 'Fleet', 'Host',
    'HostRegistry', 'NoCapacity', 'RoostError', 'Placement',
]
