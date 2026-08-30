"""Rappels — `/rappel creer|liste|supprimer` : « dans 1j12h30min, rappelle-moi … » (2026-08-30).

Idée reprise d'« Ultra Suite » (`/reminder` + `scheduler.processReminders`), dont on garde
le BON design — marquer un rappel comme envoyé AVANT de l'envoyer (anti-doublon si le bot
redémarre au milieu), traiter par lots bornés, purger l'ancien — et dont on comble les
manques : pas de récurrence, pas de choix salon/DM, pas de repli si le salon a disparu,
parseur de durée trop strict (« 2 jours » refusé), aucune borne haute cohérente.

CE QUE FAIT CE COG
------------------
- `/rappel creer dans:<durée> message:<texte> [ou:salon|dm] [repeter:<durée>] [salon]`
    · `dans` : de 1 min à 365 j, cumulable (« 1j12h30min », « 90min », « 2 jours »,
      « 1h30 ») — voir `parse_delai` ;
    · `ou` : « salon » (défaut : le salon courant, ou `salon`) ou « dm » (message privé) ;
    · `repeter` : le rappel se REPROGRAMME à chaque envoi (même id), période ≥ 1 min ;
- `/rappel liste` : mes rappels en attente ; les tiers M/O voient ceux de tout le monde ;
- `/rappel supprimer id` : le mien, ou n'importe lequel pour M/O ;
- boucle `scheduler` (15 s) : envoie les rappels échus (≤ `LOT_MAX` = 20 par tick, les
  plus anciens d'abord), en posant `envoye=True` (ou l'échéance suivante pour une
  récurrence) AVANT l'envoi ; si le salon a disparu ou refuse l'écriture → repli en DM ;
  purge des rappels envoyés depuis plus de `RAPPEL_PURGE_JOURS` (7) jours.
- État : `bot.state["rappels"]` = {id: {...}} (JSON, survit aux redémarrages).

CE QUE CE COG NE FAIT PAS
-------------------------
- Il ne mentionne JAMAIS personne d'autre que l'auteur du rappel : le texte est libre,
  donc `AllowedMentions(users=[auteur])` — pas de @everyone, pas de rôles, pas d'autrui.
- Il ne crée aucun salon, ne lit aucun message (pas d'intent `message_content`), ne
  résout aucun pseudo (pas d'intent `members`) : les listes affichent des mentions
  `<@id>`, que Discord rend côté client.
- Il ne garantit pas la seconde près : la boucle passe toutes les 15 s, et le bot peut
  être éteint (coupure secteur du 27/08…) — un rappel en retard est envoyé au retour,
  avec la mention « prévu <t:…:R> » pour ne pas prétendre qu'il est à l'heure.

PIÈGES CONNUS
-------------
- L'ordre « marquer PUIS envoyer » est volontaire : un envoi qui échoue après le
  marquage est perdu (journalisé + audit), un envoi qui réussit avant un crash serait
  DOUBLÉ au redémarrage. Entre les deux, Nico préfère le silence au spam.
- Un salon supprimé : `bot.get_channel` renvoie None (cache) — on tente aussi
  `fetch_channel` (NotFound/Forbidden ⇒ repli DM). Le DM lui-même peut être refusé
  (DM fermés) : c'est le seul cas où le rappel est perdu, et il est tracé.
- Le parseur de `core/durations.py` (minutes, 0 = illimité, pas de jours) sert au
  terminal et au 2FA ; il ne convient pas ici (jours/semaines, pas d'illimité), d'où
  `parse_delai` ci-dessous, réutilisé par le cog `sondage`.
"""
import asyncio
import logging
import re
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..core import bg
from ..core.format import BLURPLE
from ..core.permissions import channel_server, read_check, tier_of

log = logging.getLogger("discord-bot.rappel")

CLE_ETAT = "rappels"
LOT_MAX = 20                       # rappels envoyés par tick au maximum
TICK_SECONDES = 15
MIN_DELAI = 60                     # 1 min
MAX_DELAI = 365 * 86400            # 365 j
MIN_REPETER = 60
MESSAGE_MAX = 500
PURGE_JOURS_DEFAUT = 7
MAX_PAR_UTILISATEUR_DEFAUT = 25


def _now() -> float:
    """Horloge du module (remplaçable dans les tests)."""
    return time.time()


