# New-guest network containment

This applies only to the new empty `ct-animus`. It never changes Proxmox host
nftables, shared firewall activation or existing guests. Creation is disconnected;
connecting the guest and starting application containers are separate gated steps.
Use private operator values; no secret appears in guard configuration.

## Policy and ordering

The dedicated guest-local `inet animus_ingress` table attaches an IPv4/IPv6
prerouting filter at priority **-150**. Conntrack runs at -200, Docker destination
NAT at -100. Packets arriving on `eth0` therefore meet this policy before any
Docker port translation, including traffic routed directly to a container IP:

| Uplink traffic | Result |
| --- | --- |
| Any IPv6 | Drop, including link-local/NDP; this guest uses IPv4 only. |
| Invalid conntrack state | Drop. |
| Established/related **reply direction** of guest/container initiated IPv4 traffic | Accept; permits DNS/package/provider responses. |
| IPv4 addressed to guest IP, admin CIDR to TCP 22 | Accept. |
| IPv4 addressed to guest IP, backup IP to TCP 22 | Accept. |
| IPv4 addressed to guest IP, central proxy IP to TCP 8095/9000 | Accept. |
| Every other new/original-direction uplink packet | Drop. |

Existing inbound sessions still traverse the source/port checks. No blanket
established-original allowance preserves an old source after policy changes.
Only `eth0` is an external interface; exact live verification checks its IPv4
address and rejects additional interfaces outside loopback and Docker bridge/veth
names. Docker-internal traffic is left to Docker's own isolation rules. Adding
an external interface or changing container networking requires a separate review.
The guard does not authenticate an IP or protect against an attacker controlling
the trusted proxy, backup/admin machine or guest root.

