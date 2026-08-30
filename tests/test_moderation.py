"""Tests du cog `moderation` — sans réseau ni Discord (fakes légers).

POURQUOI : /purge annonce un compte qui doit être le compte RÉEL (messages > 14 j
ignorés, pas supprimés un à un) ; /lock puis /unlock doit restaurer l'overwrite
@everyone À L'IDENTIQUE (True/False/None), pas le remettre à neutre ; sans permission
Discord le bot doit refuser AVANT d'agir ; sans intent `message_content` le filtre
`contient` doit être dit « indisponible » ; chaque action doit être auditée ; les
serveurs ne se mélangent pas (salon cible d'un autre serveur = refus).
"""
import asyncio
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord  # noqa: E402

from bot.cogs import moderation as M  # noqa: E402

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------- fakes
class FakeState:
    def __init__(self):
        self.d = {}

    def get(self, k, default=None):
        return self.d.get(k, default)

    def set(self, k, v):
        self.d[k] = v


class FakeAudit:
    def __init__(self):
        self.rows = []

    def record(self, **kw):
        self.rows.append(kw)


class FakeCfg:
    guild_id = 100
    server_key = "R820"


class FakeIntents:
    message_content = False


class FakeBot:
    def __init__(self):
        self.cfg = FakeCfg()
        self.state = FakeState()
        self.audit = FakeAudit()
        self.intents = FakeIntents()


class FakeResponse:
    def __init__(self):
        self.sent = []
        self.deferred = False

    async def send_message(self, content=None, **kw):
        self.sent.append(dict(kw, content=content))

    async def defer(self, **kw):
        self.deferred = True

    def is_done(self):
        return self.deferred or bool(self.sent)


class FakeFollowup:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, **kw):
        self.sent.append(dict(kw, content=content))


class FakeUser:
    def __init__(self, uid=42):
        self.id = uid

    def __str__(self):
        return f"user{self.id}"

    @property
    def mention(self):
        return f"<@{self.id}>"


class FakeMsg:
    def __init__(self, mid, author_id=1, content="", age=timedelta(hours=1)):
        self.id = mid
        self.author = FakeUser(author_id)
        self.content = content
        self.created_at = NOW - age
        self.deleted = False

    async def delete(self):
        self.deleted = True


class FakeChannel:
    def __init__(self, cid=500, perms=None, msgs=(), everyone_ow=None):
        self.id = cid
        self.name = "general"
        self.mention = f"<#{cid}>"
        self.category = None
        self._perms = perms if perms is not None else discord.Permissions.all()
        self._msgs = list(msgs)
        self._ow = everyone_ow or discord.PermissionOverwrite()
        self.set_calls = []
        self.edits = []
        self.sent = []
        self.bulk_deleted = []

    def permissions_for(self, member):
        return self._perms

    async def history(self, limit=100):
        for m in self._msgs[:limit]:
            yield m

    async def delete_messages(self, msgs, reason=None):
        self.bulk_deleted.extend(msgs)

    def overwrites_for(self, target):
        # copie, comme discord.py (l'objet renvoyé n'est pas l'état vivant du salon)
        return discord.PermissionOverwrite.from_pair(*self._ow.pair())

    async def set_permissions(self, target, overwrite=None, reason=None):
        self.set_calls.append(overwrite)
        self._ow = overwrite if overwrite is not None else discord.PermissionOverwrite()

    async def edit(self, **kw):
        self.edits.append(kw)

    async def send(self, **kw):
        self.sent.append(kw)


class FakeGuild:
    def __init__(self):
        self.me = object()
        self.default_role = object()
        self.owner_id = 1


class FakeItx:
    def __init__(self, channel, uid=42):
        self.user = FakeUser(uid)
        self.guild_id = 100
        self.guild = FakeGuild()
        self.channel = channel
        self.response = FakeResponse()
        self.followup = FakeFollowup()

    def replies(self):
        return [s["content"] for s in self.response.sent + self.followup.sent if s.get("content")]


def run(coro):
    return asyncio.run(coro)


def _cog():
    return M.Moderation(FakeBot())


