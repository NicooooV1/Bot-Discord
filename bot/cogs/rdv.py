"""Cog rdv — notifie Nico quand quelqu'un répond au questionnaire e.nicov1.fr, et signale
les VISITES de la page (même sans réponse).

Le site (CT121, /api/answer) enregistre les réponses dans answers.jsonl et les visites de
l'accueil dans visits.jsonl (robots et appareils de Nico exclus côté serveur via /moi).
Ce cog interroge périodiquement les endpoints privés /api/answers et /api/visits (réservés
à l'IP du bot) : chaque réponse part en DM au propriétaire (repli salon privé #e-reponses
si DM fermés) ; les visites vont directement dans #e-reponses (coalescées : une même IP
n'est signalée qu'une fois par demi-heure). Curseurs persistés dans state.json. Au TOUT
premier passage des réponses, on saute celles déjà présentes (tests) pour ne notifier que
les nouvelles.
"""
import logging
import time

import discord
from discord.ext import commands, tasks

from ..core.http import ApiClient

log = logging.getLogger("discord-bot.rdv")

SITE_URL = "http://10.3.20.121:8080"   # site web-e (CT121) : endpoints privés /api/*
ANSWERS_PATH = "/api/answers"
VISITS_PATH = "/api/visits"
# 8 s (et non le défaut de core.http) : la boucle repasse toutes les 30 s et enchaîne DEUX
# appels — un site injoignable ne doit pas manger la moitié de l'intervalle.
HTTP_TIMEOUT = 8
POLL_SECONDS = 30
VISIT_COALESCE_S = 30 * 60    # une même IP -> au plus 1 notification de visite / 30 min
FALLBACK_CHANNEL = "e-reponses"   # salon privé (propriétaire seul)
MAX_VISIT_BACKLOG = 200   # au-delà, on avance le curseur de force (cf. _poll_visits)
MAX_ANSWER_FIELDS = 20    # Discord refuse un embed de plus de 25 champs
# tours de scrutation avant d'abandonner UNE réponse indélivrable (30 s chacun)
MAX_NOTIFY_RETRIES = 10


def _i(x, default=0):
    """Entier tolérant. Les valeurs viennent du site (POST public /api/answer) : un
    `null`, une chaîne ou un objet ne doit PAS tuer la boucle de notification, qui est
    la SEULE voie de signalement (2026-08-11)."""
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


def _device(ua):
    u = (ua or "").lower()
    if "iphone" in u:
        return "📱 iPhone"
    if "ipad" in u:
        return "📱 iPad"
    if "android" in u:
        return "📱 Android"
    if "windows" in u:
        return "💻 Windows"
    if "macintosh" in u or "mac os" in u:
        return "💻 Mac"
    if "linux" in u:
        return "💻 Linux"
    return "🌐 " + ((ua or "?")[:40])


