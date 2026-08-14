"""Terminal Discord INTERACTIF (2026-07-13) — console root sur un guest LXC depuis son
salon, via la console `termproxy` de l'API Proxmox, avec un ÉCRAN LIVE (émulateur pyte)
piloté par des boutons-touches → permet les applis plein-écran (claude, top, vim…).

2026-07-15 : s'y ajoute `open_node_for()`, console root sur l'HYPERVISEUR lui-même,
ouverte depuis le salon « 🔒 Lock ». Deux différences délibérées avec les guests :
  - transport SSH (clé dédiée) et non termproxy — PVE ne donne un shell root sur le nœud
    qu'à `root@pam` ; le détail du raisonnement est dans bot/core/nodeshell.py ;
  - porte PROPRIÉTAIRE (`_may_open_node`) : `NODE_TERMINAL_OWNER_IDS` **OU** le rôle
    « O <srv> » du serveur du nœud ; le tier **M** est refusé. La console des guests,
    elle, accepte en plus `TERMINAL_OWNER_ROLE_IDS`.
    ⚠️ Rectifié le 2026-08-11 : ce paragraphe annonçait « porte PROPRIÉTAIRE STRICTE :
    aucun rôle Discord n'y donne accès ». C'est FAUX depuis la refonte des rôles du
    2026-07-16, qui a délibérément ouvert la console du nœud au tier O (cf. le docstring
    de `_may_open_node`). C'est le TEXTE qui avait dérivé, pas le code. Les mêmes phrases
    périmées subsistent hors de ce fichier — README.md « Console root du nœud »,
    config.env.example (NODE_TERMINAL_OWNER_IDS) et config.py — et restent à corriger.
`TerminalSession` / `TerminalView` sont partagés : NodeShell expose le même contrat que
PveConsole. Une session nœud se reconnaît à `vmid is None` (et sa vue est la sous-classe
`NodeTerminalView`, dont la porte est celle du nœud et non celle des guests).

SÉCURITÉ (fonctionnalité la plus sensible du bot) :
  - compte PVE dédié LEAST-PRIVILEGE `botconsole@pve` (VM.Console) autorisé au niveau ACL
    UNIQUEMENT sur les guests permis → vaultwarden/mailserver/bdd/HAOS hors de portée ;
  - ouverture réservée au PROPRIÉTAIRE / au rôle (`TERMINAL_OWNER_IDS` / `_ROLE_IDS`) ;
  - guests sensibles ré-exclus côté bot (`TERMINAL_EXCLUDED_GUESTS`) — double barrière ;
  - thread PRIVÉ (aucun repli public) ; ⚠️ l'écran live est édité dans le fil (visible du
    propriétaire ET d'un membre « Gérer les fils »/Admin — compromis assumé pour le mode
    interactif) ; timeout d'inactivité ; chaque saisie auditée ; sessions bornées ;
  - 2026-08-11 : les BOUTONS de l'écran passent enfin une porte (`GatedView`). Avant, ils
    ne vérifiaient QUE `owner_id` : ni le tier, ni la session 2FA n'étaient revalidés
    après l'ouverture, si bien qu'une console ouverte survivait à l'expiration du 2FA.
"""
import asyncio
import logging
import re
import ssl
import time
import urllib.parse

import aiohttp
import discord
from discord.ext import commands

from ..core import bg
from ..core.gates import GatedView

log = logging.getLogger("discord-bot.terminal")

COLS, ROWS = 80, 24                     # taille terminal (tient dans un message Discord)

# Intervalle MINIMUM entre deux éditions de l'écran (2026-08-11). `_last_edit` était écrit
# et jamais relu : l'anti-rebond prévu n'existait pas et la boucle éditait le message à
# chaque tick, soit jusqu'à 2 PATCH/s pour un `top`/`journalctl -f`. Le limiteur client de
# discord.py absorbait la casse (il dort dans le bucket), mais on ne dépend pas d'un
# comportement implicite de la bibliothèque.
# ⚠️ 1,5 s et non 1 s : le budget d'édition est d'environ 5 PATCH / 5 s par salon (le fil
# du terminal a son propre bucket), donc 1 édition/s tient EXACTEMENT sur la limite — le
# moindre à-côté (un clic 🔄, une reprise après coupure) fait dormir la requête dans le
# bucket, et les tests d'inactivité et de plafond absolu vivent dans la MÊME boucle : ils
# seraient retardés d'autant sur un shell root. 1,5 s garde de la marge sans écran saccadé.
EDIT_MIN_INTERVAL = 1.5

try:
    import pyte
    _HAS_PYTE = True
except Exception:                       # pragma: no cover
    _HAS_PYTE = False

# sentinelle des pré-contrôles : « refus 2FA » se répond par deny_2fa (bouton /2fa), pas
# par un simple message — cf. Terminal._refuse
_NEED_2FA = object()

