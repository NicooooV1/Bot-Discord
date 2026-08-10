#!/usr/bin/env bash
# provision_ct106.sh — create CT106 (discord-bot) and deploy the code. Idempotent:
# re-running updates code + restarts the service; never clobbers config.env.
# Run ON THE HOST (pve), from the source directory.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run as root on the PVE host"; exit 1; }

CTID=106
HOSTNAME=discord-bot
IP=10.3.10.106/24
GW=10.3.10.1
NAMESERVER="8.8.8.8 9.9.9.9"   # MikroTik (.1) does not serve DNS; match the host's resolvers
TEMPLATE=local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST=/opt/discord-bot

if ! pct status "$CTID" >/dev/null 2>&1; then
  echo "==> creating CT$CTID"
  pct create "$CTID" "$TEMPLATE" \
    --hostname "$HOSTNAME" --cores 2 --memory 1024 --swap 256 \
    --rootfs local-lvm:8 \
    --net0 "name=eth0,bridge=vmbr0,firewall=1,gw=$GW,ip=$IP,type=veth" \
    --nameserver "$NAMESERVER" --ostype debian --unprivileged 1 --features nesting=1 \
    --onboot 1 --description "Discord gateway bot (Proxmox monitoring + safe actions). Code: $DEST"
  pct start "$CTID"; sleep 5
else
  echo "==> CT$CTID exists; updating code only"
  pct status "$CTID" | grep -q running || { pct start "$CTID"; sleep 3; }
fi

echo "==> base packages (BEFORE the restrictive firewall, so apt/pip can reach the mirrors)"
pct exec "$CTID" -- bash -lc '
  set -e
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y --no-install-recommends python3 python3-venv python3-pip fonts-dejavu-core ca-certificates tzdata >/dev/null
  timedatectl set-timezone Europe/Paris 2>/dev/null || true
  id discordbot >/dev/null 2>&1 || useradd --system --home /opt/discord-bot --shell /usr/sbin/nologin discordbot
'

echo "==> pushing code (excludes venv/config.env/__pycache__)"
TAR=/tmp/discord-bot-src.tar.gz
tar -C "$SRC" --exclude=venv --exclude=config.env --exclude=__pycache__ --exclude='*.pyc' -czf "$TAR" .
pct exec "$CTID" -- install -d -m 0755 "$DEST"
pct push "$CTID" "$TAR" "$DEST/_src.tar.gz"
pct exec "$CTID" -- tar -C "$DEST" -xzf "$DEST/_src.tar.gz"
pct exec "$CTID" -- rm -f "$DEST/_src.tar.gz"
rm -f "$TAR"

echo "==> install inside CT (venv, deps, config.env, systemd)"
pct exec "$CTID" -- bash "$DEST/install.sh"

echo "==> applying restrictive firewall LAST"
cp "$SRC/106.fw" "/etc/pve/firewall/106.fw"
pve-firewall compile >/dev/null 2>&1 || true

echo
echo "DONE."
echo "  1) pve_setup_token.sh has printed the PVE token secrets — put them in config.env"
echo "  2) Create a READ token in the InfluxDB UI -> INFLUX_TOKEN"
echo "  3) Create the Discord bot + invite it -> DISCORD_TOKEN + GUILD_ID + channel/admin ids"
echo "  Edit:  pct exec $CTID -- nano $DEST/config.env"
echo "  Start: pct exec $CTID -- systemctl restart discord-bot"
