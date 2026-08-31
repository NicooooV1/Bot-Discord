"""Proxmox VE API wrapper (proxmoxer). Two clients enforce least privilege at the
transport layer: a read-only token (PVEAuditor) for all monitoring/journal reads, and
a separate action token (BotSafeActions) used ONLY for start/stop/restart/backup.

proxmoxer is synchronous — callers wrap every method in asyncio.to_thread.

Multi-cluster (2026-07-17, choix Nico « tout sur le même bot ») : en plus du R820
(cluster « primaire », mono-nœud), une instance `_RemoteCluster` optionnelle supervise
le cluster AVEYRON (3 nœuds, config AVY_*). Les invités distants sont FUSIONNÉS dans
les mêmes vues sous un espace de noms dédié :
  - nom  : suffixe « -<AVY_SUFFIX> » (authentik -> authentik-avy) ;
  - vmid : décalé de AVY_OFFSET (220 -> 1000220), les vrais vmid des deux clusters
    pouvant entrer en collision (100 existe des deux côtés) ;
  - UPID : préfixe « avy: » (les nœuds s'appellent « pve » des deux côtés).
Tous les appels par invité routent sur ce codage ; les appels « hôte » (journal,
host_status, tasks globales, backup_all) restent ceux du R820. Cluster distant
injoignable => les invités distants sont simplement omis (le R820 n'est jamais impacté),
avec une fenêtre de grâce via cache pour éviter d'archiver leurs salons sur un blip.
"""
import asyncio
import concurrent.futures
import json
import logging
import threading
import time

log = logging.getLogger("discord-bot.pve")

# décalage d'espace de noms des vmid du cluster secondaire (aucun vmid réel n'approche
# ce seuil : R820 <= 200, AVEYRON <= 11010)
AVY_OFFSET = 1_000_000
# préfixe des UPID du cluster secondaire (un UPID nu ne dit pas de quel cluster il vient)
AVY_UPID_PREFIX = "avy:"
# durée (s) pendant laquelle un instantané d'invités distants reste servi si le cluster
# distant ne répond plus (évite l'archivage des salons par provision sur une coupure WG
# transitoire ; au-delà, les invités disparaissent des vues jusqu'au retour du lien)
AVY_STALE_MAX = 900
# fenêtre (s) du COUPE-CIRCUIT partagé : après un échec réseau vers le cluster secondaire,
# toute lecture distante échoue VITE (AvyUnreachable) pendant ce délai au lieu de re-payer
# un timeout ; la 1re lecture passée ce délai re-sonde le lien (motif « half-open »). Cadré
# sur la boucle avy_metrics (2 min) : au plus une sonde réelle par cycle, le reste instantané.
AVY_BACKOFF = 120
# Fenêtre pendant laquelle une lecture distante RÉUSSIE vaut preuve que le lien est
# vivant : dans cet intervalle, un échec isolé (un nœud, un stockage) n'arme plus le
# coupe-circuit partagé. Cf. Pve._avy_maybe_trip (2026-08-14).
AVY_ALIVE_WINDOW = 60
# Fenêtre du coupe-circuit PAR NŒUD : un nœud qui ne répond plus est court-circuité
# pendant ce délai, sans que ses voisins ni le cluster en pâtissent (2026-08-14).
AVY_NODE_BACKOFF = 300


def _mk_client(cfg, token_id, secret):
    from proxmoxer import ProxmoxAPI
    user, name = cfg.split_token(token_id)
    return ProxmoxAPI(
        cfg.pve_host, port=cfg.pve_port, user=user,
        token_name=name, token_value=secret,
        verify_ssl=cfg.pve_verify_ssl, timeout=15)


def _mk_remote_client(cfg, token_id, secret, timeout=30, host=None):
    from proxmoxer import ProxmoxAPI
    host = host or cfg.avy_host
    # timeout 30 s (vs 15 côté R820) : l'énumération du stockage CIFS nas-backup prend
    # ~16 s à travers le tunnel WG (mesuré 2026-07-17) ; tous les appels passent par
    # asyncio.to_thread, un appel lent ne bloque donc jamais la boucle du bot.
    if getattr(cfg, "avy_password", "") and getattr(cfg, "avy_user", ""):
        # Utilisateur + mot de passe (Nico 2026-08-28) : proxmoxer obtient un ticket et
        # le renouvelle seul (renew_age = 1 h < 2 h de validité). Même compte pour la
        # lecture et les actions ; `token_id`/`secret` sont alors ignorés.
        return ProxmoxAPI(
            host, port=cfg.avy_port, user=cfg.avy_user, password=cfg.avy_password,
            verify_ssl=cfg.avy_verify_ssl, timeout=timeout)
    user, name = cfg.split_token(token_id)
    return ProxmoxAPI(
        host, port=cfg.avy_port, user=user,
        token_name=name, token_value=secret,
        verify_ssl=cfg.avy_verify_ssl, timeout=timeout)


def _parse_agent_info(agent):
    """OS + FS + IPs d'une VM via son qemu-guest-agent — partagé entre le cluster
    primaire (R820) et Aveyron (_RemoteCluster), `agent` = endpoint proxmoxer
    `.nodes(node).qemu(vmid).agent` déjà résolu par l'appelant."""
    try:
        osr = (agent("get-osinfo").get() or {}).get("result") or {}
    except Exception:
        return None
    out = {"os": osr.get("pretty-name") or osr.get("name") or "?", "fs": []}
    try:
        seen = set()
        for f in (agent("get-fsinfo").get() or {}).get("result") or []:
            tot = f.get("total-bytes")
            mp = str(f.get("mountpoint", ""))
            # montages techniques (volumes kubelet/docker, boot, snaps) : bruit
            if (not tot or mp.startswith(("/boot", "/snap", "/run"))
                    or "/kubelet/" in mp or "/docker/" in mp
                    or "/containerd/" in mp or (f.get("name"), tot) in seen):
                continue
            seen.add((f.get("name"), tot))
            out["fs"].append((mp or "?", f.get("used-bytes") or 0, tot))
    except Exception:
        pass
    out["fs"].sort(key=lambda x: x[2], reverse=True)
    try:
        ips = []
        for i in (agent("network-get-interfaces").get() or {}).get("result") or []:
            if i.get("name") == "lo":
                continue
            for a in i.get("ip-addresses") or []:
                ip = a.get("ip-address") or ""
                if (a.get("ip-address-type") == "ipv4"
                        and not ip.startswith(("127.", "169.254."))):
                    ips.append(ip)
        out["ips"] = ips[:4]
    except Exception:
        out["ips"] = []
    return out