_ANSI = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]|\x1b\[[0-9;?]*[ -/]*[@-~]")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _fence(s: str) -> str:
    return (s or "").replace("```", "`​``")


_DUREE = re.compile(r"^(?:(?P<h>\d{1,3})\s*h\s*(?P<hm>\d{1,2})?|(?P<m>\d{1,4})\s*(?:m(?:in)?)?)$",
                    re.I)


def _parse_minutes(raw: str) -> int:
    """« 45 », « 45m », « 45 min », « 2h », « 1h30 » -> minutes. ValueError sinon.

    Les bornes (1..plafond) ne sont PAS appliquées ici : l'appelant les connaît (guest ou
    nœud) et doit pouvoir citer la valeur refusée dans son message.
    """
    m = _DUREE.match((raw or "").strip())
    if not m:
        raise ValueError(raw)
    if m.group("h") is not None:
        return int(m.group("h")) * 60 + int(m.group("hm") or 0)
    return int(m.group("m"))


class PveConsole:
    """Console termproxy -> websocket vers un LXC (auth ticket, compte least-priv)."""

    def __init__(self, cfg):
        self.cfg = cfg
        # cfg.pve_verify_ssl vaut False, True, ou un CHEMIN de CA (config._verify_ssl).
        # Sans cafile=, activer la vérification par un chemin ferait échouer la console en
        # SSLCertVerificationError : le magasin système ne contient pas le CA auto-signé
        # de PVE, et le bouton 🖥️ n'ouvrirait plus rien (2026-08-11).
        _ca = cfg.pve_verify_ssl if isinstance(cfg.pve_verify_ssl, str) else None
        self._ssl = ssl.create_default_context(cafile=_ca)
        if not cfg.pve_verify_ssl:
            self._ssl.check_hostname = False
            self._ssl.verify_mode = ssl.CERT_NONE
        self.session = None
        self.ws = None
        self._ping = None

    def _base(self):
        return f"https://{self.cfg.pve_host}:{self.cfg.pve_port}/api2/json"

    async def open(self, vmid):
        self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=self._ssl))
        async with self.session.post(
                f"{self._base()}/access/ticket",
                data={"username": self.cfg.pve_console_user,
                      "password": self.cfg.pve_console_password}) as r:
            if r.status != 200:
                raise RuntimeError(f"auth PVE ({r.status})")
            d = (await r.json())["data"]
        ticket, csrf = d["ticket"], d["CSRFPreventionToken"]
        hdr = {"Cookie": f"PVEAuthCookie={ticket}", "CSRFPreventionToken": csrf}
        node = self.cfg.pve_node
        async with self.session.post(
                f"{self._base()}/nodes/{node}/lxc/{vmid}/termproxy", headers=hdr) as r:
            if r.status != 200:
                raise RuntimeError(f"termproxy ({r.status})")
            t = (await r.json())["data"]
        url = (f"wss://{self.cfg.pve_host}:{self.cfg.pve_port}/api2/json/nodes/{node}"
               f"/lxc/{vmid}/vncwebsocket?port={t['port']}"
               f"&vncticket={urllib.parse.quote(t['ticket'])}")
        self.ws = await self.session.ws_connect(
            url, headers={"Cookie": f"PVEAuthCookie={ticket}"},
            ssl=self._ssl, protocols=["binary"], heartbeat=None)
        await self.ws.send_str(f"{t.get('user', self.cfg.pve_console_user)}:{t['ticket']}\n")
        await self.ws.send_str(f"1:{COLS}:{ROWS}:")        # resize (protocole 1:cols:rows:)
        # bg.spawn et non create_task : référence forte (la boucle ne garde qu'une weakref)
        # + journalisation si le pinger meurt (2026-08-11)
        self._ping = bg.spawn(self._pinger(), name=f"terminal:ping:{vmid}", logger=log)

    async def _pinger(self):
        try:
            while self.ws is not None and not self.ws.closed:
                await asyncio.sleep(30)
                await self.ws.send_str("2")
        except Exception:
            # perdre le keepalive n'est pas fatal (le websocket se fermera de lui-même et
            # _reader terminera la session) mais ça explique une console qui « tombe »
            log.debug("terminal: pinger arrêté", exc_info=True)

    async def send_raw(self, data: str):
        if self.ws is None or self.ws.closed:
            raise RuntimeError("session fermée")
        payload = data.encode("utf-8")
        await self.ws.send_str(f"0:{len(payload)}:{data}")

    async def force_redraw(self):
        """Toggle de taille (SIGWINCH) → force un redraw COMPLET du TUI/prompt, sinon
        on n'a que des mises à jour différentielles et l'écran est incomplet à l'attache."""
        if self.ws is None or self.ws.closed:
            return
        try:
            await self.ws.send_str(f"1:{COLS + 1}:{ROWS}:")
            await asyncio.sleep(0.25)
            await self.ws.send_str(f"1:{COLS}:{ROWS}:")
        except Exception:
            log.debug("terminal: force_redraw impossible", exc_info=True)

    async def read_chunk(self, timeout=20):
        try:
            msg = await asyncio.wait_for(self.ws.receive(), timeout=timeout)
        except asyncio.TimeoutError:
            return ""                    # simple inactivité : la session reste vivante
        except Exception:
            # lien coupé : on marque la console morte, _reader clôturera la session
            log.debug("terminal: lecture websocket interrompue", exc_info=True)
            self.ws = None
            return ""
        if msg.type in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
            return msg.data if isinstance(msg.data, str) else msg.data.decode("utf-8", "replace")
        self.ws = None
        return ""

    @property
    def alive(self):
        return self.ws is not None and not self.ws.closed

    async def close(self):
        if self._ping:
            self._ping.cancel()
        try:
            if self.ws and not self.ws.closed:
                await self.ws.close()
        except Exception:
            pass
        finally:
            try:
                if self.session and not self.session.closed:
                    await asyncio.shield(self.session.close())
            except Exception:
                pass


# séquences de touches envoyées au PTY
KEYS = {
    "enter": "\r", "up": "\x1b[A", "down": "\x1b[B", "left": "\x1b[D",
    "right": "\x1b[C", "esc": "\x1b", "ctrl_c": "\x03", "tab": "\t",
}


