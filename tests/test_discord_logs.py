"""Tests du cog `discord_logs` — sans réseau ni Discord.

POURQUOI CES TESTS : #discord-logs est le journal de SÉCURITÉ du serveur Discord. Un diff
d'overwrites faux ferait manquer (ou inventer) un rôle qui gagne « Gérer les salons » ;
une édition d'embed (aperçu de lien) journalisée comme une édition humaine noierait le
salon ; journaliser #discord-logs lui-même bouclerait ; un exécutant « deviné » sans
journal d'audit violerait « le bot réel dans ses mots ». Les cas ci-dessous verrouillent
le diff des permissions, la troncature, les exclusions (bots, salon de logs, éditions
sans changement), la dégradation sans intents, le regroupement par lots et l'absence
d'exécutant.
"""
import asyncio
import datetime as dt
import os
import sys
import unittest
from collections import deque
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord  # noqa: E402

from bot.cogs import discord_logs as dl  # noqa: E402

NOW = dt.datetime(2026, 8, 30, 12, 0, tzinfo=dt.timezone.utc)
GID = 100
LOG_ID = 555


# --------------------------------------------------------------------------- fakes
class FauxState:
    def __init__(self, d=None):
        self.d = dict(d or {})

    def get(self, k, default=None):
        return self.d.get(k, default)

    def set(self, k, v):
        self.d[k] = v


class FauxRole:
    def __init__(self, rid, name, perms=None):
        self.id, self.name = rid, name
        self.permissions = perms or discord.Permissions.none()
        self.color, self.hoist, self.mentionable = discord.Colour.default(), False, False


class FauxUser:
    def __init__(self, uid, name, bot=False, created=None):
        self.id, self.name, self.bot = uid, name, bot
        self.discriminator = "0"
        self.created_at = created or (NOW - dt.timedelta(days=400))


class FauxChannel:
    def __init__(self, cid, name, guild=None, parent=None, **kw):
        self.id, self.name, self.guild, self.parent = cid, name, guild, parent
        self.topic = kw.get("topic")
        self.nsfw = kw.get("nsfw", False)
        self.slowmode_delay = kw.get("slowmode", 0)
        self.category = kw.get("category")
        self.overwrites = kw.get("overwrites", {})
        self.sent = []

    async def send(self, *a, **kw):
        self.sent.append(kw)
        return NS(id=len(self.sent), pin=self._pin)

    async def _pin(self):
        pass

    async def fetch_message(self, mid):
        raise discord.NotFound(NS(status=404, reason="x"), "x")


class FauxGuild:
    def __init__(self, audit=False, entries=()):
        self.id = GID
        self.me = NS(guild_permissions=NS(view_audit_log=audit, manage_guild=False,
                                          manage_roles=True, manage_webhooks=False))
        self.member_count = 12
        self.roles, self.channels_by_id, self.categories = {}, {}, []
        self._entries = list(entries)
        self.default_role = FauxRole(GID, "@everyone")

    def get_role(self, rid):
        return self.roles.get(rid)

    def get_channel(self, cid):
        return self.channels_by_id.get(cid)

    async def audit_logs(self, limit=10, action=None):
        for e in self._entries:
            if action is None or e.action == action:
                yield e


class FauxBot:
    def __init__(self, guild, members=False, content=False, cfg=None):
        self.state = FauxState({"discord_logs": {"channel": LOG_ID}})
        self.cfg = cfg or NS(guild_id=GID, alert_channel_id=0, welcome_role_id=0, server_key="R820")
        self.intents = NS(members=members, message_content=content)
        self._guild = guild
        self.audit = NS(record=lambda **kw: None)
        self.recorded = []
        self.audit = NS(record=lambda **kw: self.recorded.append(kw))

    def get_guild(self, gid):
        return self._guild if gid == GID else None

    def get_channel(self, cid):
        return self._guild.channels_by_id.get(cid)

    async def fetch_channel(self, cid):
        raise discord.NotFound(NS(status=404, reason="x"), "x")

    async def wait_until_ready(self):
        pass


