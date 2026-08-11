#!/usr/bin/env bash
# deploy.sh — déploiement du bot avec filet : tests, import de TOUS les modules,
# redémarrage, vérification du démarrage réel, et ROLLBACK AUTOMATIQUE si le bot ne
# revient pas correctement.
#
# À lancer EN ROOT DANS CT106, depuis le dépôt déployé (/opt/discord-bot) :
#     ./deploy.sh            # refuse si l'arbre git est sale
#     ./deploy.sh --force    # déploie quand même (le rollback remise alors l'arbre)
#
# POURQUOI CE SCRIPT
# ------------------
# Jusqu'ici, déployer = « pousser le code puis systemctl restart », sans rien vérifier :
# une erreur de syntaxe dans un cog, un import manquant ou une config incomplète se
# traduisaient par un service en boucle de redémarrage (Restart=always, RestartSec=5) —
# donc un bot ABSENT, découvert plus tard, sans moyen simple de revenir en arrière.
# Aucune de ces trois pannes ne passe ici : import de tous les modules, tests, puis
# contrôle du journal du démarrage réel.
#
# ⚠️ CE QUE « ROLLBACK » VEUT DIRE ICI. Le commit vers lequel on revient n'est PAS HEAD
# (HEAD est justement la version qu'on déploie), mais le DERNIER COMMIT VALIDÉ par un
# déploiement réussi, mémorisé dans $MARQUEUR. Sans ce marqueur (premier déploiement
# après l'installation de ce script), il n'y a rien vers quoi revenir : le script le DIT
# au lieu de faire un « git reset » sans effet.
#
# ⚠️ Ce script ne réinstalle PAS les dépendances. Après une modification de
# requirements.txt : ./install.sh (qui ne sort sur le réseau que si un pin a réellement
# changé), puis ./deploy.sh.
#
# ⚠️ CE QU'IL NE FAIT PAS. `provision_ct106.sh`, lancé depuis l'hôte, appelle install.sh
# qui REDÉMARRE déjà le service : quand tu arrives ici, le code neuf tourne. Ce script
# est le filet qu'on passe DERRIÈRE — il revalide, et sait revenir en arrière.
set -euo pipefail

UNITE=discord-bot
ATTENTE=20                                  # fenêtre d'observation avant de juger
ATTENTE_MAX=60                              # patience max si « synced »/« Connecté » tardent
MARQUEUR=/var/lib/discord-bot/dernier-deploiement-ok   # hors du dépôt : ne le salit pas
RACINE="$(cd "$(dirname "$0")" && pwd)"
FORCE=0

for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,31p' "$0"; exit 0 ;;   # ⚠️ suivre l'en-tête s'il s'allonge
    *) echo "option inconnue: $arg (voir --help)" >&2; exit 2 ;;
  esac
done

# Couleurs seulement sur un vrai terminal : dans un journal, les échappements gênent.
if [ -t 1 ]; then G=$'\033[1m'; V=$'\033[32m'; J=$'\033[33m'; R=$'\033[31m'; Z=$'\033[0m'
else G=""; V=""; J=""; R=""; Z=""; fi
titre() { printf '\n%s==> %s%s\n' "$G" "$*" "$Z"; }
ok()    { printf '    %sOK%s   %s\n' "$V" "$Z" "$*"; }
avert() { printf '    %s/!\\%s  %s\n' "$J" "$Z" "$*"; }
mort()  { printf '\n%sÉCHEC — %s%s\n' "$R" "$*" "$Z" >&2; exit 1; }

cd "$RACINE"
[ "$(id -u)" -eq 0 ] || mort "à lancer en root (systemctl / journalctl)."
[ -d "$RACINE/bot" ] || mort "$RACINE ne contient pas le paquet bot/ — mauvais répertoire ?"

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || mort \
"$RACINE n'est pas un dépôt git — sans lui, aucun rollback n'est possible.
   Si git répond « dubious ownership » (dépôt appartenant à discordbot) :
       git config --global --add safe.directory $RACINE"

