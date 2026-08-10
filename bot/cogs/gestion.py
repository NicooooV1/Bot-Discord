"""Cog /gestion — groupes de gestion par serveur (3 tiers G/M/O) + réconciliation.

Modèle (Nico 2026-07-16), PAR SERVEUR relié au bot :
- **G** (gestion)   : VISUALISER les salons du serveur (pas de boutons, pas de Lock).
- **M** (modération): tout faire sur ce serveur (boutons + catégorie Lock).
- **O** (owner)     : aucune restriction (sauf 2FA) + peut DÉLÉGUER via /gestion.

Chaque niveau = UN rôle Discord (« G <srv> » / « M <srv> » / « O <srv> ») dont les
overwrites + les gardes runtime portent tout l'accès du tier. Le rôle est accordé
UNIQUEMENT si la personne est (a) dans le groupe du serveur ET (b) inscrite au 2FA.

DÉLÉGATION (/gestion add|remove|list) :
- Le PROPRIÉTAIRE (guild owner / ADMIN_IDS) accorde/retire G, M et O sur tout serveur.
- Un porteur de « O <srv> » accorde/retire G et M sur SON serveur (pas O).
- Personne d'autre.
Commande : /gestion add (membre) (serveur) (niveau, défaut G).

La réconciliation (boucle 3 min + après chaque add/remove + au démarrage) est la SEULE
source de vérité : elle POSE/RETIRE les rôles pour que l'état Discord = état voulu, même
si quelqu'un bricole un rôle à la main. Elle passe par l'API REST (add_role/remove_role
par id) donc ne dépend PAS de l'intent GUILD_MEMBERS pour agir ; la LISTE des membres est
lue en REST (intent « Server Members » activé côté portail — vérifié).

SESSION 2FA (Nico 2026-07-17, ÉTENDU 2026-07-18 à TOUS les niveaux) : « 2FA Complet »
ET les rôles de niveau G/M/O — SUR TOUS LES SERVEURS reliés au bot — ne restent posés
que tant que la session 2FA est ACTIVE (session expirée/fermée -> rôles retirés, donc
plus AUCUN salon de gestion visible d'aucun serveur ; re-déverrouillage -> rôles remis
en quelques secondes via TwoFA.on_change). Règle uniforme, aucune exception par niveau
ni par serveur : « aucun accès visuel sans 2FA » (Nico). Les rôles manuels de Nico
(« A », « R »…) ne sont JAMAIS touchés : _set_role refuse tout rôle hors du périmètre
géré (garde-fou codé en dur).
"""
import asyncio
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..core.permissions import is_guild_owner

log = logging.getLogger("discord-bot.gestion")

LEVELS = ("G", "M", "O")             # visualiser < modération < owner
LEVEL_KEY = {"G": "view", "M": "mod", "O": "owner"}   # niveau -> clé du rôle dans le registre
LEVEL_LABEL = {"G": "G — gestion (voir)", "M": "M — modération (tout faire)",
               "O": "O — owner"}
# migration des anciens niveaux -> nouveaux
_LEVEL_MIGRATE = {"gestion": "M", "lock": "O", "O": "O", "G": "G", "M": "M"}
RECONCILE_MIN = 3


