"""Logs subsystem: /journal /logs /tail /tasks + the live UDP/514 stream listener.

Host journal is read via the PVE API (GET /nodes/pve/journal). The API returns a flat
array mixing cursor lines (s=...;i=...) and human text lines, with no unit filter — so
/logs filters client-side. The bot inside CT106 cannot pct-exec, so per-CT application
logs come through the live stream (opt-in rsyslog forward) rather than a command.
"""
import asyncio
import io
import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

from ..core import format as fmt
from ..core.gates import GatedView
from ..core.logstream import LiveLogStream
from ..core.permissions import admin_check, read_check
from ..core.syslog_lib import SEVERITY_NUM, sev_fr

log = logging.getLogger("discord-bot.logs")

_CURSOR = re.compile(r"^s=[0-9a-f]+;i=")

SEV_CHOICES = [app_commands.Choice(name=n, value=n) for n in
               ("emerg", "alert", "crit", "err", "warning", "notice", "info", "debug")]


def split_journal(arr):
    lines = [x for x in arr if not _CURSOR.match(str(x))]
    cursors = [x for x in arr if _CURSOR.match(str(x))]
    return lines, (cursors[-1] if cursors else None)


def _fence(s):
    """Neutralise une clôture de bloc de code (``` -> ` + U+200B + ``).

    Une ligne de journal contenant ``` refermerait le bloc et le reste s'afficherait
    en markdown : de quoi maquiller la sortie du bot avec un texte forgé arrivé dans
    le journal de l'hôte ou dans Loki (2026-08-11). Même remplacement que
    `terminal._fence`, gardé local ici pour ne pas toucher un autre fichier."""
    return (s or "").replace("```", "`​``")


def _task_state(t):
    """(libellé, drapeau) d'une tâche PVE.

    ⚠️ PVE renvoie 'RUNNING' en MAJUSCULES (cf. pve.tasks) et, pour un échec, la
    chaîne d'erreur COMPLÈTE en guise de statut (« command 'xyz' failed: exit code
    2 ») : on compare sans tenir compte de la casse et on tronque, sinon quelques
    échecs suffisent à saturer la description de l'embed."""
    st = str(t.get("status") or "").strip()
    if st.upper() == "RUNNING" or (not st and not t.get("endtime")):
        return "en cours", "⏳"
    if st.upper() == "OK":
        return "OK", "✅"
    return (st or "?")[:80], "❌"


def journal_response(lines, header):
    # ⚠️ ORDRE IMPORTANT : on échappe AVANT de mesurer. L'échappement du header peut
    # presque doubler sa taille (/logsearch y renvoie une requête LogQL dense en
    # `_ ~ |`) : mesurer la version non échappée ferait dépasser les 2000 caractères
    # et casserait la commande (HTTP 400) au lieu de basculer en fichier joint.
    raw = "\n".join(str(x) for x in lines)
    body = _fence(raw) or "(aucune entrée)"
    header = discord.utils.escape_markdown(str(header))
    # content = **header**\n```\nbody\n``` -> len(header) + len(body) + 13; Discord cap 2000.
    # The second guard matters for long headers (e.g. /logsearch echoes the query).
    if len(body) > 1850 or len(body) + len(header) > 1985:
        # le fichier joint reçoit le texte BRUT : il n'est pas rendu en markdown, et y
        # semer des U+200B fausserait un grep sur le journal téléchargé (2026-08-11)
        return None, discord.File(io.BytesIO((raw or body).encode("utf-8", "replace")),
                                  "journal.txt")
    return f"**{header}**\n```\n{body}\n```", None