class _RemoteCluster:
    """Client du cluster secondaire (AVEYRON) : MULTI-NŒUDS, jetons dédiés.

    Contrairement au R820 mono-nœud, chaque appel par invité doit viser le nœud qui
    l'héberge (/nodes/pve/lxc/220 répond 500 si l'invité vit sur « nas ») : node_of()
    s'appuie sur /cluster/resources. Les vmid manipulés ici sont les VRAIS vmid."""

    def __init__(self, cfg):
        self.key = cfg.avy_key
        self.storage = cfg.avy_storage
        self._cfg = cfg
        # Points d'entrée ÉQUIVALENTS (Nico 2026-08-28) : nas / ms01 / llm. Un client par
        # (hôte, rôle), créé à la demande ; `_ro`/`_rw`/`_probe` visent l'hôte COURANT et
        # `switch_if_dead()` en change quand il ne répond plus (cf. Pve._avy_guarded).
        self._hosts = list(getattr(cfg, "avy_hosts", None) or [cfg.avy_host])
        self._hi = 0
        self._clients = {}
        by_password = bool(getattr(cfg, "avy_password", "") and getattr(cfg, "avy_user", ""))
        self._rw_enabled = by_password or bool(cfg.avy_action_token_secret)
        if by_password:
            log.info("cluster %s : authentification par mot de passe (%s), actions "
                     "activées, %d point(s) d'entrée : %s", self.key, cfg.avy_user,
                     len(self._hosts), ", ".join(self._hosts))
        elif not self._rw_enabled:
            # 2026-08-11 : sans ce log, l'oubli du secret (AVY_* n'est documenté NULLE PART
            # dans config.env.example) ne se voyait qu'au premier /ctctl sur un invité -avy,
            # sous la forme d'un « 'NoneType' object has no attribute 'nodes' ».
            log.warning("cluster %s : jeton d'action absent (AVY_PVE_ACTION_TOKEN_SECRET) "
                        "— actions distantes DÉSACTIVÉES, lectures seules", self.key)
        self._gnodes = {}
        self._nodes_cache = None
        self._nodes_ts = 0.0
        self._local_cache = None        # nœud servi SANS tunnel inter-nœuds (cf. _local_node)
        self._local_ts = 0.0
        self._node_fail = {}            # nœud -> instant jusqu'auquel on le court-circuite

    # ---- points d'entrée multiples ----
    @property
    def host(self):
        """Hôte (adresse) actuellement visé."""
        return self._hosts[self._hi]

    def _client(self, kind, host=None):
        """Client proxmoxer pour (hôte, rôle) — créé une fois, puis réutilisé (un client
        = une session/ticket ; le recréer à chaque appel referait un login par appel)."""
        host = host or self.host
        key = (host, kind)
        c = self._clients.get(key)
        if c is None:
            cfg = self._cfg
            if kind == "rw":
                c = _mk_remote_client(cfg, cfg.avy_action_token_id,
                                      cfg.avy_action_token_secret, host=host)
            elif kind == "probe":
                # Client de SONDE : timeout court. Il ne sert qu'à trancher « lien mort ou
                # ressource morte ? » quand une lecture expire — question à laquelle on ne
                # peut pas répondre avec un client dont le timeout EST la panne qu'on
                # essaie de qualifier (cf. Pve._avy_maybe_trip).
                c = _mk_remote_client(cfg, cfg.avy_token_id, cfg.avy_token_secret,
                                      timeout=5, host=host)
            else:
                c = _mk_remote_client(cfg, cfg.avy_token_id, cfg.avy_token_secret, host=host)
            self._clients[key] = c
        return c

    @property
    def _ro(self):
        return self._client("ro")

    @property
    def _rw(self):
        return self._client("rw") if self._rw_enabled else None

    @property
    def _probe(self):
        return self._client("probe")

    def _probe_host(self, host):
        try:
            self._client("probe", host).version.get()
            return True
        except Exception:  # noqa: BLE001 — toute erreur = hôte muet
            return False

    def _use_host(self, i, why):
        old = self.host
        self._hi = i
        # ce que l'ancien hôte nous avait dit de lui-même ne vaut plus pour le nouveau
        self._local_cache, self._local_ts = None, 0.0
        self._nodes_cache, self._nodes_ts = None, 0.0
        log.warning("cluster %s : bascule du point d'entrée %s -> %s (%s)",
                    self.key, old, self.host, why)

    def switch_if_dead(self):
        """Si l'hôte courant ne répond plus, passe au premier autre hôte qui répond.

        Retourne True quand on a CHANGÉ d'hôte (l'appelant peut rejouer sa lecture),
        False sinon : hôte courant vivant (l'échec était local à la ressource lue) ou
        aucun hôte ne répond (vraie coupure du lien). Sonde à timeout court : au pire
        5 s × nombre d'hôtes."""
        if self._probe_host(self.host):
            return False
        for i, h in enumerate(self._hosts):
            if i == self._hi:
                continue
            if self._probe_host(h):
                self._use_host(i, "hôte muet")
                return True
        return False

    @property
    def actions_enabled(self):
        return self._rw_enabled

    @property
    def _action_api(self):
        """Client d'ÉCRITURE, ou une erreur parlante. Les 4 méthodes d'action passent par
        là : tous les appelants affichent déjà `❌ Échec : {e}` et auditent l'exception,
        le message remonte donc tel quel plutôt qu'un AttributeError sur None (2026-08-11).

        Nommée `_action_api` et non `_wr` : à une transposition de lettres près de `_rw`
        (le client brut, qui peut valoir None), la garde se contournait à la première
        faute de frappe d'une future méthode d'écriture — relecture 2026-08-11."""
        if self._rw is None:
            raise RuntimeError(f"cluster {self.key} : jeton d'action non configuré "
                               f"(AVY_PVE_ACTION_TOKEN_SECRET)")
        return self._rw

    def nodes_online(self, ttl=60):
        # `is None` et non `not …` : une liste VIDE est une réponse légitime (cluster
        # arrêté, perte de quorum) et doit être mise en cache comme les autres, sinon
        # chaque pbs_content/delete_backup/running_vzdump_vmids re-payait un /nodes
        # distant pendant toute la fenêtre dégradée (2026-08-11).
        now = time.time()
        if self._nodes_cache is None or now - self._nodes_ts > ttl:
            self._nodes_cache = [n["node"] for n in self._ro.nodes.get()
                                 if n.get("status") == "online"]
            self._nodes_ts = now
        return self._nodes_cache

    def resources(self):
        res = self._ro.cluster.resources.get(type="vm")
        self._gnodes = {str(r["vmid"]): r.get("node") for r in res if r.get("vmid")}
        return res

    def node_of(self, vmid):
        n = self._gnodes.get(str(vmid))
        if n is None:
            self.resources()
            n = self._gnodes.get(str(vmid))
        if n is None:
            raise LookupError(f"VM/conteneur {vmid} inconnu du cluster {self.key}")
        return n

    def guest_status(self, vmid, gtype):
        api = self._ro.nodes(self.node_of(vmid))
        ep = api.qemu(vmid) if gtype == "qemu" else api.lxc(vmid)
        return ep.status.current.get()

    # ---- supervision par nœud (salons 📊 Supervision AVY-*) ----
    def agent_info(self, vmid):
        """Infos INTERNES d'une VM via le qemu-guest-agent (VM.GuestAgent.Audit) :
        OS + systèmes de fichiers. None si pas d'agent (LXC, agent absent/éteint)."""
        agent = self._ro.nodes(self.node_of(vmid)).qemu(vmid).agent
        return _parse_agent_info(agent)

    def alive(self):
        """Le lien répond-il VITE ? Sonde `/version` avec le client à timeout court.

        Sert d'arbitre à `Pve._avy_maybe_trip` : une lecture qui expire ne dit pas si
        c'est le LIEN ou la RESSOURCE qui est morte, et le client normal (30 s) ne peut
        pas répondre — son timeout est précisément la panne à qualifier."""
        if self._probe_host(self.host):
            return True
        # hôte courant muet : le LIEN n'est pas mort pour autant si un autre point
        # d'entrée répond — on bascule dessus au passage.
        return self.switch_if_dead()

    def _node_guard(self, node, fn, *a, **kw):
        """Coupe-circuit PAR NŒUD (2026-08-14).

        Un nœud peut être muet pendant que ses voisins répondent en 0,13 s : le 14/08,
        `nas` était bloqué par un partage CIFS démonté et chacune de ses lectures coûtait
        30 s de timeout, à chaque cycle, pour rien. On mémorise donc le nœud fautif et on
        échoue INSTANTANÉMENT pour lui pendant AVY_NODE_BACKOFF, sans toucher aux autres.

        L'erreur réseau est convertie en `AvyNodeUnreachable` — qui n'est PAS un OSError :
        c'est ce qui empêche `Pve._avy_read` d'armer le coupe-circuit du CLUSTER pour la
        panne d'un seul nœud. La supervision, elle, voit un nœud injoignable et l'annonce
        déjà (`_alerts` : « 🔴 nœud injoignable » après 2 cycles, « 🟢 rétabli » ensuite).
        """
        now = time.time()
        if now < self._node_fail.get(node, 0):
            raise AvyNodeUnreachable(
                f"nœud {node} muet (coupe-circuit {AVY_NODE_BACKOFF}s)")
        try:
            r = fn(*a, **kw)
        except OSError as e:
            self._node_fail[node] = now + AVY_NODE_BACKOFF
            log.warning("cluster %s : nœud %s muet (%s) — ses lectures sont "
                        "court-circuitées %ds, les autres nœuds continuent",
                        self.key, node, e, AVY_NODE_BACKOFF)
            raise AvyNodeUnreachable(f"nœud {node} : {e}") from None
        if self._node_fail.pop(node, None) is not None:
            log.info("cluster %s : nœud %s de nouveau joignable", self.key, node)
        return r

    def degraded_nodes(self):
        """Nœuds actuellement court-circuités (pour l'affichage : un trou dans la
        supervision doit se VOIR, pas se deviner)."""
        now = time.time()
        return sorted(n for n, until in self._node_fail.items() if now < until)

    def node_status(self, node):
        return self._node_guard(node, self._ro.nodes(node).status.get)

    # ---- lectures riches (graphes, matériel, cluster, config) ----
    def guest_rrd(self, vmid, gtype, timeframe):
        api = self._ro.nodes(self.node_of(vmid))
        ep = api.qemu(vmid) if gtype == "qemu" else api.lxc(vmid)
        return ep.rrddata.get(timeframe=timeframe)

    def node_rrd(self, node, timeframe):
        return self._node_guard(node, self._ro.nodes(node).rrddata.get,
                                timeframe=timeframe)

    def disks(self, node):
        return self._node_guard(node, self._ro.nodes(node).disks.list.get)

    def smart(self, node, devpath):
        return self._node_guard(node, self._ro.nodes(node).disks.smart.get,
                                disk=devpath)

    def cluster_status(self):
        return self._ro.cluster.status.get()

    def cluster_log(self, maxn=200):
        return self._ro.cluster.log.get(max=maxn)

    def certificates(self, node):
        return self._node_guard(node, self._ro.nodes(node).certificates.info.get)

    def backup_jobs(self):
        return self._ro.cluster.backup.get()

    def guest_config(self, vmid, gtype):
        api = self._ro.nodes(self.node_of(vmid))
        ep = api.qemu(vmid) if gtype == "qemu" else api.lxc(vmid)
        return ep.config.get()

    def ping_ms(self):
        """Latence de l'API (≈ latence du tunnel WG) en millisecondes."""
        t0 = time.perf_counter()
        self._ro.version.get()
        return (time.perf_counter() - t0) * 1000

    def node_storages(self, node):
        return self._node_guard(node, self._ro.nodes(node).storage.get)

    def node_tasks(self, node, limit=20, source=None):
        kw = {"limit": limit}
        if source is not None:
            kw["source"] = source
        return self._node_guard(node, self._ro.nodes(node).tasks.get, **kw)

    def node_services(self, node):
        """Services systèmes du nœud (pveproxy, corosync…) — Sys.Audit suffit."""
        return self._node_guard(node, self._ro.nodes(node).services.get)

    def node_updates(self, node):
        """Paquets APT en attente (liste déjà en cache côté nœud, lecture légère).
        ⚠️ Ce GET exige Sys.Modify sur certaines versions PVE : un jeton en lecture
        seule peut se voir refuser — l'appelant (Avy._collect) neutralise alors la
        lecture pour de bon au lieu de repayer un 403 par cycle."""
        return self._node_guard(node, self._ro.nodes(node).apt.update.get)

    def backup_node(self, node, mode, notes, exclude=""):
        """vzdump all=1 du nœud (bouton 💾 du salon hyperviseur AVY-*)."""
        kw = {"all": 1, "storage": self.storage, "mode": mode}
        if exclude:
            kw["exclude"] = exclude
        return self._action_api.nodes(node).vzdump.post(**kw, **notes)

    def action(self, verb, vmid, gtype):
        api = self._action_api.nodes(self.node_of(vmid))
        ep = api.qemu(vmid) if gtype == "qemu" else api.lxc(vmid)
        return getattr(ep.status, verb).post()

    def backup(self, vmid, mode, notes):
        return self._action_api.nodes(self.node_of(vmid)).vzdump.post(
            vmid=int(vmid), storage=self.storage, mode=mode, **notes)

    def _upid_node(self, upid):
        parts = str(upid).split(":")
        return parts[1] if len(parts) > 1 and parts[0] == "UPID" and parts[1] else None

    def task_status(self, upid):
        return self._ro.nodes(self._upid_node(upid)).tasks(upid).status.get()

    def task_log(self, upid, limit=200):
        return self._ro.nodes(self._upid_node(upid)).tasks(upid).log.get(limit=limit)

    def running_vzdump_vmids(self):
        """Vrais vmid (str) en cours de vzdump, agrégés sur les nœuds en ligne."""
        out = set()
        nodes = self.nodes_online()
        errs, last = 0, None
        for n in nodes:
            try:
                act = self._ro.nodes(n).tasks.get(source="active", limit=100) or []
            except Exception as e:
                # un nœud muet ne doit pas masquer les autres, mais le taire complètement
                # cachait aussi un jeton sans Sys.Audit sur ce nœud (2026-08-11)
                log.debug("cluster %s : tâches actives illisibles sur %s: %s", self.key, n, e)
                errs, last = errs + 1, e
                continue
            out |= {str(t.get("id")) for t in act
                    if t.get("type") == "vzdump"
                    and str(t.get("status", "")).lower() == "running" and t.get("id")}
        # TOUS les nœuds muets : ce n'est plus « un nœud absent » mais le lien. On propage
        # pour que le coupe-circuit s'arme côté Pve — sinon, tant que le cache de
        # nodes_online reste chaud (60 s), on re-payait un timeout PAR NŒUD à chaque cycle
        # sans que rien ne l'arme, l'échec étant avalé ici (2026-08-11).
        if nodes and errs == len(nodes) and last is not None:
            raise last
        return out

    def _local_node(self, ttl=300):
        """Nom du nœud qui répond DIRECTEMENT à l'adresse configurée (`local: 1` de
        /cluster/status), ou None si on n'a pas pu le déterminer.

        ⚠️ POURQUOI C'EST DÉCISIF (mesuré le 2026-08-14). pveproxy ne traite localement
        que les requêtes visant SON nœud : viser un AUTRE nœud fait passer l'appel par un
        tunnel entre nœuds. Quand la ressource demandée est en panne côté distant, ce
        tunnel ne renvoie PAS l'erreur — il reste muet puis expire (HTTP 596) au bout de
        ~30 s, soit exactement le timeout du client. Avec le stockage `nas-backup` HS :
        nœud LOCAL = erreur 500 explicite en 3 s ; nœud distant = 30 s de silence. Le
        second faisait déclarer tout le cluster injoignable (coupe-circuit armé sur un
        timeout) alors que le lien WireGuard répondait en 30 ms — 287 bascules
        « injoignable / de nouveau joignable » dans la seule journée du 14.

        Cache long (5 min) : le nœud d'entrée ne change que si l'adresse configurée change.
        """
        now = time.time()
        if self._local_cache is None or now - self._local_ts > ttl:
            try:
                st = self._ro.cluster.status.get() or []
                self._local_cache = next(
                    (n.get("name") for n in st
                     if n.get("type") == "node" and n.get("local") and n.get("name")), "")
            except Exception as e:  # noqa: BLE001 — simple optimisation : sans elle on
                # retombe sur nodes_online()[0], le comportement d'avant
                log.debug("cluster %s : nœud local indéterminé (%s)", self.key, e)
                self._local_cache = ""      # "" = cherché sans succès (pas de re-essai avant ttl)
            self._local_ts = now
        return self._local_cache or None

    def _any_node(self):
        """Le nœud à qui adresser une lecture NON liée à un invité (stockage de
        sauvegarde partagé : n'importe lequel sait répondre).

        On privilégie le nœud LOCAL de l'API contactée — pas pour la vitesse, mais pour
        ne pas dépendre du tunnel inter-nœuds, qui transforme une panne de stockage en
        timeout de 30 s (cf. _local_node). À défaut, premier nœud en ligne, comme avant.

        Liste vide = cluster entier hors ligne : on lève au lieu d'un `[0]` -> IndexError
        « list index out of range » incompréhensible côté Discord (2026-08-11).
        AvyUnreachable est définie plus bas dans le module : la résolution du nom se fait
        à l'appel, pas à la définition."""
        nodes = self.nodes_online()
        if not nodes:
            raise AvyUnreachable(f"aucun nœud en ligne sur {self.key}")
        local = self._local_node()
        return local if local in nodes else nodes[0]

    def pbs_content(self, vmid=None):
        items = self._ro.nodes(self._any_node()).storage(self.storage).content.get()
        if vmid is not None:
            items = [i for i in items if str(i.get("vmid")) == str(vmid)]
        return items

    def delete_backup(self, volid):
        return self._action_api.nodes(self._any_node()).storage(self.storage).content(volid).delete()


