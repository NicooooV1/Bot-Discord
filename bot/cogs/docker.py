"""Cog /docker — gestion des conteneurs Docker de CT120 (stack servarr + pinchflat).

Demande Nico 2026-07-16 : piloter les 18 conteneurs depuis Discord, gluetun INCLUS
(son choix explicite, contre la recommandation de le verrouiller) — d'où les
garde-fous ci-dessous plutôt qu'une exclusion :

- ⚠️ gluetun = tunnel VPN AirVPN + kill-switch de qbittorrent. Le redémarrer OU le
  (re)démarrer après un stop recrée son namespace réseau, or qbittorrent
  (network_mode: service:gluetun) reste attaché à l'ancien : après un start/restart
  de gluetun, qbittorrent est automatiquement relancé une fois gluetun healthy.
  L'arrêter laisse qbittorrent SANS réseau (le kill-switch fait qu'aucune fuite
  n'est possible, mais plus rien ne seed/download).
- qbittorrent : stop/restart interrompt les seeds en cours (le ratio en pâtit).
- Toute action est confirmée (bouton), auditée (bot.audit -> #journaux-live, la
  consultation de logs comprise) et réservée aux admins.
- Le panneau est verrouillé sur SON invocateur : la sélection est un état partagé
  du message, deux admins sur le même panneau pourraient déclencher l'action sur
  le mauvais conteneur. Chaque admin ouvre le sien avec /docker.

Transport : les actions passent par ytgrab (CT120:8770), qui valide le nom demandé
contre la liste réelle `docker ps -a` avant d'agir (pas d'injection possible) et
n'accepte que start/stop/restart/logs. Kill-switch : DOCKER_CTL_ENABLED=false.
"""
import asyncio
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

import discord
from discord import app_commands
from discord.ext import commands

from ..core.gates import GatedView
from ..core.permissions import admin_check

log = logging.getLogger("discord-bot.docker")

YTGRAB = "http://10.3.20.120:8770"

# conteneurs à risque -> avertissement spécifique avant confirmation
RISKY = {
    "gluetun": ("Tunnel VPN + kill-switch.\n"
                "• **restart/start** : VPN coupé quelques secondes, puis **qbittorrent "
                "sera relancé automatiquement** une fois gluetun healthy (son réseau "
                "vit dans gluetun).\n"
                "• **stop** : qbittorrent reste SANS réseau (aucune fuite possible, "
                "mais plus aucun download/seed) jusqu'à un start de gluetun."),
    "qbittorrent": "Les téléchargements ET les seeds en cours seront interrompus "
                   "(le ratio en pâtit).",
    "pinchflat": "Les téléchargements /yt, /tw et /musique en cours seront tués.",
}
STATE_EMOJI = {"running": "🟢", "exited": "🔴", "created": "⚪",
               "paused": "🟠", "restarting": "🟠", "dead": "🔴"}
GLUETUN_HEALTHY_WAIT = 120     # s max d'attente du retour healthy avant de relancer qbit


def _emoji(item):
    e = STATE_EMOJI.get((item.get("state") or "").lower(), "⚪")
    status = item.get("status") or ""
    if "unhealthy" in status:
        return "🟠"
    return e


