# Gatus Monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy Gatus on ct-mgmt with 3-tier service monitoring, Telegram alerts, and a status page at `status.lan`.

**Architecture:** Gatus runs as a new Docker service in the existing ct-mgmt compose stack. It monitors all 14 services via HTTP checks at tiered intervals, sends Telegram notifications on failure/recovery, and serves a status page proxied through Caddy. Telegram credentials are loaded from a gitignored `.env` file via Gatus's native `${VAR}` substitution.

**Tech Stack:** Gatus v5.35.0, Docker Compose, Caddy reverse proxy, Telegram Bot API, Pi-hole DNS

**Spec:** `docs/superpowers/specs/2026-04-16-gatus-monitoring-design.md`

---

### Task 1: Create Gatus config

**Files:**
- Create: `stacks/ct-mgmt/gatus/config.yaml`

- [ ] **Step 1: Create the gatus directory**

```bash
mkdir -p stacks/ct-mgmt/gatus
```

- [ ] **Step 2: Write the full Gatus config**

Write `stacks/ct-mgmt/gatus/config.yaml`:

```yaml
# Gatus monitoring config
# Telegram credentials loaded from .env via ${VAR} substitution

alerting:
  telegram:
    token: "${TELEGRAM_TOKEN}"
    id: "${TELEGRAM_CHAT_ID}"
    default-alert:
      failure-threshold: 3
      success-threshold: 2
      send-on-resolved: true

# --- Tier 1: Critical (30s, alert after 2 failures) ---

endpoints:
  - name: Home Assistant
    group: critical
    url: "http://192.168.3.10:8123"
    interval: 30s
    conditions:
      - "[STATUS] < 400"
    alerts:
      - type: telegram
        failure-threshold: 2
        success-threshold: 2
        send-on-resolved: true

  - name: Frigate
    group: critical
    url: "http://192.168.3.7:5000"
    interval: 30s
    conditions:
      - "[STATUS] < 400"
    alerts:
      - type: telegram
        failure-threshold: 2
        success-threshold: 2
        send-on-resolved: true

  - name: Immich
    group: critical
    url: "http://192.168.3.9:2283"
    interval: 30s
    conditions:
      - "[STATUS] < 400"
    alerts:
      - type: telegram
        failure-threshold: 2
        success-threshold: 2
        send-on-resolved: true

  - name: Jellyfin
    group: critical
    url: "http://192.168.3.8:8096"
    interval: 30s
    conditions:
      - "[STATUS] < 400"
    alerts:
      - type: telegram
        failure-threshold: 2
        success-threshold: 2
        send-on-resolved: true

  - name: Caddy
    group: critical
    url: "http://caddy:80"
    interval: 30s
    conditions:
      - "[STATUS] < 400"
    alerts:
      - type: telegram
        failure-threshold: 2
        success-threshold: 2
        send-on-resolved: true

  # --- Tier 2: Important (60s, alert after 3 failures) ---

  - name: Portainer
    group: important
    url: "http://portainer:9000"
    interval: 60s
    conditions:
      - "[STATUS] < 400"
    alerts:
      - type: telegram

  - name: Proxmox Main
    group: important
    url: "https://192.168.3.2:8006"
    interval: 60s
    client:
      insecure: true
    conditions:
      - "[STATUS] < 400"
    alerts:
      - type: telegram

  - name: Proxmox Node
    group: important
    url: "https://192.168.3.3:8006"
    interval: 60s
    client:
      insecure: true
    conditions:
      - "[STATUS] < 400"
    alerts:
      - type: telegram

  - name: Pi-hole
    group: important
    url: "http://192.168.3.5"
    interval: 60s
    conditions:
      - "[STATUS] < 400"
    alerts:
      - type: telegram

  - name: FileBrowser
    group: important
    url: "http://192.168.3.11:8080"
    interval: 60s
    conditions:
      - "[STATUS] < 400"
    alerts:
      - type: telegram

  # --- Tier 3: Background (120s, alert after 3 failures) ---

  - name: Sonarr
    group: background
    url: "http://192.168.3.8:8989"
    interval: 120s
    conditions:
      - "[STATUS] < 400"
    alerts:
      - type: telegram

  - name: Radarr
    group: background
    url: "http://192.168.3.8:7878"
    interval: 120s
    conditions:
      - "[STATUS] < 400"
    alerts:
      - type: telegram

  - name: Deluge
    group: background
    url: "http://192.168.3.8:8112"
    interval: 120s
    conditions:
      - "[STATUS] < 400"
    alerts:
      - type: telegram

  - name: Prowlarr
    group: background
    url: "http://192.168.3.8:9696"
    interval: 120s
    conditions:
      - "[STATUS] < 400"
    alerts:
      - type: telegram

  - name: FlareSolverr
    group: background
    url: "http://192.168.3.8:8191"
    interval: 120s
    conditions:
      - "[STATUS] < 400"
    alerts:
      - type: telegram
```

