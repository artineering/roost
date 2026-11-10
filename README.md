# roost

Run the controllers for robots that cannot host their own, across a fleet of
host machines.

Plenty of robots and edge devices have no computer to speak of. They have limited
memory and processing power. Deploying a higher-level controller on such devices
is out of the question. In such cases, the obvious choice is to deploy the high-level
controller on a host device like a Raspberry Pi or Jetson Nano that provides an
operating system, more processing power and RAM. This also allows one to build
controllers that work in the ROS environment. The robot and the controller share
a fixed interface through communication channels such as Bluetooth, wifi, etc.
This works very well for a single instance. However, as soon as we start deploying
more instances on the host, resource constraints start becoming a real problem.
For example, most host devices have Bluetooth adapters that can manage up to 7
device connections simultaneously. This is often a theoretical limit. In reality,
most adapters start showing degradation in their performance at 3 or 4 devices,
in real-world situations.

In scenarios where one wants to deploy a large number of such robots or edge
devices, the next step is to consider distributing their controllers over
multiple host devices, balancing the number of connections to within the acceptable
range. If it's a one-time deployment, it is possible to do it manually with
handwritten scripts. However, for any meaningful scalable solution, this needs
to be managed automatically. There is a need for a solution that manages the
multiple host devices, distributes the robot controllers across the fleet in a
balanced way and on each host, manages the life-cycle of the individual instances.

roost is one example of such a solution. It comes in two pieces:

- **agent** (`roost-agent`): runs on every host. It starts and stops *units*
  (one controller process tree per robot) on request, over a small HTTP API.
- **manager** (`roost.manager`): a library for your coordinator process. It
  knows the hosts and their capacity, places each unit on the least-loaded
  host, and rolls back when something fails.

