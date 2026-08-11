"""Safe actions (admin + channel gated, confirmation, audit, UPID polling):
/ctctl start|stop|restart, /backup, /audit.

Le suivi d'une tâche PVE (UPID) vit dans `Pve.apoll_task` et son rendu dans
`core.format.outcome_text` : ce cog n'en garde plus de copie (2026-08-11)."""
import datetime
import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..core import format as fmt
from ..core.gates import GatedView
from ..core.permissions import admin_check
from ..core.ui import ct_autocomplete
from ..views.confirm import ConfirmView

log = logging.getLogger("discord-bot.actions")

ACTIONS = [app_commands.Choice(name="start", value="start"),
           app_commands.Choice(name="stop", value="stop"),
           app_commands.Choice(name="restart", value="restart")]


class Actions(commands.Cog):
    # groupe /backup : créer / supprimer une sauvegarde PBS
    backup = app_commands.Group(name="backup",
                                description="Gérer les sauvegardes PBS : créer, supprimer.")

    def __init__(self, bot):
        self.bot = bot

    async def _confirm(self, itx, description):
        # caller has already deferred (ephemeral) -> send the prompt via followup
        view = ConfirmView(itx.user.id)
        emb = discord.Embed(title="⚠️ Confirmation", description=description, color=fmt.YELLOW)
        # wait=True : sans lui `followup.send` renvoie None (discord.py ne construit le
        # WebhookMessage que dans la branche `if wait:`), `view.message` restait None et
        # `ConfirmView.on_timeout` ne grisait donc jamais les boutons (2026-08-11).
        view.message = await itx.followup.send(embed=emb, view=view, ephemeral=True,
                                               wait=True)
        await view.wait()
        return bool(view.value)

    @app_commands.command(description="Démarrer / arrêter / redémarrer un conteneur OU une VM.")
    @app_commands.describe(action="start, stop ou restart", name="Conteneur ou VM")
    @app_commands.choices(action=ACTIONS)
    @app_commands.autocomplete(name=ct_autocomplete)
    @admin_check()
    async def ctctl(self, itx: discord.Interaction,
                    action: app_commands.Choice[str], name: str):
        if not self.bot.pve.actions_enabled:
            await itx.response.send_message("Token d'action PVE non configuré "
                                            "(`PVE_ACTION_TOKEN_SECRET`).", ephemeral=True)
            return
        await itx.response.defer(ephemeral=True)
        vmid = await self.bot.pve.avmid_of(name)
        if not vmid:
            await itx.followup.send("VM/conteneur introuvable.", ephemeral=True)
            return
        gtype = await self.bot.pve.aguest_type(name)
        kind = "VM" if gtype == "qemu" else "conteneur"
        act = action.value
        if not await self._confirm(
                itx, f"**{act}** sur le {kind} **{name}** "
                     f"(vmid {self.bot.pve.display_vmid(vmid)}) ?"):
            await itx.followup.send("Annulé.", ephemeral=True)
            return
        who = f"{itx.user}({itx.user.id})"
        pve = self.bot.pve
        lxc_fns = {"start": pve.start_ct, "stop": pve.shutdown_ct, "restart": pve.reboot_ct}
        vm_fns = {"start": pve.start_vm, "stop": pve.shutdown_vm, "restart": pve.reboot_vm}
        fn = (vm_fns if gtype == "qemu" else lxc_fns)[act]
        try:
            upid = await pve.acall(fn, vmid)
        except Exception as e:
            self.bot.audit.record(user=who, action=act, target=f"{name}/{vmid}", result=f"error:{e}")
            await itx.followup.send(f"❌ Échec: `{e}`", ephemeral=True)
            return
        self.bot.audit.record(user=who, action=act, target=f"{name}/{vmid}",
                              result="submitted", upid=str(upid))
        await itx.followup.send(f"⏳ `{act}` lancé sur **{name}** — suivi…", ephemeral=True)
        # « lost » n'est PAS un échec : c'est NOTRE suivi qui s'arrête, la tâche continue
        # côté Proxmox (rendu par outcome_text).
        outcome = await self.bot.pve.apoll_task(str(upid))
        result = fmt.outcome_text(outcome, "terminé")
        await itx.followup.send(f"**{name}** {act}: {result}", ephemeral=True)

    @backup.command(name="create", description="Lancer une sauvegarde vzdump d'un conteneur vers PBS.")
    @app_commands.describe(name="Conteneur à sauvegarder")
    @app_commands.autocomplete(name=ct_autocomplete)
    @admin_check()
    async def backup_create(self, itx: discord.Interaction, name: str):
        if not self.bot.pve.actions_enabled:
            await itx.response.send_message("Token d'action PVE non configuré.", ephemeral=True)
            return
        await itx.response.defer(ephemeral=True)
        vmid = await self.bot.pve.avmid_of(name)
        if not vmid:
            await itx.followup.send("Conteneur introuvable.", ephemeral=True)
            return
        # storage_for() interroge l'API PVE : appelé nu il gelait la boucle d'événements
        # jusqu'à 15 s (30 s pour un invité d'Aveyron) — 2026-08-11.
        sto = await self.bot.pve.astorage_for(vmid)
        if not await self._confirm(
                itx, f"Sauvegarder **{name}** "
                     f"(vmid {self.bot.pve.display_vmid(vmid)}) vers `{sto}` ?"):
            await itx.followup.send("Annulé.", ephemeral=True)
            return
        who = f"{itx.user}({itx.user.id})"
        try:
            upid = await self.bot.pve.acall(self.bot.pve.backup, vmid)
        except Exception as e:
            self.bot.audit.record(user=who, action="backup", target=f"{name}/{vmid}", result=f"error:{e}")
            await itx.followup.send(f"❌ Échec: `{e}`", ephemeral=True)
            return
        self.bot.audit.record(user=who, action="backup", target=f"{name}/{vmid}",
                              result="submitted", upid=str(upid))
        await itx.followup.send(f"⏳ Sauvegarde de **{name}** lancée — suivi…", ephemeral=True)
        outcome = await self.bot.pve.apoll_task(str(upid), timeout=900)
        result = fmt.outcome_text(outcome, "terminée")
        # le poll peut dépasser les 15 min du token d'interaction -> followup 404.
        # On tente le followup éphémère, puis on retombe sur un message de salon.
        try:
            await itx.followup.send(f"Backup **{name}**: {result}", ephemeral=True)
        except discord.HTTPException:
            await itx.channel.send(f"{itx.user.mention} Backup **{name}** : {result}")

    @backup.command(name="delete",
                    description="Supprimer une sauvegarde PBS d'un conteneur (confirmation requise).")
    @app_commands.describe(name="Conteneur dont supprimer une sauvegarde")
    @app_commands.autocomplete(name=ct_autocomplete)
    @admin_check()
    async def backup_delete(self, itx: discord.Interaction, name: str):
        if not self.bot.pve.actions_enabled:
            await itx.response.send_message("Token d'action PVE non configuré.", ephemeral=True)
            return
        await itx.response.defer(ephemeral=True)
        vmid = await self.bot.pve.avmid_of(name)
        if not vmid:
            await itx.followup.send("Conteneur introuvable.", ephemeral=True)
            return
        try:
            snaps = await self.bot.pve.apbs_content(vmid)
        except Exception as e:
            await itx.followup.send(f"❌ Lecture PBS impossible : `{e}`", ephemeral=True)
            return
        sto = await self.bot.pve.astorage_for(vmid)      # bloquant si appelé nu
        if not snaps:
            await itx.followup.send(
                f"Aucune sauvegarde pour **{name}** sur `{sto}`.",
                ephemeral=True)
            return
        snaps.sort(key=lambda s: s.get("ctime", 0) or 0, reverse=True)
        view = BackupDeleteView(self, name, snaps[:25], itx.user.id)
        if not view.options_count:
            # Discord refuse un menu vide (400) : mieux vaut le dire que se prendre le rejet.
            await itx.followup.send(
                f"Aucune sauvegarde exploitable pour **{name}** (volid manquant).",
                ephemeral=True)
            return
        emb = discord.Embed(
            title=f"🗑️ Supprimer une sauvegarde — {name}",
            description=(f"**{len(snaps)}** sauvegarde(s) sur `{sto}`.\n"
                        "Choisis celle à supprimer — **action irréversible**."),
            color=fmt.YELLOW)
        # wait=True : sinon `view.message` reste None et `on_timeout` ne grise pas le menu.
        view.message = await itx.followup.send(embed=emb, view=view, ephemeral=True,
                                               wait=True)

    @app_commands.command(description="Dernières actions du journal d'audit.")
    @app_commands.describe(limit="Nombre de lignes (def 20)")
    @admin_check()
    async def audit(self, itx: discord.Interaction, limit: int = 20):
        await itx.response.defer(ephemeral=True)
        lines = self.bot.audit.tail(max(1, min(50, limit)))
        txt = "".join(lines) or "(vide)"
        await itx.followup.send(f"```\n{txt[-1900:]}\n```", ephemeral=True)


