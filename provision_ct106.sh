#!/usr/bin/env bash
# provision_ct106.sh — crée CT106 (discord-bot) et y déploie le code. Idempotent :
# relancé, il met à jour le code et redémarre le service ; n'écrase jamais config.env.
# À lancer SUR L'HÔTE (pve), depuis le répertoire des sources.
#
#     ./provision_ct106.sh            # refuse si des modifications ne sont pas commitées
#                                     # (et si du .py neuf n'est dans aucun commit)
#     ./provision_ct106.sh --force    # déploie quand même le dernier commit
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "à lancer en root sur l'hôte PVE"; exit 1; }

CTID=106
HOSTNAME=discord-bot
IP=10.3.10.106/24
GW=10.3.10.1
NAMESERVER="8.8.8.8 9.9.9.9"   # MikroTik (.1) ne sert pas de DNS ; on copie les résolveurs de l'hôte
TEMPLATE=local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST=/opt/discord-bot
FORCE=0
case "${1:-}" in
  "") ;;
  --force) FORCE=1 ;;
  *) echo "option inconnue: $1 (seule --force existe)"; exit 2 ;;
esac

# ---------------------------------------------------------------------------
# LE CODE EST POUSSÉ PAR GIT, PAS PAR UN TAR EXTRAIT PAR-DESSUS L'EXISTANT.
#
# POURQUOI (constat d'audit 2026-08-11) : `tar -x` ne SUPPRIME rien. Un fichier retiré
# de la source survivait donc dans /opt et continuait d'être chargé — un cog supprimé
# restait actif, un module « corrigé en le supprimant » revenait par la fenêtre, et
# personne ne pouvait dire quelle version tournait réellement. `git reset --hard` suivi
# de `git clean` rend /opt/discord-bot IDENTIQUE au commit, aux fichiers ignorés près
# (config.env, venv/, servarr-apis.json) qui, eux, doivent survivre — c'est exactement
# ce que .gitignore décrit déjà.
#
# Bénéfice second, et ce n'est pas le moindre : /opt/discord-bot devient un vrai dépôt,
# donc `deploy.sh` peut revenir en arrière tout seul quand un démarrage tourne mal.
# ---------------------------------------------------------------------------
git -C "$SRC" rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
  echo "$SRC n'est pas un dépôt git — le déploiement en dépend (purge des fichiers"
  echo "supprimés, rollback de deploy.sh). À faire une fois :"
  echo "    git init && git add -A && git commit -m 'baseline'"
  exit 1
}
RACINE_GIT="$(git -C "$SRC" rev-parse --show-toplevel)"
# `pwd -P` des deux côtés : git rend un chemin PHYSIQUE, et $SRC peut être atteint par un
# lien symbolique — les comparer bruts refuserait un dépôt parfaitement valide.
if [ "$(cd "$RACINE_GIT" && pwd -P)" != "$(cd "$SRC" && pwd -P)" ]; then
  # Le bundle emporte le dépôt ENTIER : si les sources ne sont qu'un sous-répertoire,
  # /opt recevrait l'arborescence du dépôt parent — et le `git clean` qui suit
  # supprimerait tout ce qui n'y figure pas. On refuse avant de toucher au CT.
  echo "⚠️ $SRC n'est pas la racine du dépôt ($RACINE_GIT)."
  echo "   Le déploiement pousse le dépôt entier : les sources doivent en être la racine."
  exit 1
fi
BRANCHE="$(git -C "$SRC" rev-parse --abbrev-ref HEAD)"
if [ "$BRANCHE" = "HEAD" ]; then
  # HEAD détachée : `refs/heads/HEAD` n'existe pas, le `git fetch` DANS le CT échouerait
  # — après l'installation des paquets et avant la mise à jour du code, avec un message
  # git incompréhensible. On le dit ici, tant que rien n'a bougé.
  echo "⚠️ HEAD détachée dans $SRC : le déploiement pousse une BRANCHE."
  echo "   git switch -c <nom>   (ou git switch main) puis relancer."
  exit 1
fi
if [ -n "$(git -C "$SRC" status --porcelain -uno)" ]; then
  git -C "$SRC" --no-pager status --short -uno
  if [ "$FORCE" -eq 1 ]; then
    echo "⚠️ --force : ces modifications NE partent PAS (git ne pousse que des commits)."
  else
    echo "⚠️ modifications non commitées : elles ne seraient PAS déployées, et le CT"
    echo "   tournerait une version différente de celle que tu lis. Commite d'abord,"
    echo "   ou relance avec --force."
    exit 1
  fi
fi

# ⚠️ LE PIÈGE DU PASSAGE TAR -> GIT (relecture 2026-08-11). Le tar poussait TOUT le
# répertoire ; git ne pousse que ce qui est SUIVI. Un fichier neuf jamais `git add`-é
# reste donc à quai — en silence, car `status -uno` ci-dessus ne regarde pas les fichiers
# non suivis. Cas vécu à la lettre pendant cette campagne : `bot/cogs/avy.py` (suivi,
# donc poussé) fait `from . import avy_embeds`, alors que `avy_embeds.py` était neuf et
# non suivi. Un `git commit -a` puis ce script = quatre cogs en ImportError et un bot en
# boucle de redémarrage. On refuse donc quand la source neuve est un .py du bot ; le
# reste (106.fw, qui vit légitimement à côté des sources sans être dans le dépôt) n'est
# qu'un avertissement.
NEUFS="$(git -C "$SRC" ls-files --others --exclude-standard || true)"
if [ -n "$NEUFS" ]; then
  echo "⚠️ fichiers présents ici mais dans AUCUN commit — ils ne seront PAS déployés :"
  echo "$NEUFS" | sed 's/^/     /'
  PY_NEUFS="$(printf '%s\n' "$NEUFS" | grep -E '^(bot|tests)/.*\.py$' || true)"
  if [ -n "$PY_NEUFS" ] && [ "$FORCE" -eq 0 ]; then
    echo "   Du code Python du bot est dans le lot : le CT recevrait les modules qui"
    echo "   l'IMPORTENT sans le module lui-même (ImportError, service en boucle)."
    echo "   git add -A && git commit   — ou relancer avec --force en connaissance."
    exit 1
  fi
