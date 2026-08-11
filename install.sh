#!/usr/bin/env bash
# install.sh — installation idempotente DANS CT106 : venv, dépendances épinglées,
# config.env (0600, créé seulement s'il est absent), unité systemd. Reprend le patron
# éprouvé de l'installation discord-syslog.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "à lancer en root dans CT106"; exit 1; }

DEST=/opt/discord-bot
UNIT=discord-bot.service

id discordbot >/dev/null 2>&1 || useradd --system --home "$DEST" --shell /usr/sbin/nologin discordbot

echo "==> venv + dépendances épinglées"
NEUF=0
if [ ! -x "$DEST/venv/bin/python" ]; then
  python3 -m venv "$DEST/venv"
  NEUF=1
  # `pip install --upgrade` interroge TOUJOURS l'index : réservé au venv tout neuf,
  # c'est-à-dire au moment où le pare-feu restrictif n'est pas encore posé
  # (provision_ct106.sh applique 106.fw en dernier, précisément pour cela).
  "$DEST/venv/bin/pip" install --quiet --disable-pip-version-check --upgrade pip wheel
fi

# Les pins sont-ils DÉJÀ satisfaits ? Si oui, on ne lance pas pip du tout.
#
# ⚠️ POURQUOI CE CONTRÔLE EXISTE (relecture 2026-08-11, cf. l'en-tête de
# requirements.txt). Un pin qui diffère de l'installé oblige pip à SORTIR SUR LE RÉSEAU.
# Or 106.fw n'autorise pas les dépôts : sur un CT106 déjà pare-feuté, le téléchargement
# échoue, `set -euo pipefail` avorte ici même — donc AVANT les lignes qui réinstallent
# l'unité systemd et redémarrent le service — alors que le code neuf est DÉJÀ déposé
# dans /opt. État résultant : code neuf sur le disque, ancien processus toujours en vie,
# unité non mise à jour. En sautant pip quand il n'a rien à faire, le cas nominal (une
# mise à jour de code, sans changement de dépendance) ne touche plus au réseau.
#
# Le contrôle est CONSERVATEUR : tout ce qu'il ne sait pas trancher (pin non exact,
# marqueur d'environnement, paquet absent) le fait retomber sur pip.
#
# CE QU'IL NE VOIT PAS (relecture 2026-08-11) : requirements.txt n'épingle que les
# dépendances DIRECTES. Un venv amputé d'une dépendance transitive (aiohttp, par ex.)
# passe donc le contrôle, là où un `pip install -r` l'aurait réparé — au prix du réseau.
# Filet : `deploy.sh` importe TOUS les modules avant de redémarrer, ce qui met un tel
# venv à nu. Pour forcer la réparation : `venv/bin/pip install -r requirements.txt`.
ecarts() {
  "$DEST/venv/bin/python" - "$DEST/requirements.txt" <<'PY'
import sys
from importlib import metadata


def norm(n):                      # normalisation PEP 503 : discord.py == discord-py
    return n.strip().lower().replace("_", "-").replace(".", "-")


installes = {}
for dist in metadata.distributions():
    nom = (dist.metadata or {}).get("Name")
    if nom:
        installes[norm(nom)] = dist.version

manquants = []
with open(sys.argv[1], encoding="utf-8") as f:
    for ligne in f:
        ligne = ligne.split("#", 1)[0].strip()
        if not ligne:
            continue
        # Le marqueur d'environnement est IGNORÉ (« ; python_version >= "3.13" ») : au
        # pire on considère à tort une ligne comme requise, et l'on retombe sur pip —
        # jamais l'inverse.
        ligne = ligne.split(";", 1)[0].strip()
        nom, sep, version = ligne.partition("==")
        if not sep:
            manquants.append(ligne)        # pin non exact : on ne tranche pas
        elif installes.get(norm(nom)) != version.strip():
            manquants.append(ligne)
print(" ".join(manquants))
sys.exit(1 if manquants else 0)
PY
}

if ECARTS="$(ecarts)"; then
  echo "   dépendances déjà conformes aux pins — pip non lancé (aucun accès réseau)."
else
  echo "   à installer/aligner : $ECARTS"
  "$DEST/venv/bin/pip" install --quiet --disable-pip-version-check -r "$DEST/requirements.txt" || {
    echo "   ÉCHEC de pip. Sur un CT106 déjà pare-feuté (106.fw), les dépôts sont"
    echo "   injoignables : soit aligner requirements.txt sur ce qui est réellement"
    echo "   installé (venv/bin/pip freeze), soit ouvrir temporairement la sortie."
    echo "   ⚠️ Le service n'a PAS été redémarré : l'ancien processus tourne toujours."
    exit 1
  }
fi
[ "$NEUF" -eq 1 ] && echo "   (venv créé de zéro)"

install -d -m 0700 -o discordbot -g discordbot "$DEST/.mplcache"

echo "==> config.env (créé en 0600 seulement s'il est absent — jamais écrasé)"
if [ ! -f "$DEST/config.env" ]; then
  install -m 0600 -o discordbot -g discordbot "$DEST/config.env.example" "$DEST/config.env"
  echo "   $DEST/config.env créé — RENSEIGNER DISCORD_TOKEN / INFLUX_TOKEN / PVE_*_SECRET."
else
  chmod 0600 "$DEST/config.env"; chown discordbot:discordbot "$DEST/config.env"
fi

chown -R discordbot:discordbot "$DEST"
# Le dépôt git appartient donc à discordbot : sans cette ligne, git lancé en root dans
# /opt/discord-bot refuse de travailler (« detected dubious ownership ») — et deploy.sh,
# qui a besoin de git pour son rollback, ne démarrerait même pas.
git config --global --get-all safe.directory 2>/dev/null | grep -qx "$DEST" \
  || git config --global --add safe.directory "$DEST" 2>/dev/null || true

echo "==> unité systemd"
install -m 0644 "$DEST/discord-bot.service" "/etc/systemd/system/$UNIT"
systemctl daemon-reload
systemctl enable "$UNIT" >/dev/null 2>&1 || true

if grep -qE '^DISCORD_TOKEN=[^[:space:]]' "$DEST/config.env"; then
  systemctl restart "$UNIT"; sleep 1
  systemctl --no-pager --full status "$UNIT" | head -n 12 || true
else
  echo "   Service activé mais NON démarré (DISCORD_TOKEN vide)."
  echo "   Renseigner $DEST/config.env puis : systemctl start $UNIT"
fi
