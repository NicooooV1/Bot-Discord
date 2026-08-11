"""Client HTTP JSON partagé (Radarr/Sonarr/Prowlarr/qBittorrent/Jellyseerr/ytgrab…).

POURQUOI CE MODULE EXISTE
-------------------------
Neuf fichiers construisaient à la main le même bloc `urllib.request.Request` + `urlopen`
+ `json.loads`, chacun avec sa propre gestion d'erreur — c'est-à-dire neuf occasions
d'oublier le timeout, d'avaler une panne en silence, ou de confondre « service
injoignable » avec « rien à signaler » (audit du 2026-08-11).

Deux invariants sont posés ici une fois pour toutes :

  1. **TIMEOUT OBLIGATOIRE.** `urlopen` sans timeout attend indéfiniment ; dans un thread
     de `asyncio.to_thread`, cela immobilise un worker du pool PARTAGÉ (6 sur ce
     conteneur 2 cœurs) et finit par affamer toutes les boucles de fond.
  2. **None ≠ {} ≠ [].** `None` signifie « appel en échec » et RIEN D'AUTRE. Un service
     qui répond « aucun résultat » renvoie une liste vide. Sans cette distinction, une
     clé d'API révoquée est indiscernable d'une bibliothèque vide — le défaut qui rendait
     `/langues` rassurant alors que Radarr était injoignable.

urllib et non aiohttp : le reste du projet appelle ces API depuis `asyncio.to_thread`
(même contrat que proxmoxer), et introduire un second modèle d'I/O pour ces quelques
appels n'apporterait rien.
"""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("discord-bot.http")

DEFAULT_TIMEOUT = 15


def request_json(url, *, method="GET", body=None, headers=None, timeout=DEFAULT_TIMEOUT,
                 label=None, quiet=False):
    """Appel HTTP JSON SYNCHRONE. Renvoie l'objet décodé, ou None en cas d'échec.

    ⚠️ Bloquant : à appeler depuis `asyncio.to_thread`, jamais depuis une coroutine.

    `label` sert au message de log (« radarr /movie »). `quiet=True` rétrograde le log en
    debug, pour les appels dont l'échec est ATTENDU et répétitif (sondage d'un service
    volontairement éteint, coupe-circuit déjà ouvert) — sans quoi on noie le journal.
    """
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        # Le CORPS d'une réponse d'erreur porte souvent le vrai diagnostic (« API key
        # invalid ») : le perdre transforme un problème de configuration en mystère.
        detail = ""
        try:
            detail = e.read()[:200].decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        (log.debug if quiet else log.warning)(
            "%s: HTTP %s%s", label or url, e.code, f" — {detail}" if detail else "")
        return None
    except Exception as e:  # noqa: BLE001 — réseau, DNS, timeout, TLS
        (log.debug if quiet else log.warning)("%s: %s", label or url, e)
        return None
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        (log.debug if quiet else log.warning)(
            "%s: réponse non-JSON (%d octets)", label or url, len(raw))
        return None


class ApiClient:
    """Client d'une API REST JSON : base + en-têtes + timeout communs.

        radarr = ApiClient("http://10.3.10.120:7878", {"X-Api-Key": key}, label="radarr")
        films = await radarr.aget("/api/v3/movie")      # None si injoignable

    Toujours `None` en cas d'échec, jamais d'exception : les appelants sont des boucles
    de fond et des commandes Discord, qui doivent dégrader proprement — mais ils DOIVENT
    tester le None au lieu de le confondre avec une liste vide.
    """

    __slots__ = ("base", "headers", "timeout", "label")

    def __init__(self, base_url, headers=None, timeout=DEFAULT_TIMEOUT, label=None):
        self.base = str(base_url or "").rstrip("/")
        self.headers = dict(headers or {})
        self.timeout = timeout
        self.label = label or self.base

    def url(self, path, params=None):
        u = self.base + ("" if str(path).startswith("/") else "/") + str(path)
        if params:
            u += ("&" if "?" in u else "?") + urllib.parse.urlencode(params)
        return u

    def get(self, path, params=None, *, timeout=None, quiet=False):
        return request_json(self.url(path, params), headers=self.headers,
                            timeout=timeout or self.timeout,
                            label=f"{self.label} {path}", quiet=quiet)

    def post(self, path, body=None, *, timeout=None, quiet=False):
        return request_json(self.url(path), method="POST", body=body,
                            headers=self.headers, timeout=timeout or self.timeout,
                            label=f"{self.label} {path}", quiet=quiet)

    def delete(self, path, *, timeout=None, quiet=False):
        return request_json(self.url(path), method="DELETE", headers=self.headers,
                            timeout=timeout or self.timeout,
                            label=f"{self.label} {path}", quiet=quiet)

    # --- versions async : enveloppent le thread, comme Influx.aq / Pve.a* ---
    async def aget(self, path, params=None, *, timeout=None, quiet=False):
        import asyncio
        return await asyncio.to_thread(self.get, path, params,
                                       timeout=timeout, quiet=quiet)

    async def apost(self, path, body=None, *, timeout=None, quiet=False):
        import asyncio
        return await asyncio.to_thread(self.post, path, body,
                                       timeout=timeout, quiet=quiet)

    async def adelete(self, path, *, timeout=None, quiet=False):
        import asyncio
        return await asyncio.to_thread(self.delete, path, timeout=timeout, quiet=quiet)


APIS_FILE = "/opt/discord-bot/servarr-apis.json"


def load_service_apis(path=APIS_FILE):
    """Charge servarr-apis.json ({service: {url, key}}), {} si absent ou illisible.

    Trois copies de cette fonction existaient (medias, requests, servarr) et deux
    avalaient l'erreur en silence : sans ce fichier, `/langues` et le débit qBittorrent
    sont muets POUR TOUJOURS, ce qui se lit comme une seedbox à 0 o/s. On le dit.
    """
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        log.warning("%s absent : les intégrations servarr seront indisponibles", path)
        return {}
    except Exception as e:  # noqa: BLE001 — JSON cassé : le bot doit démarrer quand même
        log.warning("%s illisible: %s", path, e)
        return {}


def client_for(apis, service, timeout=DEFAULT_TIMEOUT):
    """ApiClient d'un service de servarr-apis.json, ou None s'il n'est pas configuré."""
    a = (apis or {}).get(service) or {}
    url = a.get("url")
    if not url:
        return None
    headers = {"X-Api-Key": a["key"]} if a.get("key") else {}
    return ApiClient(url, headers, timeout=timeout, label=service)