class TerminalSession:
    def __init__(self, cog, thread, vmid, name, owner_id, console, idle_min=None,
                 max_min=None):
        self.cog = cog
        self.thread = thread
        self.vmid = vmid
        self.name = name
        self.owner_id = owner_id
        self.console = console
        # le nœud a son propre délai d'inactivité (défaut = celui des guests)
        self.idle_min = idle_min or cog.cfg.terminal_idle_min
        # durée de vie absolue (0/None = illimitée) : posée pour la console du nœud
        self.max_min = max_min or 0
        self.msg = None                 # message "écran" édité en direct
        self.view = None                # vue des boutons-touches (à stop() en fin de vie)
        self.started = time.monotonic()
        # ⚠️ DEUX horloges distinctes, et c'est essentiel :
        #  - last_active = dernière ACTIVITÉ (sortie du terminal comprise) -> sert au rendu ;
        #  - last_input  = dernière SAISIE de l'utilisateur -> seule à armer le timeout.
        # Les confondre rendait le timeout inopérant : `top`/`journalctl -f`/`watch` émettent
        # en continu, ce qui réarmait le compteur en boucle et laissait un shell root ouvert
        # indéfiniment malgré la promesse « fermeture auto après N min » (corrigé 2026-07-15).
        self.last_active = time.monotonic()
        self.last_input = time.monotonic()
        self._dirty = True
        self._last_edit = 0.0
        self._loop_errors = 0           # échecs consécutifs de _renderer (anti-inondation)
        self._tasks = []
        if _HAS_PYTE:
            self.screen = pyte.Screen(COLS, ROWS)
            self.stream = pyte.Stream(self.screen)
        else:
            self.screen = None
            self._buf = ""

    def start(self):
        # bg.spawn : référence forte + journalisation d'une mort inattendue (2026-08-11)
        self._tasks = [bg.spawn(self._reader(), name=f"terminal:reader:{self.name}",
                                logger=log),
                       bg.spawn(self._renderer(), name=f"terminal:renderer:{self.name}",
                                logger=log)]
        # ⚠️ INVARIANT : « une entrée dans cog.sessions sans _renderer vivant ne doit pas
        # exister ». _renderer est la SEULE tâche qui applique le timeout d'inactivité ET
        # le plafond absolu ; si elle disparaît (coupure TCP pendant une édition, bug), le
        # shell root reste pilotable SANS aucune fermeture automatique — et, pour la
        # console du nœud, garde le verrou d'unicité jusqu'au redémarrage du bot.
        self._tasks[1].add_done_callback(self._renderer_gone)

    def _renderer_gone(self, task):
        """Filet du dernier recours : la tâche de rendu est morte -> on ferme la session.

        Callback SYNCHRONE (asyncio) : la fermeture part dans une tâche détachée. Deux
        garde-fous pour ne pas boucler sur `_end` : une annulation est une fermeture
        VOULUE (close()), et une session déjà retirée de `cog.sessions` a déjà été close.
        """
        if task.cancelled():
            return
        if self.cog.sessions.get(self.thread.id) is not self:
            return
        log.warning("terminal %s: tâche de rendu perdue -> fermeture de la session",
                    self.name)
        bg.spawn(self.cog._end(self.thread.id, "tâche de rendu perdue"),
                 name=f"terminal:end:{self.name}", logger=log)

    def _feed(self, raw):
        if not raw:
            return
        if self.screen is not None:
            try:
                self.stream.feed(raw)
            except Exception:
                # volontairement en debug : une séquence exotique peut faire trébucher
                # pyte à chaque chunk, un warning inonderait journald
                log.debug("terminal %s: pyte a rejeté un morceau de flux", self.name,
                          exc_info=True)
        else:
            self._buf = (self._buf + _CTRL.sub("", _ANSI.sub("", raw)))[-4000:]
        self._dirty = True

    def render(self):
        if self.screen is not None:
            lines = [ln.rstrip() for ln in self.screen.display]
            while lines and not lines[0]:
                lines.pop(0)
            while lines and not lines[-1]:
                lines.pop()
            text = "\n".join(lines) or "(écran vide)"
        else:
            text = self._buf.strip() or "(écran vide)"
        text = _fence(text)
        if len(text) > 1900:
            text = text[-1900:]
        return f"```\n{text}\n```"

    async def _reader(self):
        try:
            while self.console.alive:
                raw = await self.console.read_chunk()
                if raw:
                    self._feed(raw)
                    self.last_active = time.monotonic()
        except asyncio.CancelledError:
            raise                       # fermeture voulue : close() a déjà tout géré
        except Exception:
            # une lecture qui casse ne doit pas laisser une session « vivante » muette
            log.warning("terminal %s: boucle de lecture interrompue", self.name,
                        exc_info=True)
        # console fermée par la fin du flux
        if not self.console.alive:
            await self.cog._end(self.thread.id, "session PVE fermée")

    async def _renderer(self):
        idle_s = self.idle_min * 60
        max_s = self.max_min * 60
        while True:
            await asyncio.sleep(0.5)
            # ⚠️ TOUT le corps est gardé (2026-08-11) : la seule garde était
            # `except discord.HTTPException` autour de msg.edit, or discord.py ne convertit
            # PAS les erreurs de transport — une coupure TCP (ECONNRESET = 104 sous Linux,
            # non retentée par HTTPClient.request), un ClientConnectorError ou un
            # TimeoutError remontaient bruts, tuaient la tâche en silence et emportaient
            # avec eux le timeout d'inactivité ET le plafond absolu du shell root.
            try:
                # inactivité = pas de SAISIE (et non « pas de sortie ») — cf. __init__
                if time.monotonic() - self.last_input > idle_s:
                    await self.cog._end(
                        self.thread.id, f"inactivité (> {self.idle_min} min)")
                    return
                # plafond absolu : un shell root sur l'hyperviseur ne doit pas pouvoir
                # vivre indéfiniment, même piloté activement.
                if max_s and time.monotonic() - self.started > max_s:
                    await self.cog._end(
                        self.thread.id, f"durée maximale atteinte ({self.max_min} min)")
                    return
                # anti-rebond : cf. EDIT_MIN_INTERVAL. `_dirty` n'est consommé QUE si on
                # édite vraiment, sinon la dernière sortie serait perdue.
                if (self._dirty and self.msg is not None
                        and time.monotonic() - self._last_edit >= EDIT_MIN_INTERVAL):
                    self._dirty = False
                    try:
                        await self.msg.edit(content=self.render())
                        self._last_edit = time.monotonic()
                    except discord.HTTPException:
                        # 5xx/message supprimé : cette frame est ABANDONNÉE (`_dirty` a déjà
                        # été consommé) — c'est voulu, réessayer en boucle sur un message
                        # supprimé enverrait un 404 tous les demi-tours. La prochaine sortie
                        # du terminal rallumera `_dirty` et redessinera l'écran.
                        pass
                self._loop_errors = 0
            except asyncio.CancelledError:
                # redondant (CancelledError est une BaseException depuis 3.8) mais explicite
                raise
            except Exception:
                # la boucle tourne 2 fois/s : une panne réseau DURABLE ferait ~1200 lignes
                # en 10 min. On journalise le premier échec, puis au plus un par minute
                # (120 tours) — d'où le modulo.
                self._loop_errors += 1
                if self._loop_errors == 1 or self._loop_errors % 120 == 0:
                    log.warning("terminal %s: boucle de rendu (échec #%d)", self.name,
                                self._loop_errors, exc_info=True)
                # si l'échec est survenu DANS _end, la session est déjà dépilée : ne pas
                # tourner à vide sur une session morte (close() ne s'auto-annule pas)
                if self.cog.sessions.get(self.thread.id) is not self:
                    return

    async def send_input(self, data, user, label):
        self.last_active = time.monotonic()
        self.last_input = time.monotonic()      # SEUL point qui repousse le timeout
        # journalisé côté bot (journald) et PAS via bot.audit → évite de spammer
        # #journaux-live à chaque frappe. L'ouverture/fermeture, elles, sont auditées.
        log.info("terminal[%s] %s: %s", user, self.name, label[:200])
        try:
            await self.console.send_raw(data)
        except Exception:
            # une frappe perdue en silence donnait un écran figé sans explication :
            # on journalise, l'utilisateur verra la console se fermer via _reader
            log.warning("terminal %s: saisie non transmise", self.name, exc_info=True)

    async def close(self):
        for t in self._tasks:
            if t and t is not asyncio.current_task():
                t.cancel()
        await self.console.close()


