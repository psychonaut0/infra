#!/bin/sh
# Block the Hikvision cameras (IoT VLAN) from reaching the internet.
#
# Run on the workstation:  sh scripts/unifi-block-camera-internet.sh
# Needs the UniFi API key in ~/.config/unifi/api-key (outside this repo).
#
# Custom zone policies are evaluated before UniFi's predefined IoT -> External
# "Allow All", so this wins. LAN paths are untouched: Frigate on ct-nvr still pulls
# RTSP (Server -> IoT) and the cameras still reach ct-nvr's NTP (IoT -> Server).
# Idempotent: does nothing if a policy with the same name already exists.
set -eu

KEY=$(cat "$HOME/.config/unifi/api-key")
API=https://192.168.1.1/proxy/network/v2/api/site/default
NAME="Block cameras to internet"
IOT_ZONE=68d6566ed17b564715c2d1c3
EXTERNAL_ZONE=68d63976d17b564715c2cee2

if curl -sfk -H "X-API-Key: $KEY" "$API/firewall-policies" | grep -q "\"name\":\"$NAME\""; then
    echo "Policy '$NAME' already exists; nothing to do."
    exit 0
fi

curl -sfk -X POST -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
    "$API/firewall-policies" --data @- <<EOF | head -c 300; echo
{
  "name": "$NAME",
  "description": "Hikvision cameras stay LAN-only; NTP comes from ct-nvr (stacks/ct-nvr/camera-ntp.sh)",
  "enabled": true,
  "action": "BLOCK",
  "protocol": "all",
  "ip_version": "BOTH",
  "connection_state_type": "ALL",
  "connection_states": [],
  "create_allow_respond": false,
  "logging": false,
  "match_ip_sec": false,
  "match_opposite_protocol": false,
  "icmp_typename": "ANY",
  "icmp_v6_typename": "ANY",
  "schedule": {"mode": "ALWAYS"},
  "source": {
    "zone_id": "$IOT_ZONE",
    "matching_target": "IP",
    "matching_target_type": "SPECIFIC",
    "ips": ["192.168.2.10", "192.168.2.11", "192.168.2.12"],
    "match_opposite_ips": false,
    "port_matching_type": "ANY",
    "match_opposite_ports": false
  },
  "destination": {
    "zone_id": "$EXTERNAL_ZONE",
    "matching_target": "ANY",
    "port_matching_type": "ANY",
    "match_opposite_ports": false
  }
}
EOF