def make_cog(audit=False, entries=(), members=False, content=False, alert=False):
    g = FauxGuild(audit=audit, entries=entries)
    logch = FauxChannel(LOG_ID, "discord-logs", g)
    gen = FauxChannel(1, "general", g)
    g.channels_by_id = {LOG_ID: logch, 1: gen}
    bot = FauxBot(g, members=members, content=content)
    if alert:
        al = FauxChannel(77, "alertes", g)
        g.channels_by_id[77] = al
        bot.cfg.alert_channel_id = 77
    cog = dl.DiscordLogs(bot)
    return cog, g, logch, gen


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------- fonctions pures
class TestTexte(unittest.TestCase):
    def test_clip_900(self):
        self.assertEqual(len(dl.clip("x" * 5000)), 900)
        self.assertTrue(dl.clip("x" * 5000).endswith("…"))
        self.assertEqual(dl.clip("court"), "court")

    def test_safe_neutralise(self):
        s = dl._safe("a`b\nc@everyone", 50)
        self.assertNotIn("`", s)
        self.assertNotIn("\n", s)

    def test_contenu_sans_intent(self):
        self.assertIn("intent Contenu des messages désactivé", dl.content_or_reason("", False))
        self.assertIn("vide", dl.content_or_reason("", True))
        self.assertEqual(dl.content_or_reason("salut", False), "salut")

    def test_pieces_jointes(self):
        txt = dl.attachments_text([NS(filename="a.png", size=2048), NS(filename="b`.zip", size=0)])
        self.assertIn("a.png (2.0 Kio)", txt)
        self.assertNotIn("`", txt)
        self.assertIsNone(dl.attachments_text([]))


class TestDiffOverwrites(unittest.TestCase):
    def test_gain_perte_nouveau_supprime_et_danger(self):
        r1, r2, r3, m = FauxRole(1, "Invités"), FauxRole(2, "Staff"), FauxRole(3, "Anciens"), FauxUser(9, "nico")
        before = {r1: discord.PermissionOverwrite(view_channel=True, send_messages=True),
                  r3: discord.PermissionOverwrite(view_channel=False)}
        after = {r1: discord.PermissionOverwrite(view_channel=False, send_messages=True),
                 r2: discord.PermissionOverwrite(manage_channels=True),
                 m: discord.PermissionOverwrite(view_channel=True)}
        lines, danger = dl.diff_overwrites(before, after)
        blob = "\n".join(lines)
        self.assertTrue(danger)                       # Staff gagne « Gérer les salons »
        self.assertIn("@Invités", blob)
        self.assertIn("⛔ Voir le salon", blob)
        self.assertNotIn("Envoyer des messages", blob)  # inchangé : pas listé
        self.assertIn("@Staff", blob)
        self.assertIn("✅ Gérer les salons", blob)
        self.assertIn("nico", blob)
        self.assertIn("@Anciens", blob)
        self.assertIn("overwrite supprimé", blob)

    def test_identique_vide(self):
        r = FauxRole(1, "x")
        ow = {r: discord.PermissionOverwrite(view_channel=True)}
        self.assertEqual(dl.diff_overwrites(ow, ow), ([], False))

    def test_retour_a_herite_n_est_pas_dangereux(self):
        r = FauxRole(1, "x")
        lines, danger = dl.diff_overwrites({r: discord.PermissionOverwrite(manage_channels=True)},
                                           {r: discord.PermissionOverwrite()})
        self.assertFalse(danger)
        self.assertIn("hérité", lines[0])


class TestDiffRole(unittest.TestCase):
    def test_permissions_ajoutees_retirees(self):
        before = discord.Permissions(send_messages=True, connect=True)
        after = discord.Permissions(send_messages=True, ban_members=True)
        self.assertEqual(dl.diff_role_perms(before, after), (["ban_members"], ["connect"]))

    def test_diff_role_lignes_et_danger(self):
        b = FauxRole(1, "Amis", discord.Permissions(send_messages=True))
        a = FauxRole(1, "Amis+", discord.Permissions(send_messages=True, administrator=True))
        a.hoist = True
        lines, danger = dl.diff_role(b, a)
        blob = "\n".join(lines)
        self.assertTrue(danger)
        self.assertIn("Amis+", blob)
        self.assertIn("Administrateur", blob)
        self.assertIn("affiché séparément", blob)
        self.assertNotIn("retirées", blob)

    def test_diff_role_sans_changement(self):
        r = FauxRole(1, "x")
        self.assertEqual(dl.diff_role(r, r), ([], False))


