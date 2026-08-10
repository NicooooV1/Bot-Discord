"""Tiny JSON-backed persistent state (survives restarts / nightly host power-off).

Stored under StateDirectory (/var/lib/discord-bot/state.json). Used for the pinned
dashboard message id, owned-alert de-dup levels, and the last daily-report date.
"""
import json
import logging
import os
import tempfile
import threading
import time

log = logging.getLogger("discord-bot.state")


class State:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self.d = self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
        except ValueError:
            # state.json corrompu : ne PAS l'écraser en silence — on perdrait entre
            # autres les délais « jamais » de /yt (fichiers alors supprimés à 4 j).
            # Le fichier est mis de côté pour diagnostic/restauration manuelle.
            bak = f"{self.path}.corrupt-{int(time.time())}"
            try:
                os.replace(self.path, bak)
                log.warning("state.json CORROMPU -> sauvegardé en %s, état repart vide "
                            "(délais /yt et réglages perdus, restaurer si possible)", bak)
            except OSError:
                log.warning("state.json corrompu et impossible à mettre de côté")
            return {}

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), prefix=".state-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.d, f, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def get(self, key, default=None):
        with self._lock:
            return self.d.get(key, default)

    def set(self, key, value):
        with self._lock:
            self.d[key] = value
            self._save()

    # --- owned-alert de-dup (edge-triggered) ---
    def alert_level(self, key):
        return (self.d.get("alerts", {}) or {}).get(key, {}).get("level")

    def set_alert(self, key, level, value=None):
        with self._lock:
            self.d.setdefault("alerts", {})[key] = {"level": level, "value": value}
            self._save()

    def clear_alert(self, key):
        with self._lock:
            self.d.setdefault("alerts", {}).pop(key, None)
            self._save()
