#!/usr/bin/env python3
"""Secret-safe Compose/identity policy validation; never starts containers."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
PROFILE = "rs256-exact-issuer-singleton-client-audience-online-userinfo"


def require(ok, message):
    if not ok:
        raise ValueError(message)


def private_ip(value):
    address = ipaddress.ip_address(value)
    require(address.version == 4 and address.is_private and not address.is_loopback
            and not address.is_link_local and not address.is_unspecified,
            "use a reserved private IPv4 interface/resolver")


def read_env(path):
    require(path.stat().st_mode & 0o077 == 0, ".env must have mode 0600")
    values = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        require(key not in values, "duplicate .env key")
        require(re.fullmatch(r"[A-Z][A-Z0-9_]*", key), "invalid .env key")
        require(value and not any(c in value for c in "\"'$`\\"),
                "use literal unquoted .env values; shell/dotenv expansion prohibited")
        values[key] = value
    return values


def validate(env_path, synthetic=False, inspect_image=False):
    values = read_env(env_path)
    for key in ("CT_IPV4", "PROXY_IPV4", "PRIVATE_DNS_IPV4"):
        private_ip(values[key])
    require(values["CT_IPV4"] != values["PROXY_IPV4"], "proxy must be a different CT")
    hostname = values["LOGIN_FQDN"]
    require(re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", hostname), "invalid login FQDN")
    if not synthetic:
        require(hostname.endswith(".lan"),
                "phase 1 requires a temporary privately resolved .lan login name")
    require(re.fullmatch(r"local/animus-api:sha-[0-9a-f]{12,64}", values["API_IMAGE"]),
            "use a private local API tag identifying the build revision")
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", values["API_IMAGE_ID"]),
            "record the exact privately loaded API image ID")
    passwords = [values[key] for key in (
        "APP_ADMIN_DB_PASSWORD", "APP_RUNTIME_DB_PASSWORD", "AUTH_ADMIN_DB_PASSWORD", "AUTH_DB_PASSWORD")]
    require(all(re.fullmatch(r"[0-9a-f]{64}", password) for password in passwords),
            "database secrets must each be 64 lowercase hex characters")
    require(len(set(passwords)) == len(passwords), "database role secrets must be distinct")
    require(re.fullmatch(r"[0-9a-f]{128}", values["AUTHENTIK_SECRET_KEY"]),
            "Auth secret must be 128 lowercase hex characters")
    # Avoid ambient shell variables overriding --env-file and leaking credentials
    # in Compose diagnostics. Only bounded generic failures reach stdout/stderr.
    clean_env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "TMPDIR") if key in os.environ}
    clean_env["COMPOSE_DISABLE_ENV_FILE"] = "1"
    result = subprocess.run(["docker", "compose", "--project-directory", str(ROOT),
                             "--env-file", str(env_path), "-f", str(ROOT / "docker-compose.yml"),
                             "--profile", "*", "config", "--format", "json"],
                            env=clean_env, capture_output=True, text=True)
    require(result.returncode == 0, "Compose config failed; check private inputs without exporting rendered secrets")
    config = json.loads(result.stdout)
    services = config["services"]
    require(set(services) == {"app-postgres", "auth-postgres", "auth-server", "auth-worker", "api", "migrate"},
            "unexpected service; scope is API, Auth and two databases only")
    for name, service in services.items():
        require(not service.get("privileged") and not service.get("network_mode"), "privileged/host networking prohibited")
        require(not service.get("build"), "no host source build allowed")
        for volume in service.get("volumes", []):
            require("docker.sock" not in volume.get("source", ""), "Docker socket prohibited")
        require(service.get("restart") == ("no" if name == "migrate" else "unless-stopped"),
                "unexpected restart policy")
        if name.endswith("postgres"):
            require(not service.get("ports"), "databases must not publish host ports")
            require("@sha256:" in service["image"], "PostgreSQL digest pin required")
        if name.startswith("auth-") and not name.endswith("postgres"):
            require("@sha256:" in service["image"] and service.get("user") == "1000:1000", "Auth image pin/non-root user required")
            require(not any(key.startswith("AUTHENTIK_BOOTSTRAP") for key in service["environment"]),
                    "automatic production bootstrap prohibited")
            require(service["environment"]["AUTHENTIK_LISTEN__TRUSTED_PROXY_CIDRS"] == values["PROXY_IPV4"] + "/32",
                    "Auth may trust only the central proxy")
    for name, port in (("api", 8095), ("auth-server", 9000)):
        ports = services[name].get("ports", [])
        require(len(ports) == 1 and ports[0]["host_ip"] == values["CT_IPV4"]
                and ports[0]["target"] == port and int(ports[0]["published"]) == port,
                "upstreams must bind only the reserved private interface")
    api = services["api"]
    require(api["environment"].get("HUB_OIDC_PRIVATE_HTTP_ORIGIN") == "http://" + hostname,
            "private HTTP opt-in must equal the exact configured LAN login origin")
    require(api.get("profiles") == ["commissioning"] and services["migrate"].get("profiles") == ["operator"],
            "API/migration require deliberate commissioning/operator selection")
    require(api.get("user") == "65532:65532" and api.get("read_only") and api.get("pull_policy") == "never",
            "API must use the read-only privately loaded non-root image")
    for flag in ("HUB_TRAINING_SUMMARY_READS_ENABLED", "HUB_TRAINING_SUMMARY_WORKERS_ENABLED", "HUB_USER_WORKOUT_PURGE_ENABLED"):
        require(api["environment"].get(flag) == "false", "summary/purge capabilities must remain disabled")
    require("animus_runtime:" in api["environment"]["HUB_DATABASE_URL"] and
            "animus_admin:" in services["migrate"]["environment"]["HUB_DATABASE_URL"],
            "migration and runtime database roles must differ")
    mount = next(v for v in api["volumes"] if v["target"] == "/run/animus/oidc-trust.json")
    registry_path = Path(mount["source"])
    require(mount.get("read_only") and registry_path.is_file() and not registry_path.is_symlink(), "private regular read-only OIDC registry required")
    require(registry_path.stat().st_mode & 0o077 == 0, "OIDC registry must have mode 0600")
    if not synthetic:
        require(registry_path.stat().st_uid == 65532, "OIDC registry must be readable by API UID 65532")
    registry = json.loads(registry_path.read_text())
    require(registry.get("version") == 1 and len(registry.get("providers", [])) == 1,
            "exactly one deliberately qualified provider required")
    provider = registry["providers"][0]
    issuer = provider["issuer"]
    require(re.fullmatch(r"http://" + re.escape(hostname) + r"/application/o/[a-z0-9-]+/", issuer), "issuer must use the canonical login host/path")
    require(provider["profile"] == PROFILE and provider["client_id"] == provider["audience"], "unsupported trust profile/audience")
    require(provider["discovery_url"] == issuer + ".well-known/openid-configuration" and
            provider["jwks_uri"] == issuer + "jwks/" and
            provider["userinfo_endpoint"] == "http://" + hostname + "/application/o/userinfo/", "provider endpoint mismatch")
    if inspect_image:
        inspected = subprocess.run(["docker", "image", "inspect", values["API_IMAGE"]],
                                   capture_output=True, text=True, env=clean_env)
        require(inspected.returncode == 0, "private API image not inspectable on local daemon")
        actual = json.loads(inspected.stdout)[0]
        require(actual["Id"] == values["API_IMAGE_ID"] and actual["Config"]["User"] == "65532:65532",
                "loaded API image ID or non-root user mismatch")
    print("PASS: secret-safe Compose policy and exact OIDC configuration (no host/runtime qualification)")


def self_test():
    with tempfile.TemporaryDirectory(prefix="animus-config-") as directory:
        root = Path(directory)
        trust = json.loads((ROOT / "oidc-trust.json.example").read_text().replace("REPLACE_APPROVED_SLUG", "synthetic").replace("REPLACE_APPROVED_CLIENT_ID", "synthetic"))
        registry = root / "oidc.json"
        registry.write_text(json.dumps(trust))
        registry.chmod(0o600)
        values = dict(CT_IPV4="192.168.100.20", PROXY_IPV4="192.168.100.12", PRIVATE_DNS_IPV4="192.168.100.5",
                      LOGIN_FQDN="auth.lan", API_IMAGE="local/animus-api:sha-" + "a" * 40,
                      API_IMAGE_ID="sha256:" + "b" * 64, OIDC_TRUST_FILE=str(registry),
                      APP_ADMIN_DB_PASSWORD="a" * 64, APP_RUNTIME_DB_PASSWORD="b" * 64,
                      AUTH_ADMIN_DB_PASSWORD="c" * 64, AUTH_DB_PASSWORD="d" * 64, AUTHENTIK_SECRET_KEY="e" * 128)
        env = root / ".env"
        def write():
            env.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
            env.chmod(0o600)
        write()
        validate(env, synthetic=True)
        for key, invalid in (("CT_IPV4", "0.0.0.0"), ("API_IMAGE", "public/image:latest"),
                             ("APP_RUNTIME_DB_PASSWORD", values["APP_ADMIN_DB_PASSWORD"])):
            old = values[key]
            values[key] = invalid
            write()
            try:
                validate(env, synthetic=True)
            except ValueError:
                pass
            else:
                raise ValueError("negative policy case unexpectedly accepted")
            values[key] = old
        print("PASS: unsafe interface, public image and reused-role-secret rejection")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--inspect-image", action="store_true")
    parser.add_argument("--self-test", action="store_true", help="disposable synthetic config only; no daemon needed")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
        else:
            validate(args.env_file.resolve(), inspect_image=args.inspect_image)
    except (ValueError, KeyError, OSError, json.JSONDecodeError, StopIteration):
        # Never emit values, rendered config, command stderr, passwords or tokens.
        print("FAIL: private configuration did not pass policy; inspect inputs locally without exporting secrets")
        raise SystemExit(1)