class TestDiffSalon(unittest.TestCase):
    def test_nom_sujet_slowmode_categorie(self):
        b = FauxChannel(1, "general", topic="a", slowmode=0, category=NS(id=10, name="Cat A"))
        a = FauxChannel(1, "général-2", topic="b", slowmode=30, category=NS(id=11, name="Cat B"), nsfw=True)
        lines, danger = dl.diff_channel(b, a)
        blob = "\n".join(lines)
        self.assertFalse(danger)
        for s in ("général-2", "« a » → « b »", "0 s → 30 s", "Cat A → Cat B", "NSFW"):
            self.assertIn(s, blob)

    def test_sans_changement(self):
        c = FauxChannel(1, "x")
        self.assertEqual(dl.diff_channel(c, c), ([], False))


class TestAgeEtAudit(unittest.TestCase):
    def test_compte_jeune(self):
        self.assertTrue(dl.is_young(NOW - dt.timedelta(days=2), NOW))
        self.assertFalse(dl.is_young(NOW - dt.timedelta(days=8), NOW))
        self.assertIsNone(dl.account_age_s(None, NOW))
        self.assertEqual(dl.account_age_s(NOW - dt.timedelta(hours=1), NOW), 3600)

    def test_pick_audit_entry_fenetre_et_cible(self):
        old = NS(created_at=NOW - dt.timedelta(seconds=60), target=NS(id=5), user="A")
        good = NS(created_at=NOW - dt.timedelta(seconds=3), target=NS(id=5), user="B")
        other = NS(created_at=NOW - dt.timedelta(seconds=1), target=NS(id=6), user="C")
        self.assertIsNone(dl.pick_audit_entry([old], 5, NOW))
        self.assertIs(dl.pick_audit_entry([old, other, good], 5, NOW), good)
        self.assertIs(dl.pick_audit_entry([other], None, NOW), other)
        self.assertIsNone(dl.pick_audit_entry([], 5, NOW))


class TestFileEtCompteurs(unittest.TestCase):
    def test_take_batch_fifo_10(self):
        q = deque(range(25))
        self.assertEqual(dl.take_batch(q), list(range(10)))
        self.assertEqual(len(q), 15)
        self.assertEqual(dl.take_batch(q, 3), [10, 11, 12])

    def test_bump_counts_purge_90j(self):
        c = {"2026-05-01": {"voice": 3}, "2026-08-01": {"voice": 1}}
        c = dl.bump_counts(c, "message_edit", "2026-08-30")
        self.assertNotIn("2026-05-01", c)
        self.assertIn("2026-08-01", c)
        self.assertEqual(c["2026-08-30"]["message_edit"], 1)
        dl.bump_counts(c, "message_edit", "2026-08-30")
        self.assertEqual(c["2026-08-30"]["message_edit"], 2)
        self.assertEqual(dl.bump_counts("pas un dict", "x", "2026-08-30")["2026-08-30"]["x"], 1)


class TestEmbeds(unittest.TestCase):
    def test_make_embed_borne(self):
        emb = dl.make_embed("t" * 300, "d" * 5000, fields=[("n" * 300, "v" * 2000), ("vide", None)])
        self.assertLessEqual(len(emb.title), 256)
        self.assertLessEqual(len(emb.description), 4096)
        self.assertEqual(len(emb.fields), 1)
        self.assertLessEqual(len(emb.fields[0].value), 1024)

    def test_statut_degrade_sans_intents(self):
        emb = dl.status_embed(channel=FauxChannel(1, "discord-logs"), intents={"members": False, "message_content": False},
                              perms={"view_audit_log": False, "manage_guild": False, "manage_roles": True},
                              counts={"voice": 3}, queue_len=0, started=1.0, last_event=None,
                              welcome_role=None, alert_channel=None)
        blob = emb.description + "".join(f.name + f.value for f in emb.fields)
        self.assertIn("intent Membres désactivé", blob)
        self.assertIn("arrivées/départs", blob)
        self.assertIn("indisponible", blob)
        self.assertIn("exécutants « indisponibles »", blob)
        self.assertIn("voice ×3", blob)
        self.assertIn("désactivé (WELCOME_ROLE_ID=0)", blob)

    def test_statut_sans_salon_rouge(self):
        emb = dl.status_embed(channel=None, intents={"members": True, "message_content": True},
                              perms={"view_audit_log": True}, counts={}, queue_len=2, started=1.0,
                              last_event=None, welcome_role=None, alert_channel=None)
        self.assertEqual(emb.color.value, dl.fmt.RED)
        self.assertIn("aucun", emb.description)


