"""Sondages persistants — `/sondage creer|fermer|resultats|liste` + boutons de vote (2026-08-30).

POURQUOI UN SONDAGE « MAISON » ALORS QUE DISCORD EN PROPOSE
-----------------------------------------------------------
Discord a des sondages natifs (menu « + » du salon). On garde quand même les nôtres :
  - PERSISTANCE dans l'état du bot (`bot.state["sondages"]`) : le sondage, ses votes
    et son résultat restent consultables (`/sondage resultats`, `/sondage liste`) bien
    après la fermeture, même si le message a été supprimé ;
  - AUDIT : création, fermeture (manuelle ou programmée) passent par `bot.audit`, comme
    toute action du bot ;
  - DURÉES LIBRES (« 1j12h », « 30min », ou pas de fin du tout) et fermeture par le
    créateur ou un M/O, là où le natif est borné et figé ;
  - RÉSULTATS ré-affichables et gagnants marqués (🏆) ;
  - contrôle qui peut CRÉER (rôles du bot) sans restreindre qui peut VOTER.

Idée reprise d'« Ultra Suite » (`/poll` + `poll-buttons.js`) en corrigeant ses défauts :
votes réécrits dans un blob JSON par option (ici : PAR (sondage, utilisateur), sous un
verrou `asyncio.Lock` par sondage), durée et anonymat jamais honorés (ici : clôture
programmée réelle par boucle, anonymat appliqué à l'affichage ET à « Qui a voté ? »),
`end` ouvert à n'importe qui (ici : créateur ou tier M/O).

CE QUE FAIT CE COG
------------------
- `/sondage creer question:<str> options:<"a | b | c"> [multiple] [duree] [anonyme] [salon]`
    2 à 10 options (séparateur « | »), libellés tronqués à 80 caractères, un bouton par
    option (`custom_id` = `sondage:<id>:<n>`, persistant), embed avec barres `█░` sur 20
    segments, pourcentage et nombre de voix. Sans `duree` : ouvert jusqu'à `/sondage fermer`.
- Vote = TOGGLE (recliquer retire la voix) ; en choix unique, un nouveau choix REMPLACE
  l'ancien ; en choix multiple, chaque option se coche/décoche indépendamment.
- `anonyme` : on n'affiche JAMAIS qui a voté ; sinon un bouton « Qui a voté ? »
  (réponse éphémère) réservé au créateur et aux tiers M/O.
- `/sondage fermer id` (créateur ou M/O), `/sondage resultats id`, `/sondage liste`.
- Boucle `cloture` (30 s) : ferme les sondages échus. `ferme=True` est posé et SAUVÉ
  AVANT l'édition du message : au redémarrage, un sondage déjà fermé n'est pas refermé
  (pas de second audit, pas de doublon) ; si l'édition avait échoué, elle est retentée
  (`rendu_final` absent) — idempotent dans les deux sens.
- Au démarrage, les vues des sondages OUVERTS sont ré-enregistrées
  (`bot.add_view(view, message_id=…)`) : les boutons survivent aux redémarrages.

PERMISSIONS
-----------
- Créer / fermer / lister : `read_check()` — rôles G/M/O du serveur du salon (un
  sondage n'est pas une action d'infra, mais c'est le bot qui poste : on garde la porte
  de lecture pour ne pas en faire un outil de spam).
- VOTER : `gate = None` (avec `gate_reason`) — un sondage est fait pour TOUS les membres
  du serveur, y compris ceux sans rôle du bot. Le guild est quand même vérifié
  (`cfg.guild_id`) : jamais depuis un autre serveur. Voter n'exige donc pas le 2FA.

CE QUE CE COG NE FAIT PAS
-------------------------
- Pas de pseudo : sans intent `members`, « Qui a voté ? » affiche des mentions `<@id>`
  (rendues côté client) et le dit.
- Pas de modification d'un sondage après création (question/options) : on le ferme et
  on en refait un.
- Ne supprime jamais un message de sondage ; la fermeture retire seulement les boutons.

PIÈGES CONNUS
-------------
- `discord.ui.View.__init__` exige une boucle asyncio en cours : les vues sont créées
  dans `cog_load` (pas dans `__init__`), et dans les tests sous `asyncio.run`.
- `bot.add_view(view, message_id=…)` n'accepte que des vues persistantes : `timeout=None`
  et un `custom_id` sur CHAQUE bouton (y compris « Qui a voté ? »).
- Une question/option est un texte utilisateur : backticks neutralisés et
  `AllowedMentions.none()` partout.
"""
import asyncio
import logging
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..core import bg
from ..core.format import BLURPLE, GREY
from ..core.gates import GatedView
from ..core.permissions import channel_server, read_check, tier_of
from .rappel import fmt_delai, parse_delai

