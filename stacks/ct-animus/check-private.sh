#!/bin/bash
# Run separately from an approved LAN/remote tailnet client and container netns.
# No tokens, response bodies, or HTTP debug logs are printed. LAN HTTP is unencrypted.
set -euo pipefail
for key in API_FQDN LOGIN_FQDN PROXY_IPV4 OIDC_SLUG; do
  [[ -n ${!key:-} ]] || { echo "Missing $key" >&2; exit 1; }
done
[[ "$API_FQDN" =~ ^[a-z0-9.-]+$ && "$LOGIN_FQDN" =~ ^[a-z0-9.-]+$ && "$OIDC_SLUG" =~ ^[a-z0-9-]+$ ]] || exit 1
for host in "$API_FQDN" "$LOGIN_FQDN"; do
  # Actual resolver must already work; --resolve would hide remote split-DNS bugs.
  addresses=$(getent ahostsv4 "$host" | awk '{print $1}' | sort -u)
  [[ "$addresses" == "$PROXY_IPV4" ]] || { echo 'FAIL: canonical host does not resolve exclusively to the private proxy' >&2; exit 1; }
done
status=$(curl --noproxy '*' --silent --output /dev/null --write-out '%{http_code}' --max-time 15 "http://${API_FQDN}/healthz")
[[ "$status" == 200 ]] || { echo 'FAIL: API HTTP liveness' >&2; exit 1; }
discovery=$(curl --noproxy '*' --fail --silent --max-time 15 "http://${LOGIN_FQDN}/application/o/${OIDC_SLUG}/.well-known/openid-configuration")
EXPECTED_ISSUER="http://${LOGIN_FQDN}/application/o/${OIDC_SLUG}/" jq -e \
  '.issuer == env.EXPECTED_ISSUER and .jwks_uri == (env.EXPECTED_ISSUER + "jwks/")' \
  <<< "$discovery" >/dev/null
echo 'PASS: actual split DNS, API liveness and canonical issuer/JWKS discovery'
echo 'Qualified strong-policy loading and actual recent-owner proof, authenticated persisted readiness, container discovery/UserInfo, firewall negative probes, factor recovery and isolated restoration remain separate required checks.'