# ------------------------------------------------------------------------- listeners
def _edit_payload(cid, before, after_content, edited=True, author=None, mid=42):
    author = author or getattr(before, "author", None)
    after = NS(content=after_content, author=author, attachments=[], jump_url="https://discord.com/x")
    data = {"content": after_content, "author": {"id": author.id, "bot": author.bot}}
    if edited:
        data["edited_timestamp"] = "2026-08-30T12:00:00+00:00"
    return NS(guild_id=GID, channel_id=cid, message_id=mid, data=data, cached_message=before, message=after)


class TestMessages(unittest.TestCase):
    def test_edition_reelle_sans_intent_contenu(self):
        cog, g, logch, gen = make_cog()
        before = NS(author=FauxUser(9, "nico"), content="", attachments=[])
        run(cog.on_raw_message_edit(_edit_payload(1, before, "nouveau")))
        self.assertEqual(len(cog._queue), 1)
        emb = cog._queue[0]
        self.assertIn("aller au message", emb.description)
        self.assertIn("intent Contenu des messages désactivé", emb.fields[0].value)
        self.assertEqual(cog.counts["message_edit"], 1)
        self.assertIn("2026-08-30", cog.bot.state.get("discord_logs_counts") or {}) if dt.date.today().isoformat() == "2026-08-30" else None

    def test_edition_ignoree_bot_identique_embed_et_salon_logs(self):
        cog, g, logch, gen = make_cog(content=True)
        bot_user = FauxUser(2, "Edmine", bot=True)
        run(cog.on_raw_message_edit(_edit_payload(1, NS(author=bot_user, content="a", attachments=[]), "b")))
        human = NS(author=FauxUser(9, "nico"), content="même", attachments=[])
        run(cog.on_raw_message_edit(_edit_payload(1, human, "même")))            # contenu identique
        run(cog.on_raw_message_edit(_edit_payload(1, human, "autre", edited=False)))  # aperçu de lien
        run(cog.on_raw_message_edit(_edit_payload(LOG_ID, human, "autre")))      # #discord-logs lui-même
        self.assertEqual(len(cog._queue), 0)
        run(cog.on_raw_message_edit(_edit_payload(1, human, "autre")))
        self.assertEqual(len(cog._queue), 1)
        self.assertEqual(cog._queue[0].fields[0].value, "même")
        self.assertEqual(cog._queue[0].fields[1].value, "autre")

    def test_suppression_hors_cache_et_executant_indisponible(self):
        cog, g, logch, gen = make_cog(audit=False)
        run(cog.on_raw_message_delete(NS(guild_id=GID, channel_id=1, message_id=7, cached_message=None)))
        self.assertIn("hors cache", cog._queue[0].description)
        msg = NS(author=FauxUser(9, "nico"), content="x" * 2000, attachments=[NS(filename="f.txt", size=10)])
        run(cog.on_raw_message_delete(NS(guild_id=GID, channel_id=1, message_id=8, cached_message=msg)))
        emb = cog._queue[1]
        self.assertEqual(len(emb.fields[0].value), 900)
        self.assertIn("f.txt", emb.fields[1].value)
        self.assertIn("Voir les journaux d'audit", emb.fields[2].value)
        # message d'un bot ou dans #discord-logs : rien
        run(cog.on_raw_message_delete(NS(guild_id=GID, channel_id=LOG_ID, message_id=9, cached_message=msg)))
        run(cog.on_raw_message_delete(NS(guild_id=GID, channel_id=1, message_id=10,
                                         cached_message=NS(author=FauxUser(2, "b", bot=True), content="", attachments=[]))))
        self.assertEqual(len(cog._queue), 2)

    def test_executant_via_audit(self):
        entry = NS(action=discord.AuditLogAction.kick, created_at=dt.datetime.now(dt.timezone.utc),
                   target=NS(id=9), user=FauxUser(1, "nico"), reason="spam")
        cog, g, *_ = make_cog(audit=True, entries=[entry])
        user, reason, note = run(cog.executor(g, discord.AuditLogAction.kick, 9))
        self.assertEqual((user.name, reason, note), ("nico", "spam", None))
        user, reason, note = run(cog.executor(g, discord.AuditLogAction.kick, 10))
        self.assertIsNone(user)
        self.assertIn("aucune entrée d'audit", note)
        self.assertIn("nico", cog.exec_line(FauxUser(1, "nico"), "spam", None))