Why this hook: Docker's published ports are diverted before ordinary host INPUT
filtering, so UFW/INPUT-only protection is insufficient. See [Docker packet
filtering](https://docs.docker.com/engine/network/packet-filtering-firewalls/) and
the [Netfilter hook ordering](https://wiki.nftables.org/wiki-nftables/index.php/Netfilter_hooks).
This guard neither edits Docker chains nor relies on Docker's experimental nft
backend; it precedes either default DNAT implementation.

## Disconnected bootstrap

1. Parent operator runs reviewed `provision-host.sh --create-stopped`: new guest
   only, `onboot=0`, `net0 link_down=1`, 6 GiB hard limit/5 GiB memory.high.
   This stage needs a free VMID and measured capacity; an unused IP is not claimed.
2. Parent starts the disconnected guest and copies this deployment directory
   through `pct push`/console. No source checkout or container image is present.
   Through `pct exec`, run `bootstrap-ct.sh --lock`. It requires an unprivileged
   new LXC named `ct-animus`, disconnected eth0 and no installed Docker/containerd.
   It masks/stops SSH service/socket, Docker service/socket, containerd and stock
   nftables, then refuses progress if any non-loopback listening socket remains.
   Console access continues through Proxmox. Reboot at this point keeps services
   masked and starts no network listener.
3. **Before connecting net0**, parent records explicit DHCP/IPAM reservation and
   collision checks, intended admin source (including subnet-router SNAT where
   applicable), proxy/backup sources, and the exact new CT identity/config. A
   silent ping or absent neighbor entry is insufficient. Only the parent changes
   this new CT's `net0 link_down` to 0, preserving every other net0 parameter.
   No bootstrap script changes a host link or bypasses this reservation gate.
4. From `pct exec`, supply exported `CT_IPV4`, `PROXY_IPV4`, `BACKUP_IPV4`,
   `ADMIN_CIDR`, `ADDRESS_RESERVATION_VERIFIED=yes`, then run
   `bootstrap-ct.sh --install-guard`. It rechecks masks/no listeners, installs
   nftables/Python/base packages using outbound networking, validates and applies
   the dedicated table, and installs the guard's systemd dependencies. If anything
   fails, Docker and SSH remain masked; fix it through the console. Do not add
   privileges, switch to a privileged CT or activate shared firewall as a workaround.
5. Successful installation verifies exact live rules, verifies key-only SSH, and
   enables `ssh.service` behind the same guard. `ssh.socket` remains masked.
   Docker/containerd and stock `nftables.service` remain masked. Keep the stock
   nftables unit masked: its conventional `flush ruleset` would remove Docker
   and guest rules. The new Docker config disables live restore so stopping the
   daemon stops its containers; post-start verification checks the effective
   setting. Install Docker while its units remain masked; do not run
   Compose, containers or an alternate container runtime yet.

The bootstrap permission gates are operator attestations, not automatic proof.
The separate host reservation evidence and live packet checks must actually exist.
Never reuse this bootstrap on an existing guest. If bootstrap is interrupted after
writing guard configuration, inspect the stopped/masked state and complete it
manually through the console; it refuses to overwrite an existing guard blindly.

## Persistence, verification and failure behavior

`animus-ingress-guard.service` is enabled on each boot, after network-online and
before SSH/Docker. It atomically applies only its own table and records normalized
kernel rules plus a configuration hash under `/run`. Every SSH/Docker service
start requires the active guard; an additional ExecStartPre checks interface,
configuration hash and exact ordered live rules. Docker also verifies after
daemon startup. Rule counters/handles can change without invalidating the check;
priority, chain, address, port, state and verdict changes cannot.

On boot, missing config, unavailable nft capability, incorrect interface/address,
failed rule application or verification prevents SSH/Docker startup. The parent
console remains the recovery route. Guard stop/restart has **no rule removal**;
stopping the required guard also stops dependent services. Atomic replacement
failure preserves the old table, while dependent startup fails closed. A receipt
write/verify failure leaves the installed policy in place and services blocked.
Daemon restart/recreation may change Docker tables, never this dedicated table.

No process can constrain an operator with guest root. Do not manually flush the
ruleset, delete the guard or introduce new ingress interfaces while Docker runs.
This configuration verifies startup/reload; it is not a continuous watchdog for
arbitrary root rule mutations. For policy maintenance, stop Docker/socket first,
edit root-owned mode-0600 `/etc/animus-ingress-guard/config.json`, reload the guard,
verify it and repeat required source probes before restarting Docker. Keep console
access during changes; editing admin sources can intentionally close SSH.

## Checks before any application container

Local, no host mutation:

```sh
unshare --user --map-root-user --net python3 network/check-guard.py
```

The harness requires an initially empty disposable user/network namespace. It
creates synthetic veth peers, local listeners and a separate DNAT table at -100;
proves both paths initially reachable; applies the real guard; checks proxy,
admin/backup, unrelated sources, direct container routing, DB ports and IPv6;
checks egress replies, rejected atomic replacement, config/kernel drift rejection
and independent NAT-table lifecycle. It deletes no host rules. This is real kernel
policy evidence with **simulated** Docker DNAT, not LXC/systemd qualification.

On the actual new CT, while application containers remain absent:

1. `/usr/local/libexec/animus-ingress-guard verify` passes; inspect service ordering
   with `systemctl cat` and its dependency tree. Stock nftables and SSH socket
   stay masked. Verify the exact single eth0 address and expected routes.
2. From each intended source, prove admin/backup SSH works; unrelated sources and
   the proxy cannot SSH unless explicitly in the admin CIDR. Verify IPv6 denial
   using a link-local test with functioning neighbor resolution; absence of an
   IPv6 address/route alone is not enforcement evidence.
3. Use temporary controlled host listeners on 8095/9000/5432, then stop them.
   The proxy must reach only 8095/9000; admin/backup/unrelated sources must fail
   there, and everyone must fail 5432. Inspect deny counters to distinguish a
   firewall drop from a stopped listener or routing error. Include a route through
   the same subnet-router path that remote admin actually uses.
4. With console access, test failed guard configuration/reload and restart:
   old live denies survive rejected reload, service start verification rejects
   mismatches, and SSH/Docker cannot start without a valid guard. Restore the
   reviewed configuration and repeat positive/negative probes.
5. Only after these checks, explicitly unmask Docker/containerd service/socket,
   enable/start Docker, and verify the guard again. Before starting the application
   stack, use a disposable synthetic container published on the actual intended
   private port and repeat proxy/negative probes through real Docker DNAT. Remove
   it, repeat after Docker restart and a CT reboot, and confirm boot persistence.
   An empty Docker daemon starting does not authorize application-container startup.

If any path is unproven, keep application containers stopped. Record exact
configuration/image/kernel versions and sanitized outcomes privately. This
containment qualification does not open real personal data or replace coherent
backup, identity/admission and restore prerequisites.
