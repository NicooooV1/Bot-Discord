"""Tests du rendu enrichi de l'embed #hyperviseur Aveyron (avy_embeds.hyperviseur).

POURQUOI CES TESTS : l'embed a été densifié le 2026-08-31 (services, MAJ APT, stockages,
disques, activité RRD, métriques par invité). Deux dangers verrouillés ici :
  - Discord rejette un embed > 25 champs ou un champ > 1024 caractères avec un 400
    silencieux côté salon (message JAMAIS mis à jour) — on borne le pire cas réaliste ;
  - la convention « None = illisible ≠ [] = vide » (2026-08-20) doit survivre aux
    nouveaux champs : un cycle dégradé ne doit ni planter le rendu, ni maquiller une
    lecture en échec en « tout va bien ».
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.cogs import avy_embeds as e  # noqa: E402

STATUS = {
    "uptime": 86400 * 12, "cpu": 0.07, "loadavg": ["0.42", "0.31", "0.28"],
    "cpuinfo": {"model": "Intel(R) N100", "cpus": 4, "sockets": 1},
    "memory": {"used": 9 * 2**30, "total": 32 * 2**30},
    "swap": {"used": 0, "total": 4 * 2**30},
    "rootfs": {"used": 20 * 2**30, "total": 100 * 2**30},
    "pveversion": "pve-manager/9.0.3/aaaa",
    "current-kernel": {"release": "6.14.8-2-pve"},
    "boot-info": {"mode": "efi", "secureboot": 0},
    "ksm": {"shared": 512 * 2**20},
}
DATA = {
    "status": STATUS,
    "storages": [
        {"storage": "local-zfs", "type": "zfspool", "active": 1,
         "used": 90 * 2**30, "total": 100 * 2**30},
        {"storage": "nas-backup", "type": "cifs", "active": 0, "enabled": 1},
    ],
    "tasks": [{"type": "vzdump", "id": "101", "status": "OK", "user": "root@pam",
               "starttime": 1_756_600_000, "endtime": 1_756_600_240}],
    "rrd_last": {"pressurecpusome": 1.2, "pressureiosome": 0.4},
    "rrd_hour": {"iowait": (0.02, 0.11, 0.03),
                 "netin": (1_200_000.0, 9e6, 2e6),
                 "netout": (300_000.0, 2e6, 5e5)},
    "disks": [{"devpath": "/dev/nvme0n1", "model": "Samsung SSD 980", "health": "PASSED",
               "size": 1_000_204_886_016, "wearout": 88, "temp": 41}],
    "services": [
        {"name": "pveproxy", "state": "running", "unit-state": "enabled"},
        {"name": "pvedaemon", "state": "running", "unit-state": "enabled"},
        {"name": "syslog", "state": "stopped", "unit-state": "not-found"},
    ],
    "updates": [{"Package": "pve-kernel"}, {"Package": "openssl"}],
}
CLUSTER = {"quorate": True, "online": {"nas": True, "ms01": True, "llm": True},
           "ping_ms": 33.0, "certs": {"nas": 210.0}}
GUESTS = [("jellyfin-avy", {"vmid": 1_000_110, "status": "running", "cpu": 0.03,
                            "mem": 2 * 2**30, "maxmem": 8 * 2**30, "uptime": 3600}),
          ("web-avy", {"vmid": 1_000_120, "status": "stopped"})]


def _noms(emb):
    return [f.name for f in emb.fields]


class TestHyperviseurRiche(unittest.TestCase):
    def test_champs_enrichis_presents(self):
        emb = e.hyperviseur("nas", DATA, CLUSTER, GUESTS, "-avy")
        noms = _noms(emb)
        for attendu in ("Amorçage", "KSM", "Activité (1 h)", "⚙️ Services", "MAJ APT"):
            self.assertIn(attendu, noms)
        self.assertTrue(any(n.startswith("💽 Stockages") for n in noms))
        self.assertTrue(any(n.startswith("💿 Disques") for n in noms))
        # limites Discord : > 25 champs ou champ > 1024 = embed jamais publié
        self.assertLessEqual(len(emb.fields), 25)
        for f in emb.fields:
            self.assertLessEqual(len(f.value), 1024)

    def test_metriques_par_invite(self):
        emb = e.hyperviseur("nas", DATA, CLUSTER, GUESTS, "-avy")
        champ = next(f for f in emb.fields if f.name.startswith("📦"))
        self.assertIn("CPU 3 %", champ.value)
        self.assertIn("RAM 25 %", champ.value)
        # un invité éteint n'affiche pas de métriques inventées
        self.assertIn("**web**", champ.value)
        self.assertNotIn("web** (120) · CPU", champ.value)

    def test_service_arrete_visible_unites_absentes_ignorees(self):
        data = dict(DATA)
        data["services"] = DATA["services"] + [
            {"name": "pvescheduler", "state": "stopped", "unit-state": "enabled"}]
        emb = e.hyperviseur("nas", data, CLUSTER, GUESTS, "-avy")
        champ = next(f for f in emb.fields if f.name == "⚙️ Services")
        self.assertIn("pvescheduler", champ.value)
        # syslog est « not-found » : ni compté, ni accusé
        self.assertNotIn("syslog", champ.value)

    def test_cycle_degrade_convention_none(self):
        """None (illisible) ≠ [] (vide) : le rendu tient debout et le DIT."""
        data = {"status": STATUS, "storages": None, "tasks": None, "rrd_last": {},
                "rrd_hour": {}, "disks": [], "services": None, "updates": None}
        emb = e.hyperviseur("nas", data, {}, [], "-avy")
        noms = _noms(emb)
        self.assertNotIn("⚙️ Services", noms)     # illisible : champ omis, pas « 0/0 »
        self.assertNotIn("MAJ APT", noms)
        champ = next(f for f in emb.fields if f.name == "💽 Stockages")
        self.assertIn("illisibles", champ.value)

    def test_systeme_a_jour_liste_vide(self):
        data = dict(DATA)
        data["updates"] = []
        emb = e.hyperviseur("nas", data, CLUSTER, GUESTS, "-avy")
        champ = next(f for f in emb.fields if f.name == "MAJ APT")
        self.assertIn("à jour", champ.value)


if __name__ == "__main__":
    unittest.main()


POOL_OK = {"name": "data-pool", "size": 24_000_277_250_048, "alloc": 4_859_791_941_632,
           "free": 19_140_485_308_416, "health": "ONLINE", "frag": 4, "dedup": 1,
           "detail": {"state": "ONLINE", "errors": "No known data errors",
                      "scan": "scrub repaired 0B in 00:31:39 with 0 errors on "
                              "Sun Aug 30 00:46:39 2026",
                      "children": [{"leaf": 0, "name": "data-pool", "children": [
                          {"leaf": 0, "name": "raidz1-0", "state": "ONLINE", "children": [
                              {"leaf": 1, "name": "sdb1", "state": "ONLINE",
                               "read": 0, "write": 0, "cksum": 0},
                              {"leaf": 1, "name": "sdc1", "state": "ONLINE",
                               "read": 0, "write": 0, "cksum": 0},
                              {"leaf": 1, "name": "sdf1", "state": "ONLINE",
                               "read": 0, "write": 0, "cksum": 0}]}]}]}}
POOL_DEGRADED = {"name": "rpool", "size": 498_216_206_336, "alloc": 48_403_083_264,
                 "health": "DEGRADED", "frag": 43,
                 "detail": {"state": "DEGRADED",
                            "scan": "scrub repaired 0B in 00:15:01 with 3 errors on "
                                    "Wed Aug 26 14:15:20 2026",
                            "children": [{"leaf": 0, "name": "rpool", "children": [
                                {"leaf": 0, "name": "mirror-0", "children": [
                                    {"leaf": 1, "name": "sda3", "state": "ONLINE",
                                     "read": 0, "write": 0, "cksum": 0},
                                    {"leaf": 1, "name": "sde3", "state": "FAULTED",
                                     "read": 12, "write": 4, "cksum": 0}]}]}]}}
DISKS_NAS = [
    {"devpath": "/dev/nvme0n1", "type": "nvme", "health": "PASSED", "wearout": 99, "temp": 41},
    {"devpath": "/dev/sda", "type": "ssd", "health": "PASSED", "wearout": 86, "temp": 30},
    {"devpath": "/dev/sdb", "type": "hdd", "health": "PASSED", "wearout": "N/A", "temp": 38},
    {"devpath": "/dev/sdc", "type": "hdd", "health": "UNKNOWN", "wearout": "N/A"},
]


class TestDisquesEtPoolsZFS(unittest.TestCase):
    """Synthèse disques + charge E/S + pools ZFS dans #hyperviseur (Nico 2026-09-03)."""

    def _champ(self, emb, prefix):
        for f in emb.fields:
            if f.name.startswith(prefix):
                return f
        self.fail(f"champ {prefix!r} absent de {_noms(emb)}")

    def test_synthese_disques_roster_et_charge(self):
        data = {**DATA, "disks": DISKS_NAS, "zfs": [POOL_OK]}
        emb = e.hyperviseur("nas", data, CLUSTER, GUESTS, "-avy")
        f = self._champ(emb, "💿 Disques (4)")
        l1, roster, charge = f.value.split("\n")
        self.assertIn("🟢 3/4 SMART PASS", l1)
        self.assertIn("⚪ 1 inconnu", l1)
        self.assertIn("2 HDD", l1)
        self.assertIn("max 41 °C", l1)
        self.assertIn("vie mini 86 %", l1)
        # un point par disque, sans modèle ni détail
        self.assertEqual(roster, "🟢`nvme0n1` 🟢`sda` 🟢`sdb` ⚪`sdc`")
        self.assertNotIn("Samsung", f.value)
        self.assertIn("🟢 charge E/S : IO-wait 2 %", charge)
        self.assertIn("pression IO 0 %", charge)

    def test_pool_online_scrub_ok(self):
        lines, worst = e.zfs_lignes([POOL_OK])
        self.assertEqual(len(lines), 1)
        self.assertIn("🟢 `data-pool` online · raidz1 · 3/3 disques", lines[0])
        self.assertIn("frag 4 %", lines[0])
        self.assertIn("✅ scrub OK 30/08", lines[0])
        self.assertLess(worst, 80)

    def test_pool_degrade_passe_au_rouge(self):
        data = {**DATA, "disks": DISKS_NAS, "zfs": [POOL_OK, POOL_DEGRADED]}
        emb = e.hyperviseur("nas", data, CLUSTER, GUESTS, "-avy")
        f = self._champ(emb, "🧱 Pools ZFS (2)")
        ligne = f.value.split("\n")[1]
        self.assertIn("🔴 `rpool` degraded · mirror · 1/2 disques", ligne)
        self.assertIn("⚠️ 16 erreur(s) vdev", ligne)
        self.assertIn("🔴 scrub 3 erreur(s) 26/08", ligne)
        self.assertEqual(emb.color.value, e.fmt.health_color(95))

    def test_pools_illisibles_dits_et_absents_omis(self):
        emb = e.hyperviseur("nas", {**DATA, "zfs": None}, CLUSTER, GUESTS, "-avy")
        self.assertIn("illisibles", self._champ(emb, "🧱 Pools ZFS").value)
        emb = e.hyperviseur("nas", {**DATA, "zfs": []}, CLUSTER, GUESTS, "-avy")
        self.assertFalse(any(n.startswith("🧱") for n in _noms(emb)))

    def test_charge_es_forte_en_rouge(self):
        data = {**DATA, "disks": DISKS_NAS, "zfs": [],
                "rrd_hour": {"iowait": (0.31, 0.40, 0.12)}, "rrd_last": {"pressureiosome": 9.0}}
        emb = e.hyperviseur("nas", data, CLUSTER, GUESTS, "-avy")
        self.assertIn("🔴 charge E/S : IO-wait 31 %", self._champ(emb, "💿").value)

    def test_limites_discord_11_disques_5_pools(self):
        disks = [{"devpath": f"/dev/sd{c}", "type": "hdd", "health": "PASSED",
                  "wearout": "N/A", "temp": 35} for c in "abcdefghijk"]
        pools = [{**POOL_OK, "name": f"pool{i}"} for i in range(5)]
        emb = e.hyperviseur("nas", {**DATA, "disks": disks, "zfs": pools},
                            CLUSTER, GUESTS, "-avy")
        self.assertLessEqual(len(emb.fields), 25)
        for f in emb.fields:
            self.assertLessEqual(len(f.value), 1024)