class BackupDeleteView(GatedView):
    """Menu de sélection d'une sauvegarde à supprimer, puis confirmation.

    Porte « mod » (rôle M/O + session 2FA) comme la commande qui l'ouvre, ET verrou
    d'auteur : le message est éphémère, mais un composant n'est jamais couvert par
    `GatedTree` — la porte doit être ICI (2026-08-11)."""

    gate = "mod"

    def __init__(self, cog, name, snaps, user_id=None):
        super().__init__(timeout=120)
        self.cog = cog
        self.name = name
        self.gate_user_id = user_id
        self.message = None      # posé par l'appelant pour que on_timeout grise le menu
        options = []
        for s in snaps:
            vol = s.get("volid")
            if not vol:
                continue
            ct = int(s.get("ctime", 0) or 0)
            when = (datetime.datetime.fromtimestamp(ct).strftime("%Y-%m-%d %H:%M")
                    if ct else "date ?")
            size = fmt.humanize_bytes(s.get("size", 0) or 0)
            prot = " 🔒protégée" if s.get("protected") else ""
            label = f"{when} · {size}{prot}"[:100]
            options.append(discord.SelectOption(
                label=label, value=vol[:100], description=vol.split(":", 1)[-1][:90]))
        self.options_count = len(options)
        if options:
            sel = discord.ui.Select(placeholder="Sauvegarde à supprimer…", options=options)
            sel.callback = self._on_select
            self.add_item(sel)

    async def on_timeout(self):
        # sans ça le menu restait cliquable en apparence après 120 s, et un clic tardif
        # ne donnait qu'un « Cette interaction a échoué » sans explication (2026-08-11).
        for c in self.children:
            c.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                log.debug("BackupDeleteView: menu non grisé au timeout", exc_info=True)

    async def _on_select(self, itx: discord.Interaction):
        await itx.response.defer(ephemeral=True)
        vol = itx.data["values"][0]
        if not await self.cog._confirm(
                itx, f"⚠️ Supprimer **définitivement** cette sauvegarde de **{self.name}** ?\n"
                     f"`{vol}`\n(action irréversible)"):
            await itx.followup.send("Annulé.", ephemeral=True)
            return
        who = f"{itx.user}({itx.user.id})"
        try:
            res = await self.cog.bot.pve.acall(self.cog.bot.pve.delete_backup, vol)
        except Exception as e:
            self.cog.bot.audit.record(user=who, action="backup-delete", target=vol, result=f"error:{e}")
            await itx.followup.send(f"❌ Échec de la suppression : `{e}`", ephemeral=True)
            return
        self.cog.bot.audit.record(user=who, action="backup-delete", target=vol,
                                  result="submitted", upid=str(res))
        # les UPID du cluster AVEYRON arrivent préfixés « avy: » (task_status les route)
        if isinstance(res, str) and (res.startswith("UPID") or res.startswith("avy:UPID")):
            outcome = await self.cog.bot.pve.apoll_task(res, timeout=180)
            result = fmt.outcome_text(outcome, "supprimée")
        else:
            result = "✅ supprimée"
        await itx.followup.send(f"🗑️ Sauvegarde de **{self.name}** {result}.", ephemeral=True)
        self.stop()


async def setup(bot):
    await bot.add_cog(Actions(bot))