# --------------------------------------------------------------- 1. arbre de travail
titre "1/6  état du dépôt"
# -uno : on ne juge QUE les fichiers suivis. /opt/discord-bot contient en permanence des
# fichiers non suivis légitimes (.mplcache/ créé par install.sh) ; les compter comme
# « arbre sale » ferait refuser TOUS les déploiements.
SALE="$(git status --porcelain -uno)"
if [ -n "$SALE" ]; then
  echo "$SALE" | sed 's/^/    /'
  git --no-pager diff --stat HEAD | sed 's/^/    /'
  if [ "$FORCE" -eq 1 ]; then
    avert "arbre sale + --force : on continue. En cas de rollback, ces modifications
        seront REMISÉES (git stash) et non perdues."
  else
    mort "l'arbre git est sale : commite (ou remise) d'abord, sinon personne ne saura
   quelle version tourne réellement — et le rollback effacerait ce travail.
   Passer outre : ./deploy.sh --force"
  fi
else
  ok "arbre propre sur $(git rev-parse --abbrev-ref HEAD) — $(git log -1 --format='%h %s')"
fi
# .mplcache/ est exclu de la LISTE : install.sh le crée à chaque passage, il serait donc
# signalé à CHAQUE déploiement — et un avertissement qui sort toujours n'est plus lu.
# (.gitignore n'appartient pas à ce lot, d'où le filtre ici plutôt que là-bas.)
NON_SUIVIS="$(git ls-files --others --exclude-standard | grep -v '^\.mplcache/' || true)"
if [ -n "$NON_SUIVIS" ]; then
  avert "fichiers non suivis (ils ne font partie d'aucune version, et le prochain
        provision_ct106.sh les EFFACERA — git clean) :"
  echo "$NON_SUIVIS" | sed 's/^/        /'
fi

# ---------------------------------------------------------------------- 2. les tests
titre "2/6  suite de tests"
PY="$RACINE/venv/bin/python"
[ -x "$PY" ] || mort "venv absent ($PY) — lancer ./install.sh d'abord."
SORTIE="$(mktemp)"; TMPMPL="$(mktemp -d)"
trap 'rm -rf -- "$SORTIE" "$TMPMPL"' EXIT
if "$PY" -m unittest discover -s tests >"$SORTIE" 2>&1; then
  tail -n 3 "$SORTIE" | sed 's/^/    /'
  ok "suite de tests verte"
else
  sed 's/^/    /' "$SORTIE"
  mort "un test échoue — c'est exactement ce à quoi les tests servent.
   Ce script n'a rien redémarré. ⚠️ Si tu viens de lancer provision_ct106.sh depuis
   l'hôte, lui l'a DÉJÀ fait (install.sh redémarre le service) : le bot tourne donc
   sur ce code non validé. Corriger, ou revenir à la main :
       git reset --hard <commit sain> && systemctl restart discord-bot"
fi

# ------------------------------------------------------- 3. import de TOUS les modules
titre "3/6  import de tous les modules de bot/"
# Un cog cassé n'est découvert qu'à son chargement, donc APRÈS le redémarrage. On le
# découvre ici, avant de toucher au service. MPLCONFIGDIR : core/render.py importe
# matplotlib, qui râle sur un HOME non inscriptible.
MPLCONFIGDIR="$TMPMPL" "$PY" - "$RACINE" <<'PY' || mort "un module ne s'importe pas (ci-dessus) — ce script n'a rien redémarré (mais provision_ct106.sh, lui, l'a peut-être déjà fait : vérifier systemctl status $UNITE)."
import importlib
import pkgutil
import sys
import traceback

sys.path.insert(0, sys.argv[1])
import bot

echecs, total = [], 0
for mod in pkgutil.walk_packages(bot.__path__, prefix="bot."):
    total += 1
    try:
        importlib.import_module(mod.name)
    except Exception:                       # noqa: BLE001 — on veut TOUS les échecs
        echecs.append(mod.name)
        print(f"--- {mod.name}")
        traceback.print_exc()