fi

if ! pct status "$CTID" >/dev/null 2>&1; then
  echo "==> création de CT$CTID"
  pct create "$CTID" "$TEMPLATE" \
    --hostname "$HOSTNAME" --cores 2 --memory 1024 --swap 256 \
    --rootfs local-lvm:8 \
    --net0 "name=eth0,bridge=vmbr0,firewall=1,gw=$GW,ip=$IP,type=veth" \
    --nameserver "$NAMESERVER" --ostype debian --unprivileged 1 --features nesting=1 \
    --onboot 1 --description "Discord gateway bot (Proxmox monitoring + safe actions). Code: $DEST"
  pct start "$CTID"; sleep 5
else
  echo "==> CT$CTID existe ; mise à jour du code seulement"
  pct status "$CTID" | grep -q running || { pct start "$CTID"; sleep 3; }
fi

echo "==> paquets de base (AVANT le pare-feu restrictif, pour qu'apt/pip atteignent les dépôts)"
pct exec "$CTID" -- bash -lc '
  set -e
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y --no-install-recommends python3 python3-venv python3-pip git fonts-dejavu-core ca-certificates tzdata >/dev/null
  timedatectl set-timezone Europe/Paris 2>/dev/null || true
  id discordbot >/dev/null 2>&1 || useradd --system --home /opt/discord-bot --shell /usr/sbin/nologin discordbot
'

echo "==> envoi du code (branche $BRANCHE, dernier commit $(git -C "$SRC" log -1 --format='%h %s'))"
BUNDLE=/tmp/discord-bot-src.bundle
git -C "$SRC" bundle create "$BUNDLE" --branches --tags >/dev/null
pct exec "$CTID" -- install -d -m 0755 "$DEST"
pct push "$CTID" "$BUNDLE" "$DEST/_src.bundle"
rm -f "$BUNDLE"
# $1 = branche. Le script reste en apostrophes simples (pas d'interpolation surprise) ;
# /opt/discord-bot y est écrit en toutes lettres, comme dans le bloc apt ci-dessus.
# ⚠️ AUCUNE apostrophe dans le bloc ci-dessous, commentaires compris : elle fermerait la
# chaîne et casserait le script à l'exécution (piège rencontré en l'écrivant).
pct exec "$CTID" -- bash -c '
  set -e
  DEST=/opt/discord-bot
  BR="$1"
  cd "$DEST"
  # Le dépôt appartient à discordbot (chown de install.sh) : sans ceci, git lancé en
  # root refuse de travailler (« detected dubious ownership »).
  git config --global --get-all safe.directory 2>/dev/null | grep -qx "$DEST" \
    || git config --global --add safe.directory "$DEST"
  [ -d .git ] || git init -q -b "$BR" .
  git fetch -q --tags "$DEST/_src.bundle" "+refs/heads/$BR:refs/remotes/source/$BR"
  # reset --hard : aligne le contenu sur le commit, y compris en ÉCRASANT les fichiers
  # non suivis laissés par les anciens déploiements tar.
  git reset -q --hard "refs/remotes/source/$BR"
  rm -f "$DEST/_src.bundle"          # avant le clean : sinon il se signale lui-même
  # clean : supprime ce que la source ne contient plus. Sans -x, les fichiers IGNORÉS
  # (config.env, venv/, servarr-apis.json, __pycache__) sont épargnés ; .mplcache ne
  # figure pas dans .gitignore : ce dossier est donc exclu explicitement.
  git clean -nd -e .mplcache | sed "s/^Would remove /   retiré (absent de la source) : /"
  git clean -qfd -e .mplcache
  git log -1 --format="   /opt à jour sur %h %s"
' provision "$BRANCHE"

echo "==> installation dans le CT (venv, dépendances, config.env, systemd)"
pct exec "$CTID" -- bash "$DEST/install.sh"

echo "==> application du pare-feu restrictif EN DERNIER"
if [ -f "$SRC/106.fw" ]; then
  cp "$SRC/106.fw" "/etc/pve/firewall/106.fw"
  pve-firewall compile >/dev/null 2>&1 || true
else
  # Avant : `cp` échouait et `set -e` avortait le script ICI, après l'installation —
  # message illisible, et personne ne voyait que le pare-feu n'était pas posé.
  echo "   ⚠️ $SRC/106.fw absent : pare-feu NON appliqué. /etc/pve/firewall/106.fw"
  echo "      garde sa version précédente (ou aucune) — à vérifier à la main."
fi

echo
echo "TERMINÉ."
echo "  1) pve_setup_token.sh a imprimé les secrets des tokens PVE — les mettre dans config.env"
echo "  2) Créer un token READ dans l'UI InfluxDB -> INFLUX_TOKEN"
echo "  3) Créer le bot Discord + l'inviter -> DISCORD_TOKEN + GUILD_ID + ids de salons/admins"
echo "  Éditer :     pct exec $CTID -- nano $DEST/config.env"
echo "  Démarrer :   pct exec $CTID -- systemctl restart discord-bot"
echo
echo "  Mise à jour de routine : commiter ici, relancer ./provision_ct106.sh, puis"
echo "  valider DANS le CT :  pct exec $CTID -- bash -lc 'cd $DEST && ./deploy.sh'"
echo "  (tests + import de tous les modules + contrôle du démarrage + rollback auto)"
