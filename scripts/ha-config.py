#!/usr/bin/env python3
"""Diff, validate and apply the reviewed Home Assistant configuration files.

Uses SSH and HA's own installed checker. No API token or local dependencies needed.
Applying writes files only; activation is a separate reload/restart (see runbook).
"""

import argparse
import difflib
import inspect
import json
from pathlib import Path
import shlex
import subprocess
import sys


MANAGED = ("configuration.yaml", "automations.yaml", "scripts.yaml", "scenes.yaml",
           "dashboards/casa.yaml", "dashboards/impostazioni.yaml", "tools/camera_power.py")
CONFIG = Path(__file__).resolve().parents[1] / "stacks/ct-ha/config"


def remote(request, live="/config"):
    """Runs inside HA's existing container; also accepts a temp root for tests."""
    from datetime import datetime, timezone
    import fnmatch
    import os
    from pathlib import Path
    import shutil
    import subprocess
    import sys
    import tempfile

    names = ("configuration.yaml", "automations.yaml", "scripts.yaml", "scenes.yaml",
             "dashboards/casa.yaml", "dashboards/impostazioni.yaml", "tools/camera_power.py")
    live = Path(live)
    def read(name):
        path = live / name
        return path.read_bytes() if path.exists() else None
    before = {name: read(name) for name in names}
    if request["operation"] == "read":
        return {name: content.decode() if content is not None else None
                for name, content in before.items()}
    if request["operation"] not in ("check", "apply"):
        raise ValueError("Unknown operation")
    files = request["files"]
    if set(files) != set(names) or not all(isinstance(v, str) for v in files.values()):
        raise ValueError("Payload must contain exactly the managed configuration files")
    changed = [name for name in names if files[name].encode() != before[name]]

    def ignore(directory, entries):
        excluded = {"__pycache__", ".cache"}
        if Path(directory) == live:
            excluded |= {"backups", "tts", ".cloud", ".infra-backups", ".ha_run.lock"}
        return [name for name in entries if name in excluded or any(
            fnmatch.fnmatch(name, pattern)
            for pattern in ("*.db", "*.db-*", "*.log*", ".infra-*")
        )]

    # Includes private secrets, custom components, themes, blueprints and a COPY
    # of the registries. Checker migrations/writes cannot reach live .storage.
    with tempfile.TemporaryDirectory(prefix="infra-ha-") as temporary:
        stage = Path(temporary) / "config"
        shutil.copytree(live, stage, ignore=ignore)
        for name, content in files.items():
            (stage / name).parent.mkdir(parents=True, exist_ok=True)
            (stage / name).write_text(content)
        # Compile the deployed helper without writing bytecode.
        compile(files["tools/camera_power.py"], "tools/camera_power.py", "exec")
        checked = subprocess.run(
            [sys.executable, "-m", "homeassistant", "--script", "check_config",
             "--config", str(stage)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=180,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    result = {"valid": checked.returncode == 0, "output": checked.stdout,
              "changed": changed, "applied": False}
    if not result["valid"] or request["operation"] == "check" or not changed:
        return result
    # Do not clobber UI edits made while the checker was running.
    if any(read(name) != before[name] for name in names):
        raise RuntimeError("Live YAML changed during validation; run diff and retry")

    backup_root = live / ".infra-backups"
    backup_root.mkdir(mode=0o700, exist_ok=True)
    backup = Path(tempfile.mkdtemp(
        prefix=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-"), dir=backup_root,
    ))
    for name in names:
        if before[name] is not None:
            (backup / name).parent.mkdir(parents=True, exist_ok=True)
            (backup / name).write_bytes(before[name])
            (backup / name).chmod(0o600)
    import json
    manifest = backup / "manifest.json"
    manifest.write_text(json.dumps({name: before[name] is not None for name in names}))
    manifest.chmod(0o600)

    def replace(name, content):
        target = live / name
        if content is None:
            target.unlink(missing_ok=True)
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        metadata = target.stat() if target.exists() else live.stat()
        mode = target.stat().st_mode & 0o777 if target.exists() else 0o644
        fd, temporary = tempfile.mkstemp(prefix=".infra-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, mode)
            os.chown(temporary, metadata.st_uid, metadata.st_gid)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    written = []
    try:
        for name in changed:
            replace(name, files[name].encode())
            written.append(name)
    except Exception:
        for name in reversed(written):
            replace(name, before[name])
        raise
    return {**result, "applied": True, "backup": str(backup)}


def invoke(host, request):
    source = "import json,sys\n" + inspect.getsource(remote)
    source += "\nprint(json.dumps(remote(json.load(sys.stdin))))\n"
    command = "docker exec -i homeassistant python -c " + shlex.quote(source)
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    # Explicit user config avoids the workstation's system SSH include issue.
    user_config = Path.home() / ".ssh/config"
    if user_config.is_file():
        ssh += ["-F", str(user_config)]
    completed = subprocess.run(
        [*ssh, "--", host, command], input=json.dumps(request),
        text=True, stdout=subprocess.PIPE, check=True, timeout=240,
    )
    return json.loads(completed.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("diff", "check", "apply"))
    parser.add_argument("--host", default="ct-ha", help="SSH alias (default: ct-ha)")
    args = parser.parse_args()
    files = {name: (CONFIG / name).read_text() for name in MANAGED}
    if args.operation == "diff":
        live = invoke(args.host, {"operation": "read"})
        different = False
        for name in MANAGED:
            if live[name] != files[name]:
                different = True
                sys.stdout.writelines(difflib.unified_diff(
                    (live[name] or "").splitlines(keepends=True), files[name].splitlines(keepends=True),
                    fromfile="live/" + name, tofile="repo/" + name,
                ))
        if not different:
            print("Managed HA YAML matches the live instance.")
        return 2 if different else 0

    result = invoke(args.host, {"operation": args.operation, "files": files})
    print(result["output"], end="" if result["output"].endswith("\n") else "\n")
    if not result["valid"]:
        print("Validation failed; live YAML was not changed.", file=sys.stderr)
        return 1
    print("Staged HA configuration is valid.")
    if args.operation == "check":
        return 0
    if not result["applied"]:
        print("No YAML changes to apply.")
        return 0
    print("Updated: " + ", ".join(result["changed"]))
    print("Previous YAML: " + result["backup"])
    print("Files saved; activate with the matching API reload or 'infra restart homeassistant'.")
    if "configuration.yaml" in result["changed"]:
        print("configuration.yaml changed: a controlled HA restart may be required.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"ha-config: {exc}", file=sys.stderr)
        sys.exit(1)