class TextModal(GatedView, discord.ui.Modal, title="Terminal — saisir du texte"):
    # La porte de cette modale est PLUS SPÉCIFIQUE que les tiers G/M/O : propriétaire de
    # LA session console + 2FA. Elle est reposée dans on_submit ci-dessous (une modale
    # peut être soumise longtemps après son ouverture, 2FA expiré entre-temps).
    gate = None
    gate_reason = ("porte spécifique — propriétaire de la session console + session 2FA — "
                   "vérifiée dans on_submit")

    txt = discord.ui.TextInput(
        label="Texte (Entrée ajoutée à la fin)",
        style=discord.TextStyle.paragraph,
        placeholder="ex: une réponse à claude, ou une commande shell",
        required=False, max_length=1500)

    def __init__(self, cog, thread_id):
        super().__init__()
        self.cog = cog
        self.thread_id = thread_id

    async def on_submit(self, itx: discord.Interaction):
        # ⚠️ Une MODALE ne passe pas par l'interaction_check de la vue qui l'a ouverte :
        # la porte doit être reposée ici (2026-08-11). Une modale peut d'ailleurs rester
        # ouverte longtemps côté client et être soumise après l'expiration du 2FA.
        from ..core.permissions import session_2fa_ok, deny_2fa
        sess = self.cog.sessions.get(self.thread_id)
        if sess is None or not sess.console.alive or itx.user.id != sess.owner_id:
            await itx.response.send_message("Session terminée / non autorisée.", ephemeral=True)
            return
        if not session_2fa_ok(itx):
            await deny_2fa(itx)
            return
        await itx.response.defer()
        text = str(self.txt.value)
        await sess.send_input(text + "\r", itx.user, text or "<Entrée>")


class IdleModal(GatedView, discord.ui.Modal, title="Terminal — inactivité"):
    """Demande la période d'inactivité AVANT d'ouvrir la console (2026-08-14).

    Avant, le délai venait uniquement de la conf (`TERMINAL_IDLE_MIN` / `NODE_...`) : une
    session de travail long (compilation, `claude` qui réfléchit) mourait à 10 min, et
    remonter le défaut global aurait rallongé TOUTES les consoles root — l'inverse de ce
    qu'on veut. Le choix est donc par session, borné par un plafond de conf.

    Porte : `gate = None` volontairement. Ouvrir la modale ne fait RIEN de sensible ; la
    console n'est ouverte que par `open_for`/`open_node_for`, qui refont l'intégralité des
    contrôles (tier, session 2FA, exclusions, quotas) sur l'interaction de SOUMISSION —
    seule qui compte, une modale pouvant être soumise longtemps après son ouverture.
    """

    gate = None
    gate_reason = ("ouvrir la modale n'ouvre aucune console ; open_for/open_node_for "
                   "refont tous les contrôles (tier + 2FA + quotas) sur la soumission")

    minutes = discord.ui.TextInput(
        label="Fermeture auto après (minutes)",
        style=discord.TextStyle.short,
        required=False, max_length=5)

    def __init__(self, cog, name, *, node=False, default=10, maximum=120):
        super().__init__()
        self.cog = cog
        self.name = name
        self.node = node
        self.default = default
        self.maximum = maximum
        # les enfants sont deepcopy'és par instance (discord.py `_init_children`) :
        # personnaliser ici n'affecte pas les autres modales
        self.minutes.default = str(default)
        self.minutes.placeholder = f"{1} à {maximum} — vide = défaut ({default})"

    async def on_submit(self, itx: discord.Interaction):
        raw = str(self.minutes.value or "").strip()
        if not raw:
            idle = self.default
        else:
            try:
                idle = _parse_minutes(raw)
            except ValueError:
                await itx.response.send_message(
                    f"⏱️ Durée invalide : `{raw[:30]}`. Attendu un nombre de minutes "
                    f"(1 à {self.maximum}), ou `1h30`.", ephemeral=True)
                return
            if not 1 <= idle <= self.maximum:
                await itx.response.send_message(
                    f"⏱️ Durée hors bornes : **{idle} min** (autorisé 1 à "
                    f"{self.maximum} min).", ephemeral=True)
                return
        if self.node:
            await self.cog.open_node_for(itx, idle_min=idle)
        else:
            await self.cog.open_for(itx, self.name, idle_min=idle)