# ---------------------------------------------------------------------------- purs
class TestPurgeSplit(unittest.TestCase):
    def test_trop_vieux_separes(self):
        msgs = [FakeMsg(1), FakeMsg(2, age=timedelta(days=15)), FakeMsg(3, age=timedelta(days=13))]
        ok, old = M.purge_split(msgs, now=NOW)
        self.assertEqual([m.id for m in ok], [1, 3])
        self.assertEqual([m.id for m in old], [2])

    def test_filtre_membre(self):
        msgs = [FakeMsg(1, author_id=7), FakeMsg(2, author_id=8), FakeMsg(3, author_id=7)]
        ok, old = M.purge_split(msgs, member_id=7, now=NOW)
        self.assertEqual([m.id for m in ok], [1, 3])
        self.assertEqual(old, [])

    def test_filtre_contient_insensible_casse(self):
        msgs = [FakeMsg(1, content="Un LIEN ici"), FakeMsg(2, content="rien"), FakeMsg(3, content=None)]
        ok, _ = M.purge_split(msgs, contains="lien", now=NOW)
        self.assertEqual([m.id for m in ok], [1])

    def test_filtres_cumules(self):
        msgs = [FakeMsg(1, author_id=7, content="x"), FakeMsg(2, author_id=7, content="y"),
                FakeMsg(3, author_id=8, content="x")]
        ok, _ = M.purge_split(msgs, member_id=7, contains="x", now=NOW)
        self.assertEqual([m.id for m in ok], [1])


class TestOverwrite(unittest.TestCase):
    def test_snapshot_puis_lock_puis_restore_identique(self):
        ow = discord.PermissionOverwrite(send_messages=True, add_reactions=False, view_channel=True)
        snap = M.overwrite_snapshot(ow)
        self.assertEqual(snap["send_messages"], True)
        self.assertEqual(snap["add_reactions"], False)
        self.assertIsNone(snap["create_public_threads"])
        M.apply_lock(ow)
        self.assertFalse(ow.send_messages)
        self.assertTrue(ow.view_channel)                  # champ hors verrou intact
        M.restore_overwrite(ow, snap)
        self.assertEqual(ow.send_messages, True)
        self.assertEqual(ow.add_reactions, False)
        self.assertIsNone(ow.create_public_threads)
        self.assertTrue(ow.view_channel)

    def test_restore_sans_memoire_remet_neutre(self):
        ow = M.apply_lock(discord.PermissionOverwrite())
        M.restore_overwrite(ow, None)
        self.assertTrue(ow.is_empty())


class TestPermsEtLibelles(unittest.TestCase):
    def test_missing_perms(self):
        p = discord.Permissions(manage_messages=True)
        self.assertEqual(M.missing_perms(p, "manage_messages"), [])
        self.assertEqual(M.missing_perms(p, "manage_messages", "manage_roles"), ["Gérer les permissions"])

    def test_slowmode_label(self):
        self.assertEqual(M.slowmode_label(0), "désactivé")
        self.assertIn("5", M.slowmode_label(300))

    def test_bornes_slowmode_constante(self):
        self.assertEqual(M.SLOWMODE_MAX, 21600)


