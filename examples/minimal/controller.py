#!/usr/bin/env python3
"""Stand-in for a robot controller: owns one "robot" until told to stop."""

import signal
import sys
import time

name, interval = sys.argv[1], float(sys.argv[2])


def stop(*_):
    print(f'[{name}] releasing robot, bye', flush=True)
    sys.exit(0)


signal.signal(signal.SIGTERM, stop)
print(f'[{name}] connected', flush=True)
while True:
    time.sleep(interval)
    print(f'[{name}] tick', flush=True)