# --------------------------------------------------------------------------- durées
# Unités acceptées (français ET anglais : la commande est tapée vite, souvent au
# téléphone). Tout est ramené en SECONDES.
_UNITES = {
    "s": 1, "sec": 1, "secs": 1, "seconde": 1, "secondes": 1, "second": 1, "seconds": 1,
    "m": 60, "mn": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "heure": 3600, "heures": 3600, "hour": 3600,
    "hours": 3600,
    "j": 86400, "jour": 86400, "jours": 86400, "d": 86400, "day": 86400, "days": 86400,
    "sem": 604800, "semaine": 604800, "semaines": 604800, "w": 604800, "week": 604800,
    "weeks": 604800,
}
_TOKEN = re.compile(r"(\d+)\s*([a-zA-Zéè]+)?")


def parse_delai(raw: str) -> int:
    """« 1j12h30min », « 90min », « 2 jours », « 1h30 », « 45 » (= minutes) → secondes.

    Lève ValueError si la saisie est vide, contient autre chose que des paires
    nombre+unité, une unité inconnue, ou ne donne aucune durée. Les BORNES ne sont pas
    appliquées ici (l'appelant cite la valeur refusée dans son message).

    Cas particuliers : un nombre SANS unité en tête vaut des minutes (« 45 ») ; un nombre
    sans unité qui SUIT des heures vaut des minutes (« 1h30 ») ; toute autre position
    sans unité est refusée (« 1j30 » est ambigu).
    """
    s = (raw or "").strip().lower().replace(",", " ")
    s = re.sub(r"\bet\b", " ", s).replace("é", "e").replace("è", "e")
    if not s:
        raise ValueError(raw)
    total = 0
    pos = 0
    prev_unit = None
    seen = False
    for m in _TOKEN.finditer(s):
        if s[pos:m.start()].strip():                 # texte parasite entre deux tokens
            raise ValueError(raw)
        pos = m.end()
        n, unit = int(m.group(1)), m.group(2)
        if unit is None:
            if not seen:
                unit = "min"                         # « 45 » = 45 minutes
            elif prev_unit in ("h", "hr", "hrs", "heure", "heures", "hour", "hours"):
                unit = "min"                         # « 1h30 »
            else:
                raise ValueError(raw)
        if unit not in _UNITES:
            raise ValueError(raw)
        total += n * _UNITES[unit]
        prev_unit = unit
        seen = True
    if s[pos:].strip() or not seen:
        raise ValueError(raw)
    if total <= 0:
        raise ValueError(raw)
    return total


def fmt_delai(seconds) -> str:
    """Rendu FR compact : « 1 j 12 h 30 min », « 90 s », « 2 h »."""
    s = max(0, int(seconds or 0))
    j, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    parts = []
    if j:
        parts.append(f"{j} j")
    if h:
        parts.append(f"{h} h")
    if m:
        parts.append(f"{m} min")
    if s and not parts:
        parts.append(f"{s} s")
    return " ".join(parts) or "0 s"


def _ts(t) -> str:
    return f"<t:{int(t)}:R>"


def _clean(text: str) -> str:
    """Neutralise backticks/sauts de ligne multiples d'un texte libre (le reste est
    couvert par AllowedMentions)."""
    return " ".join((text or "").replace("`", "'").split())[:MESSAGE_MAX]


# --------------------------------------------------------------------------- logique pure
def echeants(rappels: dict, now: float, lot: int = LOT_MAX) -> list:
    """Ids des rappels échus, non envoyés, les plus anciens d'abord, au plus `lot`."""
    due = [(r.get("echeance", 0), rid) for rid, r in rappels.items()
           if not r.get("envoye") and float(r.get("echeance", 0)) <= now]
    due.sort()
    return [rid for _, rid in due[:lot]]


def prochaine_echeance(echeance: float, periode: int, now: float) -> float:
    """Échéance suivante d'une récurrence, STRICTEMENT dans le futur : après une longue
    coupure on ne rejoue pas les occurrences manquées (un rappel « toutes les heures »
    éteint 7 h ne doit pas envoyer 7 messages d'un coup)."""
    nxt = float(echeance) + periode
    while nxt <= now:
        nxt += periode
    return nxt


