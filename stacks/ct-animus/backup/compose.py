#!/usr/bin/env python3
"""Supported operator launcher: refuse durable holds and validate external control."""
import fcntl
import os
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True

from bundle import external_control, need
import lifecycle

STACK = Path('/opt/stacks/ct-animus')
CONTROL = Path('/etc/animus-control/recovery.env')
HOLD = Path('/var/lib/animus-backup/capture-hold.json')


def launch(arguments, *, stack=STACK, control=CONTROL, hold=HOLD):
    hold.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (hold.parent / 'capture.lock').open('a') as lock:
        if arguments == ['stop-response-owners']:
            persistence_error = None
            try:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    # Persist BEFORE waiting; a capture owning this lock must
                    # see the stop request before it resumes response owners.
                    try:
                        lifecycle.persist_stop()
                    except (OSError, ValueError) as error:
                        persistence_error = error
                    fcntl.flock(lock, fcntl.LOCK_EX)
                lifecycle.stop_owners()
                if persistence_error is not None:
                    raise persistence_error
            except BaseException:
                if persistence_error is None:
                    lifecycle.persist_stop()
                raise
            return 0
        fcntl.flock(lock, fcntl.LOCK_EX)
        if arguments in (['boot'], ['operator-resume']):
            return boot(stack, control, hold, resume=arguments == ['operator-resume'])
        return locked_launch(arguments, stack=stack, control=control, hold=hold)


def locked_launch(arguments, *, stack, control, hold):
    # Includes setup/run/start/up/restart: no supported operator path can start
    # an extra response owner or bypass a failed capture. Raw admin Docker stays
    # administrative authority, not a claimed security sandbox.
    need(not lifecycle.held(hold), 'durable capture hold requires private operator review')
    external_control(control)
    need(arguments and not any(item in ('config', 'logs') for item in arguments), 'secret-exporting commands excluded')
    # Long-lived response owners start only through the gate. Operator migration
    # commands remain available; initial provider bootstrap has a separate review.
    database_only = arguments == ['up', '-d', 'app-postgres', 'auth-postgres']
    operator = arguments[:5] == ['--profile', 'operator', 'run', '--rm', 'migrate']
    need(database_only or operator, 'only database startup and fixed operator migration service are supported')
    return compose(arguments, stack)


def compose(arguments, stack, *, quiet=False):
    environment = {key: os.environ[key] for key in ('PATH', 'HOME', 'LANG', 'TMPDIR') if key in os.environ}
    environment['COMPOSE_DISABLE_ENV_FILE'] = '1'
    result = subprocess.run(['docker', 'compose', '--project-directory', str(stack), '--env-file', str(stack / '.env'),
                             '-f', str(stack / 'docker-compose.yml'), *arguments], env=environment,
                            stdout=subprocess.DEVNULL if quiet else None, stderr=subprocess.DEVNULL,
                            timeout=120)
    return result.returncode


def boot(stack, control, hold, *, resume=False):
    # Remove any preexisting response owner BEFORE checking potentially missing
    # authority. Failure must not leave a previously started stale owner running.
    starting = False
    try:
        lifecycle.stop_owners()
        need(not lifecycle.held(hold), 'durable capture hold requires private operator review')
        generation = external_control(control)
        need(not lifecycle.held(lifecycle.STARTUP_HOLD), 'independent startup hold')
        need(resume or not lifecycle.held(lifecycle.STOP_INTENT), 'durable stop requested')
        need(compose(['up', '-d', '--wait', '--wait-timeout', '90', 'app-postgres', 'auth-postgres'], stack,
                     quiet=True) == 0, 'database startup failed')
        items = lifecycle.inventory()
        query = lambda generation: lifecycle.database_status(items, generation)
        lifecycle.gate(control, query, expected_generation=generation, allow_stop_intent=resume)
        if resume and lifecycle.held(lifecycle.STOP_INTENT):
            lifecycle.STOP_INTENT.unlink()
            lifecycle.sync_directory(lifecycle.STOP_INTENT.parent)
        lifecycle.gate(control, query, expected_generation=generation)
        if resume:
            # The operator then starts the systemd unit, which repeats all gates
            # and owns the lifecycle. Clearing intent does not itself start API.
            return 0
        starting = True
        need(compose(['--profile', 'commissioning', 'up', '-d', '--force-recreate', *lifecycle.WRITERS], stack,
                     quiet=True) == 0,
             'response owner startup failed')
        items = lifecycle.inventory()
        lifecycle.snapshot(items)
        need(all(items[name]['State']['Running'] for name in lifecycle.WRITERS), 'response owner exited during startup')
        lifecycle.gate(control, lambda generation: lifecycle.database_status(items, generation),
                       api=items['api'], expected_generation=generation)
        return 0
    except BaseException:
        try:
            lifecycle.persist_stop()
        finally:
            if starting:
                lifecycle.stop_owners()
        raise


if __name__ == '__main__':
    try:
        raise SystemExit(launch(sys.argv[1:]))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        print('FAIL: operator launcher blocked; inspect hold/external control privately', file=sys.stderr)
        raise SystemExit(1)
