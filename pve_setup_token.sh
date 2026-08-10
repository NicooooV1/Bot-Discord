#!/usr/bin/env bash
# pve_setup_token.sh — create the least-privilege PVE API user, roles and TWO tokens
# for the Discord bot. Idempotent. Run ON THE HOST (pve).
#
# Privilege model (privsep=1 => effective perms = user-ACL ∩ token-ACL, so we grant
# each role to BOTH the user and the token):
#   discordbot@pve!bot      -> PVEAuditor at /                 (read everything, incl. journal)
#   discordbot@pve!actions  -> BotSafeActions at /vms          (VM.PowerMgmt,VM.Backup,VM.Audit)
#                            + PVEDatastoreUser at /storage/<pbs>  (Datastore.AllocateSpace for vzdump)
#
# Newly-created token secrets are printed ONCE below (value=...). Copy them into
# CT106 /opt/discord-bot/config.env (PVE_TOKEN_SECRET / PVE_ACTION_TOKEN_SECRET).
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run as root on the PVE host"; exit 1; }

USER="discordbot@pve"
PBS_STORAGE="${PBS_STORAGE:-pbs}"

echo "==> user $USER"
pveum user add "$USER" --comment "Discord bot API (monitoring + safe actions)" 2>/dev/null \
  && echo "   created" || echo "   already exists"

echo "==> role BotSafeActions (power mgmt + backup)"
if pveum role list --output-format json 2>/dev/null | grep -q '"BotSafeActions"'; then
  pveum role modify BotSafeActions --privs "VM.PowerMgmt,VM.Backup,VM.Audit"
else
  pveum role add BotSafeActions --privs "VM.PowerMgmt,VM.Backup,VM.Audit"
fi

echo "==> role BotSyslog (journal read — /nodes/pve/journal needs Sys.Syslog, NOT Sys.Audit)"
if pveum role list --output-format json 2>/dev/null | grep -q '"BotSyslog"'; then
  pveum role modify BotSyslog --privs "Sys.Syslog"
else
  pveum role add BotSyslog --privs "Sys.Syslog"
fi

echo "==> tokens (privsep=1)"
pveum user token add "$USER" bot     --privsep 1 --comment "read-only (PVEAuditor)" 2>/dev/null \
  || echo "   token !bot already exists (secret not reprinted; remove+re-add to rotate)"
pveum user token add "$USER" actions --privsep 1 --comment "safe actions only" 2>/dev/null \
  || echo "   token !actions already exists (secret not reprinted; remove+re-add to rotate)"

echo "==> ACLs (granted to BOTH user and token)"
pveum acl modify /                       --users  "$USER"             --roles PVEAuditor
pveum acl modify /                       --tokens "${USER}!bot"       --roles PVEAuditor
pveum acl modify /                       --users  "$USER"             --roles BotSyslog
pveum acl modify /                       --tokens "${USER}!bot"       --roles BotSyslog
pveum acl modify /vms                    --users  "$USER"             --roles BotSafeActions
pveum acl modify /vms                    --tokens "${USER}!actions"   --roles BotSafeActions
pveum acl modify "/storage/$PBS_STORAGE" --users  "$USER"             --roles PVEDatastoreUser
pveum acl modify "/storage/$PBS_STORAGE" --tokens "${USER}!actions"   --roles PVEDatastoreUser

echo
echo "DONE. If a token was newly created its secret 'value=...' is printed above."
echo "To rotate a lost secret:  pveum user token remove $USER bot  (then re-run this script)."