log = logging.getLogger("discord-bot.sondage")

CLE_ETAT = "sondages"
TICK_SECONDES = 30
MIN_OPTIONS, MAX_OPTIONS = 2, 10
LABEL_MAX = 80
QUESTION_MAX = 250
BARRE = 20
EMOJIS = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟")
DUREE_MIN = 60
DUREE_MAX_JOURS_DEFAUT = 30
MAX_OUVERTS_DEFAUT = 20
RENDU_ESSAIS_MAX = 5


def _now() -> float:
    return time.time()


def _clean(text: str, n: int) -> str:
    return " ".join((text or "").replace("`", "'").split())[:n]


# --------------------------------------------------------------------------- logique pure
def parse_options(raw: str) -> list:
    """« a | b | c » → libellés nettoyés (≤ 80 car., vides ignorés). Lève ValueError si
    moins de 2 ou plus de 10."""
    opts = [_clean(o, LABEL_MAX) for o in (raw or "").split("|")]
    opts = [o for o in opts if o]
    if not MIN_OPTIONS <= len(opts) <= MAX_OPTIONS:
        raise ValueError(len(opts))
    return opts


def appliquer_vote(sondage: dict, user_id, idx: int) -> str:
    """Modifie `sondage["votes"][user]` en place. Renvoie « ajoute », « retire » ou
    « remplace ». Choix unique : un autre choix remplace ; le même se retire (toggle).
    Choix multiple : chaque option bascule indépendamment."""
    votes = sondage.setdefault("votes", {})
    key = str(user_id)
    cur = list(votes.get(key, []))
    if idx in cur:
        cur.remove(idx)
        action = "retire"
    elif sondage.get("multiple"):
        cur.append(idx)
        action = "ajoute"
    else:
        action = "remplace" if cur else "ajoute"
        cur = [idx]
    if cur:
        votes[key] = sorted(cur)
    else:
        votes.pop(key, None)
    return action


def decompte(sondage: dict) -> list:
    """Nombre de voix par option (index = option)."""
    n = [0] * len(sondage.get("options", []))
    for choix in (sondage.get("votes") or {}).values():
        for i in choix:
            if 0 <= i < len(n):
                n[i] += 1
    return n


def gagnants(sondage: dict) -> set:
    n = decompte(sondage)
    top = max(n) if n else 0
    return {i for i, c in enumerate(n) if c == top and top > 0}


def barre(count: int, base: int) -> str:
    pct = round(100 * count / base) if base else 0
    plein = round(pct / 100 * BARRE)
    return "█" * plein + "░" * (BARRE - plein) + f" {pct}%"


def est_echu(sondage: dict, now: float) -> bool:
    fin = sondage.get("fin_a")
    return (not sondage.get("ferme")) and fin is not None and float(fin) <= now


def build_embed(sondage: dict) -> discord.Embed:
    """Embed du sondage (ouvert ou fermé). Les pourcentages sont rapportés au nombre de
    VOTANTS (en choix multiple la somme des voix dépasse 100 %)."""
    n = decompte(sondage)
    votants = len(sondage.get("votes") or {})
    ferme = bool(sondage.get("ferme"))
    win = gagnants(sondage) if ferme else set()
    lignes = []
    for i, label in enumerate(sondage.get("options", [])):
        coupe = " 🏆" if i in win else ""
        voix = f"{n[i]} voix" if n[i] != 1 else "1 voix"
        lignes.append(f"{EMOJIS[i]} **{label}**{coupe}\n`{barre(n[i], votants)}` · {voix}")
    titre = ("📊 " if not ferme else "📊 [Terminé] ") + sondage.get("question", "")
    emb = discord.Embed(title=titre[:256], description="\n".join(lignes),
                        color=GREY if ferme else BLURPLE)
    pied = [f"id {sondage.get('id')}",
            "choix multiple" if sondage.get("multiple") else "choix unique",
            "anonyme" if sondage.get("anonyme") else "public",
            f"{votants} votant{'s' if votants != 1 else ''}"]
    emb.set_footer(text=" · ".join(pied))
    fin = sondage.get("fin_a")
    if ferme:
        emb.add_field(name="Clôture", value=(f"<t:{int(sondage.get('ferme_a') or 0)}:R> "
                                             f"({sondage.get('ferme_par') or 'indisponible'})"))
    elif fin:
        emb.add_field(name="Fin", value=f"<t:{int(fin)}:R> (<t:{int(fin)}:f>)")
    else:
        emb.add_field(name="Fin", value="à la fermeture manuelle")
    if sondage.get("createur_id"):
        emb.add_field(name="Par", value=f"<@{sondage['createur_id']}>")
    return emb


