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


def _mk_client(cfg, token_id, secret):
    from proxmoxer import ProxmoxAPI
    user, name = cfg.split_token(token_id)
    return ProxmoxAPI(
        cfg.pve_host, port=cfg.pve_port, user=user,
        token_name=name, token_value=secret,
        verify_ssl=cfg.pve_verify_ssl, timeout=15)


def _mk_remote_client(cfg, token_id, secret):
    from proxmoxer import ProxmoxAPI
    user, name = cfg.split_token(token_id)
    # timeout 30 s (vs 15 côté R820) : l'énumération du stockage CIFS nas-backup prend
    # ~16 s à travers le tunnel WG (mesuré 2026-07-17) ; tous les appels passent par
    # asyncio.to_thread, un appel lent ne bloque donc jamais la boucle du bot.
    return ProxmoxAPI(
        cfg.avy_host, port=cfg.avy_port, user=user,
        token_name=name, token_value=secret,
        verify_ssl=cfg.avy_verify_ssl, timeout=30)


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
        self._ro = _mk_remote_client(cfg, cfg.avy_token_id, cfg.avy_token_secret)
        self._rw = None
        if cfg.avy_action_token_secret:
            self._rw = _mk_remote_client(cfg, cfg.avy_action_token_id,
                                         cfg.avy_action_token_secret)
        else:
            # 2026-08-11 : sans ce log, l'oubli du secret (AVY_* n'est documenté NULLE PART
            # dans config.env.example) ne se voyait qu'au premier /ctctl sur un invité -avy,
            # sous la forme d'un « 'NoneType' object has no attribute 'nodes' ».
            log.warning("cluster %s : jeton d'action absent (AVY_PVE_ACTION_TOKEN_SECRET) "
                        "— actions distantes DÉSACTIVÉES, lectures seules", self.key)
        self._gnodes = {}
        self._nodes_cache = None
        self._nodes_ts = 0.0

    @property
    def actions_enabled(self):
        return self._rw is not None

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

    def node_status(self, node):
        return self._ro.nodes(node).status.get()

    # ---- lectures riches (graphes, matériel, cluster, config) ----
    def guest_rrd(self, vmid, gtype, timeframe):
        api = self._ro.nodes(self.node_of(vmid))
        ep = api.qemu(vmid) if gtype == "qemu" else api.lxc(vmid)
        return ep.rrddata.get(timeframe=timeframe)

    def node_rrd(self, node, timeframe):
        return self._ro.nodes(node).rrddata.get(timeframe=timeframe)

    def disks(self, node):
        return self._ro.nodes(node).disks.list.get()

    def smart(self, node, devpath):
        return self._ro.nodes(node).disks.smart.get(disk=devpath)

    def cluster_status(self):
        return self._ro.cluster.status.get()

    def cluster_log(self, maxn=200):
        return self._ro.cluster.log.get(max=maxn)

    def certificates(self, node):
        return self._ro.nodes(node).certificates.info.get()

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
        return self._ro.nodes(node).storage.get()

    def node_tasks(self, node, limit=20, source=None):
        kw = {"limit": limit}
        if source is not None:
            kw["source"] = source
        return self._ro.nodes(node).tasks.get(**kw)

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

    def _any_node(self):
        """Un nœud en ligne quelconque — le stockage de sauvegarde est PARTAGÉ (CIFS sur
        tous les nœuds), n'importe lequel répond. Liste vide = cluster entier hors ligne :
        on lève au lieu d'un `[0]` -> IndexError « list index out of range » incompréhensible
        côté Discord (2026-08-11). AvyUnreachable est définie plus bas dans le module : la
        résolution du nom se fait à l'appel, pas à la définition."""
        nodes = self.nodes_online()
        if not nodes:
            raise AvyUnreachable(f"aucun nœud en ligne sur {self.key}")
        return nodes[0]

    def pbs_content(self, vmid=None):
        items = self._ro.nodes(self._any_node()).storage(self.storage).content.get()
        if vmid is not None:
            items = [i for i in items if str(i.get("vmid")) == str(vmid)]
        return items

    def delete_backup(self, volid):
        return self._action_api.nodes(self._any_node()).storage(self.storage).content(volid).delete()


class LlmExecError(RuntimeError):
    """Échec de l'exécution dans la VM (timeout, exitcode≠0, JSON invalide)."""


class AvyUnreachable(RuntimeError):
    """Cluster secondaire (AVEYRON) injoignable — levée par le coupe-circuit partagé
    (Pve._avy_read) sans re-payer le timeout réseau tant que le lien est coupé. Sous-classe
    d'Exception : tous les appelants qui font déjà `except Exception` la capturent, la
    supervision affiche « injoignable » et le bot cesse de re-sonder un lien mort chaque cycle."""


