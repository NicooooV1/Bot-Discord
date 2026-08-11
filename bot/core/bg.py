"""Filet de sécurité des tâches de fond.

POURQUOI CE MODULE EXISTE
-------------------------
Une exception non rattrapée dans une `discord.ext.tasks.loop` **arrête la boucle
définitivement** : discord.py journalise une fois « Unhandled exception in internal
background task » et ne la relance JAMAIS. Le bot reste vivant, la commande `/health`
reste verte, le heartbeat continue de pinguer — et pourtant la supervision, les alertes
ou l'auto-suppression sont mortes jusqu'au prochain redémarrage.

Le projet comptait 21 boucles de fond et 0 gestionnaire d'erreur (audit du 2026-08-11,
constats « boucle avy morte » ×2). Ce module rend le filet OBLIGATOIRE et OBSERVABLE :

  - `guard_cog_loops(cog)` pose un gestionnaire sur toutes les boucles d'un cog ;
  - une boucle qui meurt est relancée avec un **backoff exponentiel** (15 s → 5 min) ;
  - `REGISTRY` expose l'état de chaque boucle pour `/health`.

⚠️ PIÈGE : `Loop.restart()` ne fait RIEN quand la tâche est déjà terminée
(`if self._task and not self._task.done()` dans discord.py). Après un échec la tâche EST
terminée : il faut appeler `start()`. Un `restart()` nu ici serait un no-op silencieux —
c'est-à-dire exactement le bug qu'on cherche à corriger.

⚠️ PIÈGE : ne jamais relancer depuis le gestionnaire d'erreur lui-même — il s'exécute
encore dans la tâche mourante. On programme la relance dans une tâche détachée, dont on
garde une référence forte (la boucle asyncio ne conserve qu'une weakref : une tâche sans
référence peut être ramassée par le GC en plein vol).
"""
import asyncio
import logging
import time

from discord.ext import tasks

log = logging.getLogger("discord-bot.bg")

# Backoff : 15 s, 30 s, 60 s, 120 s, 240 s, puis plafond à 300 s.
_BACKOFF_BASE = 15
_BACKOFF_MAX = 300

# Références fortes sur les tâches détachées (relances + spawn), sans quoi le
# ramasse-miettes peut les faire disparaître silencieusement en cours d'exécution.
_TASKS = set()


class LoopState:
    """État observable d'une boucle de fond, lu par /health."""

    __slots__ = ("name", "loop", "failures", "last_error", "last_error_at",
                 "last_restart_at")

    def __init__(self, name, loop):
        self.name = name
        self.loop = loop
        self.failures = 0
        self.last_error = None
        self.last_error_at = 0.0
        self.last_restart_at = 0.0

    @property
    def running(self):
        try:
            return bool(self.loop.is_running())
        except Exception:  # noqa: BLE001 — l'état de santé ne doit jamais lever
            return False

    @property
    def healthy(self):
        """Saine = en cours d'exécution ET sans échec dans les 15 dernières minutes."""
        return self.running and (not self.last_error_at
                                 or time.time() - self.last_error_at > 900)


#: nom qualifié « Cog.boucle » -> LoopState
REGISTRY = {}


def spawn(coro, name=None, logger=None):
    """`asyncio.create_task` avec référence forte + journalisation des exceptions.

    À utiliser partout où une tâche détachée est créée : sans référence forte la boucle
    asyncio ne garde qu'une weakref et la tâche peut disparaître avant la fin, sans un
    mot dans les logs.
    """
    lg = logger or log
    task = asyncio.create_task(coro, name=name)
    _TASKS.add(task)

    def _done(t):
        _TASKS.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            lg.error("tâche détachée %s terminée en erreur: %s",
                     name or t.get_name(), exc, exc_info=exc)

    task.add_done_callback(_done)
    return task


def _schedule_restart(state, delay, logger):
    """Relance la boucle après `delay` secondes, depuis une tâche détachée."""

    async def _later():
        await asyncio.sleep(delay)
        if state.running:
            return
        try:
            # start() et non restart() : restart() est un no-op quand la tâche est
            # déjà terminée, ce qui est précisément le cas après un échec.
            state.loop.start()
            state.last_restart_at = time.time()
            logger.info("boucle %s relancée après %d s d'attente", state.name, delay)
        except RuntimeError:
            pass                      # déjà relancée entre-temps
        except Exception:             # noqa: BLE001
            logger.exception("boucle %s : relance impossible", state.name)

    spawn(_later(), name=f"restart:{state.name}", logger=logger)


def _attach(loop, name, logger):
    """Pose le gestionnaire d'erreur sur UNE boucle (idempotent)."""
    if getattr(loop, "_edmine_guarded", False):
        return REGISTRY.get(name)
    state = LoopState(name, loop)
    REGISTRY[name] = state

    async def _on_error(*args):
        # discord.py appelle le gestionnaire avec (exception,) ou, pour une boucle
        # injectée dans un cog, (cog, exception) : l'exception est toujours en dernier.
        exc = args[-1] if args else None
        state.failures += 1
        state.last_error = f"{type(exc).__name__}: {exc}"[:200]
        state.last_error_at = time.time()
        delay = min(_BACKOFF_MAX, _BACKOFF_BASE * (2 ** min(state.failures - 1, 5)))
        logger.error("boucle %s interrompue (échec #%d) — relance dans %d s",
                     name, state.failures, delay, exc_info=exc)
        _schedule_restart(state, delay, logger)

    try:
        loop.error(_on_error)
        loop._edmine_guarded = True
    except Exception:  # noqa: BLE001 — poser le filet ne doit jamais casser un cog
        logger.exception("boucle %s : filet non posé", name)
    return state


def guard_cog_loops(cog, logger=None):
    """Pose le filet sur TOUTES les `tasks.loop` déclarées par un cog.

    À appeler une fois dans `cog_load()` (ou `__init__`). Idempotent : un second appel
    ne repose pas de gestionnaire.

    Renvoie la liste des noms de boucles protégées.
    """
    lg = logger or log
    names = []
    seen = set()
    for klass in type(cog).__mro__:
        for attr, value in vars(klass).items():
            if attr in seen or not isinstance(value, tasks.Loop):
                continue
            seen.add(attr)
            try:
                bound = getattr(cog, attr)      # Loop.__get__ -> boucle liée au cog
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(bound, tasks.Loop):
                continue
            qualified = f"{type(cog).__name__}.{attr}"
            _attach(bound, qualified, lg)
            names.append(qualified)
    return names


def loops_summary():
    """(saines, total, [noms en échec]) — pour /health et le tableau de bord."""
    total = len(REGISTRY)
    broken = sorted(s.name for s in REGISTRY.values() if not s.healthy)
    return total - len(broken), total, broken
