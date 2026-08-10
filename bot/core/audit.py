"""Append-only audit trail for safe actions (also mirrored to journald via logging)."""
import asyncio
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger("discord-bot.audit")


class Audit:
    def __init__(self, path):
        self.path = path
        # callable async optionnel (action=,target=,user=,result=,upid=) -> feed #live_log
        self.notifier = None

    def record(self, *, user, action, target, result, upid=None):
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        line = f"{ts}\tuser={user}\taction={action}\ttarget={target}\tresult={result}\tupid={upid or '-'}"
        log.info("AUDIT %s", line)
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as e:
            log.warning("could not write audit log %s: %s", self.path, e)
        # miroir dans le salon live-log (best-effort, non bloquant)
        if self.notifier is not None:
            try:
                asyncio.get_running_loop().create_task(
                    self.notifier(action=action, target=target, user=user,
                                  result=result, upid=upid))
            except RuntimeError:
                pass  # pas de boucle asyncio en cours

    def tail(self, limit=20):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return f.readlines()[-limit:]
        except FileNotFoundError:
            return []
