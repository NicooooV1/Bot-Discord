"""Tests du cog `snapshot` — sérialisation, diff, rendu, stockage, quotidien. ZÉRO réseau.

POURQUOI : l'instantané est la seule trace « avant/après » de la structure de sécurité du
serveur Discord. Un JSON non déterministe rendrait le diff bruyant ; un diff qui oublie les
overwrites reproduirait le défaut de la référence JS ; une alerte qui part sans changement
de permissions (ou qui ne part pas quand un overwrite s'ouvre) trahirait sa raison d'être ;
une rétention qui élague les instantanés manuels perdrait un « avant migration » voulu.
"""
import asyncio
import copy
import datetime as dt
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord  # noqa: E402

from bot.cogs import snapshot as sn  # noqa: E402


# --------------------------------------------------------------------------- fakes
class FauxState:
    def __init__(self):
        self.d = {}

    def get(self, k, default=None):
        return self.d.get(k, default)

    def set(self, k, v):
        self.d[k] = v


class Obj(NS):
    """SimpleNamespace HASHABLE (clé de dict `overwrites`, comme un Role/Member réel)."""

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        return self is other


def role(rid, name, perms=0, hoist=False, managed=False, position=0):
    return Obj(id=rid, name=name, hoist=hoist, mentionable=False, managed=managed,
               position=position, color=discord.Colour(0), permissions=discord.Permissions(perms))


def chan(cid, name, typ="text", category=None, overwrites=None, topic=None, position=0):
    return NS(id=cid, name=name, type=getattr(discord.ChannelType, typ), category=category,
              position=position, topic=topic, nsfw=False, slowmode_delay=0,
              overwrites=overwrites or {})


def ow(allow=0, deny=0):
    o = discord.PermissionOverwrite()
    o.update(**{n: True for n, on in discord.Permissions(allow) if on})
    o.update(**{n: False for n, on in discord.Permissions(deny) if on})
    return o


EVERYONE = role(100, "@everyone", perms=discord.Permissions.text().value)
MOD = role(101, "M R820", perms=discord.Permissions(manage_messages=True, view_channel=True).value)
MEMBER = Obj(id=555, name="pierre", bot=False)   # pas de `hoist` -> membre (duck typing)


def guild(roles=None, channels=None):
    return NS(id=42, name="Homelab", owner_id=1, verification_level=discord.VerificationLevel.medium,
              explicit_content_filter=None, default_notifications=None,
              mfa_level=discord.MFALevel.disabled, afk_channel=None, afk_timeout=300,
              system_channel=None, rules_channel=None, public_updates_channel=None,
              preferred_locale=discord.Locale.french, premium_tier=0, features=["COMMUNITY"],
              icon=None, roles=roles if roles is not None else [EVERYONE, MOD],
              channels=channels if channels is not None else [], emojis=[], stickers=[],
              member_count=12)


def base_guild():
    cat = chan(200, "🔒 Lock R820", "category",
               overwrites={EVERYONE: ow(deny=discord.Permissions(view_channel=True).value),
                           MOD: ow(allow=discord.Permissions(view_channel=True).value)})
    txt = chan(201, "alertes", "text", category=cat, topic="alertes du bot",
               overwrites={EVERYONE: ow(deny=discord.Permissions(view_channel=True).value)})
    return guild(channels=[cat, txt])


def snap(g, **kw):
    return sn.serialize_guild(g, now=dt.datetime(2026, 8, 30, 4, 0, tzinfo=dt.timezone.utc), **kw)