class PbsPermissionError(RuntimeError):
    """Le jeton de LECTURE n'a pas Datastore.Audit sur le stockage PBS — l'ACL
    profonde posée pour le jeton d'action MASQUE le PVEAuditor hérité de « / »
    (4e occurrence du piège ACL, campagne 2026-08-18). Comme ce n'est PAS une panne
    mais une configuration, elle est journalisée UNE seule fois par épisode, remède
    inclus, et les appelants se dégradent sans re-tracer l'exception."""


class AvyUnreachable(RuntimeError):
    """Cluster secondaire (AVEYRON) injoignable — levée par le coupe-circuit partagé
    (Pve._avy_read) sans re-payer le timeout réseau tant que le lien est coupé. Sous-classe
    d'Exception : tous les appelants qui font déjà `except Exception` la capturent, la
    supervision affiche « injoignable » et le bot cesse de re-sonder un lien mort chaque cycle."""


class AvyNodeUnreachable(AvyUnreachable):
    """UN nœud du cluster secondaire ne répond plus — le cluster, lui, va bien.

    Levée par `_RemoteCluster._node_guard`. Sous-classe d'AvyUnreachable (tous les
    appelants la traitent déjà comme « lecture distante indisponible »), mais surtout
    **pas un OSError** : c'est ce qui empêche `Pve._avy_read` d'armer le coupe-circuit du
    CLUSTER pour la panne d'un seul nœud. Distinction née de l'incident du 2026-08-14,
    où le nœud `nas` (partage CIFS démonté) faisait déclarer tout Aveyron injoignable —
    d'abord par intermittence, puis en permanence : chaque fenêtre « half-open » du
    coupe-circuit retombait sur une lecture de ce nœud, qui la ré-armait aussitôt."""