class TestMembres(unittest.TestCase):
    def _member(self, uid=9, created=None, bot=False):
        cog, g, logch, gen = make_cog(alert=True)
        m = FauxUser(uid, "new", bot=bot, created=created)
        m.guild = g
        return cog, g, m

    def test_arrivee_compte_recent_alerte_et_role_refuse(self):
        cog, g, m = self._member(created=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1))
        cog.cfg.welcome_role_id = 33
        role = FauxRole(33, "Invité")
        g.roles[33] = role

        async def add_roles(r, reason=None):
            raise discord.Forbidden(NS(status=403, reason="Forbidden"), "Missing Permissions")
        m.add_roles = add_roles
        run(cog.on_member_join(m))
        emb = cog._queue[0]
        self.assertIn("Compte récent", emb.description)
        self.assertIn("refusé par Discord", emb.description)
        self.assertEqual(emb.color.value, dl.fmt.ORANGE)
        self.assertEqual(len(g.channels_by_id[77].sent), 1)          # ligne dans #alertes
        self.assertIn("alerte: discord_young_account", g.channels_by_id[77].sent[0]["embed"].footer.text)

    def test_arrivee_normale_role_ajoute(self):
        cog, g, m = self._member()
        cog.cfg.welcome_role_id = 33
        g.roles[33] = FauxRole(33, "Invité")
        given = []

        async def add_roles(r, reason=None):
            given.append(r.name)
        m.add_roles = add_roles
        run(cog.on_member_join(m))
        self.assertEqual(given, ["Invité"])
        self.assertIn("✅ ajouté", cog._queue[0].description)
        self.assertEqual(cog._queue[0].color.value, dl.fmt.GREEN)
        self.assertEqual(g.channels_by_id[77].sent, [])
        self.assertEqual(cog.bot.recorded[0]["action"], "welcome_role")

    def test_bot_ajoute_mis_en_avant(self):
        cog, g, m = self._member(uid=50, bot=True)
        run(cog.on_member_join(m))
        self.assertIn("BOT ajouté", cog._queue[0].description)
        self.assertEqual(cog.counts["bot_added"], 1)
        self.assertEqual(len(g.channels_by_id[77].sent), 1)

    def test_depart_apres_ban_non_journalise(self):
        cog, g, m = self._member()
        m.roles = [g.default_role, FauxRole(3, "Amis")]
        m.joined_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)
        run(cog.on_member_ban(g, m))
        run(cog.on_member_remove(m))
        self.assertEqual([e.title for e in cog._queue], ["🔨 Bannissement"])
        cog._recent_bans.clear()
        run(cog.on_member_remove(m))
        self.assertEqual(cog._queue[-1].title, "📤 Départ")
        self.assertIn("@Amis", cog._queue[-1].fields[0].value)
        self.assertNotIn("@everyone", cog._queue[-1].fields[0].value)

    def test_roles_pseudo_timeout(self):
        cog, g, m = self._member()
        b = NS(guild=g, id=9, name="new", discriminator="0", roles=[FauxRole(1, "A")], nick=None, timed_out_until=None)
        a = NS(guild=g, id=9, name="new", discriminator="0",
               roles=[FauxRole(1, "A"), FauxRole(2, "Modo", discord.Permissions(kick_members=True))],
               nick="Nico", timed_out_until=dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10))
        run(cog.on_member_update(b, a))
        d = cog._queue[0].description
        self.assertIn("@Modo", d)
        self.assertIn("Nico", d)
        self.assertIn("timeout", d)
        self.assertEqual(cog._queue[0].color.value, dl.fmt.ORANGE)
        self.assertEqual(len(g.channels_by_id[77].sent), 1)
        run(cog.on_member_update(a, a))
        self.assertEqual(len(cog._queue), 1)