class TerminalView(GatedView):
    """Boutons-touches de l'écran d'une console LXC.

    Porte (2026-08-11) : tier **mod** + `gate_user_id` = l'ouvreur. Avant, chaque bouton
    ne comparait que `owner_id` : le tier ET la session 2FA n'étaient vérifiés qu'à
    l'OUVERTURE (`open_for`), donc une console survivait indéfiniment à l'expiration du
    2FA tant qu'on frappait une touche avant le timeout d'inactivité. `GatedView` les
    revalide à chaque clic — c'est la « défense en profondeur » retenue plutôt que fermer
    la session sur expiration : `send_input` ne ré-arme PAS la session 2FA, une fermeture
    automatique couperait donc les consoles en plein travail. Conséquence assumée : après
    expiration, l'utilisateur doit `/2fa unlock` puis recliquer ; la console reste ouverte
    entre-temps (et le timeout d'inactivité la fermera s'il ne revient pas).
    Le tier « mod » ne restreint personne en pratique : le bouton 🖥️ qui mène à `open_for`
    est porté par `CtControlView` (ct_channels.py), elle-même `gate = "mod"` avec le MÊME
    serveur (None = R820 ; les invités d'Aveyron y sont refusés en amont) — la porte des
    touches est donc exactement celle de l'ouverture, jamais plus fermée.
    """

    gate = "mod"

    def __init__(self, cog, thread_id, owner_id):
        super().__init__(timeout=None)
        self.cog = cog
        self.thread_id = thread_id
        self.owner_id = owner_id
        self.gate_user_id = owner_id    # fil privé : seul l'ouvreur pilote le shell

    async def _denied(self, itx, msg):
        try:
            if itx.response.is_done():
                await itx.followup.send(msg, ephemeral=True)
            else:
                await itx.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass

    async def on_denied(self, itx, why):
        if why == "user":
            # message historique, plus parlant que le libellé générique de GatedView
            await self._denied(itx, "🔒 Ce terminal n'est pas le tien.")
            return
        await super().on_denied(itx, why)

    async def _key(self, itx, key, label):
        sess = self.cog.sessions.get(self.thread_id)
        if sess is None or not sess.console.alive:
            await itx.response.send_message("Session terminée.", ephemeral=True)
            return
        await itx.response.defer()
        await sess.send_input(KEYS[key], itx.user, f"[{label}]")

    @discord.ui.button(label="Texte", emoji="⌨️", style=discord.ButtonStyle.primary, row=0)
    async def b_text(self, itx, b):
        if self.thread_id not in self.cog.sessions:
            await itx.response.send_message("Session terminée.", ephemeral=True)
            return
        await itx.response.send_modal(TextModal(self.cog, self.thread_id))

    @discord.ui.button(label="Entrée ⏎", style=discord.ButtonStyle.secondary, row=0)
    async def b_enter(self, itx, b):
        await self._key(itx, "enter", "Entrée")

    @discord.ui.button(emoji="⬆️", style=discord.ButtonStyle.secondary, row=0)
    async def b_up(self, itx, b):
        await self._key(itx, "up", "↑")

    @discord.ui.button(emoji="⬇️", style=discord.ButtonStyle.secondary, row=0)
    async def b_down(self, itx, b):
        await self._key(itx, "down", "↓")

    @discord.ui.button(emoji="🔄", style=discord.ButtonStyle.secondary, row=0)
    async def b_refresh(self, itx, b):
        # acquitter D'ABORD, comme tous les autres boutons : force_redraw contient un
        # sleep(0.25) fixe qui amputait d'autant le budget de 3 s de la réponse initiale
        # (2026-08-11).
        await itx.response.defer()
        sess = self.cog.sessions.get(self.thread_id)
        if sess and sess.console.alive:
            await sess.console.force_redraw()
            sess._dirty = True

    @discord.ui.button(emoji="⬅️", style=discord.ButtonStyle.secondary, row=1)
    async def b_left(self, itx, b):
        await self._key(itx, "left", "←")

    @discord.ui.button(emoji="➡️", style=discord.ButtonStyle.secondary, row=1)
    async def b_right(self, itx, b):
        await self._key(itx, "right", "→")

    @discord.ui.button(label="Esc", style=discord.ButtonStyle.secondary, row=1)
    async def b_esc(self, itx, b):
        await self._key(itx, "esc", "Esc")

    @discord.ui.button(label="Ctrl-C", style=discord.ButtonStyle.secondary, row=1)
    async def b_ctrlc(self, itx, b):
        await self._key(itx, "ctrl_c", "^C")

    @discord.ui.button(label="Tab", style=discord.ButtonStyle.secondary, row=1)
    async def b_tab(self, itx, b):
        await self._key(itx, "tab", "Tab")

    @discord.ui.button(label="Fermer", emoji="❌", style=discord.ButtonStyle.danger, row=2)
    async def b_close(self, itx, b):
        await itx.response.defer()
        await self.cog._end(self.thread_id, f"fermé par {itx.user}")


class NodeTerminalView(TerminalView):
    """Écran de la console root de l'HYPERVISEUR : même UI, porte du NŒUD.

    On ne réutilise pas `OwnerGatedView` (propriétaire du guild / ADMIN_IDS seulement) :
    elle refuserait le tier **O** et les `NODE_TERMINAL_OWNER_IDS` qui ne sont pas dans
    ADMIN_IDS, c'est-à-dire précisément les gens autorisés à OUVRIR cette console depuis
    le salon Lock — la vue serait alors morte pour son propre ouvreur. On repose donc la
    vraie porte du module, `_may_open_node` (user-ids dédiés OU rôle O, jamais M), qui est
    STRICTEMENT plus fermée que le tier « owner » (may_lock accepte M). Ajouté 2026-08-11 :
    avant, les boutons du shell root de l'hôte ne vérifiaient que `owner_id`.
    """

    gate = "owner"

    async def interaction_check(self, itx) -> bool:
        if not await super().interaction_check(itx):
            return False
        if not self.cog._may_open_node(itx):
            await self.on_denied(itx, "role")
            return False
        return True

    async def on_denied(self, itx, why):
        if why == "role":
            await self._denied(
                itx, "🔒 Console de l'hyperviseur réservée au propriétaire "
                     "(ou au rôle **O** du serveur du nœud).")
            return
        await super().on_denied(itx, why)