class Rdv(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._visit_seen = {}   # ip -> monotonic de la dernière notification (coalescence)
        self._api = ApiClient(SITE_URL, timeout=HTTP_TIMEOUT, label="web-e")

    async def cog_load(self):
        self.poll.start()

    def cog_unload(self):
        self.poll.cancel()

    async def _fetch(self, path, after):
        """Lot de lignes depuis `?after=<curseur>`, ou None si le site est injoignable.

        ⚠️ None (échec) et [] (rien de neuf) ne se confondent PAS : sur un échec, les
        curseurs ne doivent pas bouger, sinon une réponse d'Elisa serait sautée pour
        toujours. `quiet=True` : CT121 peut être éteint, et un avertissement toutes les
        30 s noierait le journal — l'échec reste visible en debug."""
        data = await self._api.aget(path, {"after": _i(after)}, quiet=True)
        if data is None:
            return None
        return data if isinstance(data, list) else []

    @tasks.loop(seconds=POLL_SECONDS)
    async def poll(self):
        # seul le bot PRIMAIRE relaie (une seule instance en DM Nico)
        if not getattr(self.bot.cfg, "is_primary", True):
            return
        # Corps entièrement gardé (2026-08-11) : seules les lectures HTTP étaient
        # protégées, tout le reste (données venues du site, construction de l'embed)
        # était nu. Une exception non rattrapée ARRÊTE la tâche — le filet de bg.py la
        # relancerait, mais avec 15 s à 5 min de trou à chaque fois. Ici on encaisse
        # l'anomalie, on la journalise, et le tour suivant repart normalement.
        try:
            await self._poll_answers()
            await self._poll_visits()
        except Exception:  # noqa: BLE001
            log.exception("rdv: tour de scrutation en échec")

    async def _poll_answers(self):
        st = self.bot.state
        cursor = int(st.get("rdv_cursor", 0) or 0)
        data = await self._fetch(ANSWERS_PATH, cursor)
        if not data:      # None (échec) comme [] (rien de neuf) : on ne touche à rien
            return
        # 1er passage : ne pas spammer avec les réponses déjà là (tests) — caler le curseur.
        if not st.get("rdv_init"):
            st.set("rdv_cursor", max((_i(a.get("_i")) for a in data), default=cursor))
            st.set("rdv_init", True)
            log.info("rdv: curseur initialisé (%d réponse(s) préexistante(s) ignorée(s))", len(data))
            return
        # Le curseur n'avance QUE si la réponse a été remise. Sinon on s'arrête net :
        # les suivantes seront rejouées au prochain tour, dans l'ordre, sans trou.
        # Garde-fou : après MAX_NOTIFY_RETRIES tours d'échec sur la MÊME réponse, on
        # passe outre en le criant — sans quoi une réponse indélivrable (DM fermés ET
        # salon de repli impossible) bloquerait la file pour toujours.
        for a in sorted(data, key=lambda x: _i(x.get("_i"))):
            idx = _i(a.get("_i"), cursor)
            if await self._notify(a):
                self._notify_fails = 0
                st.set("rdv_cursor", idx)
                continue
            self._notify_fails = getattr(self, "_notify_fails", 0) + 1
            if self._notify_fails >= MAX_NOTIFY_RETRIES:
                log.error("rdv: réponse #%s indélivrable après %d tentatives — curseur "
                          "forcé pour ne pas bloquer les suivantes. Contenu perdu.",
                          idx, self._notify_fails)
                self._notify_fails = 0
                st.set("rdv_cursor", idx)
                continue
            log.warning("rdv: réponse #%s non remise (tentative %d/%d), curseur figé",
                        idx, self._notify_fails, MAX_NOTIFY_RETRIES)
            return

    async def _poll_visits(self):
        """Visites de l'accueil -> message compact dans #e-reponses (pas de DM, moins intrusif).
        Pas de saut initial : visits.jsonl démarre vide, le curseur suffit."""
        st = self.bot.state
        cursor = int(st.get("rdv_visits_cursor", 0) or 0)
        data = await self._fetch(VISITS_PATH, cursor)
        if not data:      # None (échec) comme [] (rien de neuf) : on ne touche à rien
            return
        # coalescence : au sein du lot ET dans le temps, 1 notif max par IP / 30 min
        now = time.monotonic()
        # purge des entrées périmées : au-delà de la fenêtre de coalescence elles ne
        # servent plus à rien et s'accumulaient pour toute la vie du processus
        self._visit_seen = {k: t for k, t in self._visit_seen.items()
                            if (now - t) < VISIT_COALESCE_S}
        by_ip = {}
        for v in sorted(data, key=lambda x: _i(x.get("_i"))):
            by_ip[v.get("ip", "?")] = v
        newest = max((_i(v.get("_i")) for v in data), default=cursor)
        failed = []          # _i des visites qu'on n'a PAS réussi à signaler
        for ip, v in by_ip.items():
            idx = _i(v.get("_i"), cursor)
            last = self._visit_seen.get(ip)
            if last is not None and (now - last) < VISIT_COALESCE_S:
                continue     # saut DÉLIBÉRÉ (coalescence documentée), pas une perte
            when = (f"<t:{_i(v['epoch'])}:t>" if v.get("epoch")
                    else str(v.get("ts", ""))[:16])
            msg = f"👀 **Visite du site** · {_device(v.get('ua'))} · {when} · IP `{ip}`"
            ch = await self._owner_channel()
            if ch is None:
                failed.append(idx)
                continue
            try:
                await ch.send(msg)
                # marqué APRÈS l'envoi : un échec doit pouvoir être retenté au tour
                # suivant, pas être coalescé pendant 30 min (2026-08-11)
                self._visit_seen[ip] = now
                log.info("rdv: visite #%s notifiée dans #%s", v.get("_i"), FALLBACK_CHANNEL)
            except discord.HTTPException as e:
                failed.append(idx)
                log.warning("rdv: notif visite #%s impossible: %s", v.get("_i"), e)
        # Le curseur n'avance QUE jusqu'aux visites réellement traitées : l'avancer
        # inconditionnellement sautait pour toujours celles d'un tour où le salon était
        # indisponible (`?after=<curseur>` ne les renvoie plus). Garde-fou : si le
        # retard dépasse MAX_VISIT_BACKLOG, on avance quand même — sinon /api/visits
        # renverrait un lot de plus en plus gros toutes les 30 s.
        if failed:
            new_cursor = max(cursor, min(failed) - 1)
            if newest - new_cursor > MAX_VISIT_BACKLOG:
                log.error("rdv: %d visites en attente (salon indisponible) — curseur "
                          "avancé de force à #%d, les précédentes sont perdues",
                          newest - new_cursor, newest)
                new_cursor = newest
            else:
                log.warning("rdv: visites #%d..#%d non notifiées, curseur maintenu à #%d",
                            min(failed), newest, new_cursor)
        else:
            new_cursor = newest
        st.set("rdv_visits_cursor", new_cursor)

    @poll.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    async def _notify(self, a):
        """Renvoie True si la réponse a VRAIMENT été remise (DM ou salon de repli).

        Le curseur de `_poll_answers` s'appuie dessus : avancer sur un échec perdrait
        définitivement une réponse d'Elisa — l'enjeu même de ce cog (2026-08-11)."""
        ids = getattr(self.bot.cfg, "admin_ids", None) or []
        if not ids:
            log.warning("rdv: ADMIN_IDS vide, réponse #%s non notifiable", a.get("_i"))
            return False
        rdv = str(a.get("rdv", "?"))
        ans = a.get("answers") if isinstance(a.get("answers"), dict) else {}
        if rdv == "oui":
            title, color, head = "💌 Elle a répondu : OUI 🎉", 0x2ECC71, "**✅ Elle veut bien un soir de féria avec toi.**"
        elif rdv in ("peut-etre", "peut-être", "reflexion", "réflexion"):
            title, color, head = "💌 Elle a répondu", 0xE5A50A, "Elle dit **« pourquoi pas, à voir »** 🌙. La porte reste ouverte."
        else:
            title, color, head = "💌 Nouvelle réponse", 0x5865F2, f"Réponse : **{rdv}**"
        emb = discord.Embed(title=title, description=head, color=color)
        # Le site envoie aussi, depuis la refonte du 08/08, ce qu'elle a fait sur la
        # page (énigme, cartes ouvertes, surprises, durée, esquives du bouton). Sans
        # libellé ici, l'embed afficherait la clé brute ("enigme", "duree"...).
        labels = {"soir": "🌙 Le soir qui l'arrange", "envie": "✨ Ce dont elle a envie",
                  "note": None,
                  "enigme": "🧩 L'énigme", "cartes": "🃏 Les trois cartes",
                  "surprises": "🎁 Surprises trouvées", "duree": "⏱️ Temps passé sur la page",
                  "esquive": "🙈 Esquives du bouton « à voir »"}
        # ⚠️ Discord refuse un embed de plus de 25 champs et un nom de champ de plus de
        # 256 caractères : `ans` vient d'un POST PUBLIC, ses clés/valeurs ne sont pas
        # de confiance (2026-08-11). On borne, et on le dit plutôt que de perdre le DM.
        shown = 0
        for k, v in ans.items():
            if k in ("note", "essais") or not v:
                continue
            if shown >= MAX_ANSWER_FIELDS:
                emb.add_field(name="…", value="(champs supplémentaires ignorés)",
                              inline=False)
                break
            emb.add_field(name=str(labels.get(k) or k)[:256], value=str(v)[:200],
                          inline=True)
            shown += 1
        # str() défensif : le site peut renvoyer autre chose qu'une chaîne (« note »: 5)
        # et un AttributeError ici tuerait la boucle de notification.
        note = str(ans.get("note") or "").strip()
        if note:
            emb.add_field(name="📝 Son petit mot", value=note[:1000], inline=False)
        emb.set_footer(
            text=f"e.nicov1.fr · {a.get('ts', '')} · IP {a.get('ip', '?')}"[:2000])
        sent = False
        for uid in ids:
            try:
                user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
                await user.send(embed=emb)
                sent = True
                log.info("rdv: réponse #%s notifiée en DM à %s", a.get("_i"), uid)
            except discord.HTTPException as e:
                log.warning("rdv: DM à %s impossible: %s", uid, e)
        # Repli : si AUCUN DM n'a pu partir (ex. « messages privés » désactivés côté Nico),
        # poster dans un salon privé que seul le propriétaire du serveur voit.
        if not sent:
            ch = await self._owner_channel()
            if ch is None:
                log.warning("rdv: repli impossible, réponse #%s NON notifiée", a.get("_i"))
                return False
            try:
                await ch.send(embed=emb)
                sent = True
                log.info("rdv: réponse #%s postée dans #%s (repli DM)", a.get("_i"), FALLBACK_CHANNEL)
            except discord.HTTPException as e:
                log.warning("rdv: repli #%s impossible: %s", a.get("_i"), e)
        return sent

    @staticmethod
    def _private_overwrites(guild):
        """« Personne ne voit, le bot écrit » — l'unique état acceptable pour ce salon."""
        return {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }

    async def _ensure_private(self, ch):
        """True si @everyone ne voit PAS `ch` (overwrites réimposés au besoin).

        FAIL-CLOSED : si on ne peut pas le rendre privé, on ne publie RIEN."""
        ow = ch.overwrites_for(ch.guild.default_role)
        if ow.view_channel is False:
            return True
        # `None` = le salon suit sa catégorie (il a été déplacé/synchronisé) : à traiter
        # exactement comme « visible », on ne parie pas sur la catégorie. On REMPLACE
        # tous les overwrites au lieu de corriger @everyone seul : une catégorie
        # publique synchronisée peut contenir des autorisations explicites par rôle
        # qu'un simple @everyone=False ne masquerait pas.
        log.warning("rdv: #%s n'est plus privé (view_channel=%r) — réimposition",
                    ch.name, ow.view_channel)
        try:
            await ch.edit(overwrites=self._private_overwrites(ch.guild),
                          reason="rdv: salon privé (IP + message personnel)")
            return True
        except discord.HTTPException as e:
            log.error("rdv: #%s NON privé et non corrigeable (%s) — notification annulée",
                      ch.name, e)
            return False

    async def _owner_channel(self):
        """Salon privé #e-reponses (créé au besoin, visible du propriétaire seul).

        ⚠️ Ce salon reçoit l'IP + l'appareil de CHAQUE visiteur et, quand les DM sont
        fermés, l'embed complet d'une réponse (« son petit mot »). Les overwrites
        n'étaient posés qu'à la CRÉATION : n'importe quel salon homonyme préexistant,
        déplacé dans une catégorie publique ou dont les overwrites avaient dérivé, était
        adopté tel quel — et le bot y publiait tout ça devant le serveur entier. On
        persiste donc son id et on revérifie la confidentialité à CHAQUE usage
        (2026-08-11) ; aucune catégorie provisionnée ne couvre ce salon, personne d'autre
        ne repasse derrière.

        ⚠️ POURQUOI CE SALON N'EST PAS DANS « 🔒 Lock <SERVER_KEY> » (2026-08-11)
        Le reste du bot ne crée plus rien hors d'une catégorie provisionnée
        (`channels.ensure_channel`), parce qu'un salon né à la RACINE est public. Ici,
        non : la création pose explicitement `_private_overwrites`, le salon ne peut donc
        pas naître visible. Et le ranger dans Lock le rendrait au contraire MOINS privé —
        `provision._enforce_perms` (boucle de 5 min) réapplique `_lock_overwrites` à
        CHAQUE salon texte de la catégorie Lock : les porteurs des rôles M et O y
        gagneraient la lecture des réponses d'Elisa et des IP des visiteurs, et notre
        réimposition ci-dessous se battrait indéfiniment avec la sienne (deux `edit` par
        cycle, pour toujours). La catégorie Lock est le territoire de provision ; ce
        salon-ci est le seul du projet dont l'exigence est « propriétaire SEUL ».
        """
        gid = getattr(self.bot.cfg, "guild_id", 0)
        guild = self.bot.get_guild(gid) if gid else None
        if guild is None:
            log.warning("rdv: guild introuvable, pas de salon %s", FALLBACK_CHANNEL)
            return None
        st = self.bot.state
        cid = _i(st.get("rdv_channel_id", 0))
        ch = guild.get_channel(cid) if cid else None
        if not isinstance(ch, discord.TextChannel):
            # repli par NOM (première exécution, ou salon recréé à la main)
            ch = discord.utils.get(guild.text_channels, name=FALLBACK_CHANNEL)
        if ch is None:
            try:
                ch = await guild.create_text_channel(
                    FALLBACK_CHANNEL, overwrites=self._private_overwrites(guild),
                    topic="Réponses & visites du site e.nicov1.fr (privé). "
                          "Active « Messages privés des membres du serveur » pour recevoir les réponses en vrai DM.",
                    reason="rdv: salon privé réponses/visites")
                log.info("rdv: salon #%s créé (privé)", FALLBACK_CHANNEL)
            except discord.HTTPException as e:
                log.warning("rdv: création #%s impossible: %s", FALLBACK_CHANNEL, e)
                return None
        # une seule porte de confidentialité, qu'on vienne de l'id, du nom ou de la
        # création : FAIL-CLOSED, rien n'est publié dans un salon qu'on n'a pas pu fermer
        if not await self._ensure_private(ch):
            return None
        if cid != ch.id:      # state.set écrit sur disque : seulement si ça change
            st.set("rdv_channel_id", ch.id)
        return ch


async def setup(bot):
    await bot.add_cog(Rdv(bot))