class DockerPanelView(GatedView):
    """Sélecteur de conteneur + boutons d'action. Verrouillé sur l'invocateur (admin).

    Porte « mod » : rôle Gestion ET session 2FA revalidée à CHAQUE clic (2026-08-11).
    Avant, seul `is_admin()` était vérifié — un panneau ouvert restait pleinement
    opérationnel jusqu'à son expiration (900 s) après un `/2fa lock` ou une expiration
    de session, alors que le verrouillage est censé retirer ces pouvoirs tout de
    suite. Les identités break-glass (propriétaire du guild, ADMIN_IDS), que la
    réconciliation des rôles ne peut pas dépouiller, étaient les seules réellement
    concernées — c'est précisément celles qui pilotent gluetun."""

    gate = "mod"
    gate_cap = "services"

    def __init__(self, cog, items, owner_id):
        super().__init__(timeout=900)
        self.cog = cog
        self.items = items
        self.owner_id = owner_id
        # la sélection est un état PARTAGÉ du message : deux admins sur le même
        # panneau pourraient agir sur le mauvais conteneur. Chacun le sien.
        self.gate_user_id = owner_id
        self.selected = None
        self.message = None
        self.notice = None      # bandeau d'erreur transitoire (liste non rafraîchie…)
        self._build_select()
        self._sync_buttons()

    # -------------------------------------------------------------- construction
    def _build_select(self):
        opts = []
        for it in self.items[:25]:
            opts.append(discord.SelectOption(
                label=it["name"][:100], value=it["name"],
                description=(it.get("status") or "?")[:100],
                emoji=_emoji(it),
                default=(it["name"] == self.selected)))
        self.select.options = opts or [discord.SelectOption(label="(vide)", value="_")]

    def _item(self, name):
        for it in self.items:
            if it["name"] == name:
                return it
        return None

    def _sync_buttons(self):
        it = self._item(self.selected) if self.selected else None
        running = bool(it and (it.get("state") or "").lower() == "running")
        self.start_btn.disabled = it is None or running
        self.stop_btn.disabled = it is None or not running
        self.restart_btn.disabled = it is None
        self.logs_btn.disabled = it is None

    def freeze(self):
        for c in self.children:
            c.disabled = True

    async def on_timeout(self):
        self.freeze()
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    async def on_denied(self, itx: discord.Interaction, why):
        """Message dédié au verrou d'invocateur (le refus de rôle/2FA reste standard)."""
        if why != "user":
            await super().on_denied(itx, why)
            return
        msg = "⛔ Ce panneau appartient à un autre admin — ouvre le tien avec `/docker`."
        try:
            if itx.response.is_done():
                await itx.followup.send(msg, ephemeral=True)
            else:
                await itx.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass

    # -------------------------------------------------------------- rendu
    def build_embed(self):
        emb = discord.Embed(
            title="🐳 Docker — CT120 (servarr)",
            description=f"**{len(self.items)}** conteneur(s). Sélectionne, puis agis.",
            color=0x2496ED)
        lines = [f"{_emoji(it)} **{it['name']}** — {(it.get('status') or '?')[:60]}"
                 for it in self.items]
        block, n = [], 1
        for ln in lines:
            if sum(len(x) + 1 for x in block) + len(ln) > 1000:
                emb.add_field(name=f"Conteneurs ({n})", value="\n".join(block), inline=False)
                block, n = [], n + 1
            block.append(ln)
        if block:
            emb.add_field(name="Conteneurs" if n == 1 else f"Conteneurs ({n})",
                          value="\n".join(block), inline=False)
        if len(self.items) > 25:
            emb.add_field(name="⚠️ Sélecteur limité",
                          value=f"Discord limite le menu à 25 entrées — "
                                f"{len(self.items) - 25} conteneur(s) non sélectionnable(s) ici.",
                          inline=False)
        if self.selected:
            it = self._item(self.selected)
            if it:
                emb.add_field(
                    name=f"Sélection : {_emoji(it)} {it['name']}",
                    value=f"état `{it.get('state', '?')}` · {(it.get('status') or '?')[:80]}\n"
                          f"image `{(it.get('image') or '?')[:80]}`",
                    inline=False)
                if it["name"] in RISKY:
                    emb.add_field(name="⚠️ Conteneur sensible",
                                  value=RISKY[it["name"]], inline=False)
        if self.notice:
            emb.add_field(name="⚠️", value=self.notice[:1000], inline=False)
        emb.set_footer(text="Actions confirmées, auditées, réservées aux admins.")
        return emb

    async def refresh(self):
        """Rafraîchit liste + embed via self.message (jamais via le token d'interaction :
        l'appel HTTP vers CT120 peut dépasser les 3 s de la réponse initiale)."""
        items = await self.cog._dk("GET", "/docker")
        if isinstance(items, list):
            self.items = items
            self.notice = None
            if self.selected and not self._item(self.selected):
                self.selected = None
        else:
            # on GARDE l'ancienne photo mais on le DIT (sinon un stop réussi laisse
            # un panneau « tout 🟢 » mensonger quand ytgrab/CT120 est injoignable)
            self.notice = ("Liste NON rafraîchie : service docker (ytgrab CT120) "
                           "injoignable — états affichés potentiellement périmés.")
        self._build_select()
        self._sync_buttons()
        if self.message is not None:
            try:
                await self.message.edit(embed=self.build_embed(), view=self)
            except discord.HTTPException as e:
                log.warning("édition panneau docker impossible: %s", e)

    # -------------------------------------------------------------- interactions
    @discord.ui.select(placeholder="Choisir un conteneur…", min_values=1, max_values=1)
    async def select(self, itx: discord.Interaction, sel: discord.ui.Select):
        self.selected = sel.values[0] if sel.values and sel.values[0] != "_" else None
        self._build_select()
        self._sync_buttons()
        await itx.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Démarrer", emoji="▶️", style=discord.ButtonStyle.success, row=1)
    async def start_btn(self, itx: discord.Interaction, _b: discord.ui.Button):
        await self.cog.do_action(self, itx, self.selected, "start")

    @discord.ui.button(label="Arrêter", emoji="⏹️", style=discord.ButtonStyle.danger, row=1)
    async def stop_btn(self, itx: discord.Interaction, _b: discord.ui.Button):
        await self.cog.do_action(self, itx, self.selected, "stop")

    @discord.ui.button(label="Redémarrer", emoji="🔁", style=discord.ButtonStyle.primary, row=1)
    async def restart_btn(self, itx: discord.Interaction, _b: discord.ui.Button):
        await self.cog.do_action(self, itx, self.selected, "restart")

    @discord.ui.button(label="Logs", emoji="📜", style=discord.ButtonStyle.secondary, row=1)
    async def logs_btn(self, itx: discord.Interaction, _b: discord.ui.Button):
        await self.cog.show_logs(itx, self.selected)

    @discord.ui.button(label="Actualiser", emoji="🔄", style=discord.ButtonStyle.secondary, row=1)
    async def refresh_btn(self, itx: discord.Interaction, _b: discord.ui.Button):
        # defer AVANT l'appel HTTP : docker ps sur CT120 peut dépasser la fenêtre de
        # 3 s de la réponse initiale (et ytgrab down bloquerait bien plus longtemps)
        await itx.response.defer()
        await self.refresh()


