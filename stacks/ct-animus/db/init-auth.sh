#!/bin/bash
# Authentik owns its database/schema and performs its own migrations;
# its long-lived server/worker never receive cluster-superuser credentials.
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<'SQL'
\getenv auth_password AUTH_DB_PASSWORD
CREATE ROLE authentik LOGIN PASSWORD :'auth_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
CREATE DATABASE authentik OWNER authentik;
REVOKE ALL ON DATABASE authentik FROM PUBLIC;
SQL