print(f"    {total - len(echecs)}/{total} modules importés")
sys.exit(1 if echecs else 0)
PY
ok "tous les modules s'importent"

# ------------------------------------------------------------------ 4. déploiement
titre "4/6  redémarrage de $UNITE"
ACTUEL="$(git rev-parse HEAD)"
PRECEDENT=""
[ -f "$MARQUEUR" ] && PRECEDENT="$(cat "$MARQUEUR")"
if [ -n "$PRECEDENT" ] && git cat-file -e "${PRECEDENT}^{commit}" 2>/dev/null; then
  echo "    dernière version validée : $(git log -1 --format='%h %s' "$PRECEDENT")"
else
  [ -n "$PRECEDENT" ] && avert "commit mémorisé $PRECEDENT introuvable dans ce dépôt"
  PRECEDENT=""
  avert "aucune version validée mémorisée : en cas d'échec, PAS de rollback automatique."
fi
echo "    version déployée        : $(git log -1 --format='%h %s' "$ACTUEL")"
systemctl restart "$UNITE"

# ---------------------------------------------------------------- 5. vérification
titre "5/6  vérification du démarrage (${ATTENTE} s, jusqu'à ${ATTENTE_MAX} s)"

journal_invocation() {
  # Journal de CETTE invocation seulement : l'InvocationID évite de relire les
  # démarrages précédents, et ne dépend pas de l'horloge (contrairement à --since).
  local inv
  inv="$(systemctl show -p InvocationID --value "$UNITE" 2>/dev/null || true)"
  if [ -n "$inv" ]; then
    journalctl "_SYSTEMD_INVOCATION_ID=$inv" --no-pager -o cat 2>/dev/null || true
  else
    journalctl -u "$UNITE" --since "-${ATTENTE_MAX}s" --no-pager -o cat 2>/dev/null || true
  fi
}

# Fenêtre d'observation FIXE d'abord : on ne veut pas seulement voir le bot arriver, on
# laisse le temps à une boucle de redémarrage (Restart=always, RestartSec=5) de se
# manifester.
for _ in $(seq "$ATTENTE"); do sleep 1; printf '.'; done; echo

# Puis on PATIENTE tant que les deux marqueurs manquent, jusqu'à ATTENTE_MAX au total.
# `tree.sync()` dépend de Discord (limite de débit) : conclure à l'échec parce que l'API
# a mis 25 s au lieu de 10 ferait annuler — et rollbacker — une version parfaitement
# saine. Un service déjà mort, lui, n'a aucune raison de s'améliorer : on sort.
ECOULE="$ATTENTE"
while [ "$ECOULE" -lt "$ATTENTE_MAX" ] && systemctl is-active --quiet "$UNITE"; do
  JOURNAL="$(journal_invocation)"
  if grep -q 'synced' <<<"$JOURNAL" && grep -qE 'Connect(é|e)' <<<"$JOURNAL"; then
    break
  fi
  sleep 2; ECOULE=$((ECOULE + 2)); printf '+'
done
[ "$ECOULE" -gt "$ATTENTE" ] && echo

PROBLEME=""
RESERVE=", aucun ERROR/CRITICAL"
if ! systemctl is-active --quiet "$UNITE"; then
  PROBLEME="service inactif ($(systemctl is-active "$UNITE" 2>&1 || true))"