# ---------------------------------------------------------------------------- /purge
class TestPurgeCommande(unittest.TestCase):
    def test_compte_reel_et_vieux_ignores_et_audit(self):
        msgs = [FakeMsg(1), FakeMsg(2), FakeMsg(3, age=timedelta(days=20))]
        ch = FakeChannel(msgs=msgs)
        cog, itx = _cog(), FakeItx(ch)
        with mock.patch.object(M, "datetime") as dt:
            dt.now.return_value = NOW
            run(cog.purge.callback(cog, itx, nombre=10))
        self.assertTrue(itx.response.deferred)
        self.assertEqual([m.id for m in ch.bulk_deleted], [1, 2])
        txt = itx.followup.sent[0]["content"]
        self.assertIn("**2** message(s) supprimé(s) sur 3", txt)
        self.assertIn("1 ignoré(s)", txt)
        self.assertFalse(itx.followup.sent[0]["allowed_mentions"].everyone)
        self.assertEqual(cog.bot.audit.rows[-1]["action"], "purge")
        self.assertIn("2 supprimés", cog.bot.audit.rows[-1]["result"])

    def test_un_seul_message_supprime_individuellement(self):
        m = FakeMsg(1)
        ch = FakeChannel(msgs=[m])
        cog, itx = _cog(), FakeItx(ch)
        run(cog.purge.callback(cog, itx, nombre=5))
        self.assertTrue(m.deleted)
        self.assertEqual(ch.bulk_deleted, [])

    def test_refus_sans_permission_bot_rien_fait(self):
        ch = FakeChannel(perms=discord.Permissions(read_message_history=True), msgs=[FakeMsg(1)])
        cog, itx = _cog(), FakeItx(ch)
        run(cog.purge.callback(cog, itx, nombre=5))
        self.assertIn("Gérer les messages", itx.replies()[0])
        self.assertEqual(ch.bulk_deleted, [])
        self.assertEqual(cog.bot.audit.rows, [])

    def test_contient_indisponible_sans_intent(self):
        ch = FakeChannel(msgs=[FakeMsg(1, content="x")])
        cog, itx = _cog(), FakeItx(ch)
        run(cog.purge.callback(cog, itx, nombre=5, contient="x"))
        self.assertIn("indisponible", itx.replies()[0])
        self.assertEqual(ch.bulk_deleted, [])

    def test_contient_ok_avec_intent(self):
        ch = FakeChannel(msgs=[FakeMsg(1, content="spam"), FakeMsg(2, content="ok")])
        cog, itx = _cog(), FakeItx(ch)
        cog.bot.intents.message_content = True
        run(cog.purge.callback(cog, itx, nombre=5, contient="SPAM"))
        self.assertTrue(ch._msgs[0].deleted)
        self.assertFalse(ch._msgs[1].deleted)


# ---------------------------------------------------------------------------- /lock /unlock
class TestLockUnlock(unittest.TestCase):
    def test_lock_memorise_puis_unlock_restaure_exactement(self):
        prev = discord.PermissionOverwrite(send_messages=True, add_reactions=False, view_channel=True)
        ch = FakeChannel(everyone_ow=prev)
        cog, itx = _cog(), FakeItx(ch)
        run(cog.lock.callback(cog, itx, raison="incident"))
        locked = ch.set_calls[-1]
        self.assertFalse(locked.send_messages)
        self.assertFalse(locked.add_reactions)
        self.assertTrue(locked.view_channel)
        mem = cog.bot.state.get("locks")[str(ch.id)]
        self.assertEqual(mem["prev"]["send_messages"], True)
        self.assertEqual(mem["prev"]["add_reactions"], False)
        self.assertEqual(mem["reason"], "incident")
        self.assertEqual(ch.sent[0]["embed"].color.value, M.fmt.RED)
        self.assertEqual(cog.bot.audit.rows[-1]["action"], "lock")

        run(cog.unlock.callback(cog, FakeItx(ch)))
        restored = ch.set_calls[-1]
        self.assertEqual(restored.send_messages, True)     # pas None : l'état d'avant
        self.assertEqual(restored.add_reactions, False)
        self.assertTrue(restored.view_channel)
        self.assertNotIn(str(ch.id), cog.bot.state.get("locks"))
        self.assertEqual(cog.bot.audit.rows[-1]["action"], "unlock")

    def test_relock_ne_reecrase_pas_la_memoire(self):
        ch = FakeChannel(everyone_ow=discord.PermissionOverwrite(send_messages=True))
        cog = _cog()
        run(cog.lock.callback(cog, FakeItx(ch)))
        run(cog.lock.callback(cog, FakeItx(ch)))
        self.assertEqual(cog.bot.state.get("locks")[str(ch.id)]["prev"]["send_messages"], True)

    def test_unlock_sans_memoire_supprime_overwrite_vide_et_previent(self):
        ch = FakeChannel(everyone_ow=M.apply_lock(discord.PermissionOverwrite()))
        cog, itx = _cog(), FakeItx(ch)
        run(cog.unlock.callback(cog, itx))
        self.assertIsNone(ch.set_calls[-1])                # overwrite vide -> supprimé
        self.assertIn("Aucun verrou mémorisé", itx.replies()[0])

    def test_lock_refus_sans_manage_roles(self):
        ch = FakeChannel(perms=discord.Permissions(send_messages=True))
        cog, itx = _cog(), FakeItx(ch)
        run(cog.lock.callback(cog, itx))
        self.assertIn("Gérer les permissions", itx.replies()[0])
        self.assertEqual(ch.set_calls, [])
        self.assertIsNone(cog.bot.state.get("locks"))

    def test_lock_salon_autre_serveur_refuse(self):
        src, dst = FakeChannel(cid=1), FakeChannel(cid=2)
        cog, itx = _cog(), FakeItx(src)
        with mock.patch.object(M.chn, "server_of_channel", side_effect=lambda b, c: "R820" if c is src else "AVY-NAS"):
            run(cog.lock.callback(cog, itx, salon=dst))
        self.assertIn("ne se mélangent pas", itx.replies()[0])
        self.assertEqual(dst.set_calls, [])

    def test_lock_pose_meme_si_annonce_echoue(self):
        ch = FakeChannel()

        async def boom(**kw):
            raise discord.HTTPException(mock.Mock(status=403, reason="x"), "no")
        ch.send = boom
        cog, itx = _cog(), FakeItx(ch)
        run(cog.lock.callback(cog, itx))
        self.assertEqual(len(ch.set_calls), 1)
        self.assertIn("verrouillé", itx.replies()[0])