Note: Tier 2 and Tier 3 endpoints use `default-alert` (failure-threshold: 3, success-threshold: 2, send-on-resolved: true). Tier 1 endpoints override with failure-threshold: 2.

- [ ] **Step 3: Validate YAML syntax**

```bash
python3 -c "import yaml; yaml.safe_load(open('stacks/ct-mgmt/gatus/config.yaml'))" && echo "Valid YAML"
```

Expected: `Valid YAML`

---

### Task 2: Create .env.example for Telegram credentials

**Files:**
- Create: `stacks/ct-mgmt/gatus/.env.example`

- [ ] **Step 1: Write the .env.example**

Write `stacks/ct-mgmt/gatus/.env.example`:

```
# Telegram Bot API token (from @BotFather)
TELEGRAM_TOKEN=your-telegram-bot-token-here

# Telegram chat ID (your personal chat with the bot)
TELEGRAM_CHAT_ID=your-chat-id-here
```

- [ ] **Step 2: Verify root .gitignore covers this path**

```bash
cd /home/psy/Documents/personal/ops/infra && git check-ignore stacks/ct-mgmt/gatus/.env && echo "IGNORED"
```

Expected: `stacks/ct-mgmt/gatus/.env` then `IGNORED`

---

### Task 3: Add Gatus service to docker-compose.yml

**Files:**
- Modify: `stacks/ct-mgmt/docker-compose.yml`

- [ ] **Step 1: Add the gatus service block**

Add after the `caddy` service block, before the `volumes:` section:

```yaml
  gatus:
    image: twinproduction/gatus:v5.35.0
    container_name: gatus
    restart: unless-stopped
    ports:
      - "8080:8080"
    env_file:
      - ./gatus/.env
    volumes:
      - ./gatus/config.yaml:/config/config.yaml:ro
```

- [ ] **Step 2: Validate YAML syntax**

```bash
python3 -c "import yaml; yaml.safe_load(open('stacks/ct-mgmt/docker-compose.yml'))" && echo "Valid YAML"
```

Expected: `Valid YAML`

---

### Task 4: Add status.lan Caddy route

**Files:**
- Modify: `stacks/ct-mgmt/Caddyfile`

- [ ] **Step 1: Add the status.lan route**

Append to `stacks/ct-mgmt/Caddyfile`:

```
http://status.lan {
	reverse_proxy gatus:8080
}
```

---

### Task 5: Commit all local changes

- [ ] **Step 1: Stage and commit**

```bash
git add stacks/ct-mgmt/gatus/config.yaml stacks/ct-mgmt/gatus/.env.example stacks/ct-mgmt/docker-compose.yml stacks/ct-mgmt/Caddyfile
git commit -m "Add Gatus monitoring with Telegram alerts and status.lan

3-tier monitoring for all 14 services (critical/important/background),
Telegram alerting on failure and recovery, status page at status.lan.
Telegram credentials loaded from gitignored .env via Gatus env substitution."
```