class Terminal(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cfg = bot.cfg
        self.sessions = {}
        self._pending = 0
        self._node_pending = False    # réservation synchrone de l'unique console de nœud
        self._max = 3
        self._cleaned = False

    async def cog_unload(self):
        """Arrêt du bot : clôturer VRAIMENT les consoles root encore ouvertes.

        L'ancienne version (synchrone) programmait `sess.close()` et rendait la main :
        même exécutée, `close()` ne fait qu'annuler les tâches et fermer le lien — ni le
        message « Terminal fermé » dans le fil, ni l'archivage, ni l'entrée d'audit
        `terminal-close` n'étaient produits. C'est un trou de traçabilité sur la surface la
        plus sensible du bot : on passe donc par `_end` (2026-08-11).
        discord.py attend un `cog_unload` coroutine (cf. cogs/logs.py) et `Bot.close()`
        décharge les cogs AVANT de fermer le client HTTP : les deux appels Discord de
        `_end` fonctionnent encore ici.
        ⚠️ Budget GLOBAL et non par session : `_end` fait 2 requêtes HTTP chacune et il
        peut y avoir 3 sessions — un `wait_for` par session retarderait l'arrêt demandé
        par systemd au-delà de son TimeoutStopSec.
        """
        tids = list(self.sessions)
        if not tids:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*(self._end(t, "arrêt du bot") for t in tids),
                               return_exceptions=True), 5)
        # le TimeoutError du budget compris : un arrêt ne doit JAMAIS lever, systemd
        # attend le processus et discord.py avalerait l'exception sans un mot
        except Exception:  # noqa: BLE001
            log.warning("terminal: clôture des consoles à l'arrêt incomplète",
                        exc_info=True)
        finally:
            # ce qui n'a pas pu être clôturé ne doit pas rester référencé ; les liens
            # (websocket/SSH) tombent de toute façon avec le processus, et `on_ready`
            # archivera les fils orphelins au prochain démarrage.
            for tid in tids:
                self.sessions.pop(tid, None)

    @commands.Cog.listener()
    async def on_ready(self):
        if self._cleaned:
            return
        self._cleaned = True
        guild = self.bot.get_guild(self.cfg.guild_id)
        if guild is None:
            return
        for th in list(getattr(guild, "threads", [])):
            if (th.name or "").startswith("terminal-") and th.id not in self.sessions \
                    and not th.archived:
                try:
                    await th.edit(archived=True, locked=True, reason="terminal orphelin (reboot)")
                except discord.HTTPException:
                    pass

    def _may_open(self, itx):
        if itx.user.id in self.cfg.terminal_owner_ids:
            return True
        roles = self.cfg.terminal_owner_role_ids
        return bool(roles and ({r.id for r in getattr(itx.user, "roles", [])} & set(roles)))

    def _precheck(self, itx, name):
        """Contrôles d'ouverture d'une console LXC, SANS aucune I/O (donc utilisables
        avant l'ACK des 3 s d'une interaction). Retourne None si tout passe, `_NEED_2FA`,
        ou le message de refus. Rejoués par `open_for` sur l'interaction de soumission de
        la modale : entre l'affichage de celle-ci et son envoi, le 2FA peut expirer, un
        rôle être retiré ou les 3 sessions être prises."""
        from ..core.permissions import session_2fa_ok
        if not getattr(self.cfg, "terminal_ready", False):
            return "Terminal non configuré."
        if not self._may_open(itx):
            return ("🔒 Terminal réservé aux gestionnaires (rôle Gestion) ou au "
                    "propriétaire.")
        if not session_2fa_ok(itx):
            return _NEED_2FA
        if not name:
            return "VM/conteneur introuvable."
        if name.lower() in self.cfg.terminal_excluded_guests:
            return f"⛔ Terminal désactivé sur **{name}** (guest sensible)."
        if len(self.sessions) + self._pending >= self._max:
            return "Trop de terminaux ouverts — ferme-en un."
        return None

    async def _refuse(self, itx, err):
        from ..core.permissions import deny_2fa
        if err is _NEED_2FA:
            await deny_2fa(itx)
        else:
            await itx.response.send_message(err, ephemeral=True)

    async def prompt_open(self, itx: discord.Interaction, name):
        """Point d'entrée du bouton 🖥️ : demande la durée d'inactivité, puis ouvre.

        ⚠️ `send_modal` DOIT être la première réponse à l'interaction — d'où des
        pré-contrôles sans I/O ici (le bouton n'a pas de defer, cf. ct_channels)."""
        err = self._precheck(itx, name)
        if err is not None:
            await self._refuse(itx, err)
            return
        await itx.response.send_modal(IdleModal(
            self, name, default=self.cfg.terminal_idle_min,
            maximum=self.cfg.terminal_idle_max_min))

    async def open_for(self, itx: discord.Interaction, name, idle_min=None):
        err = self._precheck(itx, name)
        if err is not None:
            await self._refuse(itx, err)
            return
        idle_min = min(max(1, int(idle_min or self.cfg.terminal_idle_min)),
                       self.cfg.terminal_idle_max_min)
        self._pending += 1
        reserved = True
        try:
            await itx.response.defer(ephemeral=True)
            try:
                # enveloppes async de core/pve (proxmoxer est synchrone : un appel nu
                # gèlerait la boucle d'événements jusqu'à 15 s)
                gtype = await self.bot.pve.aguest_type(name)
                vmid = await self.bot.pve.avmid_of(name)
            except Exception:
                await itx.followup.send("❌ PVE injoignable.", ephemeral=True)
                return
            if gtype != "lxc" or not vmid:
                await itx.followup.send("Terminal réservé aux conteneurs LXC.", ephemeral=True)
                return
            try:
                st = await self.bot.pve.aguest_status(vmid, "lxc")
            except Exception:
                log.debug("terminal %s: statut LXC illisible", name, exc_info=True)
                st = {}
            if st.get("status") != "running":
                await itx.followup.send(f"**{name}** n'est pas démarré.", ephemeral=True)
                return
            console = PveConsole(self.cfg)
            try:
                await console.open(vmid)
            except Exception as e:
                log.warning("terminal console %s: %s", name, type(e).__name__)
                await console.close()
                await itx.followup.send(
                    f"❌ Console échouée (`{type(e).__name__}`).", ephemeral=True)
                return
            thread = None
            try:
                thread = await itx.channel.create_thread(
                    name=f"terminal-{name}"[:90], type=discord.ChannelType.private_thread,
                    invitable=False, auto_archive_duration=60, reason=f"terminal {itx.user}")
                await thread.add_user(itx.user)
            except discord.HTTPException as e:
                log.warning("terminal thread: %s", type(e).__name__)
                await console.close()
                if thread is not None:
                    try:
                        await thread.delete()
                    except discord.HTTPException:
                        pass
                await itx.followup.send(
                    "❌ Fil privé impossible (permission « Créer des fils privés » requise).",
                    ephemeral=True)
                return

            sess = TerminalSession(self, thread, vmid, name, itx.user.id, console,
                                   idle_min=idle_min)
            emb = discord.Embed(
                title=f"🖥️ Terminal — {name}",
                description=("Console **root** interactive (vmid %s). Écran ci-dessous, mis à "
                             "jour en direct. Boutons = touches ; **⌨️ Texte** pour saisir une "
                             "ligne (Entrée ajoutée). Idéal pour piloter une appli plein-écran "
                             "(claude, top…).\nFermeture auto après **%d min** d'inactivité "
                             "(choisi à l'ouverture)."
                             % (vmid, idle_min)),
                color=0xED4245)
            emb.set_footer(text="least-priv botconsole@pve · saisies journalisées")
            # même ordre critique que open_node_for : publier la session AVANT son UI
            # laissait, sur un thread.send en échec, une console root sans _reader/_renderer
            # (donc sans timeout d'inactivité) enregistrée dans self.sessions.
            try:
                await thread.send(embed=emb)
                # la vue est gardée sur la session : elle doit être stop()ée en fin de vie,
                # sinon son entrée reste au ViewStore de discord.py jusqu'au redémarrage
                sess.view = TerminalView(self, thread.id, itx.user.id)
                sess.msg = await thread.send("```\n(démarrage…)\n```", view=sess.view)
                self.sessions[thread.id] = sess
                sess.start()
            except Exception:
                log.warning("terminal %s: initialisation échouée", name, exc_info=True)
                self.sessions.pop(thread.id, None)
                await sess.close()
                try:
                    await thread.delete()
                except discord.HTTPException:
                    pass
                await itx.followup.send("❌ Ouverture du terminal échouée.", ephemeral=True)
                return
            self._pending -= 1
            reserved = False
            self.bot.audit.record(user=f"{itx.user}({itx.user.id})", action="terminal-open",
                                  target=name, result=f"ok (inactivité {idle_min} min)")
            await console.force_redraw()        # capture l'écran complet dès l'ouverture
            await itx.followup.send(f"✅ Terminal ouvert : {thread.mention}", ephemeral=True)
        finally:
            if reserved:
                self._pending -= 1

    # ------------------------------------------------------------ console du NŒUD
    def _may_open_node(self, itx):
        """Shell root de l'hyperviseur : PROPRIÉTAIRE ou tier **O** du serveur du nœud —
        PAS M. Raison (revue sécu 2026-07-16) : le shell root du nœud permet `pct enter`
        vers N'IMPORTE quel conteneur (dont vaultwarden/bdd/mailserver), contournant les
        exclusions `terminal_excluded_guests` de la console LXC. On réserve donc ce pouvoir
        à O/owner ; M garde les boutons Lock (rafraîchir/sauvegarder) + la console LXC (qui,
        elle, respecte les exclusions). Session 2FA exigée en plus (open_node_for)."""
        if itx.user.id in self.cfg.node_terminal_owner_ids:
            return True
        o = getattr(self.cfg, "node_owner_role_id", 0)
        return bool(o and o in {r.id for r in getattr(itx.user, "roles", [])})

    def _precheck_node(self, itx):
        """Idem `_precheck`, pour le shell root de l'hyperviseur. Sans I/O."""
        from ..core.permissions import session_2fa_ok
        if not getattr(self.cfg, "node_terminal_ready", False):
            return "Terminal du nœud non configuré (`NODE_TERMINAL_ENABLED` / clé SSH)."
        if not self._may_open_node(itx):
            return "🔒 Console de l'hyperviseur réservée au propriétaire."
        # Root shell sur l'hyperviseur : la session 2FA est exigée EN PLUS (même pour le
        # propriétaire) — cf. « le 2FA protège l'ensemble des usages » + catégorie Lock.
        if not session_2fa_ok(itx):
            return _NEED_2FA
        # asymétrie assumée avec les guests (qui dégradent vers un buffer texte) : piloter
        # un shell root sur l'hyperviseur avec un rendu approximatif est trop risqué.
        if not _HAS_PYTE:
            return "Émulateur `pyte` absent."
        # Une seule console de nœud à la fois. Le drapeau `_node_pending` est indispensable :
        # `self.sessions` n'est peuplé qu'APRÈS l'ouverture SSH (~1 s), donc un second clic
        # pendant cette fenêtre passerait le test et ouvrirait un DEUXIÈME shell root.
        # Test + réservation sans `await` entre les deux -> atomique en asyncio (le test
        # ci-dessous et la pose du drapeau dans open_node_for restent dans le MÊME bloc
        # synchrone ; ce pré-contrôle-ci, joué avant la modale, ne réserve rien).
        if self._node_pending or any(s.vmid is None for s in self.sessions.values()):
            return "Une console du nœud est déjà ouverte — ferme-la d'abord."
        if len(self.sessions) + self._pending >= self._max:
            return "Trop de terminaux ouverts — ferme-en un."
        return None

    async def prompt_open_node(self, itx: discord.Interaction):
        """Point d'entrée du bouton 🖥️ du salon nœud : durée d'inactivité puis ouverture."""
        err = self._precheck_node(itx)
        if err is not None:
            await self._refuse(itx, err)
            return
        await itx.response.send_modal(IdleModal(
            self, self.cfg.pve_node, node=True,
            default=self.cfg.node_terminal_idle_min,
            maximum=self.cfg.node_terminal_idle_max_min))

    async def open_node_for(self, itx: discord.Interaction, idle_min=None):
        """Ouvre un shell root sur l'hyperviseur (nœud PVE) via SSH (cf. core/nodeshell)."""
        from ..core.nodeshell import NodeShell

        err = self._precheck_node(itx)
        if err is not None:
            await self._refuse(itx, err)
            return
        # le plafond de conf est déjà borné par node_terminal_max_min (cf. config.py) :
        # promettre plus d'inactivité que la durée de vie absolue n'aurait aucun sens
        idle_min = min(max(1, int(idle_min or self.cfg.node_terminal_idle_min)),
                       self.cfg.node_terminal_idle_max_min)

        self._pending += 1
        self._node_pending = True
        reserved = True
        console = None
        thread = None
        try:
            await itx.response.defer(ephemeral=True)
            console = NodeShell(self.cfg, COLS, ROWS)
            try:
                await console.open()
            except Exception as e:
                log.warning("terminal nœud: %s: %s", type(e).__name__, e)
                await console.close()
                await itx.followup.send(
                    f"❌ Connexion SSH au nœud échouée (`{type(e).__name__}`).", ephemeral=True)
                return
            try:
                thread = await itx.channel.create_thread(
                    name="terminal-noeud-pve", type=discord.ChannelType.private_thread,
                    invitable=False, auto_archive_duration=60,
                    reason=f"terminal nœud {itx.user}")
                await thread.add_user(itx.user)
            except discord.HTTPException as e:
                log.warning("terminal nœud thread: %s", type(e).__name__)
                await console.close()
                if thread is not None:
                    try:
                        await thread.delete()
                    except discord.HTTPException:
                        pass
                await itx.followup.send(
                    "❌ Fil privé impossible (permission « Créer des fils privés » requise).",
                    ephemeral=True)
                return

            # vmid=None est le MARQUEUR « session nœud » (cf. le test d'unicité ci-dessus)
            sess = TerminalSession(self, thread, None, self.cfg.pve_node, itx.user.id,
                                   console, idle_min=idle_min,
                                   max_min=self.cfg.node_terminal_max_min)
            emb = discord.Embed(
                title=f"🖥️ Terminal — HYPERVISEUR {self.cfg.pve_node}",
                description=(
                    "⚠️ Console **root sur l'hôte Proxmox lui-même** — pas un conteneur. "
                    "Tout ce qui est tapé ici s'exécute sur la machine qui fait tourner "
                    "toutes les VM/conteneurs.\nBoutons = touches ; **⌨️ Texte** pour saisir une "
                    f"ligne. Fermeture auto après **{idle_min} min** d'inactivité "
                    "(choisi à l'ouverture)"
                    + (f", durée de vie maximale **{self.cfg.node_terminal_max_min} min**."
                       if self.cfg.node_terminal_max_min else ".")),
                color=0xED4245)
            emb.set_footer(text="SSH clé dédiée · propriétaire uniquement · ouverture "
                                "auditée (bot + sshd de l'hôte)")
            # ⚠️ ORDRE CRITIQUE : la session n'entre dans self.sessions qu'une fois son UI
            # posée, et sess.start() suit immédiatement. Publier plus tôt exposait à un
            # shell root ORPHELIN : si un thread.send échouait (5xx Discord), la session
            # restait enregistrée SANS _reader/_renderer — donc sans timeout, root ouvert
            # indéfiniment — tout en occupant le verrou d'unicité, ce qui bloquait le
            # bouton jusqu'au redémarrage du bot (aucun bouton ❌ n'ayant été posté).
            try:
                await thread.send(embed=emb)
                # NodeTerminalView : porte du NŒUD (cf. sa docstring), pas celle des guests
                sess.view = NodeTerminalView(self, thread.id, itx.user.id)
                sess.msg = await thread.send("```\n(démarrage…)\n```", view=sess.view)
                self.sessions[thread.id] = sess
                sess.start()
            except Exception:
                log.warning("terminal nœud: initialisation échouée", exc_info=True)
                self.sessions.pop(thread.id, None)
                await sess.close()
                try:
                    await thread.delete()
                except discord.HTTPException:
                    pass
                await itx.followup.send("❌ Ouverture du terminal échouée.", ephemeral=True)
                return
            self._pending -= 1
            reserved = False
            self.bot.audit.record(user=f"{itx.user}({itx.user.id})",
                                  action="terminal-node-open",
                                  target=f"noeud/{self.cfg.pve_node}",
                                  result=f"ok (inactivité {idle_min} min)")
            await console.force_redraw()
            await itx.followup.send(f"✅ Terminal nœud ouvert : {thread.mention}", ephemeral=True)
        finally:
            if reserved:
                self._pending -= 1
            # la session posée dans self.sessions (vmid=None) prend le relais du drapeau ;
            # sur tout échec, il doit retomber, sinon le bouton reste mort jusqu'au reboot.
            self._node_pending = False

    async def _end(self, thread_id, reason):
        sess = self.sessions.pop(thread_id, None)
        if sess is None:
            return
        await sess.close()
        # libérer l'entrée du ViewStore : avec timeout=None et sans custom_id stable, une
        # vue n'en sort QUE sur stop() — sinon chaque session en laissait une résidente
        # jusqu'au redémarrage du bot (2026-08-11). Contrepartie assumée : un clic sur
        # l'écran d'un terminal déjà fermé n'affiche plus « Session terminée. » mais
        # l'échec générique de Discord — le fil est de toute façon archivé + verrouillé.
        if sess.view is not None:
            try:
                sess.view.stop()
            except Exception:  # noqa: BLE001 — ne doit jamais empêcher la clôture
                log.debug("terminal %s: stop() de la vue", sess.name, exc_info=True)
        self.bot.audit.record(user="system",
                              action="terminal-close" if sess.vmid else "terminal-node-close",
                              target=sess.name, result=reason[:80])
        try:
            await sess.thread.send(f"🔒 **Terminal fermé** — {reason}.")
            await sess.thread.edit(archived=True, locked=True)
        except discord.HTTPException:
            pass
        except Exception:
            # une erreur de TRANSPORT (coupure TCP) n'est pas une HTTPException : sans ce
            # filet elle remonterait dans l'appelant — _renderer, _reader ou cog_unload —
            # alors que la console, elle, est déjà fermée (2026-08-11).
            log.warning("terminal %s: fil non clôturé proprement", sess.name,
                        exc_info=True)


async def setup(bot):
    await bot.add_cog(Terminal(bot))