class Pve:
    def __init__(self, cfg):
        self.cfg = cfg
        self.node = cfg.pve_node
        self._ro = None
        self._rw = None
        self._cache = None
        self._cache_ts = 0.0
        self._avy = None
        self._avy_last = None       # dernier instantané resources() distant réussi
        self._avy_last_ts = 0.0
        self._avy_warned = False
        self._pbs_denied_warned = False   # cf. PbsPermissionError (log unique)
        if cfg.pve_enabled:
            try:
                self._ro = _mk_client(cfg, cfg.pve_token_id, cfg.pve_token_secret)
            except Exception:
                log.exception("PVE read client init failed")
        if cfg.pve_actions_enabled:
            try:
                self._rw = _mk_client(cfg, cfg.pve_action_token_id, cfg.pve_action_token_secret)
            except Exception:
                log.exception("PVE action client init failed")
        if getattr(cfg, "avy_enabled", False):
            try:
                self._avy = _RemoteCluster(cfg)
                log.info("cluster secondaire %s: client initialisé (%s)",
                         cfg.avy_key, cfg.avy_host)
            except Exception:
                log.exception("init du client du cluster %s", cfg.avy_key)

    @property
    def enabled(self):
        return self._ro is not None

    @property
    def actions_enabled(self):
        return self._rw is not None

    # ---------- espace de noms du cluster secondaire ----------
    @property
    def avy_enabled(self):
        return self._avy is not None

    @property
    def avy_actions_enabled(self):
        """`actions_enabled` ne parle QUE du R820 : un déploiement où le jeton d'action
        distant manque laisse quand même passer les gardes des cogs. L'échec reste
        explicite grâce à _RemoteCluster._action_api ; cette propriété permet de l'annoncer AVANT
        (diagnostic de démarrage, gardes côté cogs) — 2026-08-11."""
        return self._avy is not None and self._avy.actions_enabled

    @property
    def avy_key(self):
        return self._avy.key if self._avy else None

    def _is_avy(self, vmid):
        return self._avy is not None and int(vmid) >= AVY_OFFSET

    def is_avy_name(self, name):
        return (self._avy is not None
                and str(name).endswith("-" + self.cfg.avy_suffix))

    @staticmethod
    def display_vmid(vmid):
        """vmid RÉEL pour l'affichage (les vmid Aveyron circulent décalés de +1M)."""
        v = int(vmid)
        return v % AVY_OFFSET if v >= AVY_OFFSET else v

    @staticmethod
    def avy_server_key(node):
        """Clé serveur d'un NŒUD Aveyron : nas -> AVY-NAS, ms01 -> AVY-MS01, llm ->
        AVY-LLM. C'est la clé utilisée dans GESTION_SERVERS (rôles G/M/O) et dans les
        noms de catégories (📊 Supervision AVY-PVE / Gestion AVY-PVE) — Aveyron =
        « trois serveurs différents » (choix Nico 2026-07-17), un tiers complet chacun."""
        return f"AVY-{str(node).upper()}"

    def avy_nodes(self):
        """Nœuds Aveyron supervisés : liste STATIQUE de la config (AVY_NODES) — un nœud
        éteint doit garder ses salons — ou, à défaut, découverte en ligne."""
        static = getattr(self.cfg, "avy_nodes", None)
        if static:
            return list(static)
        if self._avy is None:
            return []
        try:
            return self._avy.nodes_online()
        except Exception:
            return []

    def server_of_name(self, name):
        """Clé serveur d'un invité (« AVY-PVE »…) ou None pour le cluster primaire —
        utilisée pour choisir les rôles Discord qui gardent ses boutons et la catégorie
        de son salon."""
        if not self.is_avy_name(name):
            return None
        g = self.guest_map().get(name) or {}
        node = g.get("node")
        return self.avy_server_key(node) if node else None

    def storage_for(self, vmid):
        """Nom du stockage de sauvegarde de l'invité (libellés des confirmations)."""
        if self._is_avy(vmid):
            return self._avy.storage
        return self.cfg.pve_pbs_storage

    # ---- enrichissement des salons par invité (IP/tags/PSI/OS-FS), R820 ET Aveyron —
    # dispatch transparent selon _is_avy(vmid), consommé par ct_channels.build_ct
    # (2026-07-18, remplace les salons #invites-X supprimés à la demande de Nico :
    # « je veux uniquement les informations par salon »).
    def agent_info(self, vmid):
        """OS/FS/IPs d'une VM (R820 ou Aveyron) via son guest-agent. None si LXC ou
        agent absent/éteint."""
        if self._is_avy(vmid):
            return self.avy_agent_info(vmid)
        agent = self._ro.nodes(self.node).qemu(vmid).agent
        return _parse_agent_info(agent)

    def guest_rrd(self, vmid, gtype, timeframe):
        """Séries RRD d'un invité (R820 ou Aveyron) — PSI, débit réseau, etc."""
        if self._is_avy(vmid):
            return self.avy_guest_rrd(vmid, gtype, timeframe)
        api = self._ro.nodes(self.node)
        ep = api.qemu(vmid) if gtype == "qemu" else api.lxc(vmid)
        return ep.rrddata.get(timeframe=timeframe)

    def guest_config(self, vmid, gtype):
        """Config d'un invité (R820 ou Aveyron) — cores/memory/tags/net."""
        if self._is_avy(vmid):
            return self.avy_guest_config(vmid, gtype)
        api = self._ro.nodes(self.node)
        ep = api.qemu(vmid) if gtype == "qemu" else api.lxc(vmid)
        return ep.config.get()

    # ---- supervision Aveyron (consommée par le cog avy.py) ----
    # Toutes ces LECTURES passent par le coupe-circuit _avy_read : lien coupé => échec
    # immédiat (AvyUnreachable) au lieu d'un timeout réseau à chaque appel et chaque cycle.
    def avy_node_status(self, node):
        return self._avy_read(self._avy.node_status, node)

    def avy_node_storages(self, node):
        return self._avy_read(self._avy.node_storages, node)

    def avy_node_tasks(self, node, limit=20, source=None):
        return self._avy_read(self._avy.node_tasks, node, limit=limit, source=source)

    def avy_node_services(self, node):
        return self._avy_read(self._avy.node_services, node)

    def avy_node_updates(self, node):
        return self._avy_read(self._avy.node_updates, node)

    def avy_pbs_content(self):
        """Contenu COMPLET du stockage de sauvegarde Aveyron (l'énumération CIFS prend
        ~16 s : UNE lecture par cycle, répartie ensuite par nœud via le vmid).

        `_avy_soft_read` : la seule lecture assez lente pour expirer toute seule, donc la
        seule qui ne doit pas pouvoir déclarer le cluster mort (cf. _avy_soft_read)."""
        return self._avy_soft_read(self._avy.pbs_content)

    def avy_backup_node(self, node, mode="snapshot"):
        """vzdump all=1 d'UN nœud Aveyron (bouton 💾 du salon hyperviseur AVY-*). ACTION
        déclenchée par l'utilisateur : NON court-circuitée (on tente toujours)."""
        return AVY_UPID_PREFIX + str(self._avy.backup_node(node, mode, self._NOTES))

    def avy_agent_info(self, vmid):
        """Infos internes (OS, FS, IPs) d'une VM Aveyron — vmid VIRTUEL en entrée."""
        return self._avy_read(self._avy.agent_info, int(vmid) - AVY_OFFSET)

    def avy_guest_rrd(self, vmid, gtype, timeframe):
        """Séries RRD d'un invité Aveyron (vmid VIRTUEL) — source des graphes."""
        return self._avy_read(self._avy.guest_rrd, int(vmid) - AVY_OFFSET, gtype, timeframe)

    def avy_node_rrd(self, node, timeframe):
        return self._avy_read(self._avy.node_rrd, node, timeframe)

    def avy_disks(self, node):
        return self._avy_read(self._avy.disks, node)

    def avy_smart(self, node, devpath):
        return self._avy_read(self._avy.smart, node, devpath)

    def avy_cluster_status(self):
        return self._avy_read(self._avy.cluster_status)

    def avy_cluster_log(self, maxn=200):
        return self._avy_read(self._avy.cluster_log, maxn)

    def avy_certificates(self, node):
        return self._avy_read(self._avy.certificates, node)

    def avy_backup_jobs(self):
        return self._avy_read(self._avy.backup_jobs)

    def avy_guest_config(self, vmid, gtype):
        """Config d'un invité Aveyron (cores/memory/tags/net) — vmid VIRTUEL."""
        return self._avy_read(self._avy.guest_config, int(vmid) - AVY_OFFSET, gtype)

    def avy_ping_ms(self):
        return self._avy_read(self._avy.ping_ms)

    # ---------- coupe-circuit partagé du cluster secondaire ----------
    def _avy_maybe_trip(self):
        """Arme le coupe-circuit SAUF si une autre lecture distante vient de réussir.

        ⚠️ Le coupe-circuit protège d'un LIEN mort — pas d'une ressource morte. Or il
        était armé par n'importe quel timeout, y compris celui d'un seul nœud malade :
        le 2026-08-14, le nœud Aveyron `nas` était bloqué par un partage CIFS démonté
        (`/nodes/nas/...` = 30 s de silence puis 596) pendant que `ms01` et `llm`
        répondaient en 0,13 s — et tout le cluster était déclaré injoignable 120 s à
        chaque cycle (287 fois dans la journée).

        Preuve utilisée, sans aucun trafic supplémentaire : une lecture distante RÉUSSIE
        il y a moins d'AVY_ALIVE_WINDOW secondes. Le lien est alors démontré vivant, donc
        l'échec est LOCAL à ce qu'on lisait. Sur une vraie coupure, plus rien ne réussit :
        la fenêtre se vide et le premier échec suivant arme normalement — au pire on paie
        les timeouts d'un cycle de retard, ce qui est exactement le prix d'un diagnostic
        honnête.
        """
        if time.time() - getattr(self, "_avy_ok_ts", 0) <= AVY_ALIVE_WINDOW:
            log.debug("cluster %s : échec isolé (une lecture a réussi il y a moins de "
                      "%ds) — coupe-circuit NON armé", self.avy_key or "AVEYRON",
                      AVY_ALIVE_WINDOW)
            return
        # Preuve indirecte trop vieille : on DEMANDE au lien. Les boucles de supervision
        # tournent toutes les 2 à 4 min, donc en début de cycle le dernier succès a
        # presque toujours plus d'AVY_ALIVE_WINDOW — la seule fenêtre de récence ne
        # suffisait pas (mesuré le 2026-08-14 : le coupe-circuit se ré-armait quand même,
        # jusqu'à rester ouvert en continu). La sonde tranche en 5 s au plus.
        if self._avy is not None and self._avy.alive():
            self._avy_ok_ts = time.time()
            log.debug("cluster %s : sonde OK — échec local, coupe-circuit NON armé",
                      self.avy_key or "AVEYRON")
            return
        self._avy_trip()

    def _avy_trip(self):
        """Arme le coupe-circuit (une seule alerte par épisode d'indisponibilité)."""
        self._avy_fail_ts = time.time()
        if not self._avy_warned:
            log.warning("cluster %s injoignable — lectures distantes court-circuitées "
                        "(%ds), invités servis du cache %ds puis omis",
                        self.avy_key or "AVEYRON", AVY_BACKOFF, AVY_STALE_MAX)
            self._avy_warned = True

    def _avy_ok(self):
        """Ferme le coupe-circuit après une lecture réussie (journalise le retour une fois).

        Horodate aussi la dernière PREUVE de vie du lien, sur laquelle _avy_maybe_trip
        s'appuie pour ne pas confondre « une ressource est morte » et « le lien est mort »."""
        self._avy_ok_ts = time.time()
        if self._avy_warned:
            log.info("cluster %s de nouveau joignable", self.avy_key or "AVEYRON")
            self._avy_warned = False

    def _avy_read(self, fn, *a, **kw):
        """Exécute une LECTURE distante à travers le coupe-circuit partagé. Lien connu
        coupé (fenêtre AVY_BACKOFF) -> AvyUnreachable IMMÉDIATE, sans nouveau timeout.
        Sinon on tente : un échec réseau (OSError — dont les ConnectionError/Timeout de
        requests, qui en héritent) arme le coupe-circuit et lève AvyUnreachable ; toute
        autre exception (5xx PVE, LookupError…) remonte telle quelle pour rester visible."""
        return self._avy_guarded(fn, a, kw, arm=True)

    def _avy_soft_read(self, fn, *a, **kw):
        """Lecture distante qui OBÉIT au coupe-circuit sans jamais l'ARMER (2026-08-14).

        Réservé aux lectures à la fois LENTES et ISOLÉES — aujourd'hui la seule
        énumération du stockage de sauvegarde, qui prend ~16 s en marche normale (CIFS) et
        expire quand le partage est démonté côté Aveyron. Un timeout sur CET appel ne dit
        rien du lien : le prendre pour une panne de cluster coupait pendant 120 s toutes
        les autres lectures — supervision, métriques, graphes — alors que le tunnel
        répondait en 30 ms. Il reste COURT-CIRCUITÉ quand le lien est réellement connu
        coupé : on ne re-paie pas son timeout à chaque cycle d'une vraie panne.

        Contrepartie assumée : une panne qui ne se manifesterait QUE sur cette lecture
        n'arme plus rien — c'est voulu, un stockage mort n'est pas un cluster mort.
        """
        return self._avy_guarded(fn, a, kw, arm=False)

    def _avy_guarded(self, fn, a, kw, *, arm):
        if time.time() - getattr(self, "_avy_fail_ts", 0) < AVY_BACKOFF:
            raise AvyUnreachable(f"{self.avy_key or 'AVEYRON'} injoignable (coupe-circuit)")
        try:
            r = fn(*a, **kw)
        except OSError as e:
            # Hôte d'entrée muet mais un autre répond ? On bascule et on REJOUE une fois :
            # l'appelant ne voit rien (Nico 2026-08-28 : « si l'un est down, les deux
            # autres continuent la gestion »).
            switch = getattr(self._avy, "switch_if_dead", None)
            if switch is not None and switch():
                try:
                    r = fn(*a, **kw)
                except OSError as e2:
                    e = e2
                else:
                    self._avy_ok()
                    return r
            if arm:
                self._avy_maybe_trip()
                raise AvyUnreachable(str(e) or "réseau") from None
            # pas d'_avy_trip ET pas d'_avy_ok : cet appel ne tranche RIEN sur l'état du
            # lien, ni dans un sens ni dans l'autre. L'appelant voit un échec normal.
            raise AvyUnreachable(str(e) or "réseau") from None
        # succès : lui, prouve bien que le lien est là — il referme le coupe-circuit.
        self._avy_ok()
        return r

    def _avy_resources(self):
        """Invités distants au format primaire (nom suffixé, vmid décalé), avec une
        fenêtre de grâce sur panne : mieux vaut un instantané un peu vieux que voir
        provision archiver tous les salons AVEYRON sur une coupure WG d'une minute."""
        now = time.time()
        # backoff après échec : lien coupé => chaque tentative coûte un timeout réseau ;
        # on n'en paie qu'une par fenêtre AVY_BACKOFF, le reste sert cache/omission. Le
        # coupe-circuit est PARTAGÉ avec _avy_read (_avy_fail_ts/_avy_warned communs).
        if now - getattr(self, "_avy_fail_ts", 0) < AVY_BACKOFF:
            if self._avy_last is not None and now - self._avy_last_ts <= AVY_STALE_MAX:
                cur = self._avy_last
            else:
                return []
        else:
            try:
                try:
                    cur = self._avy.resources()
                except OSError:
                    # même règle que _avy_guarded : autre point d'entrée vivant -> rejouer
                    switch = getattr(self._avy, "switch_if_dead", None)
                    if switch is None or not switch():
                        raise
                    cur = self._avy.resources()
                # liste VIDE = anomalie (jeton aveugle / glitch), pas « zéro invité » :
                # la servir ferait archiver tous les salons AVY par provision (vécu
                # 2026-07-17 : ACL /vms écrasant PVEAuditor -> resources()=[] sans
                # erreur -> archivage massif). Même règle que provision._guests.
                if not cur:
                    raise RuntimeError("resources() distant vide — anomalie")
                self._avy_last, self._avy_last_ts = cur, now
                self._avy_ok()
            except Exception:
                # lecture À L'ÉCHELLE DU CLUSTER (pas d'un nœud) : si elle échoue alors
                # que rien d'autre ne répond, c'est bien le lien — d'où maybe_trip.
                self._avy_maybe_trip()
                if self._avy_last is None or now - self._avy_last_ts > AVY_STALE_MAX:
                    return []
                cur = self._avy_last
        sfx = "-" + self.cfg.avy_suffix
        out = []
        for r in cur:
            rr = dict(r)
            if rr.get("name"):
                rr["name"] = rr["name"] + sfx
            if rr.get("vmid") is not None:
                rr["vmid"] = int(rr["vmid"]) + AVY_OFFSET
            out.append(rr)
        return out

    # ---------- reads ----------
    def resources(self):
        res = list(self._ro.cluster.resources.get(type="vm"))
        if self._avy is not None:
            res.extend(self._avy_resources())
        return res

    def lxc_list(self):
        return [r for r in self.resources() if r.get("type") == "lxc"]

    def guest_map(self, ttl=30):
        """{name: {vmid,type,status,node}} with a short TTL cache (sync; used by
        autocomplete). `node` sert à router les invités -avy vers la catégorie et les
        rôles de LEUR serveur (AVY-PVE / AVY-NAS / AVY-LLM)."""
        now = time.time()
        if self._cache is None or now - self._cache_ts > ttl:
            self._cache = {r["name"]: {"vmid": r["vmid"], "type": r.get("type"),
                                       "status": r.get("status"), "node": r.get("node")}
                           for r in self.resources() if r.get("name")}
            self._cache_ts = now
        return self._cache

    def vmid_of(self, name):
        g = self.guest_map().get(name)
        return g["vmid"] if g else None

    def ct_status(self, vmid):
        if self._is_avy(vmid):
            return self._avy_read(self._avy.guest_status, int(vmid) - AVY_OFFSET, "lxc")
        return self._ro.nodes(self.node).lxc(vmid).status.current.get()

    def vm_status(self, vmid):
        """État d'une VM QEMU (API /qemu). ⚠️ `ct_status` tape /lxc et LÈVE sur une VM
        (110 HAOS, 111 win11, 112 arch) -> embed vide/faux. Router sur guest_type. Mêmes
        champs que ct_status (status/uptime/mem/maxmem/cpu/maxdisk ; disk souvent 0 sans
        agent invité)."""
        if self._is_avy(vmid):
            return self._avy_read(self._avy.guest_status, int(vmid) - AVY_OFFSET, "qemu")
        return self._ro.nodes(self.node).qemu(vmid).status.current.get()

    def guest_status(self, vmid, gtype):
        """Statut selon le type d'invité : 'qemu' -> vm_status, sinon ct_status (lxc)."""
        return self.vm_status(vmid) if gtype == "qemu" else self.ct_status(vmid)

    def host_status(self):
        return self._ro.nodes(self.node).status.get()

    def journal(self, lastentries=None, since=None, startcursor=None):
        kw = {}
        if lastentries is not None:
            kw["lastentries"] = int(lastentries)
        else:
            if since is not None:
                kw["since"] = int(since)
            if startcursor is not None:
                kw["startcursor"] = startcursor
        return self._ro.nodes(self.node).journal.get(**kw)

    def tasks(self, vmid=None, limit=50, source=None):
        """`source=None` -> défaut PVE = 'archive', c.-à-d. les tâches TERMINÉES seulement.

        Pour voir ce qui tourne il faut source='active' (ou 'all') : sans lui, `status`
        vaut l'état de sortie ('OK', 'ERROR'…) et jamais 'RUNNING' — d'où l'inefficacité
        historique des gardes « une sauvegarde tourne-t-elle ? » (corrigé 2026-07-15).
        ⚠️ PVE renvoie 'RUNNING' en MAJUSCULES : comparer sans tenir compte de la casse.
        Portée : tâches du NŒUD R820 uniquement (celles du cluster AVEYRON ne remontent
        que via les suivis d'action ciblés, task_status/task_log préfixés)."""
        kw = {"limit": limit}
        if vmid is not None:
            kw["vmid"] = int(vmid)
        if source is not None:
            kw["source"] = source
        return self._ro.nodes(self.node).tasks.get(**kw)

    def running_vzdump_vmids(self):
        """Set des vmid (str) dont une sauvegarde vzdump est EN COURS (les vmid du
        cluster secondaire arrivent DÉCALÉS, cohérents avec guest_map)."""
        try:
            act = self.tasks(source="active", limit=100)
        except Exception as e:
            log.debug("tâches actives R820 illisibles: %s", e)
            act = []
        out = {str(t.get("id")) for t in (act or [])
               if t.get("type") == "vzdump"
               and str(t.get("status", "")).lower() == "running" and t.get("id")}
        if self._avy is not None:
            try:
                # 2026-08-11 : passe désormais par le coupe-circuit. Cette lecture est
                # appelée à CHAQUE cycle des salons (2 min) : sans lui, elle re-payait le
                # timeout de 30 s du client distant pendant toute une coupure WG, alors que
                # le reste du code ne sonde plus qu'une fois par fenêtre AVY_BACKOFF.
                out |= {str(int(v) + AVY_OFFSET)
                        for v in self._avy_read(self._avy.running_vzdump_vmids)}
            except Exception as e:
                # une sauvegarde distante non listée ne coûte qu'un badge 💾 manquant :
                # jamais une panne du bot, mais plus jamais un échec totalement muet
                log.debug("vzdump en cours (cluster distant) illisible: %s", e)
        return out

    # task_status / task_log / pbs_content ci-dessous appellent le cluster distant SANS
    # coupe-circuit : ce sont des suivis d'ACTION déclenchée par l'utilisateur (même
    # exemption assumée que avy_backup_node) — on tente toujours, quitte à payer le
    # timeout, plutôt que de répondre « injoignable » sans avoir essayé.
    def task_status(self, upid):
        if str(upid).startswith(AVY_UPID_PREFIX):
            return self._avy.task_status(str(upid)[len(AVY_UPID_PREFIX):])
        return self._ro.nodes(self.node).tasks(upid).status.get()

    def task_log(self, upid, limit=200):
        if str(upid).startswith(AVY_UPID_PREFIX):
            return self._avy.task_log(str(upid)[len(AVY_UPID_PREFIX):], limit=limit)
        return self._ro.nodes(self.node).tasks(upid).log.get(limit=limit)

    def pbs_content(self, vmid=None):
        if vmid is not None and self._is_avy(vmid):
            return self._avy.pbs_content(int(vmid) - AVY_OFFSET)
        sto = self.cfg.pve_pbs_storage
        try:
            items = self._ro.nodes(self.node).storage(sto).content.get()
        except Exception as e:
            # Droit manquant = CONFIGURATION, pas panne : un log par épisode (pas un
            # traceback par appel), avec le remède exact. Les appelants attrapent
            # PbsPermissionError et se dégradent sans re-journaliser.
            if "Permission check failed" in str(e):
                if not self._pbs_denied_warned:
                    self._pbs_denied_warned = True
                    log.warning(
                        "PBS « %s » : lecture REFUSÉE au jeton de lecture (%s). "
                        "Dit UNE seule fois — remède : pveum acl modify /storage/%s "
                        "--roles BotBackupStorage --tokens '%s'",
                        sto, e, sto, self.cfg.pve_token_id)
                raise PbsPermissionError(
                    f"lecture de « {sto} » refusée au jeton du bot "
                    f"(Datastore.Audit manquant sur /storage/{sto})") from None
            raise
        if self._pbs_denied_warned:
            log.info("PBS « %s » : lecture de nouveau permise", sto)
            self._pbs_denied_warned = False
        if vmid is not None:
            items = [i for i in items if str(i.get("vmid")) == str(vmid)]
        return items

    def reachable(self):
        try:
            self._ro.version.get()
            return True
        except Exception:
            return False

    # ---------- safe actions (action token) ----------
    def _avy_action(self, verb, vmid, gtype):
        upid = self._avy.action(verb, int(vmid) - AVY_OFFSET, gtype)
        return AVY_UPID_PREFIX + str(upid)

    def start_ct(self, vmid):
        if self._is_avy(vmid):
            return self._avy_action("start", vmid, "lxc")
        return self._rw.nodes(self.node).lxc(vmid).status.start.post()

    def stop_ct(self, vmid):
        if self._is_avy(vmid):
            return self._avy_action("stop", vmid, "lxc")
        return self._rw.nodes(self.node).lxc(vmid).status.stop.post()

    def shutdown_ct(self, vmid):
        if self._is_avy(vmid):
            return self._avy_action("shutdown", vmid, "lxc")
        return self._rw.nodes(self.node).lxc(vmid).status.shutdown.post()

    def reboot_ct(self, vmid):
        if self._is_avy(vmid):
            return self._avy_action("reboot", vmid, "lxc")
        return self._rw.nodes(self.node).lxc(vmid).status.reboot.post()

    # ---------- VM qemu (mêmes actions, API qemu) ----------
    def start_vm(self, vmid):
        if self._is_avy(vmid):
            return self._avy_action("start", vmid, "qemu")
        return self._rw.nodes(self.node).qemu(vmid).status.start.post()

    def stop_vm(self, vmid):
        if self._is_avy(vmid):
            return self._avy_action("stop", vmid, "qemu")
        return self._rw.nodes(self.node).qemu(vmid).status.stop.post()

    def shutdown_vm(self, vmid):
        if self._is_avy(vmid):
            return self._avy_action("shutdown", vmid, "qemu")
        return self._rw.nodes(self.node).qemu(vmid).status.shutdown.post()

    def reboot_vm(self, vmid):
        if self._is_avy(vmid):
            return self._avy_action("reboot", vmid, "qemu")
        return self._rw.nodes(self.node).qemu(vmid).status.reboot.post()

    def guest_type(self, name):
        """'lxc' ou 'qemu' — pour router les actions vers la bonne API."""
        g = self.guest_map().get(name)
        return g.get("type") if g else None

    # ------------------------------------------------------------------ API async
    # proxmoxer est SYNCHRONE (requests) : un appel depuis la boucle d'événements la
    # gèle jusqu'à 15 s (R820) ou 30 s (Aveyron) — plus aucune interaction acquittée,
    # plus de heartbeat gateway, reconnexion possible de la session Discord.
    #
    # Le contrat « enveloppez chaque appel dans to_thread » n'existait qu'en commentaire
    # (pve.py:5) : 92 sites le respectaient, 9 l'avaient oublié (audit 2026-08-11).
    # Ces enveloppes rendent le contrat EXÉCUTABLE — appeler `await pve.aguest_map()`
    # est plus court que la version fautive, donc c'est le chemin naturel.
    #
    # Même patron que Influx.aq(), déjà en place côté InfluxDB.

    async def acall(self, fn, *args, **kwargs):
        """Enveloppe générique : `await pve.acall(pve.node_status, "pve")`."""
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def aresources(self):
        return await asyncio.to_thread(self.resources)

    async def aguest_map(self, ttl=30):
        return await asyncio.to_thread(self.guest_map, ttl)

    async def aguest_status(self, vmid, gtype):
        return await asyncio.to_thread(self.guest_status, vmid, gtype)

    async def aguest_config(self, vmid, gtype):
        return await asyncio.to_thread(self.guest_config, vmid, gtype)

    async def avmid_of(self, name):
        return await asyncio.to_thread(self.vmid_of, name)

    async def aguest_type(self, name):
        return await asyncio.to_thread(self.guest_type, name)

    async def aserver_of_name(self, name):
        return await asyncio.to_thread(self.server_of_name, name)

    async def astorage_for(self, vmid):
        return await asyncio.to_thread(self.storage_for, vmid)

    async def apbs_content(self, vmid=None):
        return await asyncio.to_thread(self.pbs_content, vmid)

    async def atask_status(self, upid):
        return await asyncio.to_thread(self.task_status, upid)

    async def apoll_task(self, upid, timeout=180, poll_every=2, max_fails=5):
        """Suit une tâche PVE jusqu'à `timeout` secondes de temps RÉEL.

        Renvoie "OK", le code de sortie PVE, "running" (pas finie dans le délai) ou
        "lost" (NOTRE suivi s'est interrompu — la tâche, elle, continue côté Proxmox).
        `core.format.outcome_text()` rend ce verdict pour l'utilisateur.

        Trois cogs avaient leur propre copie (actions, ct_channels, rdv), avec les mêmes
        deux défauts corrigés le 2026-08-11 :
          - un SEUL hoquet réseau (pveproxy rechargé, tunnel WG qui clignote sur un UPID
            « avy: ») terminait le suivi sur « unknown » : une sauvegarde RÉUSSIE passait
            pour un échec. On tolère `max_fails` échecs consécutifs.
          - la borne était un COMPTE d'itérations supposant N secondes par tour ; un échec
            réseau coûte le timeout distant (jusqu'à 30 s), donc la durée réelle dérivait
            très au-delà de la valeur annoncée. La borne est l'horloge MONOTONE.
        """
        deadline = time.monotonic() + max(2, timeout)
        fails = 0
        while time.monotonic() < deadline:
            try:
                st = await self.atask_status(upid)
            except Exception:  # noqa: BLE001
                fails += 1
                if fails >= max_fails:
                    log.warning("suivi de la tâche %s abandonné (%d échecs consécutifs) "
                                "— la tâche continue côté PVE", upid, fails, exc_info=True)
                    return "lost"
                await asyncio.sleep(poll_every)
                continue
            fails = 0
            if (st or {}).get("status") == "stopped":
                return st.get("exitstatus") or "OK"
            await asyncio.sleep(poll_every)
        return "running"

    # Le paramètre est « notes-template » (tiret), pas « notes_template » : avec l'underscore
    # l'API répond 400 « property is not defined in schema » AVANT tout contrôle de
    # permission — le bouton 💾 des salons d'invités était donc inopérant depuis toujours
    # (corrigé 2026-07-15). Tiret => pas un identifiant Python => passage par un dict.
    _NOTES = {"notes-template": "discord-bot {{guestname}}"}

    def backup(self, vmid, mode="snapshot"):
        if self._is_avy(vmid):
            return AVY_UPID_PREFIX + str(self._avy.backup(
                int(vmid) - AVY_OFFSET, mode, self._NOTES))
        return self._rw.nodes(self.node).vzdump.post(
            vmid=int(vmid), storage=self.cfg.pve_pbs_storage, mode=mode, **self._NOTES)

    def backup_all(self, mode="snapshot"):
        """vzdump de TOUS les invités (bouton 💾 du salon de l'hyperviseur).

        Reproduit le périmètre du job planifié `pbs-daily-cts` de /etc/pve/jobs.cfg, qui
        est `all 1` + `exclude 200` : CT200 est le PBS lui-même, et le sauvegarder vers un
        datastore qu'il sert (pbs-nas, monté en NFS par CT200) reviendrait à lui demander
        de s'écrire dedans. La liste est configurable (NODE_BACKUP_EXCLUDE) plutôt que
        codée en dur, comme les autres garde-fous du projet.

        Les invités sur lesquels le token n'a pas VM.Backup sont silencieusement ignorés
        par PVE (VZDump.pm : check(..., noerr => $opts->{all})), sans échec global.
        Portée : R820 uniquement (le salon-nœud est celui du R820)."""
        kw = {"all": 1, "storage": self.cfg.pve_pbs_storage, "mode": mode}
        excl = getattr(self.cfg, "node_backup_exclude", "")
        if excl:
            kw["exclude"] = excl
        return self._rw.nodes(self.node).vzdump.post(**kw, **self._NOTES)

    def delete_backup(self, volid):
        """Supprime UNE sauvegarde (volid PBS) via l'API storage/content. Renvoie
        généralement un UPID (tâche) que l'appelant peut suivre. Les volid du cluster
        secondaire se reconnaissent à leur stockage (« nas-backup:... »)."""
        if (self._avy is not None
                and str(volid).split(":", 1)[0] == self._avy.storage):
            return AVY_UPID_PREFIX + str(self._avy.delete_backup(volid))
        return self._rw.nodes(self.node).storage(
            self.cfg.pve_pbs_storage).content(volid).delete()