# --------------------------------------------------------------------------- sérialisation
class TestSerialisation(unittest.TestCase):
    def test_deterministe_meme_entree_meme_json(self):
        a, b = sn.dump_json(snap(base_guild())), sn.dump_json(snap(base_guild()))
        self.assertEqual(a, b)

    def test_ordre_des_salons_sans_effet_sur_le_json(self):
        g = base_guild()
        g2 = base_guild()
        g2.channels = list(reversed(g2.channels))
        g2.roles = list(reversed(g2.roles))
        self.assertEqual(sn.dump_json(snap(g)), sn.dump_json(snap(g2)))

    def test_permissions_en_bits_et_en_noms(self):
        s = snap(base_guild())
        r = s["roles"]["101"]["permissions"]
        self.assertEqual(r["bits"], MOD.permissions.value)
        self.assertIn("manage_messages", r["names"])
        self.assertEqual(r["names"], sorted(r["names"]))

    def test_overwrites_enregistres_role_et_membre(self):
        cat = chan(300, "prive", overwrites={MOD: ow(allow=1024), MEMBER: ow(deny=1024)})
        s = snap(guild(channels=[cat]))
        o = s["channels"]["300"]["overwrites"]
        self.assertEqual(set(o), {"role:101", "member:555"})
        self.assertEqual(o["role:101"]["allow"]["names"], ["view_channel"])
        self.assertEqual(o["member:555"]["deny"]["names"], ["view_channel"])

    def test_cible_disparue_nom_indisponible(self):
        obj = discord.Object(id=999, type=discord.Role)
        s = snap(guild(channels=[chan(300, "x", overwrites={obj: ow(allow=1024)})]))
        o = s["channels"]["300"]["overwrites"]["role:999"]
        self.assertEqual(o["name"], "indisponible")

    def test_parent_et_type_et_meta_hors_diff(self):
        s = snap(base_guild(), members_intent=False)
        self.assertEqual(s["channels"]["201"]["parent_id"], "200")
        self.assertEqual(s["channels"]["201"]["type"], "text")
        self.assertEqual(s["meta"]["counts"], {"roles": 2, "channels": 2, "overwrites": 3,
                                                "emojis": 0, "stickers": 0})
        self.assertIs(s["meta"]["members_intent"], False)
        # le compteur de membres et l'horodatage ne créent pas de diff
        t = copy.deepcopy(s)
        t["meta"]["member_count"] = 99
        t["meta"]["taken_at"] = "2027-01-01T00:00:00+00:00"
        self.assertTrue(sn.diff_is_empty(sn.diff_snapshots(s, t)))