class ConfirmActionView(GatedView):
    """Confirmation éphémère, verrouillée sur le cliqueur, grisée à l'expiration.

    Porte « mod » comme le panneau : c'est CE clic qui déclenche réellement le
    `docker stop`, il doit revalider rôle + session 2FA (2026-08-11). Une session
    verrouillée entre l'ouverture du panneau et la confirmation laisse donc la
    confirmation expirer (value reste None = action annulée)."""

    gate = "mod"
    gate_cap = "services"

    def __init__(self, user_id, timeout=60):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.gate_user_id = user_id
        self.value = None
        self.message = None   # posé par l'appelant (original_response) pour on_timeout

    def _disable(self):
        for c in self.children:
            c.disabled = True

    @discord.ui.button(label="Confirmer", emoji="⚠️", style=discord.ButtonStyle.danger)
    async def yes(self, itx: discord.Interaction, _b: discord.ui.Button):
        self.value = True
        self._disable()
        await itx.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def no(self, itx: discord.Interaction, _b: discord.ui.Button):
        self.value = False
        self._disable()
        await itx.response.edit_message(view=self)
        self.stop()

    async def on_timeout(self):
        self._disable()
        if self.message is not None:
            try:
                await self.message.edit(content="⏱️ Confirmation expirée — action annulée.",
                                        view=self)
            except discord.HTTPException:
                pass


