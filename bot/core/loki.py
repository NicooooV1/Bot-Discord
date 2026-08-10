"""Loki read layer (HTTP API, aiohttp — shipped transitively with discord.py).

Mirrors influx.py: disabled when LOKI_URL is empty, lazy client session, every
failure is logged and returns an empty result (commands degrade gracefully).

Label contract (FROZEN — must match the Alloy/Promtail config on the Loki side):
- host : machine hostname (pve, jellyfin, reverseproxy, discord-bot, ...)
- unit : systemd unit
- level: journald vocabulary (emerg alert crit err warning notice info debug)
- job  : systemd-journal | jellyfin | caddy
Endpoint: http://10.3.10.123:3100 (LOKI_URL).
"""
import logging
import time

log = logging.getLogger("discord-bot.loki")


def _esc(s):
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def build_logql(host=None, unit=None, level=None, contains=None):
    """Stream selector from the provided labels (else match-all) + optional line filter."""
    sel = []
    if host:
        sel.append(f'host="{_esc(host)}"')
    if unit:
        sel.append(f'unit="{_esc(unit)}"')
    if level:
        sel.append(f'level="{_esc(level)}"')
    q = "{" + (", ".join(sel) if sel else 'host=~".+"') + "}"
    if contains:
        q += f' |= "{_esc(contains)}"'
    return q


class Loki:
    def __init__(self, cfg):
        self.cfg = cfg
        self.url = (cfg.loki_url or "").rstrip("/")
        self._session = None

    @property
    def enabled(self):
        return bool(self.url)

    async def _sess(self):
        if self._session is None or self._session.closed:
            import aiohttp
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10))
        return self._session

    async def aclose(self):
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def _get(self, path, params):
        if not self.enabled:
            return None
        try:
            sess = await self._sess()
            async with sess.get(self.url + path, params=params) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.error("Loki %s -> HTTP %s: %s", path, resp.status, body[:300])
                    return None
                return await resp.json()
        except Exception:
            log.exception("requête Loki échouée: %s %s", path, params)
            return None

    @staticmethod
    def _parse_result(data):
        """Streams or vector -> list of (unix_ts, labels_dict, line) sorted ascending."""
        out = []
        try:
            for entry in (data or {}).get("data", {}).get("result", []):
                labels = entry.get("stream") or entry.get("metric") or {}
                values = entry.get("values")
                if values is None and "value" in entry:
                    values = [entry["value"]]
                for ts, line in values or []:
                    ts = float(ts)
                    if ts > 1e15:  # streams carry ns; vectors carry seconds
                        ts /= 1e9
                    out.append((ts, labels, line))
        except Exception:
            log.exception("réponse Loki illisible")
            return []
        out.sort(key=lambda x: x[0])
        return out

    async def query_range(self, logql, minutes, limit=100):
        """Log lines over the last <minutes>, newest-first server-side, ascending out."""
        now = time.time()
        params = {
            "query": logql,
            "start": str(int((now - minutes * 60) * 1e9)),
            "end": str(int(now * 1e9)),
            "limit": str(limit),
            "direction": "backward",
        }
        return self._parse_result(await self._get("/loki/api/v1/query_range", params))

    async def instant(self, logql):
        """Instant (metric) query — e.g. topk over count_over_time."""
        return self._parse_result(await self._get("/loki/api/v1/query", {"query": logql}))