# --------------------------------------------------------------------------- diff
class TestDiff(unittest.TestCase):
    def setUp(self):
        self.a = snap(base_guild())

    def test_identique_vide(self):
        d = sn.diff_snapshots(self.a, copy.deepcopy(self.a))
        self.assertTrue(sn.diff_is_empty(d))
        self.assertFalse(sn.touches_permissions(d))
        self.assertEqual(sn.diff_counts(d), 0)

    def test_role_cree_supprime(self):
        g = base_guild()
        g.roles = [EVERYONE, role(102, "Invité", perms=1024)]
        d = sn.diff_snapshots(self.a, snap(g))
        self.assertEqual([r["name"] for r in d["roles"]["created"]], ["Invité"])
        self.assertEqual([r["name"] for r in d["roles"]["deleted"]], ["M R820"])
        self.assertTrue(sn.touches_permissions(d))

    def test_role_renomme_et_permissions_plus_moins(self):
        g = base_guild()
        g.roles = [EVERYONE, role(101, "Modo", perms=discord.Permissions(
            administrator=True, view_channel=True).value)]
        d = sn.diff_snapshots(self.a, snap(g))
        self.assertEqual(d["roles"]["renamed"], [{"id": "101", "old": "M R820", "new": "Modo"}])
        p = d["roles"]["perms"][0]
        self.assertEqual(p["added"], ["administrator"])
        self.assertEqual(p["removed"], ["manage_messages"])
        self.assertTrue(sn.touches_permissions(d))

    def test_renommage_seul_ne_touche_pas_aux_permissions(self):
        g = base_guild()
        g.roles = [EVERYONE, role(101, "Modo", perms=MOD.permissions.value)]
        g.channels[1].name = "alertes-bot"
        g.channels[1].topic = "autre"
        d = sn.diff_snapshots(self.a, snap(g))
        self.assertFalse(sn.diff_is_empty(d))
        self.assertFalse(sn.touches_permissions(d))
        self.assertEqual(d["channels"]["renamed"][0]["new"], "alertes-bot")
        self.assertEqual(d["channels"]["changed"][0]["field"], "topic")

    def test_salon_cree_supprime_deplace(self):
        g = base_guild()
        cat2 = chan(210, "📊 Supervision R820", "category")
        g.channels = [g.channels[0], cat2, chan(201, "alertes", category=cat2,
                                               topic="alertes du bot",
                                               overwrites=g.channels[1].overwrites),
                      chan(202, "dns", category=cat2)]
        d = sn.diff_snapshots(self.a, snap(g))
        self.assertEqual({c["name"] for c in d["channels"]["created"]}, {"📊 Supervision R820", "dns"})
        self.assertEqual(d["channels"]["deleted"], [])
        m = d["channels"]["moved"][0]
        self.assertEqual((m["name"], m["old_parent"], m["new_parent"]),
                         ("alertes", "🔒 Lock R820", "📊 Supervision R820"))
        d2 = sn.diff_snapshots(snap(g), self.a)
        self.assertEqual({c["name"] for c in d2["channels"]["deleted"]}, {"📊 Supervision R820", "dns"})

    def test_overwrite_gagne_perdu_modifie(self):
        g = base_guild()
        cat, txt = g.channels
        # @everyone perd son deny sur #alertes (salon devient public) = perdu
        txt.overwrites = {MEMBER: ow(allow=1024)}
        # la catégorie : M gagne send_messages, perd rien ; @everyone deny étendu
        cat.overwrites = {EVERYONE: ow(deny=discord.Permissions(view_channel=True, connect=True).value),
                          MOD: ow(allow=discord.Permissions(view_channel=True, send_messages=True).value)}
        d = sn.diff_snapshots(self.a, snap(g))
        o = d["overwrites"]
        self.assertEqual([(e["channel"], e["kind"], e["target"]) for e in o["gained"]],
                         [("alertes", "member", "pierre")])
        self.assertEqual([(e["channel"], e["target"], e["deny"]) for e in o["lost"]],
                         [("alertes", "@everyone", ["view_channel"])])
        ch = {(e["target"]): e for e in o["changed"]}
        self.assertEqual(ch["M R820"]["allow_added"], ["send_messages"])
        self.assertEqual(ch["@everyone"]["deny_added"], ["connect"])
        self.assertTrue(sn.touches_permissions(d))

    def test_salon_cree_avec_overwrites_les_compte_comme_gagnes(self):
        g = base_guild()
        g.channels.append(chan(250, "secret", category=g.channels[0],
                               overwrites={MOD: ow(allow=1024)}))
        d = sn.diff_snapshots(self.a, snap(g))
        self.assertEqual(d["overwrites"]["gained"][0]["channel"], "secret")
        self.assertTrue(sn.touches_permissions(d))

    def test_parametres_serveur_et_mfa(self):
        b = copy.deepcopy(self.a)
        b["guild"]["mfa_level"] = "require_2fa"
        b["guild"]["name"] = "Homelab 2"
        d = sn.diff_snapshots(self.a, b)
        self.assertEqual({x["field"] for x in d["guild"]}, {"mfa_level", "name"})
        self.assertTrue(sn.touches_permissions(d))
        b2 = copy.deepcopy(self.a)
        b2["guild"]["name"] = "Homelab 2"
        self.assertFalse(sn.touches_permissions(sn.diff_snapshots(self.a, b2)))

    def test_emojis_stickers(self):
        g = base_guild()
        g.emojis = [NS(id=7, name="pve", animated=False, managed=False)]
        d = sn.diff_snapshots(self.a, snap(g))
        self.assertEqual(d["emojis"]["created"], [{"id": "7", "name": "pve"}])
        self.assertFalse(sn.touches_permissions(d))


# --------------------------------------------------------------------------- rendu
class TestRendu(unittest.TestCase):
    def test_vide(self):
        t = sn.render_diff(sn.diff_snapshots(snap(base_guild()), snap(base_guild())), "a", "b")
        self.assertIn("Aucune différence", t)

    def test_sections_et_lignes(self):
        a = snap(base_guild())
        g = base_guild()
        g.roles = [EVERYONE, role(101, "Modo`", perms=discord.Permissions(administrator=True).value)]
        g.channels[1].overwrites = {}
        t = sn.render_diff(sn.diff_snapshots(a, snap(g)), "s1", "LIVE")
        self.assertIn("Diff s1 → LIVE", t)
        self.assertIn("## Rôles", t)
        self.assertIn("rôle renommé « M R820 » → « Modo' »", t)      # backtick neutralisé
        self.assertIn("+administrator", t)
        self.assertIn("-manage_messages", t)
        self.assertIn("## Overwrites de permissions", t)
        self.assertIn("- #alertes role « @everyone »", t)
        self.assertNotIn("## Salons", t)

    def test_troncature_propre(self):
        text = "\n".join(f"ligne {i} " + "x" * 50 for i in range(200))
        short, full = sn.truncate_report(text, 3500)
        self.assertLessEqual(len(short), 3500 + 80)
        self.assertTrue(short.rstrip().endswith("rapport complet en pièce jointe)"))
        self.assertNotIn("xxx\nligne", short.split("… (")[0][-5:])   # coupe sur une frontière de ligne
        self.assertEqual(full, text)
        s2, f2 = sn.truncate_report("court", 3500)
        self.assertEqual((s2, f2), ("court", None))


