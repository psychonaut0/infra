#!/usr/bin/env python3
"""Supported operator launcher: refuse durable holds and validate external control."""
import fcntl
import os
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True

from bundle import external_control, need

STACK = Path('/opt/stacks/ct-animus')
CONTROL = Path('/etc/animus-control/recovery.env')
HOLD = Path('/var/lib/animus-backup/capture-hold.json')


def launch(arguments, *, stack=STACK, control=CONTROL, hold=HOLD):
    hold.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (hold.parent / 'capture.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return locked_launch(arguments, stack=stack, control=control, hold=hold)


def locked_launch(arguments, *, stack, control, hold):
    # Includes setup/run/start/up/restart: no supported operator path can start
    # an extra response owner or bypass a failed capture. Raw admin Docker stays
    # administrative authority, not a claimed security sandbox.
    need(not hold.exists(), 'durable capture hold requires private operator review')
    external_control(control)
    need(arguments and not any(item in ('config', 'logs') for item in arguments), 'secret-exporting commands excluded')
    environment = {key: os.environ[key] for key in ('PATH', 'HOME', 'LANG', 'TMPDIR') if key in os.environ}
    environment['COMPOSE_DISABLE_ENV_FILE'] = '1'
    result = subprocess.run(['docker', 'compose', '--project-directory', str(stack), '--env-file', str(stack / '.env'),
                             '-f', str(stack / 'docker-compose.yml'), *arguments], env=environment)
    return result.returncode


if __name__ == '__main__':
    try:
        raise SystemExit(launch(sys.argv[1:]))
    except (OSError, ValueError):
        print('FAIL: operator launcher blocked; inspect hold/external control privately', file=sys.stderr)
        raise SystemExit(1)