roost runs on any Linux host, from a single-board computer to a server, and
hosts in one fleet do not have to match. You describe each host's limits and
what it may run; roost keeps deployments inside them (see
[Fitting your constraints](#fitting-your-constraints)).

It is framework-agnostic. A unit is any command; ROS 2 is just one thing it
can launch (`examples/ros2/`).

```
   coordinator process                     host A                  host B
 +----------------------+   HTTP +   +----------------+     +----------------+
 | your code            |   bearer   | roost-agent    |     | roost-agent    |
 |   Fleet.deploy()     |   token    |  units.yaml    |     |  units.yaml    |
 |   Fleet.remove()  ---+----------->|   |- unit r1   |     |   |- unit r2   |
 |   Fleet.sync()       |            |   '- unit r3   |     |   '- unit r4   |
 |  hosts.yaml          |            +----|-------|---+     +----|-------|---+
 +----------------------+               radio/serial/...       radio/serial/...
                                         r1      r3              r2      r4
```

## Quickstart (no hardware)

```bash
pip install -e .
python examples/minimal/demo.py
```

The demo starts two agents on localhost with two slots each, deploys five
stand-in controllers (the fifth is refused: no capacity), removes one,
deploys again, and shows a "restarted" manager recovering its state from the
agents.

By hand, the same thing looks like this:

```bash
export ROOST_TOKEN=$(python -c 'import secrets; print(secrets.token_hex(16))')
cd examples/minimal
roost-agent --config units.yaml --port 18181 &
roost-agent --config units.yaml --port 18182 &
```

```python
from roost.manager import Fleet, HostRegistry

fleet = Fleet(HostRegistry.from_yaml('hosts.yaml'))   # token from ROOST_TOKEN
fleet.sync()                                          # learn what is running
placement = fleet.deploy('demo', 'robot-1', {'interval': 0.5})
print(placement.host.name, placement.unit['pid'])
fleet.remove('robot-1')
```

## Agent

```
roost-agent [--config units.yaml] [--host 127.0.0.1] [--port 8181] [--log-dir DIR]
```

| Setting   | Flag        | Environment      | Default            |
|-----------|-------------|------------------|--------------------|
| Token     | (none)      | `ROOST_TOKEN`    | required           |
| Config    | `--config`  | `ROOST_CONFIG`   | required           |
| Bind host | `--host`    | `ROOST_HOST`     | `127.0.0.1`        |
| Bind port | `--port`    | `ROOST_PORT`     | `8181`             |
| Log dir   | `--log-dir` | `ROOST_LOG_DIR`  | `<tmpdir>/roost`   |

The agent refuses to start without a token. To be reachable from the manager
it must bind a LAN address (`--host 10.0.0.11`); the default is loopback.

### Unit types

The config file declares the *unit types* this host is willing to run. The
manager can only name a type and supply its parameters; it never sends a
command line.

```yaml
units:
  rover:
    command: [ros2, run, micro_ros_agent, micro_ros_agent,
              serial, --dev, "{device}", -b, "{baudrate}"]
    params:
      device: {type: str, required: true}
      baudrate: {type: int, default: 115200}
    env: {ROBOT_NAME: "{name}"}
    startup_grace_s: 2
    term_timeout_s: 10
```

| Key               | Meaning |
|-------------------|---------|
| `command`         | argv list, run without a shell. `{name}` and `{param}` are substituted inside each element. |
| `params`          | Parameter schema. `type` is `str` (default), `int`, `float` or `bool`. Each parameter needs `required: true` or a `default`. |
| `env`             | Optional extra environment, templated like `command`, merged over the agent's own environment (which units inherit). |
| `startup_grace_s` | After starting, wait this long and confirm the process is still alive (default 2). |
| `term_timeout_s`  | On teardown, how long to wait after SIGTERM before SIGKILL (default 10). |

Requests are validated strictly: unknown parameters, missing required ones
and wrong types are rejected (no coercion; `"5001"` is not an int). Booleans
render as `true` / `false`. Unit names must match
`[A-Za-z0-9][A-Za-z0-9_.-]{0,63}`; they are unique per agent and name the log
file. Unknown placeholders and other config mistakes fail when the agent
starts.

### Process handling

- Each unit runs in its own session and process group, with stdout and stderr
  appended to `<log dir>/<name>.log`.
- Teardown sends SIGTERM to the whole group, waits `term_timeout_s` for the
  unit to exit, then sends SIGKILL to the group. Nothing the unit started is
  left holding the robot's link.
- When the agent itself gets SIGTERM or SIGINT it tears down every unit
  before exiting. Units do not outlive their agent.
- A unit that dies on its own stays listed with `"alive": false` (and still
  counts against the host's capacity) until it is removed or redeployed under
  the same name.

### HTTP API

Every route requires `Authorization: Bearer <token>` (401 otherwise). Bodies
are JSON; errors look like `{"error": "..."}`.

| Method & path          | Success | Errors |
|------------------------|---------|--------|
| `GET /health`          | 200 `{"status": "ok"}` | |
| `GET /types`           | 200 `{"types": {"rover": {"params": {...}}}}` | |
| `GET /units`           | 200 `{"units": [unit, ...]}` | |
| `POST /units`          | 201 `{"unit": unit}` | 400 invalid name/params, 404 unknown type, 409 name already running, 500 failed to start (adds `"log_path"`) |
| `DELETE /units/<name>` | 200 `{"stopped": "<name>"}` | 404 no such unit |

`POST /units` takes `{"type": "rover", "name": "rover-07", "params": {"device": "/dev/ttyACM0"}}`
and answers after `startup_grace_s`. `DELETE` answers once the unit is gone.
A `unit` is:

```json
{"name": "rover-07", "type": "rover", "params": {"device": "/dev/ttyACM0"},
 "pid": 4242, "alive": true, "started_at": 1790000000.0,
 "log_path": "/var/log/roost/rover-07.log"}
```

`systemd/roost-agent.service` is a template for running the agent as a
service.

## Fitting your constraints

A unit is the atomic thing you deploy: one controller for one device. What a
unit needs from a host depends entirely on your setup, so roost does not
measure anything. You work out how many units each host can carry and tell
it, using three settings:

- **`capacity`** (hosts file): how many units the host runs at once. Set it to
  the count of whatever runs out first there: 4 on a board whose radio holds
  four links, 2 on a machine with two free serial ports, 30 on a server that
  is only limited by CPU. A full host is refused rather than overloaded.
- **`types`** (hosts file): which unit types may be placed on the host, so
  units that need a GPU or a particular adapter only land where one exists.
- **unit types** (each host's own agent config): the same type name can map to
  a different command, environment or timeouts on different hosts, and a host
  simply omits the types it cannot run.

```yaml
hosts:
  - {name: sbc-01, host: 10.0.0.11, capacity: 4, types: [rover]}
  - {name: sbc-02, host: 10.0.0.12, capacity: 2, types: [rover]}
  - {name: gpu-01, host: 10.0.0.20, capacity: 12, types: [rover, vision]}
```

## Manager

### Hosts file

```yaml
agent:
  token: ""          # prefer the ROOST_TOKEN environment variable (it wins)

hosts:
  - name: host-01      # unique
    host: 10.0.0.11        # agent address
    port: 8181             # default 8181
    capacity: 4            # slots: max simultaneous units (required)
    types: [rover]         # optional: only place these unit types here
    online: false          # optional: take the host out of placement
```

### Library

```python
from roost.manager import Fleet, HostRegistry, NoCapacity, AgentError

fleet = Fleet(HostRegistry.from_yaml('hosts.yaml'))
fleet.sync()
```

- `fleet.deploy(unit_type, name, params=None) -> Placement`: picks the
  enabled, online host with the most free slots that accepts the type (ties:
  config order), reserves a slot, and asks its agent to start the unit. On any
  failure the reservation is rolled back. If the host is **unreachable** it is
  marked offline and the next host is tried. If the host **rejects** the
  request, `AgentError` is raised (`.status` is the HTTP status) and no other
  host is tried: it would reject it too, or start a second controller for the
  same robot. Raises `NoCapacity` when no host is left.
- `fleet.remove(name)`: stops the unit and frees its slot. `KeyError` if the
  fleet does not know the name; `AgentError` (state kept) if the agent cannot
  be reached.
- `fleet.sync()`: rebuilds per-host counts, the unit-to-host map and the
  online flags from each agent's `GET /units`. The agents are the source of
  truth, so call it at startup (a restarted manager picks up where it left
  off) and periodically (it is also what brings an offline host back).
  Unreachable hosts keep their last known units.
- `fleet.units()` returns `{unit name: host name}`; `fleet.registry.all()`
  returns the `Host` objects (`name`, `host`, `port`, `capacity`, `count`,
  `free`, `online`, `enabled`, `types`).

`Fleet` is thread-safe and does not hold its lock across HTTP calls, so
deploys run concurrently without overbooking a host.

`AgentClient(base_url, token, timeout=(3, 30))` is the thin HTTP client
underneath (`health()`, `types()`, `units()`, `spawn()`, `remove()`), usable
on its own. The read timeout must be longer than the agents'
`startup_grace_s` and `term_timeout_s`.

## Security

roost is built for a trusted, isolated LAN (a robot network), not the
internet.

- **Bearer token on every route**, compared in constant time; the agent will
  not start without one. Keep it out of version control: pass it through
  `ROOST_TOKEN` (for systemd, an `EnvironmentFile` readable only by root).
- **Declared types only.** A token holder can start the commands in that
  host's `units.yaml` with validated parameters, and nothing else. Commands
  run without a shell, so parameters cannot inject shell syntax. A `str`
  parameter is still passed to your command verbatim; declare one only if the
  command treats it safely.
- **No TLS.** The token and all traffic cross the network in clear text.
  Anyone who can sniff the LAN can replay the token. Bind the agent to the
  robot-LAN interface only, firewall the port, and put a TLS reverse proxy or
  a VPN in front if the network is shared.
- The agent runs units as its own user. Give it a dedicated, unprivileged
  account.

## Development

```bash
pip install -e '.[test]'
pytest
```

The tests use real subprocesses and real HTTP on localhost; they need Linux
(`/proc`) but no robots and no ROS.

## License

MIT. See `LICENSE`.
