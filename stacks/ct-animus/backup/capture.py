#!/usr/bin/env python3
"""Capture or stream one coherent bundle; failures retain a durable writer hold."""
import argparse
import fcntl
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time
import uuid

sys.dont_write_bytecode = True

from bundle import FORMAT, GENERATION_KEYS, external_control, inventory, need, private_file, safe_name, verify
import lifecycle

WRITERS = ('api', 'auth-server', 'auth-worker')
SERVICES = ('app-postgres', 'auth-postgres') + WRITERS


def command(args, *, data=None):
    # stderr may contain DSNs or provider material. Never forward it to logs.
    result = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=600)
    need(result.returncode == 0, 'runtime command failed')
    return result.stdout


class Capture:
    def __init__(self, config):
        self.c = config
        self.spool = Path(config['spool'])
        self.hold = self.spool / 'capture-hold.json'
        self.containers = {}

    def headroom(self, estimate=0):
        need(shutil.disk_usage(self.spool).free >= self.c['reserve_bytes'] + estimate,
             'insufficient capture headroom')

    def inspect(self, identifier):
        values = json.loads(command(['docker', 'inspect', identifier]))
        need(len(values) == 1, 'ambiguous container')
        return values[0]

    def identify(self):
        ids = command(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + self.c['project']]).split()
        need(ids, 'project absent')
        for identifier in ids:
            item = self.inspect(identifier.decode())
            service = item['Config']['Labels'].get('com.docker.compose.service')
            if service == 'migrate' and not item['State']['Running']:
                continue
            need(service in SERVICES and service not in self.containers, 'foreign or duplicate project container')
            need(item['State']['Running'] and not item['State'].get('Paused')
                 and not item['State'].get('Restarting'), 'all capture services must be running')
            self.containers[service] = item
        need(set(self.containers) == set(SERVICES), 'capture service missing')
        allowed_ids = {item['Id'] for item in self.containers.values()}
        networks = {name for item in self.containers.values() for name in item['NetworkSettings']['Networks']}
        for network in networks:
            net = json.loads(command(['docker', 'network', 'inspect', network]))[0]
            need(set(net.get('Containers', {})) <= allowed_ids, 'foreign network peer')
        for service in ('app-postgres', 'auth-postgres'):
            item = self.containers[service]
            need(not item['HostConfig'].get('PortBindings'), 'database host ports forbidden')
            for name in item['NetworkSettings']['Networks']:
                net = json.loads(command(['docker', 'network', 'inspect', name]))[0]
                need(net['Internal'], 'database network must be internal')
        for item in self.containers.values():
            need(not item['HostConfig'].get('Privileged') and not item['HostConfig'].get('PidMode') == 'host',
                 'unsupported privileged container')
        lifecycle.snapshot(self.containers)

    def pg(self, which, *args):
        database = self.c['databases'][which]
        return ['docker', 'exec', '-i', self.containers[database['service']]['Id'], *args]

    def sql(self, which, query):
        database = self.c['databases'][which]
        return command(self.pg(which, 'psql', '-X', '-U', database['user'], '-d', database['name'],
                               '-At', '-v', 'ON_ERROR_STOP=1', '-c', query)).decode().strip()

    def save_hold(self, record):
        temporary = self.spool / 'capture-hold.tmp'
        temporary.write_text(json.dumps(record, sort_keys=True) + '\n')
        temporary.chmod(0o600)
        with temporary.open('rb') as source:
            os.fsync(source.fileno())
        temporary.replace(self.hold)
        descriptor = os.open(self.spool, os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def quiesce(self, record):
        # Persist BEFORE changing any policy; a partial hold is never auto-resumed.
        self.save_hold(record)
        for service in WRITERS:
            command(['docker', 'update', '--restart=no', self.containers[service]['Id']])
        for service in WRITERS:
            command(['docker', 'stop', '--time', str(self.c['stop_seconds']), self.containers[service]['Id']])
        for service in WRITERS:
            item = self.inspect(self.containers[service]['Id'])
            need(not item['State']['Running'] and item['HostConfig']['RestartPolicy']['Name'] == 'no',
                 'writer hold not proven')
            # A timeout/SIGKILL is uncertainty about response completion.
            need(item['State']['ExitCode'] != 137 and not item['State'].get('OOMKilled'),
                 'uncertain writer drain')
        for which in ('app', 'auth'):
            need(self.sql(which, "SELECT count(*) FROM pg_stat_activity WHERE pid <> pg_backend_pid() "
                          "AND backend_type = 'client backend'") == '0', 'remaining database client')
        self.provider_volumes(held=True)
        record['phase'] = 'quiesced'
        self.save_hold(record)

    def dump(self, args, destination):
        with destination.open('xb') as output:
            with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
                try:
                    while chunk := process.stdout.read(1024 * 1024):
                        self.headroom(len(chunk))
                        output.write(chunk)
                    need(process.wait() == 0 and output.tell() > 0, 'logical dump failed')
                except BaseException:
                    process.kill()
                    process.wait()
                    raise
        destination.chmod(0o600)

    def provider_volumes(self, held=False):
        expected = {'auth-server': {'/data': 'auth-data', '/templates': 'auth-templates'},
                    'auth-worker': {'/data': 'auth-data', '/templates': 'auth-templates', '/certs': 'auth-certs'}}
        blueprints = next(Path(item['source']).resolve() for item in self.c['config_sources']
                          if item['name'] == 'blueprints')
        providers = {}
        volumes = {}
        for service, topology in expected.items():
            item = self.inspect(self.containers[service]['Id'])
            labels = item['Config']['Labels']
            need(item['Id'] == self.containers[service]['Id']
                 and labels.get('com.docker.compose.project') == self.c['project']
                 and labels.get('com.docker.compose.service') == service, 'provider identity changed')
            providers[item['Id']] = item
            need(all(mount['Type'] in ('volume', 'bind', 'tmpfs') for mount in item['Mounts']),
                 'unsupported provider mount type')
            persistent = [mount for mount in item['Mounts'] if mount['Type'] != 'tmpfs']
            destinations = [mount['Destination'] for mount in persistent]
            required = set(topology) | ({'/blueprints/ct-animus'} if service == 'auth-worker' else set())
            need(len(destinations) == len(required) and set(destinations) == required,
                 'unexpected persistent provider mount')
            for mount in persistent:
                destination = mount['Destination']
                if destination == '/blueprints/ct-animus':
                    need(mount['Type'] == 'bind' and mount['RW'] is False
                         and Path(mount['Source']).resolve() == blueprints, 'unexpected blueprint mount')
                    continue
                name = topology[destination]
                need(mount['Type'] == 'volume' and mount['RW'] is True, 'provider writable named volume required')
                value = json.loads(command(['docker', 'volume', 'inspect', mount['Name']]))[0]
                labels = value.get('Labels') or {}
                need(value['Name'] == mount['Name']
                     and labels.get('com.docker.compose.project') == self.c['project']
                     and labels.get('com.docker.compose.volume') == name
                     and Path(mount['Source']).resolve() == Path(value['Mountpoint']).resolve(),
                     'foreign provider volume or changed mount source')
                identity = (value['Name'], Path(value['Mountpoint']))
                need(name not in volumes or volumes[name] == identity, 'ambiguous provider volume')
                volumes[name] = identity
        need(set(volumes) == {'auth-data', 'auth-templates', 'auth-certs'}, 'provider volume omitted')
        captured = {name for name, _ in volumes.values()}
        # Include stopped containers and every network/project: labels and network
        # isolation alone cannot prove no other process owns a writable mount.
        ids = command(['docker', 'ps', '-aq']).decode().split()
        seen = set()
        for identifier in ids:
            item = self.inspect(identifier)
            seen.add(item['Id'])
            if item['Id'] in providers:
                need(item['Mounts'] == providers[item['Id']]['Mounts'], 'provider mount topology changed during check')
            for mount in item['Mounts']:
                source = Path(mount['Source']).resolve() if mount.get('Source') else None
                overlaps = source is not None and any(source.is_relative_to(point.resolve())
                    or point.resolve().is_relative_to(source) for _, point in volumes.values())
                if (mount.get('Type') == 'volume' and mount.get('Name') in captured) or overlaps:
                    need(item['Id'] in providers and mount in providers[item['Id']]['Mounts'],
                         'foreign provider volume consumer')
        need(set(providers) <= seen, 'provider container disappeared during volume check')
        if held:
            for service in WRITERS:
                item = self.inspect(self.containers[service]['Id'])
                need(not item['State']['Running'] and item['HostConfig']['RestartPolicy']['Name'] == 'no',
                     'writer resumed during capture')
                if item['Id'] in providers:
                    need(item['Mounts'] == providers[item['Id']]['Mounts'], 'held provider mount topology changed')
        return {name: point for name, (_, point) in volumes.items()}

    def copy_provider(self, name, target):
        # Reinspect all consumers/topology immediately before each file copy.
        volumes = self.provider_volumes(held=True)
        return self.archive([(volumes[name], name)], target)

    def archive(self, sources, target, env=False):
        names = set()
        with tarfile.open(target, 'w:') as archive:
            for source, destination in sources:
                source = Path(source)
                entries = [source] + sorted(source.rglob('*')) if source.is_dir() else [source]
                for entry in entries:
                    self.headroom()
                    need(entry.exists() and not entry.is_symlink(), 'missing or linked configuration/provider file')
                    need(entry.is_dir() or entry.is_file(), 'special source file forbidden')
                    name = safe_name(destination + (('/' + str(entry.relative_to(source))) if entry != source else ''))
                    need(name not in names, 'duplicate configuration member')
                    names.add(name)
                    # Never include canonical external control or a second copy.
                    need(entry.resolve() != Path(self.c['external_control']).resolve(), 'external control excluded')
                    info = archive.gettarinfo(str(entry), arcname=name)
                    need(not info.mode & 0o6000, 'privileged source mode forbidden')
                    if entry.is_file():
                        if env and name == 'stack.env':
                            lines = entry.read_text().splitlines(keepends=True)
                            kept = []
                            keys = set()
                            for line in lines:
                                match = re.match(r'\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=', line)
                                need(match is not None or not line.strip() or line.lstrip().startswith('#'),
                                     'only single-line environment assignments supported')
                                if match:
                                    need(match.group(1) not in keys, 'duplicate environment key')
                                    keys.add(match.group(1))
                                if match and match.group(1) in GENERATION_KEYS:
                                    continue
                                kept.append(line)
                            need({'API_IMAGE', 'APP_ADMIN_DB_PASSWORD', 'APP_RUNTIME_DB_PASSWORD', 'AUTH_ADMIN_DB_PASSWORD',
                                  'AUTH_DB_PASSWORD', 'AUTHENTIK_SECRET_KEY', 'OIDC_TRUST_FILE'} <= keys,
                                 'private secret/config inventory incomplete')
                            value = ''.join(kept).encode()
                            info.size = len(value)
                            archive.addfile(info, io.BytesIO(value))
                        else:
                            with entry.open('rb') as stream:
                                owner = self
                                class Guarded:
                                    def read(self, count=-1):
                                        owner.headroom(max(0, count))
                                        return stream.read(count)
                                archive.addfile(info, Guarded())
                    else:
                        archive.addfile(info)
        target.chmod(0o600)
        return sorted(names)

    def run(self):
        need(not lifecycle.held(self.hold), 'existing capture hold requires operator review')
        self.identify()
        prior_generation = external_control(Path(self.c['external_control']))
        api_environment = dict(item.split('=', 1) for item in self.containers['api']['Config']['Env'])
        need(api_environment.get('HUB_RECOVERY_MODE') == 'enforced'
             and api_environment.get('HUB_RECOVERY_PROFILE') == 'native-workout-v1'
             and api_environment.get('HUB_RECOVERY_GENERATION') == prior_generation,
             'API must use this independently held enforced control')
        lifecycle.gate(Path(self.c['external_control']), lambda generation: lifecycle.database_status(self.containers, generation),
                       api=self.containers['api'], expected_generation=prior_generation)
        estimate = sum(int(self.sql(which, 'SELECT pg_database_size(current_database())')) for which in ('app', 'auth')) * 3
        sources = list(self.provider_volumes().values())
        sources += [Path(item['source']) for item in self.c['config_sources']]
        for source in sources:
            for entry in ([source] + list(source.rglob('*')) if source.is_dir() else [source]):
                need(entry.exists() and not entry.is_symlink(), 'missing or linked capture source')
                if entry.is_file():
                    estimate += 2 * entry.stat().st_size
        self.headroom(estimate)
        capture_id = str(uuid.uuid4())
        partial = self.spool / (capture_id + '.partial')
        partial.mkdir(mode=0o700)
        started = int(time.time())
        images = {name: {'reference': item['Config']['Image'], 'id': item['Image']}
                  for name, item in self.containers.items()}
        record = {'capture_id': capture_id, 'phase': 'holding', 'generation': prior_generation,
                  'containers': lifecycle.snapshot(self.containers)}
        self.quiesce(record)
        boundary = int(time.time())
        versions = {}
        for which in ('app', 'auth'):
            db = self.c['databases'][which]
            # External tablespace state is not part of this portable format.
            need(self.sql(which, "SELECT count(*) FROM pg_tablespace WHERE spcname NOT IN ('pg_default','pg_global')") == '0',
                 'external tablespaces unsupported')
            versions[which] = command(self.pg(which, 'pg_dump', '--version')).decode().strip()
            self.dump(self.pg(which, 'pg_dump', '-U', db['user'], '-Fc', '--create', db['name']), partial / (which + '.dump'))
            self.dump(self.pg(which, 'pg_dumpall', '-U', db['user'], '--globals-only'), partial / (which + '.globals.sql'))
            with (partial / (which + '.dump')).open('rb') as source:
                result = subprocess.run(self.pg(which, 'pg_restore', '--list'), stdin=source,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                need(result.returncode == 0, 'invalid logical archive')
        for name in ('auth-data', 'auth-templates', 'auth-certs'):
            self.copy_provider(name, partial / (name + '.tar'))
        members = self.archive([(item['source'], item['name']) for item in self.c['config_sources']],
                               partial / 'private-config.tar', env=True)
        manifest = {'format': FORMAT, 'capture_id': capture_id, 'project': self.c['project'],
                    'source_revision': self.c['source_revision'], 'prior_generation': prior_generation, 'started_unix': started,
                    'boundary_unix': boundary, 'completed_unix': int(time.time()),
                    'images': images, 'postgres_versions': versions, 'quiesced': list(WRITERS),
                    'databases': {'app': 'animus', 'auth': 'authentik'}, 'config_members': members,
                    'files': inventory(partial)}
        (partial / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True) + '\n')
        (partial / 'manifest.json').chmod(0o600)
        verify(partial)
        # Payload persistence precedes publishing/resuming. No partial directory is streamable.
        for path in partial.iterdir():
            with path.open('rb') as source:
                os.fsync(source.fileno())
        complete = self.spool / capture_id
        partial.rename(complete)
        record['phase'] = 'complete-local'
        self.save_hold(record)
        # Resume only the exact processes this successful capture stopped.
        # Restore has no corresponding resume/open path.
        self.resume_or_contain(record)
        # Keep at most two verified completed local captures; no partial/unknown
        # directory is deleted automatically. These are staging, not replicas/backups.
        prior = []
        for candidate in self.spool.iterdir():
            if candidate.is_dir() and candidate != complete:
                try:
                    prior.append((verify(candidate)['completed_unix'], candidate))
                except (OSError, ValueError, KeyError, TypeError):
                    continue
        for _, obsolete in sorted(prior, key=lambda row: row[0], reverse=True)[1:]:
            shutil.rmtree(obsolete)
        return complete

    def resume_or_contain(self, record):
        try:
            self.resume(record)
            self.hold.unlink()
        except BaseException:
            intent = Path(self.c['external_control']).parent / 'stop-intent'
            try:
                lifecycle.persist_stop(intent)
            except (OSError, ValueError):
                pass  # Still attempt containment of every owner.
            try:
                lifecycle.contain_ids([record['containers'][name]['id'] for name in WRITERS],
                                      stop_seconds=self.c['stop_seconds'], stop_intent=intent)
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
                pass  # Preserve original failure and durable capture hold.
            raise

    def resume(self, record):
        private_file(self.hold)
        need(json.loads(self.hold.read_text()) == record and record['phase'] == 'complete-local',
             'only this complete capture may resume')
        expected = record['containers']
        started = []
        def check():
            current = {name: self.inspect(expected[name]['id']) for name in WRITERS}
            need(lifecycle.snapshot(current) == expected, 'response-owner identity/environment changed')
            lifecycle.gate(Path(self.c['external_control']), lambda generation: lifecycle.database_status(self.containers, generation),
                           api=current['api'], expected_generation=record['generation'])
            need(all(current[name]['State']['Running'] is (name in started) for name in WRITERS),
                 'writer changed during capture')
            return current
        check()
        for service in reversed(WRITERS):
            if service == 'api':
                # API startup loads OIDC discovery. Direct docker start does not
                # honor Compose depends_on, so wait on stock provider health.
                deadline = time.monotonic() + 180
                while True:
                    current = check()
                    if all(current[name]['State'].get('Health', {}).get('Status') == 'healthy'
                           for name in ('auth-server', 'auth-worker')):
                        break
                    need(time.monotonic() < deadline, 'provider did not become ready before API resume')
                    time.sleep(1)
            check()
            command(['docker', 'start', expected[service]['id']])
            started.append(service)
        check()


def config(path):
    private_file(path)
    value = json.loads(path.read_text())
    need(set(value) == {'project', 'spool', 'external_control', 'source_revision', 'reserve_bytes',
                        'stop_seconds', 'databases', 'config_sources'}, 'unexpected capture configuration')
    need(re.fullmatch(r'[a-z][a-z0-9-]{2,60}', value['project']), 'explicit project required')
    need(re.fullmatch(r'[0-9a-f]{40}', value['source_revision']) and value['source_revision'] != '0' * 40, 'full source revision required')
    need(isinstance(value['reserve_bytes'], int) and value['reserve_bytes'] >= 256 * 1024 * 1024,
         'at least 256 MiB reserve required')
    need(10 <= value['stop_seconds'] <= 300, 'bounded drain required')
    need(value['databases'] == {'app': {'service': 'app-postgres', 'user': 'animus_admin', 'name': 'animus'},
                                'auth': {'service': 'auth-postgres', 'user': 'postgres', 'name': 'authentik'}},
         'unsupported database inventory')
    names = [item['name'] for item in value['config_sources']]
    need(len(set(names)) == len(names) and {'stack.env', 'docker-compose.yml', 'oidc-trust.json', 'db', 'blueprints', 'backup'} <= set(names),
         'required private configuration sources absent')
    for item in value['config_sources']:
        need(set(item) == {'name', 'source'} and Path(item['source']).is_absolute(), 'explicit absolute sources required')
        safe_name(item['name'])
    need(Path(value['spool']).is_absolute() and Path(value['external_control']).is_absolute(), 'absolute paths required')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('/etc/animus-backup/capture.json'))
    parser.add_argument('--stream', action='store_true', help='write only completed bundle tar to stdout')
    args = parser.parse_args()
    try:
        value = config(args.config)
        spool = Path(value['spool'])
        spool.mkdir(mode=0o700, parents=True, exist_ok=True)
        need(spool.stat().st_uid == os.geteuid() and not spool.stat().st_mode & 0o077, 'private spool required')
        with (spool / 'capture.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            complete = Capture(value).run()
            if args.stream:
                with tarfile.open(fileobj=sys.stdout.buffer, mode='w|') as archive:
                    for path in sorted(complete.iterdir()):
                        archive.add(path, arcname=path.name, recursive=False)
            else:
                print('PASS: verified local coherent bundle; off-host protection unqualified')
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, tarfile.TarError):
        print('FAIL: coherent capture unavailable; inspect durable hold privately; no protection claim', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
