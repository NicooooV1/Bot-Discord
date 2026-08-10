"""Terminal Discord INTERACTIF (2026-07-13) — console root sur un guest LXC depuis son
salon, via la console `termproxy` de l'API Proxmox, avec un ÉCRAN LIVE (émulateur pyte)
piloté par des boutons-touches → permet les applis plein-écran (claude, top, vim…).

2026-07-15 : s'y ajoute `open_node_for()`, console root sur l'HYPERVISEUR lui-même,
ouverte depuis le salon « 🔒 Lock ». Deux différences délibérées avec les guests :
  - transport SSH (clé dédiée) et non termproxy — PVE ne donne un shell root sur le nœud
    qu'à `root@pam` ; le détail du raisonnement est dans bot/core/nodeshell.py ;
  - porte PROPRIÉTAIRE STRICTE (`_may_open_node`) : aucun rôle Discord n'y donne accès,
    alors que la console des guests accepte TERMINAL_OWNER_ROLE_IDS.
`TerminalSession` / `TerminalView` sont partagés : NodeShell expose le même contrat que
PveConsole. Une session nœud se reconnaît à `vmid is None`.

SÉCURITÉ (fonctionnalité la plus sensible du bot) :
  - compte PVE dédié LEAST-PRIVILEGE `botconsole@pve` (VM.Console) autorisé au niveau ACL
    UNIQUEMENT sur les guests permis → vaultwarden/mailserver/bdd/HAOS hors de portée ;
  - ouverture réservée au PROPRIÉTAIRE / au rôle (`TERMINAL_OWNER_IDS` / `_ROLE_IDS`) ;
  - guests sensibles ré-exclus côté bot (`TERMINAL_EXCLUDED_GUESTS`) — double barrière ;
  - thread PRIVÉ (aucun repli public) ; ⚠️ l'écran live est édité dans le fil (visible du
    propriétaire ET d'un membre « Gérer les fils »/Admin — compromis assumé pour le mode
    interactif) ; timeout d'inactivité ; chaque saisie auditée ; sessions bornées.
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

log = logging.getLogger("discord-bot.terminal")

COLS, ROWS = 80, 24                     # taille terminal (tient dans un message Discord)

try:
    import pyte
    _HAS_PYTE = True
except Exception:                       # pragma: no cover
    _HAS_PYTE = False

_ANSI = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]|\x1b\[[0-9;?]*[ -/]*[@-~]")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _fence(s: str) -> str:
    return (s or "").replace("```", "`​``")


class PveConsole:
    """Console termproxy -> websocket vers un LXC (auth ticket, compte least-priv)."""

    def __init__(self, cfg):
        self.cfg = cfg
        self._ssl = ssl.create_default_context()
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
        self._ping = asyncio.create_task(self._pinger())

    async def _pinger(self):
        try:
            while self.ws is not None and not self.ws.closed:
                await asyncio.sleep(30)
                await self.ws.send_str("2")
        except Exception:
            pass

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
            pass

    async def read_chunk(self, timeout=20):
        try:
            msg = await asyncio.wait_for(self.ws.receive(), timeout=timeout)
        except asyncio.TimeoutError:
            return ""
        except Exception:
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
        self._tasks = []
        if _HAS_PYTE:
            self.screen = pyte.Screen(COLS, ROWS)
            self.stream = pyte.Stream(self.screen)
        else:
            self.screen = None
            self._buf = ""

    def start(self):
        self._tasks = [asyncio.create_task(self._reader()),
                       asyncio.create_task(self._renderer())]

    def _feed(self, raw):
        if not raw:
            return
        if self.screen is not None:
            try:
                self.stream.feed(raw)
            except Exception:
                pass
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
            pass
        # console fermée par la fin du flux
        if not self.console.alive:
            await self.cog._end(self.thread.id, "session PVE fermée")

    async def _renderer(self):
        idle_s = self.idle_min * 60
        max_s = self.max_min * 60
        try:
            while True:
                await asyncio.sleep(0.5)
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
                if self._dirty and self.msg is not None:
                    self._dirty = False
                    try:
                        await self.msg.edit(content=self.render())
                        self._last_edit = time.monotonic()
                    except discord.HTTPException:
                        pass
        except asyncio.CancelledError:
            pass

    async def send_input(self, data, user, label):
        self.last_active = time.monotonic()
        self.last_input = time.monotonic()      # SEUL point qui repousse le timeout
        # journalisé côté bot (journald) et PAS via bot.audit → évite de spammer
        # #journaux-live à chaque frappe. L'ouverture/fermeture, elles, sont auditées.
        log.info("terminal[%s] %s: %s", user, self.name, label[:200])
        try:
            await self.console.send_raw(data)
        except Exception:
            pass

    async def close(self):
        for t in self._tasks:
            if t and t is not asyncio.current_task():
                t.cancel()
        await self.console.close()


class TextModal(discord.ui.Modal, title="Terminal — saisir du texte"):
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
        sess = self.cog.sessions.get(self.thread_id)
        if sess is None or not sess.console.alive or itx.user.id != sess.owner_id:
            await itx.response.send_message("Session terminée / non autorisée.", ephemeral=True)
            return
        await itx.response.defer()
        text = str(self.txt.value)
        await sess.send_input(text + "\r", itx.user, text or "<Entrée>")


class TerminalView(discord.ui.View):
    def __init__(self, cog, thread_id, owner_id):
        super().__init__(timeout=None)
        self.cog = cog
        self.thread_id = thread_id
        self.owner_id = owner_id

    async def _key(self, itx, key, label):
        if itx.user.id != self.owner_id:
            await itx.response.send_message("🔒 Ce terminal n'est pas le tien.", ephemeral=True)
            return
        sess = self.cog.sessions.get(self.thread_id)
        if sess is None or not sess.console.alive:
            await itx.response.send_message("Session terminée.", ephemeral=True)
            return
        await itx.response.defer()
        await sess.send_input(KEYS[key], itx.user, f"[{label}]")

    @discord.ui.button(label="Texte", emoji="⌨️", style=discord.ButtonStyle.primary, row=0)
    async def b_text(self, itx, b):
        if itx.user.id != self.owner_id:
            await itx.response.send_message("🔒 Ce terminal n'est pas le tien.", ephemeral=True)
            return
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
        if itx.user.id != self.owner_id:
            await itx.response.send_message("🔒", ephemeral=True)
            return
        sess = self.cog.sessions.get(self.thread_id)
        if sess and sess.console.alive:
            await sess.console.force_redraw()
            sess._dirty = True
        await itx.response.defer()

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
        if itx.user.id != self.owner_id:
            await itx.response.send_message("🔒", ephemeral=True)
            return
        await itx.response.defer()
        await self.cog._end(self.thread_id, f"fermé par {itx.user}")


class Terminal(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cfg = bot.cfg
        self.sessions = {}
        self._pending = 0
        self._node_pending = False    # réservation synchrone de l'unique console de nœud
        self._max = 3
        self._cleaned = False

    def cog_unload(self):
        for tid in list(self.sessions):
            sess = self.sessions.pop(tid, None)
            if sess:
                try:
                    asyncio.create_task(sess.close())
                except RuntimeError:
                    pass

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

    async def open_for(self, itx: discord.Interaction, name):
        from ..core.permissions import session_2fa_ok, deny_2fa
        if not getattr(self.cfg, "terminal_ready", False):
            await itx.response.send_message("Terminal non configuré.", ephemeral=True)
            return
        if not self._may_open(itx):
            await itx.response.send_message(
                "🔒 Terminal réservé aux gestionnaires (rôle Gestion) ou au propriétaire.",
                ephemeral=True)
            return
        if not session_2fa_ok(itx):
            await deny_2fa(itx)
            return
        if not name:
            await itx.response.send_message("VM/conteneur introuvable.", ephemeral=True)
            return
        if name.lower() in self.cfg.terminal_excluded_guests:
            await itx.response.send_message(
                f"⛔ Terminal désactivé sur **{name}** (guest sensible).", ephemeral=True)
            return
        if len(self.sessions) + self._pending >= self._max:
            await itx.response.send_message(
                "Trop de terminaux ouverts — ferme-en un.", ephemeral=True)
            return
        self._pending += 1
        reserved = True
        try:
            await itx.response.defer(ephemeral=True)
            try:
                gtype = await asyncio.to_thread(self.bot.pve.guest_type, name)
                vmid = await asyncio.to_thread(self.bot.pve.vmid_of, name)
            except Exception:
                await itx.followup.send("❌ PVE injoignable.", ephemeral=True)
                return
            if gtype != "lxc" or not vmid:
                await itx.followup.send("Terminal réservé aux conteneurs LXC.", ephemeral=True)
                return
            try:
                st = await asyncio.to_thread(self.bot.pve.ct_status, vmid)
            except Exception:
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

            sess = TerminalSession(self, thread, vmid, name, itx.user.id, console)
            emb = discord.Embed(
                title=f"🖥️ Terminal — {name}",
                description=("Console **root** interactive (vmid %s). Écran ci-dessous, mis à "
                             "jour en direct. Boutons = touches ; **⌨️ Texte** pour saisir une "
                             "ligne (Entrée ajoutée). Idéal pour piloter une appli plein-écran "
                             "(claude, top…).\nFermeture auto après **%d min** d'inactivité."
                             % (vmid, self.cfg.terminal_idle_min)),
                color=0xED4245)
            emb.set_footer(text="least-priv botconsole@pve · saisies journalisées")
            # même ordre critique que open_node_for : publier la session AVANT son UI
            # laissait, sur un thread.send en échec, une console root sans _reader/_renderer
            # (donc sans timeout d'inactivité) enregistrée dans self.sessions.
            try:
                await thread.send(embed=emb)
                sess.msg = await thread.send(
                    "```\n(démarrage…)\n```", view=TerminalView(self, thread.id, itx.user.id))
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
                                  target=name, result="ok")
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

    async def open_node_for(self, itx: discord.Interaction):
        """Ouvre un shell root sur l'hyperviseur (nœud PVE) via SSH (cf. core/nodeshell)."""
        from ..core.nodeshell import NodeShell

        if not getattr(self.cfg, "node_terminal_ready", False):
            await itx.response.send_message(
                "Terminal du nœud non configuré (`NODE_TERMINAL_ENABLED` / clé SSH).",
                ephemeral=True)
            return
        if not self._may_open_node(itx):
            await itx.response.send_message(
                "🔒 Console de l'hyperviseur réservée au propriétaire.", ephemeral=True)
            return
        # Root shell sur l'hyperviseur : la session 2FA est exigée EN PLUS (même pour le
        # propriétaire) — cf. « le 2FA protège l'ensemble des usages » + catégorie Lock.
        from ..core.permissions import session_2fa_ok, deny_2fa
        if not session_2fa_ok(itx):
            await deny_2fa(itx)
            return
        # asymétrie assumée avec les guests (qui dégradent vers un buffer texte) : piloter
        # un shell root sur l'hyperviseur avec un rendu approximatif est trop risqué.
        if not _HAS_PYTE:
            await itx.response.send_message("Émulateur `pyte` absent.", ephemeral=True)
            return
        # Une seule console de nœud à la fois. Le drapeau `_node_pending` est indispensable :
        # `self.sessions` n'est peuplé qu'APRÈS l'ouverture SSH (~1 s), donc un second clic
        # pendant cette fenêtre passerait le test et ouvrirait un DEUXIÈME shell root.
        # Test + réservation sans `await` entre les deux -> atomique en asyncio.
        if self._node_pending or any(s.vmid is None for s in self.sessions.values()):
            await itx.response.send_message(
                "Une console du nœud est déjà ouverte — ferme-la d'abord.", ephemeral=True)
            return
        if len(self.sessions) + self._pending >= self._max:
            await itx.response.send_message(
                "Trop de terminaux ouverts — ferme-en un.", ephemeral=True)
            return

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
                                   console, idle_min=self.cfg.node_terminal_idle_min,
                                   max_min=self.cfg.node_terminal_max_min)
            emb = discord.Embed(
                title=f"🖥️ Terminal — HYPERVISEUR {self.cfg.pve_node}",
                description=(
                    "⚠️ Console **root sur l'hôte Proxmox lui-même** — pas un conteneur. "
                    "Tout ce qui est tapé ici s'exécute sur la machine qui fait tourner "
                    "toutes les VM/conteneurs.\nBoutons = touches ; **⌨️ Texte** pour saisir une "
                    f"ligne. Fermeture auto après **{self.cfg.node_terminal_idle_min} min** "
                    "d'inactivité."),
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
                sess.msg = await thread.send(
                    "```\n(démarrage…)\n```", view=TerminalView(self, thread.id, itx.user.id))
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
                                  target=f"noeud/{self.cfg.pve_node}", result="ok")
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
        self.bot.audit.record(user="system",
                              action="terminal-close" if sess.vmid else "terminal-node-close",
                              target=sess.name, result=reason[:80])
        try:
            await sess.thread.send(f"🔒 **Terminal fermé** — {reason}.")
            await sess.thread.edit(archived=True, locked=True)
        except discord.HTTPException:
            pass


async def setup(bot):
    await bot.add_cog(Terminal(bot))