- [ ] **Step 2: Push to GitHub**

```bash
git push origin master
```

---

### Task 6: Deploy files to ct-mgmt

- [ ] **Step 1: Create gatus directory on ct-mgmt**

```bash
ssh root@192.168.3.2 'pct exec 108 -- mkdir -p /opt/stacks/ct-mgmt/gatus'
```

- [ ] **Step 2: Copy config and compose files to ct-mgmt**

```bash
scp stacks/ct-mgmt/gatus/config.yaml root@192.168.3.2:/tmp/gatus-config.yaml
scp stacks/ct-mgmt/docker-compose.yml root@192.168.3.2:/tmp/ct-mgmt-compose.yml
scp stacks/ct-mgmt/Caddyfile root@192.168.3.2:/tmp/ct-mgmt-caddyfile

ssh root@192.168.3.2 '
  pct push 108 /tmp/gatus-config.yaml /opt/stacks/ct-mgmt/gatus/config.yaml &&
  pct push 108 /tmp/ct-mgmt-compose.yml /opt/stacks/ct-mgmt/docker-compose.yml &&
  pct push 108 /tmp/ct-mgmt-caddyfile /opt/stacks/ct-mgmt/Caddyfile &&
  rm /tmp/gatus-config.yaml /tmp/ct-mgmt-compose.yml /tmp/ct-mgmt-caddyfile
'
```

- [ ] **Step 3: Verify files landed**

```bash
ssh root@192.168.3.2 'pct exec 108 -- ls -la /opt/stacks/ct-mgmt/gatus/config.yaml /opt/stacks/ct-mgmt/docker-compose.yml /opt/stacks/ct-mgmt/Caddyfile'
```

Expected: All three files listed with recent timestamps.

---

### Task 7: Add Pi-hole DNS entry for status.lan

- [ ] **Step 1: Add DNS record pointing status.lan to ct-mgmt**

```bash
ssh root@192.168.3.2 'pct exec 102 -- pihole-FTL --config dns.hosts'
```

Check existing entries to confirm format, then add:

```bash
ssh root@192.168.3.2 'pct exec 102 -- pihole-FTL --config dns.hosts "192.168.3.12 status.lan"'
```

Note: `pihole-FTL --config dns.hosts` is append-based. The first call (no arg) lists existing entries to see the format. The second call adds the new entry.

- [ ] **Step 2: Verify DNS resolves**

```bash
dig +short status.lan @192.168.3.5
```

Expected: `192.168.3.12`

---

### Task 8: Start Gatus and Caddy (without Telegram — test health checks first)

- [ ] **Step 1: Create a temporary .env with placeholder values**

Gatus needs the .env file to exist (docker compose will fail otherwise). Write placeholders so the container starts but Telegram is non-functional:

```bash
ssh root@192.168.3.2 'pct exec 108 -- sh -c "cat > /opt/stacks/ct-mgmt/gatus/.env && chmod 600 /opt/stacks/ct-mgmt/gatus/.env"' <<'EOF'
TELEGRAM_TOKEN=placeholder
TELEGRAM_CHAT_ID=placeholder
EOF
```

- [ ] **Step 2: Pull Gatus image and recreate services**

```bash
ssh root@192.168.3.2 'pct exec 108 -- bash -c "cd /opt/stacks/ct-mgmt && docker compose pull gatus && docker compose up -d"'
```

Expected: Gatus pulled, gatus + caddy recreated (Caddy picks up new Caddyfile).

- [ ] **Step 3: Verify Gatus is running**

```bash
ssh root@192.168.3.2 'pct exec 108 -- docker ps --filter name=gatus --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"'
```

Expected: `gatus   Up X seconds   0.0.0.0:8080->8080/tcp`

