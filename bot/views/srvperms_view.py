"""Panneau `/gestion perms` : l'OWNER d'un serveur règle ce que ses tiers G et M peuvent
faire (capacités) et voir (salons masqués) — Nico 2026-08-29.

Le panneau est éphémère et verrouillé sur son ouvreur ; chaque changement est PERSISTÉ
tout de suite (core/srvperms) puis les overwrites Discord du serveur sont réappliqués
dans la foulée (Provision.enforce_server_perms) — la boucle de 5 min reste le filet.
"""
import logging

import discord

from ..core import bg, srvperms
from ..core.gates import GatedView
from ..core.permissions import is_breakglass, tier_of

log = logging.getLogger("discord-bot.srvperms")

TIER_LABEL = {"G": "G — Gestion (voir)", "M": "M — Modérateur (agir)"}


class SrvPermsView(GatedView):
    """Un serveur, un tier sélectionné, trois contrôles : capacités, salons masqués,
    remise à zéro. Porte : tier O du serveur (ou break-glass) + ouvreur + 2FA."""

    gate = "mod"

    def __init__(self, bot, server, opener_id, guild):
        super().__init__(timeout=600)
        self.bot = bot
        self.server = server
        self.gate_server = server
        self.gate_user_id = opener_id
        self.guild = guild
        self.tier = "M"
        self.message = None
        self._build()

    # ------------------------------------------------------------------ porte
    async def interaction_check(self, itx) -> bool:
        if not await super().interaction_check(itx):
            return False
        cfg = self.bot.cfg
        if not (is_breakglass(cfg, itx) or tier_of(cfg, itx, self.server) == "O"):
            await itx.response.send_message(
                f"⛔ Réservé à l'Owner de **{self.server}** (rôle O).", ephemeral=True)
            return False
        return True

    # ------------------------------------------------------------------ rendu
    def _server_channels(self):
        from ..core import channels as _ch
        out = []
        for _key, cat, _genre in _ch.server_categories(self.bot, self.guild, self.server):
            out.extend(cat.text_channels)
        return out

    def embed(self):
        st = self.bot.state
        emb = discord.Embed(
            title=f"🛂 Permissions — {self.server} · niveau {self.tier}",
            description=("Réglages de l'Owner pour ce niveau, **sur ce serveur seulement**. "
                         "Le niveau O n'est jamais restreint ; le propriétaire du bot non plus.\n"
                         "*(modifié)* = différent du défaut."),
            color=0x5865F2)
        lines = srvperms.summary_lines(st, self.server, self.tier)
        emb.add_field(name="Capacités", value="\n".join(lines)[:1024], inline=False)
        hidden = srvperms.hidden_channels(st, self.server, self.tier)
        if hidden:
            names = []
            for cid in sorted(hidden):
                ch = self.guild.get_channel(cid)
                names.append(ch.mention if ch is not None else f"`{cid}` (supprimé)")
            val = ", ".join(names)
        else:
            val = "_(aucun — tous les salons du serveur sont visibles de ce niveau)_"
        emb.add_field(name="Salons masqués à ce niveau", value=val[:1024], inline=False)
        emb.set_footer(text="Les overwrites Discord sont réappliqués à chaque changement "
                            "(filet : réconciliation toutes les 5 min).")
        return emb

    def _build(self):
        self.clear_items()
        st = self.bot.state
        # 1) niveau
        tier_sel = discord.ui.Select(
            placeholder="Niveau à régler", row=0,
            options=[discord.SelectOption(label=TIER_LABEL[t], value=t, default=(t == self.tier))
                     for t in srvperms.TIERS])
        tier_sel.callback = self._on_tier
        self.add_item(tier_sel)
        # 2) capacités
        eff = srvperms.effective_caps(st, self.server, self.tier)
        caps_sel = discord.ui.Select(
            placeholder="Capacités AUTORISÉES (sélection = autorisé)", row=1,
            min_values=0, max_values=len(srvperms.CAPS),
            options=[discord.SelectOption(label=spec[0][:100], value=cap,
                                          description=spec[1][:100], default=eff[cap])
                     for cap, spec in srvperms.CAPS.items()])
        caps_sel.callback = self._on_caps
        self.add_item(caps_sel)
        # 3) salons masqués (ChannelSelect : filtré côté bot aux salons DU serveur)
        hidden = srvperms.hidden_channels(st, self.server, self.tier)
        defaults = [discord.SelectDefaultValue(id=cid, type=discord.SelectDefaultValueType.channel)
                    for cid in sorted(hidden)][:25]
        ch_sel = discord.ui.ChannelSelect(
            placeholder="Salons MASQUÉS à ce niveau (vide = tout visible)", row=2,
            min_values=0, max_values=25, channel_types=[discord.ChannelType.text],
            default_values=defaults)
        ch_sel.callback = self._on_hidden
        self.add_item(ch_sel)
        # 4) boutons
        b_reset = discord.ui.Button(label="Remettre ce niveau aux défauts", emoji="↩️",
                                    style=discord.ButtonStyle.secondary, row=3)
        b_reset.callback = self._on_reset
        self.add_item(b_reset)
        b_close = discord.ui.Button(label="Fermer", style=discord.ButtonStyle.danger, row=3)
        b_close.callback = self._on_close
        self.add_item(b_close)

    async def _refresh(self, itx):
        self._build()
        await itx.response.edit_message(embed=self.embed(), view=self)

    # ------------------------------------------------------------------ actions
    def _audit(self, itx, what):
        try:
            self.bot.audit.record(user=f"{itx.user} ({itx.user.id})", action="srvperms",
                                  target=f"{self.server}/{self.tier}", result=what[:200])
        except Exception:  # noqa: BLE001
            log.debug("audit srvperms impossible", exc_info=True)

    def _apply_soon(self):
        prov = self.bot.get_cog("Provision")
        if prov is None:
            return
        bg.spawn(prov.enforce_server_perms(self.server),
                 name=f"srvperms:apply:{self.server}", logger=log)

    async def _on_tier(self, itx: discord.Interaction):
        v = (itx.data.get("values") or [self.tier])[0]
        if v in srvperms.TIERS:
            self.tier = v
        await self._refresh(itx)

    async def _on_caps(self, itx: discord.Interaction):
        allowed = set(itx.data.get("values") or [])
        srvperms.set_caps(self.bot.state, self.server, self.tier, allowed)
        self._audit(itx, "caps=" + ",".join(sorted(allowed)))
        self._apply_soon()          # G peut (ne plus) lancer des commandes -> overwrites
        await self._refresh(itx)

    async def _on_hidden(self, itx: discord.Interaction):
        from ..core import channels as _ch
        wanted = set()
        for v in (itx.data.get("values") or []):
            try:
                wanted.add(int(v))
            except (TypeError, ValueError):
                continue
        mine = _ch.server_channel_ids(self.bot, self.guild, self.server)
        foreign = wanted - mine
        wanted &= mine
        srvperms.set_hidden(self.bot.state, self.server, self.tier, wanted)
        self._audit(itx, "hidden=" + ",".join(str(c) for c in sorted(wanted)))
        self._apply_soon()
        self._build()
        await itx.response.edit_message(embed=self.embed(), view=self)
        if foreign:
            await itx.followup.send(
                f"⚠️ {len(foreign)} salon(s) ignoré(s) : ils n'appartiennent pas à "
                f"**{self.server}** (on ne règle que les salons de son propre serveur).",
                ephemeral=True)

    async def _on_reset(self, itx: discord.Interaction):
        srvperms.reset(self.bot.state, self.server, self.tier)
        self._audit(itx, "reset")
        self._apply_soon()
        await self._refresh(itx)

    async def _on_close(self, itx: discord.Interaction):
        for c in self.children:
            c.disabled = True
        await itx.response.edit_message(view=self)
        self.stop()

    async def on_timeout(self):
        for c in self.children:
            c.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