class TailView(GatedView):
    """Suivi du journal de l'hôte. Porte « read » (simple lecture) + verrou sur la
    personne qui a lancé `/tail` : le curseur est un état partagé du message."""

    gate = "read"

    def __init__(self, bot, unit, cursor, author_id, timeout=300):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.unit = unit
        self.cursor = cursor
        self.author_id = author_id
        self.gate_user_id = author_id
        self.message = None     # posé par /tail, pour griser les boutons à l'expiration

    async def on_timeout(self):
        """Sans ça, passé 5 min le bouton reste cliquable et Discord affiche
        « This interaction failed » (la vue n'existe plus côté bot)."""
        for c in self.children:
            c.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Rafraîchir", emoji="🔄", style=discord.ButtonStyle.primary)
    async def refresh(self, itx: discord.Interaction, button: discord.ui.Button):
        await itx.response.defer()
        try:
            if self.cursor:
                arr = await asyncio.to_thread(self.bot.pve.journal, None, None, self.cursor)
            else:
                arr = await asyncio.to_thread(self.bot.pve.journal, 200)
        except Exception as e:  # noqa: BLE001
            # Le repli ne vaut que pour un curseur périmé (PVE répond 400 sur un
            # startcursor obsolète) : rejouer journal(200) après un échec de
            # journal(200) relèverait à l'identique. Et comme on est dans une VUE,
            # rien ne rattrape l'exception (install_error_handler ne couvre que les
            # commandes) : l'utilisateur verrait un message inchangé, donc PÉRIMÉ,
            # sans le moindre signal. On le dit (2026-08-11).
            arr = None
            if self.cursor:
                try:
                    arr = await asyncio.to_thread(self.bot.pve.journal, 200)
                    self.cursor = None
                except Exception as e2:  # noqa: BLE001
                    e = e2
            if arr is None:
                log.warning("tail: journal PVE indisponible: %s", e)
                try:
                    await itx.followup.send(
                        "⚠️ Journal PVE indisponible — affichage inchangé (donc périmé).",
                        ephemeral=True)
                except discord.HTTPException:
                    pass
                return
        lines, cur = split_journal(arr)
        if cur:
            self.cursor = cur
        if self.unit:
            lines = [x for x in lines if self.unit in str(x).lower()]
        content, file = journal_response(lines[-40:], f"tail · {self.unit or 'journal'}")
        try:
            if file:
                await itx.message.edit(content=None, attachments=[file], view=self)
            else:
                await itx.message.edit(content=content, attachments=[], view=self)
        except discord.HTTPException as exc:
            # message supprimé entre-temps : l'exception d'une vue n'est rattrapée nulle
            # part, elle ne doit pas remonter en silence (2026-08-11)
            log.warning("tail: édition du suivi impossible: %s", exc)


