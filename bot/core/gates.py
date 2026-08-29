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
        gate_cap = "services"             # capacité srvperms exigée (Owner-réglable)

    class CtControlView(GatedView):
        gate = "mod"
        gate_caps = {"ctchannels:stop": "stop", "ctchannels:start": "start", …}

    class UnlockView(GatedView):
        gate = None                       # exemption EXPLICITE...
        gate_reason = "c'est le bouton de déverrouillage 2FA lui-même"

Oublier la porte devient impossible : `__init_subclass__` refuse une valeur de `gate`
inconnue, et exige une justification écrite pour toute exemption. `tests/test_edmine.py`
échoue si une `discord.ui.View` n'hérite pas de `GatedView`.

TIERS DISPONIBLES
  "read"  — lecture (M/O du serveur, G si « read » ouvert, READ_ROLE_IDS) + session 2FA
  "mod"   — actions (rôle M/O du serveur visé, ou break-glass) + session 2FA
  "owner" — catégorie Lock / nœud (rôle M ou O du serveur du nœud) + session 2FA
  None    — aucune porte, avec `gate_reason` obligatoire

SERVEUR (audit 2026-08-29) : `resolve_server()` renvoie la clé GESTION_SERVERS que la
porte exige. `None` signifie « le serveur de CETTE instance » (SERVER_KEY = R820) — et
plus jamais « n'importe quel rôle global » : un M AVY-NAS ne clique pas un panneau R820.

CAPACITÉS : `gate_cap` (toute la vue) ou `gate_caps` ({custom_id: cap}) désignent la
capacité srvperms exigée ; l'Owner du serveur les ouvre/ferme par tier via /gestion perms.
"""
import logging

import discord

from .permissions import cap_label, deny_2fa, gate_allows, log_refusal, session_2fa_ok

log = logging.getLogger("discord-bot.gates")

VALID_GATES = ("read", "mod", "owner", None)


class GatedView(discord.ui.View):
    """Vue dont l'autorisation est déclarée, pas réimplémentée.

    Attributs de classe (surchargeables par instance dans `__init__`) :
      gate          — "read" | "mod" | "owner" | None
      gate_server   — clé GESTION_SERVERS bornant le tier (ex. « AVY-NAS »). None = R820.
      gate_user_id  — si posé, seul cet utilisateur peut cliquer (vues éphémères).
      gate_cap      — capacité srvperms exigée pour toute la vue (None = aucune).
      gate_caps     — {custom_id: capacité} pour des boutons de capacités différentes.
      gate_reason   — obligatoire quand gate vaut None.

    Pour un serveur qui dépend de l'interaction (salon d'invité Aveyron), surcharger
    `resolve_server()` plutôt que de figer `gate_server`.
    """

    gate = "mod"
    gate_server = None
    gate_user_id = None
    gate_cap = None
    gate_caps = {}
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

    def resolve_cap(self, interaction):
        """Capacité exigée pour CE clic : `gate_caps[custom_id]`, sinon `gate_cap`."""
        cid = (getattr(interaction, "data", None) or {}).get("custom_id")
        if cid and cid in (self.gate_caps or {}):
            return self.gate_caps[cid]
        return self.gate_cap

    async def on_denied(self, interaction, why, cap=None):
        """Réponse au refus. `why` ∈ {"user", "role", "2fa", "cap"}. Surchargeable."""
        if why == "2fa":
            await deny_2fa(interaction)
            return
        srv = await self.resolve_server(interaction)
        cfg = getattr(interaction.client, "cfg", None)
        srv = srv or getattr(cfg, "server_key", "R820")
        if why == "user":
            msg = "🔒 Ces boutons appartiennent à la personne qui a lancé la commande."
        elif why == "cap":
            msg = (f"🔒 Capacité **{cap_label(cap)}** non accordée à ton niveau sur "
                   f"**{srv}** (réglable par l'Owner du serveur : `/gestion perms`).")
        else:
            tier = {"read": f"aux membres autorisés sur **{srv}**",
                    "mod": f"aux gestionnaires de **{srv}** (rôle M ou O)",
                    "owner": "aux rôles **M** ou **O** du nœud (ou au propriétaire)"}
            msg = f"🔒 Action réservée {tier.get(self.gate, 'aux gestionnaires')}."
            if cap:
                msg += " L'Owner du serveur peut l'ouvrir à ton niveau via `/gestion perms`."
        if why in ("role", "cap", "user"):
            log_refusal(interaction, why, f"{type(self).__name__} serveur={srv}"
                        + (f" cap={cap}" if cap else ""))
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

        # 3) tier + capacité, en UNE décision (permissions.gate_allows), bornée au
        #    SERVEUR de la vue (None = serveur de cette instance ; « owner » = serveur du
        #    nœud). Une capacité accordée à G par l'Owner ouvre le bouton à G — avant le
        #    29/08 au soir, le test « M/O ? » précédait la capacité et G était toujours
        #    refusé (« REFUS [role] DlRefreshView »), quoi que l'Owner ait coché.
        srv = None
        cap = None
        try:
            srv = await self.resolve_server(interaction)
            if self.gate == "owner":
                srv = srv or getattr(cfg, "node_server_key", None)
            cap = self.resolve_cap(interaction)
            ok, why, _tier = gate_allows(cfg, interaction, server=srv, gate=self.gate,
                                         cap=cap)
        except Exception:  # noqa: BLE001 — une erreur d'évaluation = refus, jamais accès
            log.exception("%s: évaluation de la porte en échec — refus",
                          type(self).__name__)
            ok, why = False, "role"
        if not ok:
            await self.on_denied(interaction, why or "role", cap=cap)
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
