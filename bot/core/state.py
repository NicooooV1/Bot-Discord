"""Tiny JSON-backed persistent state (survives restarts / nightly host power-off).

Stored under StateDirectory (/var/lib/discord-bot/state.json). Used for the pinned
dashboard message id, owned-alert de-dup levels, and the last daily-report date.
"""
import json
import logging
import os
import tempfile
import threading
import time

log = logging.getLogger("discord-bot.state")

#: sous-dict de state.json qui porte la de-dup des alertes (edge-trigger)
ALERTS_ROOT = "alerts"


class State:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self.d = self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
        except ValueError:
            # state.json corrompu : ne PAS l'écraser en silence — on perdrait entre
            # autres les délais « jamais » de /yt (fichiers alors supprimés à 4 j).
            # Le fichier est mis de côté pour diagnostic/restauration manuelle.
            bak = f"{self.path}.corrupt-{int(time.time())}"
            try:
                os.replace(self.path, bak)
                log.warning("state.json CORROMPU -> sauvegardé en %s, état repart vide "
                            "(délais /yt et réglages perdus, restaurer si possible)", bak)
            except OSError:
                log.warning("state.json corrompu et impossible à mettre de côté")
            return {}

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), prefix=".state-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.d, f, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def get(self, key, default=None):
        with self._lock:
            return self.d.get(key, default)

    def set(self, key, value):
        with self._lock:
            self.d[key] = value
            self._save()

    # --- owned-alert de-dup (edge-triggered), cloisonnée par cog ---
    def ns(self, prefix, adopt=()):
        """Espace d'alertes RÉSERVÉ à un cog, + reprise de ses clés d'avant la migration.

        MIGRATION SANS PERTE (2026-08-11) : au premier démarrage après cette version,
        `state["alerts"]` ne contient que des clés NUES (« ipmi_temp »,
        « servarr_vpn_down ») écrites par la version précédente. Les ignorer remettrait
        l'edge-trigger à zéro : toutes les alertes EN COURS seraient re-postées dans
        #alertes (spam), et une condition résolue entre-temps ne posterait jamais son
        « ✅ Résolu ». Chaque cog déclare donc dans `adopt` les clés nues qui lui
        appartiennent ; elles sont DÉPLACÉES dans son espace, une fois pour toutes.

        Un cog ne reprend QUE ses clés : celles d'un autre restent intactes même s'il est
        chargé bien plus tard (`alerts` est chargé avant `servarr`). C'est précisément ce
        que l'ancien code ne savait pas faire.
        """
        space = AlertSpace(self, prefix)
        if adopt:
            with self._lock:
                d = self.d.setdefault(ALERTS_ROOT, {})
                changed = False
                for k in adopt:
                    if k not in d:
                        continue
                    # espace déjà peuplé (migration déjà faite) : la clé nue n'est plus
                    # qu'un résidu, la valeur préfixée fait foi.
                    v = d.pop(k)
                    d.setdefault(space.key(k), v)
                    changed = True
                if changed:
                    log.info("alertes : clés reprises dans l'espace « %s » (migration)",
                             prefix)
                    self._save()
        return space

    def alerts_active(self):
        """Alertes encore maintenues, TOUS espaces confondus : {clé nue: niveau}.

        /health, /alerts et le tableau de bord doivent compter l'UNION des espaces :
        filtrer sur les clés d'un seul cog faisait afficher « alertes actives: 0 »
        pendant que servarr tenait un « tunnel VPN DOWN » (2026-08-11).

        Le préfixe de rangement est retiré ici : ce qui s'affiche reste le NOM de
        l'alerte (celui du pied de page « alerte: <clé> »), inchangé. Les clés nues
        rencontrées (état d'avant la migration, cog pas encore chargé) sont incluses
        telles quelles.
        """
        with self._lock:
            items = list((self.d.get(ALERTS_ROOT) or {}).items())
        return {k.split(":", 1)[-1]: v["level"] for k, v in items
                if isinstance(v, dict) and v.get("level")}


class AlertSpace:
    """Sous-espace préfixé de `state["alerts"]`, réservé à UN cog.

    POURQUOI (2026-08-11) : `state["alerts"]` était un dict PLAT partagé par les cogs
    `alerts` et `servarr`. Chacun y écrivait ses clés et aucun ne pouvait distinguer les
    siennes — si bien que `Alerts.__init__`, qui purge au démarrage les clés qu'il n'émet
    plus, effaçait aussi les 6 clés `servarr_*` BIEN VIVANTES (alerts est chargé AVANT
    servarr) : le tunnel VPN coupé depuis trois jours était re-pagé à chaque redémarrage
    matinal, et une condition résolue pendant l'arrêt ne postait jamais son « ✅ Résolu ».
    Une liste de clés périmées tenue à la main rattrapait le coup ; le préfixe rend la
    séparation STRUCTURELLE — un cog ne VOIT plus que son espace, la purge de démarrage
    est sûre par construction.

    ⚠️ Le NOM de l'alerte ne change pas : `servarr_vpn_down` reste `servarr_vpn_down`
    partout où il est visible (pied de page « alerte: <clé> » que relit le bouton Snooze,
    /alerts, /health). Seule la clé de STOCKAGE est préfixée. Renommer les alertes aurait
    perdu les sommeils (`alert_snooze`) en cours et changé trois affichages.
    """

    __slots__ = ("_st", "prefix")

    def __init__(self, state, prefix):
        self._st = state
        self.prefix = str(prefix)

    def key(self, k):
        """Clé de stockage réelle (« servarr:servarr_vpn_down »)."""
        return f"{self.prefix}:{k}"

    def get(self, k, default=None):
        v = (self._st.d.get(ALERTS_ROOT) or {}).get(self.key(k))
        return default if v is None else v

    def level(self, k):
        """Niveau persisté (« warn »/« crit ») ou None — base de l'edge-trigger."""
        v = self.get(k)
        return v.get("level") if isinstance(v, dict) else None

    def set(self, k, value):
        with self._st._lock:
            self._st.d.setdefault(ALERTS_ROOT, {})[self.key(k)] = value
            self._st._save()

    def set_level(self, k, level, value=None):
        self.set(k, {"level": level, "value": value})

    def keys(self):
        """Clés NUES présentes dans CET espace (jamais celles d'un autre cog)."""
        p = self.prefix + ":"
        return [k[len(p):] for k in (self._st.d.get(ALERTS_ROOT) or {}) if k.startswith(p)]

    def clear(self, k=None):
        """Efface une clé de l'espace, ou l'espace entier si `k` est None."""
        with self._st._lock:
            d = self._st.d.setdefault(ALERTS_ROOT, {})
            if k is None:
                p = self.prefix + ":"
                for kk in [x for x in d if x.startswith(p)]:
                    d.pop(kk, None)
            else:
                d.pop(self.key(k), None)
            self._st._save()
