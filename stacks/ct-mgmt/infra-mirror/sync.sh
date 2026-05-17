#!/bin/sh
# infra release mirror sync — polls GitHub Releases for the latest infra
# CLI tag and re-publishes the artifacts to the LAN-served Caddy directory.
#
# Run by infra-mirror.timer (every 5 min). Exits 0 if there's nothing new or
# the publish succeeded; non-zero on any failure (existing manifest is left
# untouched).
set -eu

REPO="psychonaut0/infra"
TOKEN_FILE="/etc/infra-mirror/token"
STATE_FILE="/etc/infra-mirror/state.json"
PUBLISH_DIR="/var/www/infra-bin"
INSTALL_SCRIPT_SRC="/opt/infra-mirror/install.sh"

require() {
    command -v "$1" >/dev/null 2>&1 || { echo "missing dependency: $1" >&2; exit 2; }
}
require curl
require jq
require sha256sum

[ -r "$TOKEN_FILE" ] || { echo "missing $TOKEN_FILE" >&2; exit 2; }
TOKEN=$(cat "$TOKEN_FILE")
AUTH="Authorization: Bearer ${TOKEN}"
ACCEPT="Accept: application/vnd.github+json"

API="https://api.github.com/repos/${REPO}/releases/latest"
release_json=$(curl -fsSL -H "$AUTH" -H "$ACCEPT" "$API")

tag=$(echo "$release_json" | jq -r '.tag_name')
[ -n "$tag" ] && [ "$tag" != "null" ] || { echo "no tag in release JSON" >&2; exit 1; }

last_tag=""
[ -f "$STATE_FILE" ] && last_tag=$(jq -r '.last_tag // ""' < "$STATE_FILE" 2>/dev/null || true)
if [ "$tag" = "$last_tag" ]; then
    exit 0
fi

published_at=$(echo "$release_json" | jq -r '.published_at')
sha=$(echo "$release_json" | jq -r '.target_commitish' | cut -c1-7)

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

ARCHES="amd64 arm64"
for arch in $ARCHES; do
    bin_name="infra-linux-${arch}"
    bin_url=$(echo "$release_json" | jq -r ".assets[] | select(.name==\"${bin_name}\") | .url")
    sha_url=$(echo "$release_json" | jq -r ".assets[] | select(.name==\"${bin_name}.sha256\") | .url")
    [ -n "$bin_url" ] && [ "$bin_url" != "null" ] || { echo "asset ${bin_name} not found in release" >&2; exit 1; }

    curl -fsSL -H "$AUTH" -H "Accept: application/octet-stream" "$bin_url"  -o "$work/$bin_name"
    curl -fsSL -H "$AUTH" -H "Accept: application/octet-stream" "$sha_url" -o "$work/$bin_name.sha256"

    expected=$(awk '{print $1}' "$work/$bin_name.sha256")
    actual=$(sha256sum "$work/$bin_name" | awk '{print $1}')
    [ "$expected" = "$actual" ] || { echo "sha256 mismatch for $bin_name" >&2; exit 1; }
    echo "$arch:$expected" >> "$work/sums"
done

# Stage the publish dir.
mkdir -p "$PUBLISH_DIR/linux/amd64" "$PUBLISH_DIR/linux/arm64"
for arch in $ARCHES; do
    bin_name="infra-linux-${arch}"
    install -m 0755 "$work/$bin_name" "$PUBLISH_DIR/linux/${arch}/infra.new"
    mv "$PUBLISH_DIR/linux/${arch}/infra.new" "$PUBLISH_DIR/linux/${arch}/infra"
done

# Refresh install.sh.
[ -f "$INSTALL_SCRIPT_SRC" ] && install -m 0755 "$INSTALL_SCRIPT_SRC" "$PUBLISH_DIR/install.sh"

# Build manifest.
sum_amd64=$(awk -F: '$1=="amd64"{print $2}' "$work/sums")
sum_arm64=$(awk -F: '$1=="arm64"{print $2}' "$work/sums")
cat > "$work/manifest.json" <<EOF
{
  "version": "${tag}",
  "commit": "${sha}",
  "published_at": "${published_at}",
  "binaries": {
    "linux/amd64": {
      "url": "http://infra-bin.lan/linux/amd64/infra",
      "sha256": "${sum_amd64}"
    },
    "linux/arm64": {
      "url": "http://infra-bin.lan/linux/arm64/infra",
      "sha256": "${sum_arm64}"
    }
  }
}
EOF
mv "$work/manifest.json" "$PUBLISH_DIR/manifest.json"

# Persist state.
mkdir -p "$(dirname "$STATE_FILE")"
echo "{\"last_tag\":\"${tag}\"}" > "$STATE_FILE"

echo "published ${tag}"
