#!/bin/bash
# Fresh-volume initialization only; forward migrations run separately.
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\getenv runtime_password APP_RUNTIME_DB_PASSWORD
CREATE ROLE animus_runtime LOGIN PASSWORD :'runtime_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
REVOKE ALL ON DATABASE animus FROM PUBLIC;
GRANT CONNECT ON DATABASE animus TO animus_runtime;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO animus_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE animus_admin IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO animus_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE animus_admin IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO animus_runtime;
SQL
