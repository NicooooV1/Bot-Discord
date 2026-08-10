"""Shared app-command autocomplete helpers (CT names from the PVE API, cached)."""
import asyncio

from discord import app_commands


async def _lxc_choices(bot, current, include_host=False):
    try:
        gm = await asyncio.to_thread(bot.pve.guest_map) if bot.pve.enabled else {}
    except Exception:
        gm = {}
    cur = (current or "").lower()
    out = []
    if include_host and (not cur or cur in bot.cfg.pve_node.lower()):
        out.append(app_commands.Choice(name=f"{bot.cfg.pve_node} (hôte)", value=bot.cfg.pve_node))
    # nœuds du cluster Aveyron : pseudo-cibles « avy:<nom> » (graphes via RRD)
    if include_host and getattr(bot.pve, "avy_enabled", False):
        for n in bot.pve.avy_nodes():
            label = f"{n} (hôte Aveyron)"
            if not cur or cur in label.lower() or cur in f"avy:{n}":
                out.append(app_commands.Choice(name=label, value=f"avy:{n}"))
    for name, info in sorted(gm.items()):
        if info.get("type") not in ("lxc", "qemu"):
            continue
        if cur and cur not in name.lower() and cur not in str(info.get("vmid", "")):
            continue
        emoji = "🟢" if info.get("status") == "running" else "🔴"
        kind = " 🖥️VM" if info.get("type") == "qemu" else ""
        out.append(app_commands.Choice(
            name=f"{name} ({info.get('vmid')}) {emoji}{kind}", value=name))
        if len(out) >= 25:
            break
    return out[:25]


async def ct_autocomplete(interaction, current):
    return await _lxc_choices(interaction.client, current, include_host=False)


async def target_autocomplete(interaction, current):
    return await _lxc_choices(interaction.client, current, include_host=True)
