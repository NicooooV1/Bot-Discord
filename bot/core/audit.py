"""Append-only audit trail for safe actions (also mirrored to journald via logging)."""
import asyncio
import logging
import os
from collections import deque
from datetime import datetime, timezone

from .bg import spawn

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
            # La boucle est vérifiée AVANT de créer la coroutine : l'inverse produirait
            # un « coroutine was never awaited » si record() est appelé hors boucle.
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return  # pas de boucle asyncio en cours (appel depuis un thread)
            # bg.spawn et non create_task nu : la boucle ne garde qu'une weakref sur les
            # tâches, et une exception du notifier finissait en « Task exception was
            # never retrieved » sans jamais être journalisée. (2026-08-11)
            spawn(self.notifier(action=action, target=target, user=user,
                                result=result, upid=upid),
                  name=f"audit-feed:{action}", logger=log)

    def tail(self, limit=20):
        # deque(maxlen=…) : mémoire bornée. readlines() chargeait TOUT le journal
        # (append-only, jamais tourné) pour n'en garder que les 20 dernières lignes.
        # La rotation reste à faire hors code (/etc/logrotate.d/discord-bot ; record()
        # rouvre le fichier à chaque écriture, un rename suffit). 2026-08-11
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return list(deque(f, maxlen=max(0, limit)))
        except FileNotFoundError:
            return []
        except OSError as e:
            log.warning("lecture du journal d'audit %s impossible: %s", self.path, e)
            return []