class Logs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.stream = None

    async def cog_load(self):
        self.stream = LiveLogStream(self.bot)
        self.stream.start()

    async def cog_unload(self):
        if self.stream:
            await self.stream.aclose()

    @app_commands.command(description="Journal de l'hôte (dernières entrées).")
    @app_commands.describe(lastentries="Nombre de lignes (def 60, max 2000)")
    @read_check()
    async def journal(self, itx: discord.Interaction, lastentries: int = 60):
        await itx.response.defer()
        if not self.bot.pve.enabled:
            await itx.followup.send("Proxmox API non configurée.")
            return
        n = max(1, min(2000, lastentries))
        arr = await asyncio.to_thread(self.bot.pve.journal, n)
        lines, _ = split_journal(arr)
        content, file = journal_response(lines[-n:], f"journal · hôte {self.bot.cfg.pve_node}")
        if file:
            await itx.followup.send(content=None, file=file)
        else:
            await itx.followup.send(content=content)

    @app_commands.command(description="Logs filtrés par unité/mot-clé (journal hôte).")
    @app_commands.describe(unit="Unité/service ou mot-clé", lastentries="Fenêtre de recherche")
    @read_check()
    async def logs(self, itx: discord.Interaction, unit: str, lastentries: int = 1000):
        await itx.response.defer()
        if not self.bot.pve.enabled:
            await itx.followup.send("Proxmox API non configurée.")
            return
        n = max(50, min(3000, lastentries))
        arr = await asyncio.to_thread(self.bot.pve.journal, n)
        lines, _ = split_journal(arr)
        matched = [x for x in lines if unit.lower() in str(x).lower()]
        # au-delà de 80 correspondances on n'affiche que la fin : le dire, sinon le
        # compte fait croire que tout est là (2026-08-20)
        count = (f"{len(matched)} lignes, 80 dernières affichées" if len(matched) > 80
                 else f"{len(matched)} lignes")
        content, file = journal_response(matched[-80:], f"logs « {unit} » ({count})")
        if file:
            await itx.followup.send(content=None, file=file)
        else:
            await itx.followup.send(content=content)

    @app_commands.command(description="Suivi d'un service (bouton Rafraîchir, 5 min).")
    @app_commands.describe(unit="Unité/mot-clé à suivre (vide = tout)")
    @read_check()
    async def tail(self, itx: discord.Interaction, unit: str = ""):
        await itx.response.defer()
        if not self.bot.pve.enabled:
            await itx.followup.send("Proxmox API non configurée.")
            return
        arr = await asyncio.to_thread(self.bot.pve.journal, 200)
        lines, cur = split_journal(arr)
        u = unit.lower()
        if u:
            lines = [x for x in lines if u in str(x).lower()]
        view = TailView(self.bot, u, cur, itx.user.id)
        content, file = journal_response(lines[-40:], f"tail · {unit or 'journal'}")
        # garder le message : sans lui, on_timeout ne peut pas griser les boutons
        if file:
            view.message = await itx.followup.send(content=None, file=file, view=view)
        else:
            view.message = await itx.followup.send(content=content, view=view)

    @app_commands.command(description="Tâches PVE récentes (start/stop/backup), filtre vmid.")
    @app_commands.describe(vmid="Filtrer par vmid (optionnel)")
    @read_check()
    async def tasks(self, itx: discord.Interaction, vmid: int = None):
        await itx.response.defer()
        if not self.bot.pve.enabled:
            await itx.followup.send("Proxmox API non configurée.")
            return
        # 3e paramètre = `source`. Sans lui l'API PVE ne renvoie QUE l'archive (tâches
        # TERMINÉES) : la branche « en cours » ci-dessous était du code mort et une
        # sauvegarde vzdump en train de tourner n'apparaissait PAS DU TOUT — le
        # diagnostic inverse de la réalité (corrigé 2026-08-11, même piège que
        # running_vzdump_vmids). La limite passe à 50 parce qu'elle s'applique
        # désormais à actives+archive : une rafale de tâches finies pouvait repousser
        # la tâche active hors de la fenêtre.
        ts = await asyncio.to_thread(self.bot.pve.tasks, vmid, 50, "all")
        rows = list(ts or [])
        rows.sort(key=lambda t: 0 if _task_state(t)[0] == "en cours" else 1)
        lines = []
        for t in rows:
            status, flag = _task_state(t)
            who = t.get("id") or t.get("vmid") or "-"
            lines.append(f"{flag} `{t.get('type')}` {who} — {status}")
        # troncatures annoncées (limite API 50 tâches, plafond embed 4096) : une coupe
        # muette ferait passer la liste pour complète (2026-08-20)
        desc = "\n".join(lines) or "Aucune tâche."
        if len(rows) >= 50:
            desc += "\n… liste tronquée (limite 50 tâches)"
        if len(desc) > 4000:
            desc = desc[:3970] + "\n… liste tronquée"
        emb = discord.Embed(title="🗂️ Tâches PVE", description=desc, color=fmt.BLURPLE)
        await itx.followup.send(embed=emb)

    # ----- /logstream admin group (runtime control of the live UDP stream) -----

    logstream = app_commands.Group(name="logstream",
                                   description="Gestion du flux de logs live (admin).")

    def _stream_or_none(self):
        st = self.stream
        if st is None or not st.channel_id:
            return None
        return st

    @logstream.command(name="stats", description="Statistiques du flux de logs live.")
    @admin_check(cap="services")
    async def logstream_stats(self, itx: discord.Interaction):
        st = self._stream_or_none()
        if st is None:
            await itx.response.send_message(
                "Flux de logs non configuré (LIVE_LOG_CHANNEL_ID).", ephemeral=True)
            return
        s = st.stats()
        emb = discord.Embed(title="📡 Flux de logs — statistiques", color=fmt.BLURPLE)
        emb.add_field(name="Reçus", value=str(s["received"]), inline=True)
        emb.add_field(name="Filtrés", value=str(s["filtered"]), inline=True)
        emb.add_field(name="Groupes publiés", value=str(s["posted_groups"]), inline=True)
        emb.add_field(name="Répétitions supprimées",
                      value=str(s["suppressed_repeats"]), inline=True)
        emb.add_field(name="Groupes repliés (anti-flood)",
                      value=str(s["overflow_folded"]), inline=True)
        emb.add_field(name="Échecs d'envoi", value=str(s["send_failures"]), inline=True)
        emb.add_field(name="Réessais perdus", value=str(s["retry_dropped"]), inline=True)
        emb.add_field(name="File de réessai", value=str(len(st.pending)), inline=True)
        sevname = sev_fr(st.min_sev)
        # « ▶️ actif » alors que le bind UDP a échoué = l'écran ment exactement comme
        # avant le correctif : l'échec d'écoute prime sur la pause (2026-08-11).
        if getattr(st, "listen_error", None):
            state = f"⛔ écoute HS : {st.listen_error}"
        elif not getattr(st, "listening", lambda: True)():
            state = "⛔ écoute non démarrée"
        else:
            state = "⏸️ en pause" if st.paused else "▶️ actif"
        emb.add_field(name="État", value=f"{state} · sévérité ≤ `{sevname}`", inline=True)
        emb.add_field(name="Depuis", value=f"<t:{int(s['since_ts'])}:R>", inline=True)
        await itx.response.send_message(embed=emb, ephemeral=True)

    @logstream.command(name="severity",
                       description="Change la sévérité minimale du flux (runtime).")
    @app_commands.describe(niveau="Sévérité minimale (emerg = le plus grave)")
    @app_commands.choices(niveau=SEV_CHOICES)
    @admin_check(cap="services")
    async def logstream_severity(self, itx: discord.Interaction,
                                 niveau: app_commands.Choice[str]):
        st = self._stream_or_none()
        if st is None:
            await itx.response.send_message(
                "Flux de logs non configuré (LIVE_LOG_CHANNEL_ID).", ephemeral=True)
            return
        st.min_sev = SEVERITY_NUM[niveau.value]
        await itx.response.send_message(
            f"Sévérité minimale du flux réglée sur `{niveau.value}` "
            "(runtime uniquement, non persistant — modifier `LIVE_LOG_MIN_SEVERITY` "
            "dans config.env pour rendre le changement permanent).", ephemeral=True)

    @logstream.command(name="pause", description="Met le flux de logs en pause.")
    @admin_check(cap="services")
    async def logstream_pause(self, itx: discord.Interaction):
        st = self._stream_or_none()
        if st is None:
            await itx.response.send_message(
                "Flux de logs non configuré (LIVE_LOG_CHANNEL_ID).", ephemeral=True)
            return
        st.paused = True
        self.bot.state.set("logstream_paused", True)
        await itx.response.send_message(
            "⏸️ Flux de logs en pause (les paquets reçus sont ignorés). "
            "_(état persistant — conservé après redémarrage)_", ephemeral=True)

    @logstream.command(name="resume", description="Reprend le flux de logs.")
    @admin_check(cap="services")
    async def logstream_resume(self, itx: discord.Interaction):
        st = self._stream_or_none()
        if st is None:
            await itx.response.send_message(
                "Flux de logs non configuré (LIVE_LOG_CHANNEL_ID).", ephemeral=True)
            return
        st.paused = False
        self.bot.state.set("logstream_paused", False)
        await itx.response.send_message(
            "▶️ Flux de logs repris. _(état persistant — conservé après redémarrage)_",
            ephemeral=True)


async def setup(bot):
    await bot.add_cog(Logs(bot))