# ---------------------------------------------------------------------------- /slowmode
class TestSlowmode(unittest.TestCase):
    def test_applique_et_audit(self):
        ch = FakeChannel()
        cog, itx = _cog(), FakeItx(ch)
        run(cog.slowmode.callback(cog, itx, secondes=300))
        self.assertEqual(ch.edits[-1]["slowmode_delay"], 300)
        self.assertEqual(cog.bot.audit.rows[-1]["result"], "300s")

    def test_hors_bornes_refuse(self):
        ch = FakeChannel()
        cog = _cog()
        for v in (-1, M.SLOWMODE_MAX + 1):
            itx = FakeItx(ch)
            run(cog.slowmode.callback(cog, itx, secondes=v))
            self.assertIn("hors bornes", itx.replies()[0])
        self.assertEqual(ch.edits, [])

    def test_refus_sans_manage_channels(self):
        ch = FakeChannel(perms=discord.Permissions(manage_messages=True))
        cog, itx = _cog(), FakeItx(ch)
        run(cog.slowmode.callback(cog, itx, secondes=0))
        self.assertIn("Gérer les salons", itx.replies()[0])
        self.assertEqual(ch.edits, [])


# ---------------------------------------------------------------------------- /note
class TestNotes(unittest.TestCase):
    def test_ajout_liste_suppression(self):
        cog, ch = _cog(), FakeChannel()
        bob = FakeUser(77)
        run(cog.note_add.callback(cog, FakeItx(ch), membre=bob, texte="a insisté pour un accès root"))
        run(cog.note_add.callback(cog, FakeItx(ch), membre=bob, texte="deuxième"))
        run(cog.note_add.callback(cog, FakeItx(ch), membre=FakeUser(78), texte="autre"))
        self.assertEqual(cog.bot.state.get("notes")["seq"], 3)
        self.assertEqual(cog.bot.audit.rows[-1]["action"], "note_add")
        itx = FakeItx(ch)
        run(cog.note_list.callback(cog, itx, membre=bob))
        emb = itx.response.sent[0]["embed"]
        self.assertTrue(itx.response.sent[0]["ephemeral"])
        self.assertIn("#2", emb.description)
        self.assertIn("#1", emb.description)
        self.assertNotIn("autre", emb.description)
        run(cog.note_delete.callback(cog, FakeItx(ch), id=1))
        self.assertEqual([n["id"] for n in cog.bot.state.get("notes")["items"]], [2, 3])
        itx = FakeItx(ch)
        run(cog.note_delete.callback(cog, itx, id=1))
        self.assertIn("introuvable", itx.replies()[0])

    def test_note_vide_ou_trop_longue(self):
        cog, ch = _cog(), FakeChannel()
        itx = FakeItx(ch)
        run(cog.note_add.callback(cog, itx, membre=FakeUser(1), texte="   "))
        self.assertIn("vide", itx.replies()[0])
        itx = FakeItx(ch)
        run(cog.note_add.callback(cog, itx, membre=FakeUser(1), texte="x" * 501))
        self.assertIn("trop longue", itx.replies()[0])
        self.assertIsNone(cog.bot.state.get("notes"))


if __name__ == "__main__":
    unittest.main()