def a_purger(rappels: dict, now: float, jours: int) -> list:
    limite = now - jours * 86400
    return [rid for rid, r in rappels.items()
            if r.get("envoye") and float(r.get("envoye_a") or 0) < limite]


def mentions_pour(auteur_id: int) -> discord.AllowedMentions:
    """SEUL l'auteur peut être mentionné — jamais @everyone, jamais un rôle, jamais un
    tiers cité dans le texte."""
    return discord.AllowedMentions(everyone=False, roles=False, replied_user=False,
                                   users=[discord.Object(id=int(auteur_id))])


# --------------------------------------------------------------------------- cog
class Rappel(commands.Cog):
    """Rappels personnels persistants (salon ou DM), avec récurrence."""

    rappel = app_commands.Group(name="rappel",
                                description="Rappels : dans X, rappelle-moi …")

    def __init__(self, bot):
        self.bot = bot
        self.cfg = bot.cfg
        self._rappels = dict(bot.state.get(CLE_ETAT, {}) or {})
        self._verrou = asyncio.Lock()
        self.purge_jours = int(getattr(self.cfg, "rappel_purge_jours", PURGE_JOURS_DEFAUT)
                               or PURGE_JOURS_DEFAUT)
        self.max_par_utilisateur = int(getattr(self.cfg, "rappel_max_par_utilisateur",
                                               MAX_PAR_UTILISATEUR_DEFAUT)
                                       or MAX_PAR_UTILISATEUR_DEFAUT)

    async def cog_load(self):
        bg.guard_cog_loops(self, log)
        self.scheduler.start()

    async def cog_unload(self):
        self.scheduler.cancel()

    # ------------------------------------------------------------------ état
    def _save(self):
        self.bot.state.set(CLE_ETAT, self._rappels)

    def _nouvel_id(self) -> str:
        n = 0
        for rid in self._rappels:
            try:
                n = max(n, int(rid))
            except ValueError:
                continue
        return str(n + 1)

    def _audit(self, user, action, target, result):
        audit = getattr(self.bot, "audit", None)
        if audit is not None:
            audit.record(user=user, action=action, target=target, result=result)

    def _est_mod(self, itx) -> bool:
        srv = channel_server(itx)
        return tier_of(self.cfg, itx, srv) in ("M", "O")

    # ------------------------------------------------------------------ envoi
    async def _salon(self, cid):
        """Salon par id, cache puis API ; None s'il a disparu ou nous est interdit."""
        if not cid:
            return None
        ch = self.bot.get_channel(int(cid))
        if ch is not None:
            return ch
        try:
            return await self.bot.fetch_channel(int(cid))
        except (discord.NotFound, discord.Forbidden):
            return None
        except discord.HTTPException as e:
            log.warning("rappel: salon %s illisible (%s)", cid, e)
            return None

    async def _dm(self, uid):
        user = self.bot.get_user(int(uid))
        if user is None:
            try:
                user = await self.bot.fetch_user(int(uid))
            except discord.HTTPException:
                return None
        return user

    def _texte(self, r, en_retard: bool) -> str:
        retard = f" (prévu {_ts(r['echeance_prevue'])})" if en_retard else ""
        rep = f"\n🔁 se répète toutes les {fmt_delai(r['repeter'])}" if r.get("repeter") else ""
        return f"⏰ <@{r['auteur_id']}> Rappel{retard} : {r['message']}{rep}"

    async def envoyer(self, rid: str) -> bool:
        """Envoie UN rappel. Le marquage (envoyé / échéance suivante) est posé et
        sauvegardé AVANT tout appel réseau. Renvoie True si un message est parti."""
        r = self._rappels.get(rid)
        if r is None:
            return False
        now = _now()
        r["echeance_prevue"] = r.get("echeance", now)
        en_retard = now - float(r["echeance_prevue"]) > 2 * TICK_SECONDES
        # --- 1) marquage AVANT envoi (anti-doublon au redémarrage)
        if r.get("repeter"):
            r["echeance"] = prochaine_echeance(r["echeance"], int(r["repeter"]), now)
            r["dernier_envoi"] = now
            r["envois"] = int(r.get("envois", 0)) + 1
        else:
            r["envoye"] = True
            r["envoye_a"] = now
        self._save()
        # --- 2) envoi
        texte = self._texte(r, en_retard)
        sent = False
        cible = "dm"
        if r.get("mode") == "salon":
            ch = await self._salon(r.get("salon_id"))
            if ch is not None:
                try:
                    await ch.send(texte, allowed_mentions=mentions_pour(r["auteur_id"]))
                    sent = True
                    cible = f"salon:{r.get('salon_id')}"
                except discord.Forbidden:
                    log.warning("rappel %s : écriture refusée dans %s -> repli DM",
                                rid, r.get("salon_id"))
                except discord.HTTPException as e:
                    log.warning("rappel %s : envoi salon échoué (%s) -> repli DM", rid, e)
            else:
                log.info("rappel %s : salon %s disparu -> repli DM", rid, r.get("salon_id"))
        if not sent:
            user = await self._dm(r["auteur_id"])
            if user is not None:
                try:
                    prefixe = "" if r.get("mode") == "dm" else "(le salon prévu est indisponible) "
                    await user.send(prefixe + texte,
                                    allowed_mentions=discord.AllowedMentions.none())
                    sent = True
                except discord.HTTPException as e:
                    log.warning("rappel %s : DM refusé (%s) — rappel PERDU", rid, e)
        if not sent:
            r["erreur"] = "envoi impossible (salon et DM)"
            self._save()
        self._audit(user="system", action="rappel-envoi", target=f"#{rid}",
                    result=("ok " + cible) if sent else "échec salon+DM")
        return sent

    @tasks.loop(seconds=TICK_SECONDES)
    async def scheduler(self):
        await self.tick()

    @scheduler.before_loop
    async def _avant(self):
        await self.bot.wait_until_ready()

    async def tick(self):
        """Un passage : lot d'échus, puis purge. Séparé de la boucle pour les tests."""
        async with self._verrou:
            now = _now()
            for rid in echeants(self._rappels, now, LOT_MAX):
                try:
                    await self.envoyer(rid)
                except Exception:  # noqa: BLE001 — un rappel cassé ne bloque pas les autres
                    log.exception("rappel %s : erreur d'envoi", rid)
            vieux = a_purger(self._rappels, now, self.purge_jours)
            if vieux:
                for rid in vieux:
                    self._rappels.pop(rid, None)
                self._save()
                log.info("rappel: %d rappel(s) envoyé(s) purgé(s) (> %d j)", len(vieux),
                         self.purge_jours)

    # ------------------------------------------------------------------ commandes
    @rappel.command(name="creer", description="Créer un rappel (dans 1j12h30min…)")
    @app_commands.describe(
        dans="Délai : 1min à 365j, cumulable (ex. 2h, 1j12h, 90min, 2 jours)",
        message="Texte du rappel (≤ 500 caractères)",
        ou="Où l'envoyer : ce salon (défaut) ou en message privé",
        repeter="Répéter toutes les … (ex. 1j, 1 semaine) — vide = une seule fois",
        salon="Salon cible (défaut : le salon courant)")
    @app_commands.choices(ou=[app_commands.Choice(name="salon", value="salon"),
                              app_commands.Choice(name="dm", value="dm")])
    @read_check(scope="channel")
    async def creer(self, itx: discord.Interaction, dans: str, message: str,
                    ou: Optional[app_commands.Choice[str]] = None,
                    repeter: Optional[str] = None,
                    salon: Optional[discord.TextChannel] = None):
        try:
            delai = parse_delai(dans)
        except ValueError:
            await itx.response.send_message(
                f"❌ Délai illisible : `{_clean(dans)[:40]}`. Exemples : `30min`, `2h`, "
                "`1j12h`, `2 jours`, `1h30`.", ephemeral=True)
            return
        if not MIN_DELAI <= delai <= MAX_DELAI:
            await itx.response.send_message(
                f"❌ Délai {fmt_delai(delai)} hors bornes (1 min à 365 j).", ephemeral=True)
            return
        periode = None
        if repeter:
            try:
                periode = parse_delai(repeter)
            except ValueError:
                await itx.response.send_message(
                    f"❌ Période illisible : `{_clean(repeter)[:40]}`.", ephemeral=True)
                return
            if not MIN_REPETER <= periode <= MAX_DELAI:
                await itx.response.send_message(
                    f"❌ Période {fmt_delai(periode)} hors bornes (1 min à 365 j).",
                    ephemeral=True)
                return
        texte = _clean(message)
        if not texte:
            await itx.response.send_message("❌ Message vide.", ephemeral=True)
            return
        mode = (ou.value if ou else "salon")
        uid = itx.user.id
        actifs = sum(1 for r in self._rappels.values()
                     if r.get("auteur_id") == uid and not r.get("envoye"))
        if actifs >= self.max_par_utilisateur:
            await itx.response.send_message(
                f"❌ Tu as déjà {actifs} rappels en attente (plafond "
                f"{self.max_par_utilisateur}) : supprime-en avec `/rappel supprimer`.",
                ephemeral=True)
            return
        cible = salon or itx.channel
        async with self._verrou:
            rid = self._nouvel_id()
            now = _now()
            self._rappels[rid] = {
                "id": rid, "auteur_id": uid, "guild_id": itx.guild_id,
                "mode": mode, "salon_id": (cible.id if (mode == "salon" and cible) else None),
                "message": texte, "cree_a": now, "echeance": now + delai,
                "repeter": periode, "envoye": False,
            }
            self._save()
        self._audit(user=f"{itx.user} ({uid})", action="rappel-creer", target=f"#{rid}",
                    result=f"{mode} dans {fmt_delai(delai)}"
                           + (f" toutes les {fmt_delai(periode)}" if periode else ""))
        ou_txt = "en message privé" if mode == "dm" else f"dans <#{cible.id}>"
        rep = f"\n🔁 Répété toutes les **{fmt_delai(periode)}**." if periode else ""
        await itx.response.send_message(
            f"✅ Rappel **#{rid}** créé : {_ts(now + delai)} {ou_txt}.\n📝 {texte}{rep}",
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @rappel.command(name="liste", description="Mes rappels en attente (M/O : tous)")
    @read_check(scope="channel")
    async def liste(self, itx: discord.Interaction):
        tous = self._est_mod(itx)
        uid = itx.user.id
        items = sorted((r for r in self._rappels.values()
                        if not r.get("envoye") and (tous or r.get("auteur_id") == uid)),
                       key=lambda r: float(r.get("echeance", 0)))
        emb = discord.Embed(title="⏰ Rappels en attente", color=BLURPLE)
        if not items:
            emb.description = "Aucun rappel en attente."
        else:
            lignes = []
            for r in items[:25]:
                ou = "DM" if r.get("mode") == "dm" else f"<#{r.get('salon_id')}>"
                qui = f" — <@{r['auteur_id']}>" if tous else ""
                rep = f" · 🔁 {fmt_delai(r['repeter'])}" if r.get("repeter") else ""
                lignes.append(f"**#{r['id']}** {_ts(r['echeance'])} · {ou}{rep}{qui}\n"
                              f"  {r['message'][:100]}")
            emb.description = "\n".join(lignes)
            if len(items) > 25:
                emb.set_footer(text=f"{len(items)} rappels, 25 affichés")
        if not getattr(self.bot.intents, "members", False) and tous:
            emb.set_footer(text="Auteurs affichés en mention (intent members absent)")
        await itx.response.send_message(embed=emb, ephemeral=True,
                                        allowed_mentions=discord.AllowedMentions.none())

    @rappel.command(name="supprimer", description="Supprimer un rappel (le mien ; M/O : tous)")
    @app_commands.describe(id="Numéro du rappel (voir /rappel liste)")
    @read_check(scope="channel")
    async def supprimer(self, itx: discord.Interaction, id: str):
        rid = id.strip().lstrip("#")
        r = self._rappels.get(rid)
        if r is None or r.get("envoye"):
            await itx.response.send_message(f"❌ Rappel #{_clean(rid)[:20]} introuvable.",
                                            ephemeral=True)
            return
        if r.get("auteur_id") != itx.user.id and not self._est_mod(itx):
            await itx.response.send_message("🔒 Ce rappel n'est pas le tien.", ephemeral=True)
            return
        async with self._verrou:
            self._rappels.pop(rid, None)
            self._save()
        self._audit(user=f"{itx.user} ({itx.user.id})", action="rappel-supprimer",
                    target=f"#{rid}", result="ok")
        await itx.response.send_message(f"🗑️ Rappel **#{rid}** supprimé.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Rappel(bot))
