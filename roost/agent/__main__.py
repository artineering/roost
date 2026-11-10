"""`roost-agent`: run the per-host agent."""

import argparse
import os
import signal
import sys
import tempfile
import threading

from werkzeug.serving import make_server

from roost.agent.app import create_app
from roost.agent.supervisor import Supervisor
from roost.agent.units import load_unit_types


def main(argv=None) -> int:
    env = os.environ
    parser = argparse.ArgumentParser(
        prog='roost-agent',
        description='Launch declared unit types on this host on behalf of an '
                    'roost manager. The bearer token is read from '
                    'ROOST_TOKEN.')
    parser.add_argument('--config', default=env.get('ROOST_CONFIG'),
                        help='unit types YAML (env ROOST_CONFIG)')
    parser.add_argument('--host', default=env.get('ROOST_HOST', '127.0.0.1'),
                        help='bind address (env ROOST_HOST)')
    parser.add_argument('--port', type=int,
                        default=int(env.get('ROOST_PORT', '8181')),
                        help='bind port (env ROOST_PORT)')
    parser.add_argument('--log-dir',
                        default=env.get('ROOST_LOG_DIR', os.path.join(
                            tempfile.gettempdir(), 'roost')),
                        help='per-unit log directory (env ROOST_LOG_DIR)')
    args = parser.parse_args(argv)

    token = env.get('ROOST_TOKEN', '')
    if not token:
        parser.error('ROOST_TOKEN must be set (shared bearer token)')
    if not args.config:
        parser.error('--config or ROOST_CONFIG is required')

    supervisor = Supervisor(load_unit_types(args.config), args.log_dir)
    # Bind here, in the main thread, so a port clash fails loudly at startup.
    server = make_server(args.host, args.port,
                         create_app(supervisor, token), threaded=True)
    print(f'roost-agent listening on {args.host}:{server.server_port}; '
          f'unit types: {sorted(supervisor.unit_types)}; '
          f'logs: {args.log_dir}', flush=True)

    # Serve from a daemon thread and keep the MAIN thread free for signals.
    # A serving loop blocked in a syscall can swallow or defer a signal
    # handler; parking the main thread on an Event guarantees that
    # SIGTERM/SIGINT (systemd stop, Ctrl-C) runs teardown_all -- so every unit
    # releases its robot -- before the agent exits.
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        stop.wait()
    finally:
        print('roost-agent stopping; tearing down all units', flush=True)
        supervisor.teardown_all()
    return 0


if __name__ == '__main__':
    sys.exit(main())
