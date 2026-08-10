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
import asyncio
import json
import logging
import time
import urllib.request

import discord
from discord.ext import commands, tasks

log = logging.getLogger("discord-bot.rdv")

ANSWERS_URL = "http://10.3.20.121:8080/api/answers"   # endpoints privés du site web-e (CT121)
VISITS_URL = "http://10.3.20.121:8080/api/visits"
POLL_SECONDS = 30
VISIT_COALESCE_S = 30 * 60    # une même IP -> au plus 1 notification de visite / 30 min
FALLBACK_CHANNEL = "e-reponses"   # salon privé (propriétaire seul)


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

    async def cog_load(self):
        self.poll.start()

    def cog_unload(self):
        self.poll.cancel()

    def _get(self, url, after):
        req = urllib.request.Request(f"{url}?after={int(after)}")
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read() or b"[]")
        return data if isinstance(data, list) else []

    @tasks.loop(seconds=POLL_SECONDS)
    async def poll(self):
        # seul le bot PRIMAIRE relaie (une seule instance en DM Nico)
        if not getattr(self.bot.cfg, "is_primary", True):
            return
        await self._poll_answers()
        await self._poll_visits()

    async def _poll_answers(self):
        st = self.bot.state
        cursor = int(st.get("rdv_cursor", 0) or 0)
        try:
            data = await asyncio.to_thread(self._get, ANSWERS_URL, cursor)
        except Exception as e:  # noqa: BLE001
            log.debug("rdv poll answers: %s", e)
            return
        if not data:
            return
        # 1er passage : ne pas spammer avec les réponses déjà là (tests) — caler le curseur.
        if not st.get("rdv_init"):
            st.set("rdv_cursor", max((int(a.get("_i", 0)) for a in data), default=cursor))
            st.set("rdv_init", True)
            log.info("rdv: curseur initialisé (%d réponse(s) préexistante(s) ignorée(s))", len(data))
            return
        for a in sorted(data, key=lambda x: int(x.get("_i", 0))):
            await self._notify(a)
            st.set("rdv_cursor", int(a.get("_i", cursor)))

    async def _poll_visits(self):
        """Visites de l'accueil -> message compact dans #e-reponses (pas de DM, moins intrusif).
        Pas de saut initial : visits.jsonl démarre vide, le curseur suffit."""
        st = self.bot.state
        cursor = int(st.get("rdv_visits_cursor", 0) or 0)
        try:
            data = await asyncio.to_thread(self._get, VISITS_URL, cursor)
        except Exception as e:  # noqa: BLE001
            log.debug("rdv poll visits: %s", e)
            return
        if not data:
            return
        # coalescence : au sein du lot ET dans le temps, 1 notif max par IP / 30 min
        now = time.monotonic()
        by_ip = {}
        for v in sorted(data, key=lambda x: int(x.get("_i", 0))):
            by_ip[v.get("ip", "?")] = v
        for ip, v in by_ip.items():
            last = self._visit_seen.get(ip)
            if last is not None and (now - last) < VISIT_COALESCE_S:
                continue
            self._visit_seen[ip] = now
            when = f"<t:{int(v['epoch'])}:t>" if v.get("epoch") else str(v.get("ts", ""))[:16]
            msg = f"👀 **Visite du site** · {_device(v.get('ua'))} · {when} · IP `{ip}`"
            ch = await self._owner_channel()
            if ch is not None:
                try:
                    await ch.send(msg)
                    log.info("rdv: visite #%s notifiée dans #%s", v.get("_i"), FALLBACK_CHANNEL)
                except discord.HTTPException as e:
                    log.warning("rdv: notif visite impossible: %s", e)
        st.set("rdv_visits_cursor", max(int(v.get("_i", 0)) for v in data))

    @poll.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    async def _notify(self, a):
        ids = getattr(self.bot.cfg, "admin_ids", None) or []
        if not ids:
            return
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
        for k, v in ans.items():
            if k in ("note", "essais") or not v:
                continue
            emb.add_field(name=labels.get(k, k), value=str(v)[:200], inline=True)
        note = (ans.get("note") or "").strip()
        if note:
            emb.add_field(name="📝 Son petit mot", value=note[:1000], inline=False)
        emb.set_footer(text=f"e.nicov1.fr · {a.get('ts', '')} · IP {a.get('ip', '?')}")
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
                return
            try:
                await ch.send(embed=emb)
                log.info("rdv: réponse #%s postée dans #%s (repli DM)", a.get("_i"), FALLBACK_CHANNEL)
            except discord.HTTPException as e:
                log.warning("rdv: repli #%s impossible: %s", a.get("_i"), e)

    async def _owner_channel(self):
        """Salon privé #e-reponses (créé au besoin, visible du propriétaire seul)."""
        gid = getattr(self.bot.cfg, "guild_id", 0)
        guild = self.bot.get_guild(gid) if gid else None
        if guild is None:
            log.warning("rdv: guild introuvable, pas de salon %s", FALLBACK_CHANNEL)
            return None
        ch = discord.utils.get(guild.text_channels, name=FALLBACK_CHANNEL)
        if ch is not None:
            return ch
        try:
            me = guild.me
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            }
            ch = await guild.create_text_channel(
                FALLBACK_CHANNEL, overwrites=overwrites,
                topic="Réponses & visites du site e.nicov1.fr (privé). "
                      "Active « Messages privés des membres du serveur » pour recevoir les réponses en vrai DM.",
                reason="rdv: salon privé réponses/visites")
            log.info("rdv: salon #%s créé", FALLBACK_CHANNEL)
            return ch
        except discord.HTTPException as e:
            log.warning("rdv: création #%s impossible: %s", FALLBACK_CHANNEL, e)
            return None


async def setup(bot):
    await bot.add_cog(Rdv(bot))
