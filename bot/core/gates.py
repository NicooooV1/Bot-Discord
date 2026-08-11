"""Porte d'autorisation OBLIGATOIRE des vues (boutons, menus, modales).

POURQUOI CE MODULE EXISTE
-------------------------
`GatedTree` (bot/__main__.py) barre toutes les commandes slash derrière le 2FA. Il ne
couvre PAS les interactions de composants : un clic de bouton ne passe jamais par
`CommandTree.interaction_check`. La protection des boutons reposait donc sur une
convention — « chaque vue doit se souvenir d'appeler la bonne porte » — et l'audit du
2026-08-11 a mesuré le résultat : 23 classes `View`, 12 `interaction_check`, 3 patrons
concurrents, et 12 vues sans aucune porte (dont le panneau `/docker`, la console root et
les boutons de `#ratio`).

Ici la porte devient une **donnée déclarée**, pas une décision reprise à chaque fichier :

    class DockerPanelView(GatedView):
        gate = "mod"                      # rôle M/O du serveur + session 2FA

    class RatioRefreshView(GatedView):
        gate = "read"                     # simple lecture + session 2FA

    class UnlockView(GatedView):
        gate = None                       # exemption EXPLICITE...
        gate_reason = "c'est le bouton de déverrouillage 2FA lui-même"

Oublier la porte devient impossible : `__init_subclass__` refuse une valeur de `gate`
inconnue, et exige une justification écrite pour toute exemption. `tests/test_gates.py`
échoue si une `discord.ui.View` n'hérite pas de `GatedView`.

TIERS DISPONIBLES
  "read"  — lecture (rôle READ_ROLE_IDS ou mieux) + session 2FA
  "mod"   — actions (rôle M/O du serveur visé, ou break-glass) + session 2FA
  "owner" — catégorie Lock / nœud (rôle M ou O du serveur du nœud) + session 2FA
  None    — aucune porte, avec `gate_reason` obligatoire

⚠️ Choisir le BON tier. Mettre "mod" partout casserait l'accès légitime du tier lecture
aux boutons de `#ratio`, `/yt` ou `/logs` — c'est l'erreur que l'audit a explicitement
signalée.
"""
import logging

import discord

from .permissions import (can_read, deny_2fa, is_admin, may_lock, session_2fa_ok)

log = logging.getLogger("discord-bot.gates")

VALID_GATES = ("read", "mod", "owner", None)


class GatedView(discord.ui.View):
    """Vue dont l'autorisation est déclarée, pas réimplémentée.

    Attributs de classe (surchargeables par instance dans `__init__`) :
      gate          — "read" | "mod" | "owner" | None
      gate_server   — clé GESTION_SERVERS bornant le tier (ex. « AVY-PVE »). None = R820.
      gate_user_id  — si posé, seul cet utilisateur peut cliquer (vues éphémères).
      gate_reason   — obligatoire quand gate vaut None.

    Pour un serveur qui dépend de l'interaction (salon d'invité Aveyron), surcharger
    `resolve_server()` plutôt que de figer `gate_server`.
    """

    gate = "mod"
    gate_server = None
    gate_user_id = None
    gate_reason = ""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.gate not in VALID_GATES:
            raise TypeError(
                f"{cls.__name__}.gate = {cls.gate!r} — attendu parmi {VALID_GATES}")
        if cls.gate is None and not cls.gate_reason:
            raise TypeError(
                f"{cls.__name__} désactive la porte (gate=None) sans `gate_reason`. "
                "Toute exemption doit être justifiée par écrit.")

    # ------------------------------------------------------------------ à surcharger
    async def resolve_server(self, interaction):
        """Clé GESTION_SERVERS à exiger pour CETTE interaction. None = serveur primaire."""
        return self.gate_server

    async def on_denied(self, interaction, why):
        """Réponse au refus. `why` ∈ {"user", "role", "2fa"}. Surchargeable."""
        if why == "2fa":
            await deny_2fa(interaction)
            return
        if why == "user":
            msg = "🔒 Ces boutons appartiennent à la personne qui a lancé la commande."
        else:
            srv = await self.resolve_server(interaction)
            tier = {"read": "aux membres autorisés",
                    "mod": "aux gestionnaires (rôle Gestion"
                           + (f" {srv}" if srv else "") + ")",
                    "owner": "aux rôles **M** ou **O** (ou au propriétaire)"}
            msg = f"🔒 Action réservée {tier.get(self.gate, 'aux gestionnaires')}."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass

    # ------------------------------------------------------------------ la porte
    async def interaction_check(self, interaction) -> bool:
        # 1) propriété de la vue (vues éphémères réservées au lanceur)
        uid = self.gate_user_id
        if uid and interaction.user.id != uid:
            await self.on_denied(interaction, "user")
            return False

        if self.gate is None:
            return True

        cfg = getattr(interaction.client, "cfg", None)
        if cfg is None:                       # bot mal initialisé : fail-CLOSED
            await self.on_denied(interaction, "role")
            return False

        # 2) défense en profondeur : jamais depuis un autre guild
        if getattr(cfg, "guild_id", 0) and interaction.guild_id != cfg.guild_id:
            await self.on_denied(interaction, "role")
            return False

        # 3) tier
        try:
            if self.gate == "read":
                ok = can_read(cfg, interaction)
            elif self.gate == "owner":
                ok = may_lock(cfg, interaction)
            else:
                srv = await self.resolve_server(interaction)
                ok = is_admin(cfg, interaction, server=srv)
        except Exception:  # noqa: BLE001 — une erreur d'évaluation = refus, jamais accès
            log.exception("%s: évaluation de la porte en échec — refus",
                          type(self).__name__)
            ok = False
        if not ok:
            await self.on_denied(interaction, "role")
            return False

        # 4) session 2FA — les boutons ne passent pas par GatedTree, c'est ICI que le
        #    2FA s'applique à eux. Une session expirée doit fermer un panneau déjà posté.
        if not session_2fa_ok(interaction):
            await self.on_denied(interaction, "2fa")
            return False
        return True


class OwnerGatedView(GatedView):
    """Vue réservée au PROPRIÉTAIRE du guild (ou ADMIN_IDS), sans repli sur les rôles.

    Pour les surfaces où le modèle documenté dit « propriétaire uniquement » et où aucun
    rôle Discord ne doit ouvrir la porte (console root de l'hyperviseur).
    """

    gate = "owner"

    async def interaction_check(self, interaction) -> bool:
        cfg = getattr(interaction.client, "cfg", None)
        owner = (cfg is not None
                 and (getattr(getattr(interaction, "guild", None), "owner_id", None)
                      == interaction.user.id
                      or interaction.user.id in (getattr(cfg, "admin_ids", None) or ())))
        if not owner:
            await self.on_denied(interaction, "role")
            return False
        if not session_2fa_ok(interaction):
            await self.on_denied(interaction, "2fa")
            return False
        return True