class TestVocalEtLots(unittest.TestCase):
    def test_vocal_compact(self):
        cog, g, *_ = make_cog()
        m = FauxUser(9, "nico")
        m.guild = g
        v1, v2 = FauxChannel(5, "Général", g), FauxChannel(6, "Jeux", g)
        run(cog.on_voice_state_update(m, NS(channel=None), NS(channel=v1)))
        run(cog.on_voice_state_update(m, NS(channel=v1), NS(channel=v2)))
        run(cog.on_voice_state_update(m, NS(channel=v2), NS(channel=v2)))   # mute : ignoré
        run(cog.on_voice_state_update(m, NS(channel=v2), NS(channel=None)))
        self.assertEqual(cog.counts["voice"], 3)
        self.assertIn("→", cog._queue[1].description)

    def test_flush_par_lots_de_10(self):
        cog, g, logch, gen = make_cog()
        for i in range(25):
            cog.enqueue("voice", discord.Embed(description=str(i)))
        for _ in range(3):
            run(cog.flush.coro(cog))
        self.assertEqual([len(k["embeds"]) for k in logch.sent], [10, 10, 5])
        self.assertEqual(len(cog._queue), 0)
        for k in logch.sent:      # AllowedMentions n'a pas d'__eq__ : on lit ses champs
            am = k["allowed_mentions"]
            self.assertFalse(am.everyone or am.users or am.roles)
        run(cog.flush.coro(cog))
        self.assertEqual(len(logch.sent), 3)          # file vide : aucun envoi

    def test_flush_erreur_http_remet_en_file(self):
        cog, g, logch, gen = make_cog()

        async def boom(*a, **kw):
            raise discord.HTTPException(NS(status=500, reason="boom"), "boom")
        logch.send = boom
        for i in range(3):
            cog.enqueue("voice", discord.Embed(description=str(i)))
        run(cog.flush.coro(cog))
        self.assertEqual([e.description for e in cog._queue], ["0", "1", "2"])
        self.assertIn("envoi impossible", cog.last_error)


class TestSecurite(unittest.TestCase):
    def test_alerte_anti_doublon(self):
        cog, g, *_ = make_cog(alert=True)
        self.assertTrue(run(cog.security_alert("invite", "x")))
        self.assertFalse(run(cog.security_alert("invite", "x")))
        self.assertTrue(run(cog.security_alert("webhook", "y")))
        self.assertEqual(len(g.channels_by_id[77].sent), 2)

    def test_alerte_sans_salon(self):
        cog, g, *_ = make_cog(alert=False)
        self.assertFalse(run(cog.security_alert("invite", "x")))

    def test_salon_modifie_overwrites_dangereux(self):
        cog, g, logch, gen = make_cog(alert=True)
        staff = FauxRole(2, "Staff")
        b = FauxChannel(1, "general", g)
        a = FauxChannel(1, "general", g, overwrites={staff: discord.PermissionOverwrite(manage_webhooks=True)})
        run(cog.on_guild_channel_update(b, a))
        d = cog._queue[0].description
        self.assertIn("@Staff", d)
        self.assertIn("✅ Gérer les webhooks", d)
        self.assertIn("exécutant indisponible", d)
        self.assertEqual(len(g.channels_by_id[77].sent), 1)

    def test_invitation(self):
        cog, g, *_ = make_cog(alert=True)
        inv = NS(guild=g, code="abc`123", inviter=FauxUser(9, "nico"), channel=FauxChannel(1, "general"),
                 max_uses=0, max_age=86400, temporary=False)
        run(cog.on_invite_create(inv))
        d = cog._queue[0].description
        self.assertIn("abc'123", d)
        self.assertIn("illimité", d)
        self.assertIn("dans 1j 0h", d)
        self.assertEqual(len(g.channels_by_id[77].sent), 1)

    def test_autre_guild_ignore(self):
        cog, g, *_ = make_cog()
        other = FauxGuild()
        other.id = 999
        m = FauxUser(9, "x")
        m.guild = other
        run(cog.on_member_join(m))
        run(cog.on_raw_message_delete(NS(guild_id=999, channel_id=1, message_id=7, cached_message=None)))
        self.assertEqual(len(cog._queue), 0)


if __name__ == "__main__":
    unittest.main()