# --------------------------------------------------------------------------- stockage
class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="snap-")
        self.state = FauxState()
        self.store = sn.SnapshotStore(self.state, os.path.join(self.tmp, "snapshots"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _now(self, day, h=4):
        return dt.datetime(2026, 8, day, h, 0)   # naïf = heure locale, id stable en test

    def test_chemin_depuis_state_path(self):
        cfg = NS(state_path="/var/lib/discord-bot/state.json")
        self.assertEqual(sn.snapshots_dir(cfg), "/var/lib/discord-bot/snapshots")
        self.assertEqual(sn.snapshots_dir(NS()), "/var/lib/discord-bot/snapshots")

    def test_id_et_label_assainis(self):
        self.assertEqual(sn.make_id(self._now(30), "Avant Migration!"), "2026-08-30_040000-avant-migration")
        self.assertEqual(sn.make_id(self._now(30)), "2026-08-30_040000")
        with self.assertRaises(ValueError):
            self.store._path(42, "../../etc/passwd")

    def test_save_load_index_delete(self):
        s = snap(base_guild())
        e = self.store.save(42, s, label="init", author=7, now=self._now(30))
        self.assertEqual(e["id"], "2026-08-30_040000-init")
        self.assertEqual((e["roles"], e["channels"], e["overwrites"], e["auto"]), (2, 2, 3, False))
        self.assertTrue(os.path.exists(os.path.join(self.store.base, "42", e["id"] + ".json")))
        self.assertEqual(self.store.load(42, e["id"]), s)
        self.assertEqual([x["id"] for x in self.store.index(42)], [e["id"]])
        self.assertEqual(self.store.index(43), [])
        self.assertIsNone(self.store.load(42, "2026-01-01_000000"))
        self.assertTrue(self.store.delete(42, e["id"]))
        self.assertFalse(self.store.delete(42, e["id"]))
        self.assertEqual(self.store.index(42), [])

    def test_retention_n_elague_que_les_automatiques(self):
        s = snap(base_guild())
        self.store.save(42, s, label="manuel", author=7, auto=False, now=self._now(1))
        for day in range(2, 8):
            self.store.save(42, s, auto=True, now=self._now(day))
        gone = self.store.prune(42, keep=3)
        self.assertEqual(gone, ["2026-08-02_040000", "2026-08-03_040000", "2026-08-04_040000"])
        ids = [e["id"] for e in self.store.index(42)]
        self.assertEqual(ids, ["2026-08-01_040000-manuel", "2026-08-05_040000",
                               "2026-08-06_040000", "2026-08-07_040000"])
        for sid in gone:
            self.assertFalse(os.path.exists(os.path.join(self.store.base, "42", sid + ".json")))
        self.assertEqual(self.store.latest(42)["id"], "2026-08-07_040000")


# --------------------------------------------------------------------------- quotidien
class FauxChannel:
    def __init__(self):
        self.sent = []

    async def send(self, *a, **kw):
        self.sent.append((a, kw))


class FauxAudit:
    def __init__(self):
        self.rows = []

    def record(self, **kw):
        self.rows.append(kw)


def make_cog(tmp, guild_obj, alert_channel=None, keep=30):
    cfg = NS(state_path=os.path.join(tmp, "state.json"), guild_id=42, snapshot_keep=keep,
             alert_channel_id=777 if alert_channel is not None else 0, admin_ids=[1])
    bot = NS(cfg=cfg, state=FauxState(), audit=FauxAudit(), intents=NS(members=False),
             get_guild=lambda gid: guild_obj if gid == 42 else None,
             get_channel=lambda cid: alert_channel if cid == 777 else None)
    return sn.Snapshot(bot)


class TestQuotidien(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="snapd-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_premier_passage_ecrit_sans_alerte(self):
        ch = FauxChannel()
        cog = make_cog(self.tmp, base_guild(), ch)
        e, d = asyncio.run(cog.run_daily(base_guild()))
        self.assertIsNotNone(e)
        self.assertTrue(e["auto"])
        self.assertIsNone(d)
        self.assertEqual(ch.sent, [])

    def test_rien_change_pas_de_fichier(self):
        ch = FauxChannel()
        cog = make_cog(self.tmp, base_guild(), ch)
        asyncio.run(cog.run_daily(base_guild(), now=dt.datetime(2026, 8, 29, 4)))
        e, d = asyncio.run(cog.run_daily(base_guild(), now=dt.datetime(2026, 8, 30, 4)))
        self.assertIsNone(e)
        self.assertTrue(sn.diff_is_empty(d))
        self.assertEqual(len(cog.store.index(42)), 1)
        self.assertEqual(len(os.listdir(os.path.join(cog.store.base, "42"))), 1)
        self.assertEqual(ch.sent, [])

    def test_changement_sans_permissions_silencieux(self):
        ch = FauxChannel()
        cog = make_cog(self.tmp, base_guild(), ch)
        asyncio.run(cog.run_daily(base_guild(), now=dt.datetime(2026, 8, 29, 4)))
        g = base_guild()
        g.channels[1].name = "alertes-v2"
        e, d = asyncio.run(cog.run_daily(g, now=dt.datetime(2026, 8, 30, 4)))
        self.assertIsNotNone(e)
        self.assertFalse(sn.touches_permissions(d))
        self.assertEqual(len(cog.store.index(42)), 2)
        self.assertEqual(ch.sent, [])

    def test_changement_de_permissions_alerte(self):
        ch = FauxChannel()
        cog = make_cog(self.tmp, base_guild(), ch)
        asyncio.run(cog.run_daily(base_guild(), now=dt.datetime(2026, 8, 29, 4)))
        g = base_guild()
        g.channels[1].overwrites = {}       # #alertes devient visible de @everyone
        e, d = asyncio.run(cog.run_daily(g, now=dt.datetime(2026, 8, 30, 4)))
        self.assertIsNotNone(e)
        self.assertEqual(len(ch.sent), 1)
        _a, kw = ch.sent[0]
        self.assertIn("Permissions Discord modifiées", kw["embed"].title)
        self.assertIn("- #alertes role « @everyone »", kw["embed"].description)
        self.assertIsInstance(kw["allowed_mentions"], discord.AllowedMentions)
        self.assertNotIn("file", kw)

    def test_alerte_longue_jointe_en_fichier(self):
        ch = FauxChannel()
        cog = make_cog(self.tmp, base_guild(), ch)
        asyncio.run(cog.run_daily(base_guild(), now=dt.datetime(2026, 8, 29, 4)))
        g = base_guild()
        g.roles = [EVERYONE, MOD] + [role(1000 + i, f"role-{i}", perms=8) for i in range(60)]
        _e, _d = asyncio.run(cog.run_daily(g, now=dt.datetime(2026, 8, 30, 4)))
        _a, kw = ch.sent[0]
        self.assertIn("file", kw)
        self.assertLessEqual(len(kw["embed"].description), 4096)

    def test_retention_appliquee_au_quotidien(self):
        cog = make_cog(self.tmp, base_guild(), None, keep=2)
        for day in range(1, 6):
            g = base_guild()
            g.channels[1].topic = f"jour {day}"
            asyncio.run(cog.run_daily(g, now=dt.datetime(2026, 8, day, 4)))
        ids = [e["id"] for e in cog.store.index(42)]
        self.assertEqual(ids, ["2026-08-04_040000", "2026-08-05_040000"])

    def test_pas_de_salon_alertes_configure_ne_leve_pas(self):
        cog = make_cog(self.tmp, base_guild(), None)
        asyncio.run(cog.run_daily(base_guild(), now=dt.datetime(2026, 8, 29, 4)))
        g = base_guild()
        g.roles = [EVERYONE]
        e, _d = asyncio.run(cog.run_daily(g, now=dt.datetime(2026, 8, 30, 4)))
        self.assertIsNotNone(e)


class TestOwner(unittest.TestCase):
    def test_owner_ok_breakglass_ou_tier_O(self):
        cog = make_cog(tempfile.mkdtemp(prefix="snapo-"), base_guild())
        cog.cfg.gestion_servers = {"R820": {"view": 10, "mod": 11, "owner": 12}}
        cog.cfg.server_key = "R820"

        def itx(uid, roles=()):
            return NS(user=NS(id=uid, roles=[NS(id=r) for r in roles]), guild=NS(owner_id=1))
        self.assertTrue(cog._owner_ok(itx(1)))            # propriétaire du guild
        self.assertTrue(cog._owner_ok(itx(1, ())))
        self.assertTrue(cog._owner_ok(itx(50, (12,))))    # rôle O
        self.assertFalse(cog._owner_ok(itx(50, (11,))))   # rôle M = refus
        self.assertFalse(cog._owner_ok(itx(50, (10,))))


if __name__ == "__main__":
    unittest.main()
