#!/usr/bin/env bash
# install.sh — idempotent install INSIDE CT106. Builds venv, deps, config.env (0600
# only if absent), systemd unit. Mirrors the proven discord-syslog install pattern.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run as root inside CT106"; exit 1; }

DEST=/opt/discord-bot
UNIT=discord-bot.service

id discordbot >/dev/null 2>&1 || useradd --system --home "$DEST" --shell /usr/sbin/nologin discordbot

echo "==> venv + pinned deps"
[ -x "$DEST/venv/bin/python" ] || python3 -m venv "$DEST/venv"
"$DEST/venv/bin/pip" install --quiet --upgrade pip wheel
"$DEST/venv/bin/pip" install --quiet -r "$DEST/requirements.txt"

install -d -m 0700 -o discordbot -g discordbot "$DEST/.mplcache"

echo "==> config.env (created 0600 only if absent — never clobbers secrets)"
if [ ! -f "$DEST/config.env" ]; then
  install -m 0600 -o discordbot -g discordbot "$DEST/config.env.example" "$DEST/config.env"
  echo "   created $DEST/config.env — FILL DISCORD_TOKEN / INFLUX_TOKEN / PVE_*_SECRET."
else
  chmod 0600 "$DEST/config.env"; chown discordbot:discordbot "$DEST/config.env"
fi

chown -R discordbot:discordbot "$DEST"

echo "==> systemd unit"
install -m 0644 "$DEST/discord-bot.service" "/etc/systemd/system/$UNIT"
systemctl daemon-reload
systemctl enable "$UNIT" >/dev/null 2>&1 || true

if grep -qE '^DISCORD_TOKEN=[^[:space:]]' "$DEST/config.env"; then
  systemctl restart "$UNIT"; sleep 1
  systemctl --no-pager --full status "$UNIT" | head -n 12 || true
else
  echo "   Service enabled but NOT started (DISCORD_TOKEN empty)."
  echo "   Fill $DEST/config.env then: systemctl start $UNIT"
fi
