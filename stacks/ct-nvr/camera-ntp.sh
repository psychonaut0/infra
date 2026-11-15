#!/bin/sh
# Serve NTP from ct-nvr to the Hikvision cameras, which have no internet access.
#
# Run on ct-nvr as root:  ssh ct-nvr 'sh -s' < stacks/ct-nvr/camera-ntp.sh
#
# ct-nvr is a privileged LXC with CAP_SYS_TIME dropped: the Proxmox host keeps the
# clock right and the container cannot set it. chronyd therefore runs with -x
# (never touches the system clock) and only serves time. Debian's unit refuses to
# start without CAP_SYS_TIME, hence the ConditionCapability override.
# The UniFi IoT -> Server policy already lets 192.168.2.0/24 reach 192.168.3.7.
set -eu

DEBIAN_FRONTEND=noninteractive apt-get install -y -q chrony

cat > /etc/chrony/conf.d/infra-camera-ntp.conf <<'EOF'
# Hikvision cameras on the IoT VLAN (see stacks/ct-nvr/camera-ntp.sh)
allow 192.168.2.10
allow 192.168.2.11
allow 192.168.2.12
EOF

sed -i 's/^DAEMON_OPTS=.*/DAEMON_OPTS="-F 1 -x"/' /etc/default/chrony

mkdir -p /etc/systemd/system/chrony.service.d
cat > /etc/systemd/system/chrony.service.d/lxc.conf <<'EOF'
[Unit]
# LXC drops CAP_SYS_TIME; chronyd runs with -x and only serves time.
ConditionCapability=
EOF

systemctl daemon-reload
systemctl enable chrony
systemctl restart chrony
sleep 8
systemctl is-active chrony
chronyc -n tracking | grep -E 'Stratum|Leap'
ss -ulpn | grep ':123 '