- [ ] **Step 4: Verify status page is reachable via Caddy**

```bash
curl -s -o /dev/null -w "%{http_code}" http://status.lan
```

Expected: `200`

- [ ] **Step 5: Check Gatus logs for endpoint polling**

```bash
ssh root@192.168.3.2 'pct exec 108 -- docker logs gatus --tail 30 2>&1'
```

Expected: Logs showing endpoint evaluations for all 14 services. Look for `[STATUS] < 400` condition results.

- [ ] **Step 6: Verify Gatus API returns endpoint statuses**

```bash
curl -s http://status.lan/api/v1/endpoints/statuses | python3 -c "import sys,json; data=json.load(sys.stdin); print(f'{len(data)} endpoints monitored'); [print(f'  {e[\"name\"]}: {\"UP\" if e[\"results\"][-1][\"success\"] else \"DOWN\"}') for e in data if e.get('results')]"
```

Expected: 15 endpoints listed (14 services + Caddy), most showing `UP`.

- [ ] **Step 7: Commit verification checkpoint**

At this point Gatus is running and monitoring all services. Status page works at `http://status.lan`. Telegram is not yet wired up — that's the next task.

---

### Task 9: Wire up Telegram notifications

**Prerequisite:** User must have created a Telegram bot via @BotFather and obtained:
- Bot token (e.g., `1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ`)
- Chat ID (numeric, e.g., `987654321`)

- [ ] **Step 1: Write real .env with Telegram credentials**

Replace `TOKEN` and `CHAT_ID` with real values from the user:

```bash
ssh root@192.168.3.2 'pct exec 108 -- sh -c "cat > /opt/stacks/ct-mgmt/gatus/.env && chmod 600 /opt/stacks/ct-mgmt/gatus/.env"' <<'EOF'
TELEGRAM_TOKEN=<real-bot-token>
TELEGRAM_CHAT_ID=<real-chat-id>
EOF
```

- [ ] **Step 2: Restart Gatus to pick up new env**

```bash
ssh root@192.168.3.2 'pct exec 108 -- bash -c "cd /opt/stacks/ct-mgmt && docker compose restart gatus"'
```

- [ ] **Step 3: Verify Gatus restarted cleanly**

```bash
ssh root@192.168.3.2 'pct exec 108 -- docker logs gatus --tail 10 2>&1'
```

Expected: No errors about Telegram token. Normal endpoint evaluation logs.

- [ ] **Step 4: Test alert delivery by temporarily breaking a check**

Temporarily stop a non-critical service to trigger a real alert:

```bash
ssh root@192.168.3.2 'pct exec 108 -- docker stop portainer'
```

Wait ~3 minutes (Portainer is tier 2: 60s interval, 3 failures = 3 minutes to trigger).

Expected: Telegram message received on phone indicating Portainer is down.

- [ ] **Step 5: Verify recovery alert**

```bash
ssh root@192.168.3.2 'pct exec 108 -- docker start portainer'
```

Wait ~2 minutes (success-threshold: 2, interval: 60s).

Expected: Telegram message indicating Portainer is back up.

- [ ] **Step 6: Confirm both messages received**

User confirms they received both the down and recovery Telegram messages. If not, check Gatus logs for Telegram API errors:

```bash
ssh root@192.168.3.2 'pct exec 108 -- docker logs gatus 2>&1 | grep -i telegram'
```

---

### Task 10: Update CLAUDE.md with Gatus documentation

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add Gatus to the ct-mgmt section**

In the ct-mgmt block of CLAUDE.md, add Gatus to the services listed. Update the Ports line to include 8080.

- [ ] **Step 2: Add status.lan to the Services section**

Add: `Gatus monitoring runs on ct-mgmt (http://status.lan or http://192.168.3.12:8080) for service uptime monitoring and Telegram alerting.`

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "Add Gatus monitoring to infrastructure docs"
git push origin master
```