class _LlmBridge:
    """Pont vers le serveur LiteLLM (port 4000) tournant DANS la VM ubuntu-llm
    (nœud llm, cluster Aveyron) — un Qwen 35B local sur RTX 3090 passthrough.

    Aucune route réseau directe (le VLAN de la VM n'est pas routé par le tunnel WG) :
    chaque appel passe par l'API PVE -> guest-agent -> `exec curl localhost:4000` à
    l'INTÉRIEUR de la VM, avec un jeton discordbot@pve!llm scopé À CETTE SEULE VM
    (ACL /vms/<vmid>, PAS /vms) — même en cas de fuite du jeton, le pire cas reste une
    exécution de commande dans cette VM précise, jamais sur le reste du cluster."""

    # Concurrence des CONVERSATIONS (2026-08-11). Chaque appel occupe un thread jusqu'à
    # 90 s (sondage exec-status à travers le tunnel WG) et /assistant comme #chat-ia
    # lancent une conversation PAR MESSAGE, jusqu'à 3 tours, sans file ni anti-rebond :
    # huit questions d'affilée immobilisaient huit workers du pool PARTAGÉ d'asyncio
    # (~6-8 dans le CT106), donc AUSSI les lectures PVE/Influx des boucles de fond —
    # embeds épinglés figés et boutons hors du budget de 3 s, sans rien dans les logs.
    # On REFUSE au-delà de la limite au lieu d'attendre : attendre immobiliserait
    # justement le worker qu'on cherche à libérer. Le moniteur périodique (monitor(),
    # 1 appel / 5 min) n'est PAS compté — le refuser afficherait « IA locale
    # injoignable » à tort dans le salon AVY-LLM.
    _MAX_CHATS_INFLIGHT = 2

    def __init__(self, cfg):
        self.cfg = cfg
        self.node = cfg.avy_llm_node
        self.vmid = cfg.avy_llm_vmid
        self._api = _mk_remote_client(cfg, cfg.avy_llm_token_id, cfg.avy_llm_token_secret)
        self._chat_sem = threading.BoundedSemaphore(self._MAX_CHATS_INFLIGHT)

    @staticmethod
    def _fix_mojibake(s):
        """L'API PVE renvoie out-data/err-data en UTF-8 réinterprété comme Latin-1
        (« Prêt » -> « PrÃªt », vérifié 2026-07-18) — le ré-encodage restaure les
        accents. Repli silencieux sur le texte brut si ce n'est pas le cas (contenu
        déjà correct, ou binaire non-UTF8) plutôt que de planter dessus."""
        try:
            return s.encode("latin1").decode("utf8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            return s

    def _exec(self, argv):
        """Lance argv (liste, PAS de shell — execve direct par le guest-agent) et
        attend la fin. Renvoie (stdout, stderr, exitcode).

        Le sondage part court puis ralentit (0,3 s -> plafond 2 s) : une réponse rapide
        revient en ~0,3 s au lieu de 1,5 s, et une réponse lente coûte ~48 requêtes au
        lieu de 60 sur le tunnel WG (2026-08-11 ; chiffre recompté à la relecture — le
        plafond de 2 s ne divise pas le nombre de sondages par 3, c'est la LATENCE des
        réponses courtes qui gagne, pas le volume)."""
        ep = self._api.nodes(self.node).qemu(self.vmid).agent
        r = ep.exec.post(command=argv)
        pid = r["pid"]
        deadline = time.time() + 90
        delay = 0.3
        while time.time() < deadline:
            st = ep("exec-status").get(pid=pid)
            if st.get("exited"):
                out = self._fix_mojibake(st.get("out-data", ""))
                err = self._fix_mojibake(st.get("err-data", ""))
                return out, err, st.get("exitcode")
            time.sleep(delay)
            delay = min(2.0, delay * 1.6)
        raise LlmExecError("timeout (90s) en attente de la réponse du modèle")

    def chat(self, messages, tools=None, max_tokens=700, temperature=0.3):
        """Appelle /v1/chat/completions du LiteLLM local. Renvoie le message complet
        de l'API (dict avec 'content' et éventuellement 'tool_calls')."""
        payload = {"model": self.cfg.avy_llm_model, "messages": messages,
                   "max_tokens": max_tokens, "temperature": temperature}
        if tools:
            payload["tools"] = tools
        argv = ["curl", "-s", "--max-time", "80", "-X", "POST",
                f"http://localhost:{self.cfg.avy_llm_port}/v1/chat/completions",
                "-H", "Content-Type: application/json",
                "--data-binary", json.dumps(payload)]
        # refus immédiat au-delà de _MAX_CHATS_INFLIGHT (cf. commentaire de classe) :
        # l'appelant affiche le message tel quel, personne n'attend en occupant un worker
        if not self._chat_sem.acquire(blocking=False):
            raise LlmExecError(
                f"trop de demandes en cours ({self._MAX_CHATS_INFLIGHT} au maximum) "
                f"— réessaie dans un instant")
        try:
            out, err, code = self._exec(argv)
        finally:
            self._chat_sem.release()
        if not out:
            raise LlmExecError(f"réponse vide (exitcode={code}, err={err[:200]})")
        try:
            data = json.loads(out)
        except json.JSONDecodeError as e:
            raise LlmExecError(f"réponse non-JSON : {out[:200]}") from e
        if "error" in data:
            raise LlmExecError(str(data["error"])[:300])
        return data["choices"][0]["message"]

    # Script exécuté DANS la VM (un seul aller-retour guest-agent au lieu de ~8) :
    # GPU (nvidia_gpu_exporter:9835), 4 services systemd, disque racine, 3 health
    # checks, infos modèle, charge (node_exporter:9100). Testé de bout en bout
    # 2026-07-18. `python3 -c <script>` en UN SEUL argv -> zéro souci d'échappement
    # (execve direct par le guest-agent, pas de shell).
    _MONITOR_SCRIPT = r'''
import json, subprocess, urllib.request, shutil

def get(url, timeout=3):
    try:
        return urllib.request.urlopen(url, timeout=timeout).read().decode()
    except Exception:
        return None

def metric(text, name):
    if not text:
        return None
    for line in text.splitlines():
        if line.startswith(name + "{") or line.startswith(name + " "):
            try:
                return float(line.rsplit(" ", 1)[-1])
            except ValueError:
                return None
    return None

out = {}
gpu_txt = get("http://localhost:9835/metrics")
out["gpu"] = {
    "temp": metric(gpu_txt, "nvidia_smi_temperature_gpu"),
    "mem_used": metric(gpu_txt, "nvidia_smi_memory_used_bytes"),
    "mem_total": metric(gpu_txt, "nvidia_smi_memory_total_bytes"),
    "util": metric(gpu_txt, "nvidia_smi_utilization_gpu_ratio"),
    "power": metric(gpu_txt, "nvidia_smi_power_draw_watts"),
    "fan": metric(gpu_txt, "nvidia_smi_fan_speed_ratio"),
}
services = {}
for s in ("llama-server", "litellm", "llm-router", "llama-server-small"):
    r = subprocess.run(["systemctl", "is-active", s], capture_output=True, text=True)
    services[s] = r.stdout.strip()
out["services"] = services

du = shutil.disk_usage("/")
out["disk"] = {"total": du.total, "used": du.used, "free": du.free}

out["llama_health"] = get("http://localhost:8080/health")
out["litellm_alive"] = get("http://localhost:4000/health/liveliness")
out["router_health"] = get("http://localhost:8000/health")

models_txt = get("http://localhost:8080/v1/models")
try:
    m = json.loads(models_txt)["data"][0]["meta"]
    out["model"] = {"n_ctx": m.get("n_ctx"), "n_params": m.get("n_params"),
                    "size_bytes": m.get("size")}
except Exception:
    out["model"] = None

load_txt = get("http://localhost:9100/metrics")
out["load1"] = metric(load_txt, "node_load1")

print(json.dumps(out))
'''

    def monitor(self):
        """Un instantané complet du stack IA (GPU/services/disque/santé/modèle).
        Lève LlmExecError si le script échoue ou renvoie du JSON invalide."""
        argv = ["python3", "-c", self._MONITOR_SCRIPT]
        out, err, code = self._exec(argv)
        if not out:
            raise LlmExecError(f"monitor: sortie vide (exitcode={code}, err={err[:200]})")
        try:
            return json.loads(out)
        except json.JSONDecodeError as e:
            raise LlmExecError(f"monitor: JSON invalide : {out[:200]}") from e


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
        self._llm = None
        self._llm_pool = None       # pool dédié, créé à la 1re utilisation (_llm_executor)
        if getattr(cfg, "avy_llm_enabled", False):
            try:
                self._llm = _LlmBridge(cfg)
                log.info("assistant IA local: pont initialisé (VM %s sur %s)",
                         cfg.avy_llm_vmid, cfg.avy_llm_node)
            except Exception:
                log.exception("init du pont assistant IA local")

    @property
    def llm_enabled(self):
        return self._llm is not None

    def llm_chat(self, messages, tools=None, max_tokens=700, temperature=0.3):
        """Chat/function-calling via le Qwen local (voir _LlmBridge). Lève
        LlmExecError si indisponible/en échec — à l'appelant de décider du message
        d'erreur affiché."""
        if self._llm is None:
            raise LlmExecError("assistant IA local non configuré")
        return self._llm_guarded(self._llm.chat, messages, tools=tools,
                                 max_tokens=max_tokens, temperature=temperature)

    def llm_monitor(self):
        """Instantané complet du stack IA locale (GPU/services/disque/santé/modèle)."""
        if self._llm is None:
            raise LlmExecError("assistant IA local non configuré")
        return self._llm_guarded(self._llm.monitor)

    def _llm_executor(self):
        """Pool DÉDIÉ aux appels IA (créé à la première utilisation).

        `asyncio.to_thread` utilise le pool PAR DÉFAUT de la boucle (~6-8 threads ici),
        partagé avec toutes les lectures PVE/Influx/qBittorrent : un appel IA qui dure
        90 s y prend une place que les boucles de fond attendent. Ces deux enveloppes
        sortent l'IA de ce pool — à préférer à `asyncio.to_thread(pve.llm_chat, …)`
        (2026-08-11).

        Taille = _MAX_CHATS_INFLIGHT + 2, et surtout PAS 2 (relecture 2026-08-11) : la
        limite de concurrence est le sémaphore de _LlmBridge, qui REFUSE tout de suite
        au-delà de 2 conversations. Avec un pool de 2, les demandes en trop n'auraient
        jamais atteint ce refus : elles auraient attendu en file DANS l'exécuteur (file
        non bornée) jusqu'à 90 s — exactement le comportement « mise en file muette »
        que le sémaphore existe pour éviter. Les workers libres servent au refus
        immédiat et au monitor() périodique, qui n'est pas compté par le sémaphore et ne
        doit pas rester coincé derrière deux conversations."""
        if self._llm_pool is None:
            self._llm_pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=_LlmBridge._MAX_CHATS_INFLIGHT + 2, thread_name_prefix="llm")
        return self._llm_pool

    async def allm_chat(self, messages, tools=None, max_tokens=700, temperature=0.3):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._llm_executor(),
            lambda: self.llm_chat(messages, tools=tools, max_tokens=max_tokens,
                                  temperature=temperature))

    async def allm_monitor(self):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._llm_executor(), self.llm_monitor)

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
        """Clé serveur d'un NŒUD Aveyron : pve -> AVY-PVE, nas -> AVY-NAS, llm ->
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

    def avy_pbs_content(self):
        """Contenu COMPLET du stockage de sauvegarde Aveyron (l'énumération CIFS prend
        ~16 s : UNE lecture par cycle, répartie ensuite par nœud via le vmid)."""
        return self._avy_read(self._avy.pbs_content)

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
    def _avy_trip(self):
        """Arme le coupe-circuit (une seule alerte par épisode d'indisponibilité)."""
        self._avy_fail_ts = time.time()
        if not self._avy_warned:
            log.warning("cluster %s injoignable — lectures distantes court-circuitées "
                        "(%ds), invités servis du cache %ds puis omis",
                        self.avy_key or "AVEYRON", AVY_BACKOFF, AVY_STALE_MAX)
            self._avy_warned = True

    def _avy_ok(self):
        """Ferme le coupe-circuit après une lecture réussie (journalise le retour une fois)."""
        if self._avy_warned:
            log.info("cluster %s de nouveau joignable", self.avy_key or "AVEYRON")
            self._avy_warned = False

    def _avy_read(self, fn, *a, **kw):
        """Exécute une LECTURE distante à travers le coupe-circuit partagé. Lien connu
        coupé (fenêtre AVY_BACKOFF) -> AvyUnreachable IMMÉDIATE, sans nouveau timeout.
        Sinon on tente : un échec réseau (OSError — dont les ConnectionError/Timeout de
        requests, qui en héritent) arme le coupe-circuit et lève AvyUnreachable ; toute
        autre exception (5xx PVE, LookupError…) remonte telle quelle pour rester visible."""
        if time.time() - getattr(self, "_avy_fail_ts", 0) < AVY_BACKOFF:
            raise AvyUnreachable(f"{self.avy_key or 'AVEYRON'} injoignable (coupe-circuit)")
        try:
            r = fn(*a, **kw)
        except OSError as e:
            self._avy_trip()
            raise AvyUnreachable(str(e) or "réseau") from None
        self._avy_ok()
        return r

    def _llm_guarded(self, fn, *a, **kw):
        """Comme _avy_read mais pour le pont IA (même hôte Aveyron) : lève LlmExecError
        (et non AvyUnreachable) pour que /assistant & #chat-ia l'affichent proprement via
        leur `except LlmExecError`. Coupe-circuit PARTAGÉ avec les lectures distantes."""
        if time.time() - getattr(self, "_avy_fail_ts", 0) < AVY_BACKOFF:
            raise LlmExecError("cluster AVEYRON injoignable")
        try:
            r = fn(*a, **kw)
        except OSError as e:
            self._avy_trip()
            raise LlmExecError(f"assistant IA injoignable ({e})") from None
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
                self._avy_trip()
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
        items = self._ro.nodes(self.node).storage(self.cfg.pve_pbs_storage).content.get()
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
