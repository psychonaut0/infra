#!/bin/sh
# infra CLI bootstrap installer — fetched as `curl -fsSL http://infra-bin.lan/install.sh | sh`.
# Detects arch, downloads the matching binary from the LAN mirror, verifies
# its sha256 against manifest.json, and installs to /usr/local/bin/infra.
set -eu

MIRROR="${INFRA_MIRROR:-http://infra-bin.lan}"
DEST="${INFRA_INSTALL_DIR:-/usr/local/bin}"

case "$(uname -m)" in
    x86_64)  ARCH=amd64 ;;
    aarch64) ARCH=arm64 ;;
    *) echo "unsupported arch: $(uname -m)" >&2; exit 1 ;;
esac

require() {
    command -v "$1" >/dev/null 2>&1 || { echo "missing dependency: $1" >&2; exit 2; }
}
require curl
require sha256sum
require python3

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT

curl -fsSL "$MIRROR/linux/$ARCH/infra" -o "$tmp"
expected=$(curl -fsSL "$MIRROR/manifest.json" | python3 -c "import sys, json; print(json.load(sys.stdin)['binaries']['linux/${ARCH}']['sha256'])")
actual=$(sha256sum "$tmp" | awk '{print $1}')
if [ "$expected" != "$actual" ]; then
    echo "checksum mismatch (got $actual, expected $expected)" >&2
    exit 1
fi

install -m 0755 "$tmp" "$DEST/infra"
echo "installed $DEST/infra ($("$DEST/infra" version 2>/dev/null || echo 'no version subcommand yet'))"
