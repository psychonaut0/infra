#!/bin/bash
# The fleet b2.env is shell syntax (export assignments), not EnvironmentFile.
set -euo pipefail
source /etc/restic/b2.env
exec /usr/bin/restic check --read-data-subset '5%'