else
  JOURNAL="$(journal_invocation)"
  # « synced N commands » = l'arbre de commandes a été poussé à Discord ;
  # « Connecté: … » = on_ready, la gateway est réellement ouverte.
  if ! grep -q 'synced' <<<"$JOURNAL"; then
    PROBLEME="aucun « synced » dans le journal : commandes non synchronisées"
  elif ! grep -qE 'Connect(é|e)' <<<"$JOURNAL"; then
    PROBLEME="aucun « Connecté » dans le journal : la gateway ne s'est pas ouverte"
  else
    # Format des logs : « %(asctime)s %(levelname)s %(name)s: %(message)s ».
    ERREURS="$(grep -E ' (ERROR|CRITICAL) ' <<<"$JOURNAL" || true)"
    if [ -n "$ERREURS" ]; then
      echo "$ERREURS" | sed 's/^/    /'
      # ⚠️ TOUTE ERROR NE CONDAMNE PAS LA VERSION (relecture 2026-08-11). Le bot compte
      # 112 points de `log.error`/`log.exception`, dont la plupart dans des boucles de
      # fond qui font leur PREMIER tour juste après on_ready — donc DANS cette fenêtre.
      # Aveyron injoignable (le lien WG tombe régulièrement), Influx qui hoquette ou PVE
      # lent suffisaient alors à faire rollbacker un déploiement sain : on revenait à une
      # version qui journalise exactement la même erreur, en concluant de travers.
      # Ne font échouer le déploiement que les défauts qui mettent en cause LE CODE
      # DÉPLOYÉ : un CRITICAL, un cog qui ne se charge pas, une config incomplète. Les
      # trois pannes visées par ce script restent couvertes (une erreur de syntaxe ou un
      # import manquant échoue déjà à l'étape 3, et ressort ici en « cogs non chargés » ;
      # une config incomplète fait sortir le bot en code 2, donc service inactif).
      FATALES="$(grep -E ' CRITICAL |CHARGEMENT DU COG|cogs non chargés|Config incomplète' \
                 <<<"$ERREURS" || true)"
      if [ -n "$FATALES" ]; then
        PROBLEME="le code déployé ne se charge pas correctement (ci-dessus)"
      else
        RESERVE=" — MAIS des ERROR au journal (ci-dessus)"
        avert "des ERROR figurent au journal (ci-dessus), mais aucune ne met en cause le
        chargement du code : version CONSERVÉE. À lire quand même — c'est souvent une
        source externe injoignable (Aveyron, Influx, PVE)."
      fi
    fi
  fi
fi

if [ -z "$PROBLEME" ]; then
  ok "service actif, commandes synchronisées, gateway ouverte$RESERVE"
  titre "6/6  version validée"
  install -d "$(dirname "$MARQUEUR")"
  printf '%s\n' "$ACTUEL" > "$MARQUEUR"
  ok "$(git log -1 --format='%h %s' "$ACTUEL") — mémorisée comme dernière version saine"
  echo
  echo "    Journal : journalctl -u $UNITE -f"
  exit 0
fi

# ------------------------------------------------------------------- 6. rollback
titre "6/6  ROLLBACK — $PROBLEME"
if [ -z "$PRECEDENT" ] || [ "$PRECEDENT" = "$ACTUEL" ]; then
  echo "    Aucune version saine mémorisée : impossible de revenir en arrière tout seul."
  echo "    Le service tourne (ou boucle) sur $(git rev-parse --short HEAD)."
  echo "    À la main : git log --oneline ; git reset --hard <commit sain> ;"
  echo "                systemctl restart $UNITE"
  mort "démarrage KO et rollback impossible."
fi

if [ -n "$(git status --porcelain -uno)" ]; then
  # --force + arbre sale : on ne DÉTRUIT jamais le travail en cours, on le remise.
  git -c user.name="deploy.sh" -c user.email="deploy@localhost" \
      stash push -u -m "deploy.sh — avant rollback $(date '+%F %T')" >/dev/null
  avert "modifications locales remisées : les récupérer avec « git stash pop »"
fi
git reset --hard "$PRECEDENT" >/dev/null
echo "    revenu sur $(git log -1 --format='%h %s')"
systemctl restart "$UNITE"
sleep 10
if systemctl is-active --quiet "$UNITE"; then
  ok "service redémarré sur la version précédente"
else
  avert "le service ne redémarre PAS non plus sur la version précédente : la panne
        n'est probablement pas dans le code (config.env ? réseau ? Discord ?).
        journalctl -u $UNITE -n 50"
fi
mort "$PROBLEME — version rejetée, retour à $(git rev-parse --short HEAD)."
