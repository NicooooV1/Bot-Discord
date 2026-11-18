"""storages() : storages PVE « dir » remplacés par les disques de l'hôte (NAS médias inclus)."""
import asyncio
import unittest

from bot.core.influx import Influx


class TestStorages(unittest.TestCase):
    def test_dir_remplaces_par_disques_et_nas(self):
        inf = Influx.__new__(Influx)
        inf.cfg = type("C", (), {"pve_node": "pve", "influx_bucket": "Proxmox"})()
        pve = [{"host": "local", "type": "dir", "used": 5, "avail": 5},
               {"host": "vps-dump", "type": "dir", "used": 87, "avail": 13},
               {"host": "local-lvm", "type": "lvmthin", "used": 1, "avail": 3},
               {"host": "pbs-nas", "type": "pbs", "used": 2, "avail": 8}]
        disk = [{"path": "/mnt/nas-media", "used": 9, "free": 1},
                {"path": "/mnt/media-local", "used": 87, "free": 13}]

        async def aq(flux):
            return pve if '"system"' in flux else disk
        inf.aq = aq
        out = {s["name"]: s for s in asyncio.run(inf.storages())}
        self.assertNotIn("local", out)
        self.assertNotIn("vps-dump", out)
        self.assertIn("local-lvm", out)
        self.assertEqual(out["NAS — médias (volume2)"]["total"], 10)
        self.assertEqual(out["NAS — sauvegardes PBS (volume1)"]["pct"], 20.0)
        self.assertEqual(out["Disque local médias + VPS (sda4)"]["pct"], 87.0)


if __name__ == "__main__":
    unittest.main()