class Gestion(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._lock = asyncio.Lock()

    async def cog_load(self):
        self._migrate_levels()
        # sessions 2FA : retrait/remise IMMÉDIATE des rôles de session (sinon la seule
        # boucle de 3 min laisserait « 2FA Complet »/« O » traîner après expiration)
        self._twofa_tasks = set()
        self.bot.twofa.on_change = self._on_twofa_change
        self.reconcile_loop.start()
        self.session_sweep_loop.start()

    def _migrate_levels(self):
        """Migre les anciens niveaux (gestion/lock) vers G/M/O. Idempotent."""
        groups = self._members()
        changed = False
        for mem in groups.values():
            for uid, lvl in list(mem.items()):
                new = _LEVEL_MIGRATE.get(lvl)
                if new and new != lvl:
                    mem[uid] = new
                    changed = True
        if changed:
            self.bot.state.set("gestion_members", groups)
            log.info("niveaux de gestion migrés vers G/M/O")

    def cog_unload(self):
        self.reconcile_loop.cancel()
        self.session_sweep_loop.cancel()
        # ⚠️ == et pas `is` : l'accès à self._on_twofa_change crée un NOUVEL objet méthode
        # liée à chaque fois — `is` serait toujours faux et on_change ne serait jamais
        # détaché (tâches fantômes après un unload/reload du cog).
        if getattr(self.bot.twofa, "on_change", None) == self._on_twofa_change:
            self.bot.twofa.on_change = None

    # ------------------------------------------------------------------ state
    def _members(self):
        # copie PROFONDE : dict(d) ne clone que le niveau haut ; muter un inner dict
        # toucherait state.d avant persistance (divergence mémoire/disque si _save échoue).
        d = self.bot.state.get("gestion_members", {})
        if not isinstance(d, dict):
            return {}
        return {srv: dict(mem) for srv, mem in d.items() if isinstance(mem, dict)}

    def _server_choices(self):
        return list(self.bot.cfg.gestion_servers.keys())

    def _role_id(self, server, level):
        """Id du rôle Discord pour (serveur, niveau)."""
        return self.bot.cfg.gestion_servers.get(server, {}).get(LEVEL_KEY[level], 0)

    def _managed_role_ids(self):
        """Tous les rôles gérés par le bot (G/M/O de chaque serveur + 2FA Complet)."""
        out = set()
        for roles in self.bot.cfg.gestion_servers.values():
            for k in ("view", "mod", "owner"):
                if roles.get(k):
                    out.add(roles[k])
        if self.bot.cfg.twofa_role_id:
            out.add(self.bot.cfg.twofa_role_id)
        return out

    # ------------------------------------------------------------------ délégation
    def _grantable(self, itx, server):
        """Ensemble des niveaux que l'invocateur peut accorder/retirer sur `server`.
        Propriétaire -> {G,M,O} ; porteur de « O <server> » -> {G,M} ; sinon vide."""
        cfg = self.bot.cfg
        if is_guild_owner(itx) or (cfg.admin_ids and itx.user.id in cfg.admin_ids):
            return {"G", "M", "O"}
        o = cfg.gestion_servers.get(server, {}).get("owner", 0)
        rids = {r.id for r in getattr(itx.user, "roles", [])}
        if o and o in rids:
            return {"G", "M"}
        return set()

    # ------------------------------------------------------------------ REST
    async def _guild_members(self):
        """Liste complète des membres via REST (pagination). Renvoie None en cas d'échec
        (on ne retire alors AUCUN rôle : ne jamais révoquer sur une lecture ratée)."""
        gid = self.bot.cfg.guild_id
        out, after = [], 0
        try:
            while True:
                chunk = await self.bot.http.get_members(gid, 1000, after)
                if not chunk:
                    break
                out.extend(chunk)
                if len(chunk) < 1000:
                    break
                after = int(chunk[-1]["user"]["id"])
            return out
        except Exception as e:  # noqa: BLE001
            log.warning("liste des membres indisponible: %s", e)
            return None

    async def _set_role(self, uid, rid, want, reason):
        """Ajoute (want=True) ou retire (want=False) un rôle par id. Chaque changement
        RÉUSSI est AUDITÉ (→ #journaux-live) pour qu'aucun mouvement ne soit silencieux."""
        if rid not in self._managed_role_ids():
            # GARDE-FOU : le bot ne pose/retire QUE ses rôles gérés (G/M/O par serveur +
            # « 2FA Complet »). Les rôles attribués à la main par Nico (« A », « R »…)
            # sont intouchables, quel que soit le chemin de code qui arrive ici.
            log.error("refus de toucher au rôle non géré %s (membre %s)", rid, uid)
            return False
        gid = self.bot.cfg.guild_id
        try:
            if want:
                await self.bot.http.add_role(gid, uid, rid, reason=reason)
            else:
                await self.bot.http.remove_role(gid, uid, rid, reason=reason)
        except discord.HTTPException as e:
            log.warning("rôle %s sur %s (%s): %s", rid, uid, "add" if want else "remove", e)
            return False
        try:
            g = self.bot.get_guild(gid)
            r = g.get_role(rid) if g else None
            self.bot.audit.record(user="réconciliation gestion/2FA",
                                  action="role-grant" if want else "role-revoke",
                                  target=f"{(r.name if r else rid)} → {uid} ({reason})",
                                  result="ok")
        except Exception:  # noqa: BLE001
            pass
        return True

    # ------------------------------------------------------------------ réconciliation
    async def _apply_member(self, m, groups):
        """Aligne les rôles d'UN membre (dict REST brut) sur l'état voulu :
        - rôle de niveau (G/M/O), sur CHAQUE serveur : dans le groupe + inscrit 2FA
          + session 2FA ACTIVE — les 3 conditions, POUR TOUS LES NIVEAUX (2026-07-18 :
          « aucun accès visuel sans 2FA », plus d'exception pour G/M ; expirée/fermée ->
          rôle retiré, salons de gestion invisibles ; remis en quelques secondes au
          prochain déverrouillage).
        - « 2FA Complet » : inscrit ET session active.
        Chaque membre n'a QUE le rôle de son niveau par serveur (les autres rôles gérés
        sont retirés). Les rôles hors périmètre (« A », « R »…) ne sont JAMAIS touchés."""
        cfg = self.bot.cfg
        tf = self.bot.twofa
        if m["user"].get("bot"):
            return
        uid = int(m["user"]["id"])
        have = {int(r) for r in m.get("roles", [])}
        enrolled = tf.enrolled(uid)
        # Break-glass TWOFA_ENABLED=false : miroir EXACT de GatedTree/session_2fa_ok.
        # Sans 2FA exigé, aucune exigence de session — sinon les rôles seraient dépouillés
        # en boucle alors que plus personne ne PEUT ouvrir de session (téléphone perdu +
        # codes de secours épuisés = le scénario même du break-glass).
        trusted = tf.trusted(uid) or not getattr(cfg, "twofa_enabled", False)
        R = "réconciliation gestion/2FA"

        # rôle voulu par serveur = celui du niveau, si (dans le groupe ET 2FA inscrit
        # ET session active) — TOUS niveaux confondus (2026-07-18, plus d'exception G/M)
        for srv, roles in cfg.gestion_servers.items():
            lvl = groups.get(srv, {}).get(str(uid))
            want = lvl in LEVELS and enrolled and trusted
            want_rid = self._role_id(srv, lvl) if want else 0
            srv_roles = {roles.get(k) for k in ("view", "mod", "owner") if roles.get(k)}
            # poser le rôle voulu AVANT de retirer les autres (pas de trou d'accès)
            if want_rid and want_rid not in have:
                if await self._set_role(uid, want_rid, True, R):
                    have.add(want_rid)
            # ⚠️ ne DÉPOUILLER les anciens rôles QUE si le rôle voulu est bien en place
            # (want_rid==0 = aucun rôle voulu -> on retire tout ; sinon on attend que
            # l'ajout ait réussi, sans quoi un échec 403/429 laisserait le membre SANS
            # aucun rôle, potentiellement de façon permanente).
            if want_rid == 0 or want_rid in have:
                for rid in srv_roles:
                    if rid != want_rid and rid in have:
                        if await self._set_role(uid, rid, False, R):
                            have.discard(rid)

        # rôle « 2FA Complet » = session 2FA ACTIVE (plus seulement « inscrit »)
        tr = cfg.twofa_role_id
        if tr:
            want_tr = enrolled and trusted
            if want_tr and tr not in have:
                await self._set_role(uid, tr, True, R)
            elif not want_tr and tr in have:
                await self._set_role(uid, tr, False, R)

    async def reconcile(self):
        """Aligne les rôles Discord de TOUS les membres (groupes × niveau × 2FA)."""
        tf = self.bot.twofa
        # 2FA store illisible -> enrolled() renverrait False pour TOUS = révocation de masse.
        if getattr(tf, "degraded", False):
            log.warning("réconciliation gelée : magasin 2FA dégradé (aucun rôle retiré)")
            return
        members = await self._guild_members()
        if members is None:
            return
        groups = self._members()          # {srv: {uid(str): niveau}}
        for m in members:
            await self._apply_member(m, groups)

    async def _reconcile_member_now(self, uid):
        """Réconciliation CIBLÉE d'un seul membre (déclenchée par TwoFA.on_change) : le
        rôle suit la session sans attendre la boucle. Échec silencieux acceptable — la
        boucle périodique reste le filet de sécurité."""
        if getattr(self.bot.twofa, "degraded", False):
            return
        # get_member SOUS le verrou : toutes les mutations de rôles se font sous _lock,
        # donc le snapshot lu ici ne peut pas être invalidé par un retrait/ajout concurrent
        # (sinon « want_rid not in have » sauterait la re-pose juste après un unlock).
        async with self._lock:
            try:
                m = await self.bot.http.get_member(self.bot.cfg.guild_id, uid)
            except discord.HTTPException:
                return      # membre parti / API indisponible : la boucle rattrapera
            except Exception as e:  # noqa: BLE001 — erreurs RÉSEAU (OSError/aiohttp),
                # pas des HTTPException : sans ce filet la tâche fire-and-forget mourrait
                # en « Task exception was never retrieved »
                log.warning("réconciliation ciblée %s : lecture membre impossible (%r)", uid, e)
                return
            try:
                await self._apply_member(m, self._members())
            except Exception:  # noqa: BLE001
                log.exception("réconciliation ciblée échouée pour %s", uid)

    def _on_twofa_change(self, uid):
        """Branché sur TwoFA.on_change (session ouverte/fermée/expirée, désinscription).
        Ne fait que PLANIFIER une tâche : jamais bloquant pour le déverrouillage appelant.
        Référence forte conservée (la boucle ne garde que des weakrefs sur les tâches)."""
        try:
            uid = int(uid)
        except (TypeError, ValueError):
            return
        try:
            t = asyncio.get_running_loop().create_task(self._reconcile_member_now(uid))
        except RuntimeError:
            return          # pas de boucle (arrêt en cours) : la réconciliation suffira
        self._twofa_tasks.add(t)
        t.add_done_callback(self._twofa_tasks.discard)

    @tasks.loop(minutes=RECONCILE_MIN)
    async def reconcile_loop(self):
        async with self._lock:
            try:
                await self.reconcile()
            except Exception:  # noqa: BLE001
                log.exception("réconciliation gestion échouée")

    @tasks.loop(seconds=30)
    async def session_sweep_loop(self):
        """Fait EXPIRER activement les sessions 2FA échues. expire_stale() déclenche
        on_change -> retrait immédiat de « 2FA Complet » et « O <srv> » ; sans balayage,
        l'expiration ne serait constatée que si l'utilisateur retentait une commande, ou
        au passage de la boucle de 3 min."""
        try:
            self.bot.twofa.expire_stale()
        except Exception:  # noqa: BLE001
            log.exception("balayage des sessions 2FA échoué")

    @reconcile_loop.before_loop
    @session_sweep_loop.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------ commandes
    group = app_commands.Group(
        name="gestion",
        description="Accès de gestion par serveur (propriétaire + porteurs de O)")

    async def _server_ac(self, itx: discord.Interaction, current: str):
        return [app_commands.Choice(name=s, value=s) for s in self._server_choices()
                if current.lower() in s.lower()][:25]

    @group.command(name="add", description="Donner un accès de gestion à un membre sur un serveur")
    @app_commands.describe(membre="Le membre à autoriser", serveur="Ex : R820",
                           niveau="G = voir · M = tout faire · O = owner (défaut : G)")
    @app_commands.choices(niveau=[app_commands.Choice(name=LEVEL_LABEL[l], value=l) for l in LEVELS])
    @app_commands.autocomplete(serveur=_server_ac)
    async def add(self, itx: discord.Interaction, membre: discord.Member, serveur: str,
                  niveau: Optional[str] = "G"):
        await itx.response.defer(ephemeral=True)
        cfg = self.bot.cfg
        if cfg.guild_id and itx.guild_id != cfg.guild_id:
            await itx.followup.send("Serveur non autorisé.", ephemeral=True)
            return
        if serveur not in cfg.gestion_servers:
            await itx.followup.send(
                f"⛔ Serveur inconnu « {serveur} ». Connus : {', '.join(self._server_choices()) or 'aucun'}.",
                ephemeral=True)
            return
        niveau = (niveau or "G").upper()
        if niveau not in LEVELS:
            niveau = "G"
        allowed = self._grantable(itx, serveur)
        # niveau VOULU autorisé ?
        if niveau not in allowed:
            await itx.followup.send(
                "⛔ Tu ne peux pas accorder ce niveau sur ce serveur." +
                (f" Tu peux accorder : {', '.join(sorted(allowed))}." if allowed else
                 " Réservé au propriétaire ou à un porteur de O."), ephemeral=True)
            return
        async with self._lock:
            groups = self._members()
            # niveau ACTUEL de la cible contrôlé SOUS le verrou (sinon TOCTOU : deux
            # /gestion concurrents liraient l'état ancien et un O pourrait rétrograder/
            # écraser un autre O fraîchement promu — contournement de la protection).
            current = groups.get(serveur, {}).get(str(membre.id))
            if current is not None and current not in allowed:
                await itx.followup.send(
                    f"⛔ **{membre.display_name}** est déjà niveau **{current}** — tu ne peux pas le "
                    "modifier (réservé au propriétaire).", ephemeral=True)
                return
            groups.setdefault(serveur, {})[str(membre.id)] = niveau
            self.bot.state.set("gestion_members", groups)
        self.bot.audit.record(user=f"{itx.user} ({itx.user.id})", action="gestion-add",
                              target=f"{serveur}/{membre.id}={niveau}", result="ok")
        enrolled = self.bot.twofa.enrolled(membre.id)
        if not enrolled:
            note = ("\n⚠️ Ce membre n'a **pas encore activé le 2FA** : le rôle ne sera accordé "
                    "qu'après son `/2fa setup` (dans #général).")
        elif (niveau == "O" and self.bot.cfg.twofa_enabled
              and not self.bot.twofa.trusted(membre.id)):
            note = ("\n⏳ Sa session 2FA est fermée : le rôle **O** ne sera posé qu'à son "
                    "prochain `/2fa unlock` (et retiré à chaque expiration de session).")
        else:
            note = ""
        await itx.followup.send(
            f"✅ **{membre.display_name}** — niveau **{niveau}** sur **{serveur}**.{note}",
            ephemeral=True)
        async with self._lock:
            await self.reconcile()

    @group.command(name="remove", description="Retirer l'accès de gestion d'un membre sur un serveur")
    @app_commands.describe(membre="Le membre à retirer", serveur="Ex : R820")
    @app_commands.autocomplete(serveur=_server_ac)
    async def remove(self, itx: discord.Interaction, membre: discord.Member, serveur: str):
        await itx.response.defer(ephemeral=True)
        cfg = self.bot.cfg
        if cfg.guild_id and itx.guild_id != cfg.guild_id:
            await itx.followup.send("Serveur non autorisé.", ephemeral=True)
            return
        allowed = self._grantable(itx, serveur)
        async with self._lock:
            groups = self._members()
            # lecture + contrôle + mutation dans la MÊME section critique (TOCTOU sinon,
            # cf. add) ; on ne peut retirer un membre que d'un niveau qu'on aurait pu accorder
            cur = groups.get(serveur, {}).get(str(membre.id))
            if cur is None:
                await itx.followup.send(
                    f"ℹ️ **{membre.display_name}** n'a aucun accès sur **{serveur}**.", ephemeral=True)
                return
            if cur not in allowed:
                await itx.followup.send(
                    f"⛔ Tu ne peux pas retirer un membre de niveau **{cur}** (réservé au propriétaire).",
                    ephemeral=True)
                return
            groups.get(serveur, {}).pop(str(membre.id), None)
            self.bot.state.set("gestion_members", groups)
        self.bot.audit.record(user=f"{itx.user} ({itx.user.id})", action="gestion-remove",
                              target=f"{serveur}/{membre.id}", result="ok")
        # révocation DIRECTE des 3 rôles du serveur (ne pas dépendre du seul reconcile)
        roles = cfg.gestion_servers.get(serveur, {})
        ok = True
        for k in ("owner", "mod", "view"):
            if roles.get(k):
                ok = await self._set_role(membre.id, roles[k], False, "gestion remove") and ok
        msg = (f"✅ **{membre.display_name}** retiré de **{serveur}** — rôles révoqués."
               if ok else
               f"⚠️ **{membre.display_name}** retiré de **{serveur}**, mais une révocation a "
               "échoué (réessai automatique à la réconciliation).")
        await itx.followup.send(msg, ephemeral=True)
        async with self._lock:
            await self.reconcile()

    @group.command(name="list", description="Lister les accès de gestion")
    @app_commands.describe(serveur="Filtrer sur un serveur (facultatif)")
    @app_commands.autocomplete(serveur=_server_ac)
    async def list_(self, itx: discord.Interaction, serveur: Optional[str] = None):
        is_owner = is_guild_owner(itx) or (self.bot.cfg.admin_ids and itx.user.id in self.bot.cfg.admin_ids)
        # serveurs que l'invocateur peut GÉRER (le propriétaire les voit tous)
        manageable = self._server_choices() if is_owner else [
            s for s in self._server_choices() if self._grantable(itx, s)]
        if not manageable:
            await itx.response.send_message(
                "⛔ Réservé au propriétaire ou à un porteur de O.", ephemeral=True)
            return
        groups = self._members()
        emb = discord.Embed(title="👥 Accès de gestion", color=0x5865F2)
        # un O ne voit QUE ses serveurs (pas de fuite inter-serveurs)
        if serveur:
            keys = [serveur] if serveur in manageable else []
        else:
            keys = manageable
        for srv in keys:
            mem = groups.get(srv, {})
            if not mem:
                emb.add_field(name=srv, value="_(personne)_", inline=False)
                continue
            lines = []
            for uid, lvl in sorted(mem.items(), key=lambda kv: LEVELS.index(kv[1]) if kv[1] in LEVELS else 9):
                tf = self.bot.twofa
                if not tf.enrolled(int(uid)):
                    enr = "⏳ pas de 2FA"
                elif tf.trusted(int(uid)):
                    enr = "🔓 session active"
                else:
                    enr = "🔐 verrouillé" + (" — O suspendu" if lvl == "O" else "")
                lines.append(f"• <@{uid}> — **{lvl}** {enr}")
            emb.add_field(name=srv, value="\n".join(lines)[:1024], inline=False)
        emb.set_footer(text="G voir · M tout faire · O owner (rôle O + 2FA Complet posés "
                            "seulement pendant une session 2FA active)")
        await itx.response.send_message(embed=emb, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Gestion(bot))