# --------------------------------------------------------------------------- vue
class SondageView(GatedView):
    """Boutons de vote d'UN sondage (persistants). Porte volontairement ouverte : un
    sondage s'adresse à tous les membres du serveur, voter n'est pas une action d'infra
    et n'exige ni rôle du bot ni 2FA. Le guild reste vérifié dans `interaction_check`.
    Le bouton « Qui a voté ? » a sa propre porte (créateur ou M/O) dans son callback."""

    gate = None
    gate_reason = ("voter à un sondage est ouvert à tous les membres du serveur — ce n'est "
                   "pas une action d'infrastructure ; le guild est vérifié, le bouton "
                   "« Qui a voté ? » vérifie lui-même créateur/M/O")

    def __init__(self, cog, sondage: dict):
        super().__init__(timeout=None)
        self.cog = cog
        self.sid = str(sondage["id"])
        for i, label in enumerate(sondage.get("options", [])):
            b = discord.ui.Button(label=label[:80], emoji=EMOJIS[i],
                                  style=discord.ButtonStyle.secondary,
                                  custom_id=f"sondage:{self.sid}:{i}", row=i // 5)
            b.callback = self._voter(i)
            self.add_item(b)
        if not sondage.get("anonyme"):
            b = discord.ui.Button(label="Qui a voté ?", emoji="👥",
                                  style=discord.ButtonStyle.primary,
                                  custom_id=f"sondage:{self.sid}:qui", row=2)
            b.callback = self._qui
            self.add_item(b)

    async def interaction_check(self, interaction) -> bool:
        cfg = getattr(interaction.client, "cfg", None)
        gid = getattr(cfg, "guild_id", 0)
        if gid and getattr(interaction, "guild_id", None) != gid:
            return False                      # autre serveur : silence, comme les autres portes
        return await super().interaction_check(interaction)

    def _voter(self, idx):
        async def cb(interaction):
            await self.cog.voter(interaction, self.sid, idx)
        return cb

    async def _qui(self, interaction):
        await self.cog.qui_a_vote(interaction, self.sid)


# --------------------------------------------------------------------------- cog
class Sondage(commands.Cog):
    """Sondages persistants à boutons, clôture programmée, résultats consultables."""

    sondage = app_commands.Group(name="sondage", description="Sondages à boutons")

    def __init__(self, bot):
        self.bot = bot
        self.cfg = bot.cfg
        self._sondages = dict(bot.state.get(CLE_ETAT, {}) or {})
        self._verrous = {}                    # id -> asyncio.Lock
        self._vues = {}                       # id -> SondageView enregistrée
        self.duree_max = int(getattr(self.cfg, "sondage_duree_max_jours",
                                     DUREE_MAX_JOURS_DEFAUT) or DUREE_MAX_JOURS_DEFAUT) * 86400
        self.max_ouverts = int(getattr(self.cfg, "sondage_max_ouverts", MAX_OUVERTS_DEFAUT)
                               or MAX_OUVERTS_DEFAUT)

    async def cog_load(self):
        self.enregistrer_vues()
        bg.guard_cog_loops(self, log)
        self.cloture.start()

    async def cog_unload(self):
        self.cloture.cancel()

    # ------------------------------------------------------------------ état
    def _save(self):
        self.bot.state.set(CLE_ETAT, self._sondages)

    def _verrou(self, sid) -> asyncio.Lock:
        return self._verrous.setdefault(str(sid), asyncio.Lock())

    def _nouvel_id(self) -> str:
        n = 0
        for sid in self._sondages:
            try:
                n = max(n, int(sid))
            except ValueError:
                continue
        return str(n + 1)

    def _audit(self, user, action, target, result):
        audit = getattr(self.bot, "audit", None)
        if audit is not None:
            audit.record(user=user, action=action, target=target, result=result)

    def _est_mod(self, itx) -> bool:
        return tier_of(self.cfg, itx, channel_server(itx)) in ("M", "O")

    def _peut_gerer(self, itx, s) -> bool:
        return itx.user.id == s.get("createur_id") or self._est_mod(itx)

    def enregistrer_vues(self) -> int:
        """Ré-enregistre les vues des sondages OUVERTS (boutons persistants). À appeler
        sous une boucle asyncio. Renvoie le nombre de vues enregistrées."""
        n = 0
        for sid, s in self._sondages.items():
            if s.get("ferme") or not s.get("message_id"):
                continue
            view = SondageView(self, s)
            self.bot.add_view(view, message_id=int(s["message_id"]))
            self._vues[str(sid)] = view
            n += 1
        if n:
            log.info("sondage: %d vue(s) persistante(s) ré-enregistrée(s)", n)
        return n

    # ------------------------------------------------------------------ message
    async def _message(self, s):
        ch = self.bot.get_channel(int(s.get("salon_id") or 0))
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(int(s.get("salon_id") or 0))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
                return None
        try:
            return await ch.fetch_message(int(s["message_id"]))
        except (discord.NotFound, discord.Forbidden):
            return None
        except discord.HTTPException as e:
            log.warning("sondage %s : message illisible (%s)", s.get("id"), e)
            return None

    async def _rafraichir(self, s, view=None, retirer_boutons=False) -> bool:
        msg = await self._message(s)
        if msg is None:
            return False
        try:
            if retirer_boutons:
                await msg.edit(embed=build_embed(s), view=None)
            elif view is not None:
                await msg.edit(embed=build_embed(s), view=view)
            else:
                await msg.edit(embed=build_embed(s))
            return True
        except discord.HTTPException as e:
            log.warning("sondage %s : édition échouée (%s)", s.get("id"), e)
            return False

    # ------------------------------------------------------------------ vote
    async def voter(self, itx, sid: str, idx: int):
        s = self._sondages.get(str(sid))
        if s is None or s.get("ferme") or est_echu(s, _now()):
            await itx.response.send_message("⛔ Ce sondage est terminé.", ephemeral=True)
            return
        if not 0 <= idx < len(s.get("options", [])):
            await itx.response.send_message("❌ Option inconnue.", ephemeral=True)
            return
        async with self._verrou(sid):
            action = appliquer_vote(s, itx.user.id, idx)
            self._save()
            emb = build_embed(s)
        try:
            await itx.response.edit_message(embed=emb)
        except discord.HTTPException:
            try:
                await itx.response.send_message("✅ Vote pris en compte.", ephemeral=True)
            except discord.HTTPException:
                pass
        libelle = {"ajoute": "✅ Voix ajoutée", "retire": "↩️ Voix retirée",
                   "remplace": "🔁 Choix remplacé"}[action]
        try:
            await itx.followup.send(f"{libelle} : **{s['options'][idx]}**", ephemeral=True,
                                    allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass

    async def qui_a_vote(self, itx, sid: str):
        s = self._sondages.get(str(sid))
        if s is None:
            await itx.response.send_message("❌ Sondage introuvable.", ephemeral=True)
            return
        if s.get("anonyme"):
            await itx.response.send_message("🔒 Sondage anonyme.", ephemeral=True)
            return
        if not self._peut_gerer(itx, s):
            await itx.response.send_message(
                "🔒 Réservé au créateur du sondage et aux gestionnaires (M/O).",
                ephemeral=True)
            return
        await itx.response.send_message(embed=self.embed_votants(s), ephemeral=True,
                                        allowed_mentions=discord.AllowedMentions.none())

    def embed_votants(self, s) -> discord.Embed:
        emb = discord.Embed(title=f"👥 Qui a voté — {s.get('question', '')}"[:256],
                            color=BLURPLE)
        votes = s.get("votes") or {}
        for i, label in enumerate(s.get("options", [])):
            ids = [uid for uid, choix in votes.items() if i in choix]
            val = ", ".join(f"<@{u}>" for u in ids[:40]) or "—"
            if len(ids) > 40:
                val += f" … (+{len(ids) - 40})"
            emb.add_field(name=f"{EMOJIS[i]} {label}", value=val[:1024], inline=False)
        if not getattr(self.bot.intents, "members", False):
            emb.set_footer(text="Mentions <@id> (intent members absent : pseudos non résolus)")
        return emb

    # ------------------------------------------------------------------ clôture
    async def fermer(self, sid: str, par: str) -> bool:
        """Ferme un sondage. `ferme=True` est sauvé AVANT l'édition (idempotent au
        redémarrage). Renvoie True si le message a été rendu dans son état final."""
        s = self._sondages.get(str(sid))
        if s is None:
            return False
        async with self._verrou(sid):
            if not s.get("ferme"):
                s["ferme"] = True
                s["ferme_a"] = _now()
                s["ferme_par"] = par
                s["rendu_final"] = False
                s["rendu_essais"] = 0
                self._save()
                self._audit(user="system" if par == "programmé" else par,
                            action="sondage-fermer", target=f"#{sid}",
                            result=f"{par} ; votants={len(s.get('votes') or {})}")
            if s.get("rendu_final"):
                return True
            view = self._vues.pop(str(sid), None)
            if view is not None:
                view.stop()
            ok = await self._rafraichir(s, retirer_boutons=True)
            s["rendu_essais"] = int(s.get("rendu_essais", 0)) + 1
            if ok or s["rendu_essais"] >= RENDU_ESSAIS_MAX:
                # message supprimé/injoignable : on n'insiste pas indéfiniment
                s["rendu_final"] = True
                if not ok:
                    log.warning("sondage %s : message introuvable, rendu final abandonné", sid)
            self._save()
            return ok

    @tasks.loop(seconds=TICK_SECONDES)
    async def cloture(self):
        await self.tick()

    @cloture.before_loop
    async def _avant(self):
        await self.bot.wait_until_ready()

    async def tick(self):
        """Un passage : sondages échus → fermés ; fermés non rendus → rendu retenté."""
        now = _now()
        for sid, s in list(self._sondages.items()):
            if est_echu(s, now):
                await self.fermer(sid, "programmé")
            elif s.get("ferme") and not s.get("rendu_final"):
                await self.fermer(sid, s.get("ferme_par") or "programmé")

    # ------------------------------------------------------------------ commandes
    @sondage.command(name="creer", description="Créer un sondage à boutons")
    @app_commands.describe(
        question="La question (≤ 250 caractères)",
        options="2 à 10 options séparées par | (ex. Oui | Non | Sans avis)",
        multiple="Autoriser plusieurs choix par personne (défaut : non)",
        duree="Durée avant clôture automatique (ex. 2h, 1j12h, 30min) — vide = manuelle",
        anonyme="Ne jamais afficher qui a voté (défaut : non)",
        salon="Salon où poster (défaut : le salon courant)")
    @read_check(scope="channel")
    async def creer(self, itx: discord.Interaction, question: str, options: str,
                    multiple: Optional[bool] = False, duree: Optional[str] = None,
                    anonyme: Optional[bool] = False,
                    salon: Optional[discord.TextChannel] = None):
        q = _clean(question, QUESTION_MAX)
        if not q:
            await itx.response.send_message("❌ Question vide.", ephemeral=True)
            return
        try:
            opts = parse_options(options)
        except ValueError as e:
            await itx.response.send_message(
                f"❌ Il faut entre {MIN_OPTIONS} et {MAX_OPTIONS} options séparées par "
                f"`|` (reçu : {e}).", ephemeral=True)
            return
        fin = None
        if duree:
            try:
                secs = parse_delai(duree)
            except ValueError:
                await itx.response.send_message(
                    f"❌ Durée illisible : `{_clean(duree, 40)}`. Exemples : `30min`, `2h`, "
                    "`1j12h`.", ephemeral=True)
                return
            if not DUREE_MIN <= secs <= self.duree_max:
                await itx.response.send_message(
                    f"❌ Durée {fmt_delai(secs)} hors bornes (1 min à "
                    f"{fmt_delai(self.duree_max)}).", ephemeral=True)
                return
            fin = _now() + secs
        ouverts = sum(1 for s in self._sondages.values() if not s.get("ferme"))
        if ouverts >= self.max_ouverts:
            await itx.response.send_message(
                f"❌ {ouverts} sondages déjà ouverts (plafond {self.max_ouverts}) : "
                "ferme-en avec `/sondage fermer`.", ephemeral=True)
            return
        cible = salon or itx.channel
        await itx.response.defer(ephemeral=True, thinking=True)
        sid = self._nouvel_id()
        s = {"id": sid, "guild_id": itx.guild_id, "salon_id": cible.id, "message_id": None,
             "createur_id": itx.user.id, "question": q, "options": opts,
             "multiple": bool(multiple), "anonyme": bool(anonyme),
             "cree_a": _now(), "fin_a": fin, "ferme": False, "votes": {}}
        view = SondageView(self, s)
        try:
            msg = await cible.send(embed=build_embed(s), view=view,
                                   allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:
            await itx.followup.send(f"❌ Je ne peux pas écrire dans <#{cible.id}>.",
                                    ephemeral=True)
            return
        except discord.HTTPException as e:
            await itx.followup.send(f"❌ Envoi impossible : `{type(e).__name__}`.",
                                    ephemeral=True)
            return
        s["message_id"] = msg.id
        self._sondages[sid] = s
        self._vues[sid] = view
        self._save()
        self._audit(user=f"{itx.user} ({itx.user.id})", action="sondage-creer",
                    target=f"#{sid}",
                    result=f"{len(opts)} options, {'multiple' if multiple else 'unique'}, "
                           f"{'anonyme' if anonyme else 'public'}, "
                           f"fin={'manuelle' if fin is None else fmt_delai(fin - s['cree_a'])}")
        await itx.followup.send(
            f"✅ Sondage **#{sid}** créé dans <#{cible.id}>"
            + (f", clôture <t:{int(fin)}:R>." if fin else " (clôture manuelle)."),
            ephemeral=True)

    @sondage.command(name="fermer", description="Fermer un sondage (créateur ou M/O)")
    @app_commands.describe(id="Numéro du sondage (voir /sondage liste)")
    @read_check(scope="channel")
    async def fermer_cmd(self, itx: discord.Interaction, id: str):
        sid = id.strip().lstrip("#")
        s = self._sondages.get(sid)
        if s is None:
            await itx.response.send_message(f"❌ Sondage #{_clean(sid, 20)} introuvable.",
                                            ephemeral=True)
            return
        if s.get("ferme"):
            await itx.response.send_message(f"ℹ️ Le sondage #{sid} est déjà fermé.",
                                            ephemeral=True)
            return
        if not self._peut_gerer(itx, s):
            await itx.response.send_message(
                "🔒 Seuls le créateur du sondage et les gestionnaires (M/O) peuvent le fermer.",
                ephemeral=True)
            return
        await itx.response.defer(ephemeral=True, thinking=True)
        ok = await self.fermer(sid, f"{itx.user} ({itx.user.id})")
        await itx.followup.send(
            f"🔒 Sondage **#{sid}** fermé." + ("" if ok else " (message d'origine introuvable, "
                                              "résultats via `/sondage resultats`)"),
            ephemeral=True)

    @sondage.command(name="resultats", description="Résultats d'un sondage (ouvert ou fermé)")
    @app_commands.describe(id="Numéro du sondage")
    @read_check(scope="channel")
    async def resultats(self, itx: discord.Interaction, id: str):
        sid = id.strip().lstrip("#")
        s = self._sondages.get(sid)
        if s is None:
            await itx.response.send_message(f"❌ Sondage #{_clean(sid, 20)} introuvable.",
                                            ephemeral=True)
            return
        await itx.response.send_message(embed=build_embed(s), ephemeral=True,
                                        allowed_mentions=discord.AllowedMentions.none())

    @sondage.command(name="liste", description="Sondages ouverts et récents")
    @read_check(scope="channel")
    async def liste(self, itx: discord.Interaction):
        items = sorted(self._sondages.values(),
                       key=lambda s: (bool(s.get("ferme")), -float(s.get("cree_a", 0))))
        emb = discord.Embed(title="📊 Sondages", color=BLURPLE)
        if not items:
            emb.description = "Aucun sondage enregistré."
        else:
            lignes = []
            for s in items[:20]:
                etat = "🔒 fermé" if s.get("ferme") else (
                    f"🟢 ouvert, fin <t:{int(s['fin_a'])}:R>" if s.get("fin_a") else "🟢 ouvert")
                lignes.append(f"**#{s['id']}** {etat} · {len(s.get('votes') or {})} votant(s) · "
                              f"<#{s.get('salon_id')}>\n  {s.get('question', '')[:100]}")
            emb.description = "\n".join(lignes)
            if len(items) > 20:
                emb.set_footer(text=f"{len(items)} sondages, 20 affichés")
        await itx.response.send_message(embed=emb, ephemeral=True,
                                        allowed_mentions=discord.AllowedMentions.none())


async def setup(bot):
    await bot.add_cog(Sondage(bot))