class Docker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # une seule séquence gluetun→qbittorrent à la fois : deux boucles d'attente
        # concurrentes pourraient relancer qbittorrent dans un netns en destruction
        self._glue_lock = asyncio.Lock()

    # ------------------------------------------------------------------ HTTP
    # Client local, et PAS core.http (relecture 2026-08-11) : ytgrab répond aux refus par
    # un code d'erreur HTTP dont le CORPS porte le diagnostic ({"error": "..."}), et c'est
    # ce texte qui est montré à l'utilisateur (« Liste impossible : … », le détail après
    # un `docker stop` refusé, « Logs indisponibles : … »). `request_json` réduit toute
    # réponse d'erreur à None — le corps ne part que dans les logs : on afficherait
    # « injoignable » à la place de la vraie raison. Contrat volontairement différent,
    # donc duplication ASSUMÉE (même helper que youtube._yt, qui parle au même service).
    def _dk_sync(self, method, path, body=None, timeout=200):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(YTGRAB + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")

    async def _dk(self, method, path, body=None, timeout=200):
        try:
            return await asyncio.to_thread(self._dk_sync, method, path, body, timeout)
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read())
            except Exception:  # noqa: BLE001
                return {"error": f"HTTP {e.code}"}
        except Exception as e:  # noqa: BLE001
            log.warning("dockerctl %s %s: %s", method, path, e)
            return None

    async def list_containers(self, timeout=10):
        """Liste `docker ps -a` de CT120 via ytgrab (lecture seule). Consommée par
        /docker et par l'embed du salon #servarr (cog CtChannels). None si injoignable."""
        items = await self._dk("GET", "/docker", timeout=timeout)
        return items if isinstance(items, list) else None

    # ------------------------------------------------------------------ commande
    @app_commands.command(name="docker",
                          description="Gérer les conteneurs Docker de CT120 (servarr, gluetun, pinchflat…)")
    @admin_check(require_admin_channel=False, cap="services")
    async def docker(self, itx: discord.Interaction):
        if not getattr(self.bot.cfg, "docker_ctl_enabled", True):
            await itx.response.send_message("⛔ /docker est désactivé (DOCKER_CTL_ENABLED=false).",
                                            ephemeral=True)
            return
        await itx.response.defer()
        items = await self._dk("GET", "/docker")
        if not isinstance(items, list):
            await itx.followup.send(
                f"⚠️ Liste impossible : {(items or {}).get('error', 'ytgrab (CT120:8770) injoignable')}")
            return
        view = DockerPanelView(self, items, itx.user.id)
        msg = await itx.followup.send(embed=view.build_embed(), view=view)
        try:
            view.message = await itx.channel.fetch_message(msg.id)  # durable (cf. youtube._durable)
        except (discord.HTTPException, AttributeError):
            view.message = msg

    # ------------------------------------------------------------------ actions
    async def do_action(self, panel: DockerPanelView, itx: discord.Interaction,
                        name, action):
        if not name:
            await itx.response.send_message("Sélectionne d'abord un conteneur.", ephemeral=True)
            return
        glue_seq = (name == "gluetun" and action in ("start", "restart"))
        if glue_seq and self._glue_lock.locked():
            await itx.response.send_message(
                "⏳ Une séquence gluetun→qbittorrent est déjà en cours — attends sa fin.",
                ephemeral=True)
            return
        # confirmation systématique — avertissement renforcé pour les sensibles
        warn = RISKY.get(name, "")
        txt = f"**docker {action} {name}** — confirmer ?"
        if warn and action in ("start", "stop", "restart"):
            txt += f"\n\n⚠️ {warn}"
        conf = ConfirmActionView(itx.user.id)
        await itx.response.send_message(txt, view=conf, ephemeral=True)
        try:
            conf.message = await itx.original_response()
        except discord.HTTPException:
            pass
        await conf.wait()
        if not conf.value:
            return

        actor = f"{itx.user.display_name}"
        if glue_seq:
            async with self._glue_lock:
                ok = await self._run_action(itx, name, action, actor)
                if ok:
                    # le netns de qbittorrent vit DANS gluetun : attendre healthy puis relancer
                    note = await self._requickstart_qbit(itx, actor)
                    try:
                        await itx.followup.send(note, ephemeral=True)
                    except discord.HTTPException:
                        pass
        else:
            await self._run_action(itx, name, action, actor)
        await panel.refresh()

    async def _run_action(self, itx, name, action, actor):
        """Exécute l'action, audite, informe. Retourne True si OK."""
        r = await self._dk("POST", f"/docker/{name}/{action}", {"actor": actor})
        ok = bool(r and r.get("ok"))
        self.bot.audit.record(user=f"{itx.user} ({itx.user.id})",
                              action=f"docker-{action}", target=f"CT120/{name}",
                              result="ok" if ok else "échec")
        detail = "" if ok else f" : `{((r or {}).get('error') or (r or {}).get('out') or 'injoignable')[:200]}`"
        try:
            await itx.followup.send(
                f"{'✅' if ok else '❌'} `docker {action} {name}`{detail}", ephemeral=True)
        except discord.HTTPException:
            pass
        return ok

    async def _requickstart_qbit(self, itx, actor):
        """Après un start/restart de gluetun : attendre le retour healthy, relancer qbittorrent."""
        deadline = asyncio.get_event_loop().time() + GLUETUN_HEALTHY_WAIT
        healthy = False
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(5)
            items = await self._dk("GET", "/docker")
            if isinstance(items, list):
                g = next((i for i in items if i["name"] == "gluetun"), None)
                if g and "healthy" in (g.get("status") or "") \
                        and "unhealthy" not in (g.get("status") or ""):
                    healthy = True
                    break
        if not healthy:
            return ("⚠️ gluetun n'est pas repassé *healthy* en 2 min — qbittorrent n'a "
                    "PAS été relancé. Relance-le à la main quand le VPN sera revenu.")
        r = await self._dk("POST", "/docker/qbittorrent/restart",
                           {"actor": f"{actor} (auto après gluetun)"})
        ok = bool(r and r.get("ok"))
        self.bot.audit.record(user=f"bot (séquence gluetun, demandé par {actor})",
                              action="docker-restart", target="CT120/qbittorrent",
                              result="ok" if ok else "échec")
        return ("✅ gluetun healthy — qbittorrent relancé automatiquement (son réseau vit "
                "dans gluetun)." if ok else
                "❌ gluetun est revenu mais le restart de qbittorrent a échoué — à relancer à la main.")

    async def show_logs(self, itx: discord.Interaction, name):
        if not name:
            await itx.response.send_message("Sélectionne d'abord un conteneur.", ephemeral=True)
            return
        await itx.response.defer(ephemeral=True)
        r = await self._dk("GET", f"/docker/{urllib.parse.quote(name)}/logs?tail=40",
                           timeout=45)
        # consulter des logs = accès à de l'information sensible -> audité comme le reste
        self.bot.audit.record(user=f"{itx.user} ({itx.user.id})", action="docker-logs",
                              target=f"CT120/{name}",
                              result="ok" if (r and "logs" in r) else "échec")
        if not r or "logs" not in r:
            await itx.followup.send(
                f"⚠️ Logs indisponibles : {(r or {}).get('error', 'injoignable')}", ephemeral=True)
            return
        txt = (r["logs"] or "(vide)").replace("`", "'") or "(vide)"
        await itx.followup.send(f"📜 **{name}** — 40 dernières lignes :\n```\n{txt[-1850:]}\n```",
                                ephemeral=True)


async def setup(bot):
    await bot.add_cog(Docker(bot))
