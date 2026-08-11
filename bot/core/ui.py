"""Helpers d'interface partagés : autocomplétion des noms d'invités + message épinglé."""
import asyncio
import logging

import discord
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


# ---------------------------------------------------------------- message épinglé
async def pin_edit(channel, embed=None, *, message_id=None, view=None, content=None,
                   files=None, clear_attachments=False, label=None, log=None):
    """Édite le message épinglé d'un salon, ou le poste et l'épingle au premier passage.

    Renvoie `(message, message_id)` — ou `(None, None)` si l'opération a échoué.
    L'appelant PERSISTE lui-même `message_id` : le stockage varie d'un cog à l'autre
    (`state["avy_msgs"][cid]`, `state["servarr_ratio"]["message"]`…) et le migrer aurait
    orphelin les messages existants, donc fait poster un DOUBLON — le bug déjà vécu sur
    12 salons -avy le 2026-07-17. On ne factorise QUE la danse Discord, qui est l'endroit
    où sont les défauts :

      - `fetch_message` sur un id périmé lève `NotFound` -> il faut reposter, pas abandonner ;
      - `Forbidden` est DURABLE (permission retirée) : le taire fige le salon sans un mot
        dans les logs, ce que faisaient 3 des 5 copies ;
      - `pin()` peut échouer seul (50 épingles max par salon) sans que le message soit
        perdu : on continue ;
      - sans `attachments=[]`, Discord CONSERVE l'ancienne image quand le rendu échoue —
        le tableau de bord affiche alors un graphe périmé sous des champs à « — ».

    Ce bloc était copié dans 5 cogs (avy, servarr, medias, meta, provision).
    """
    if channel is None:
        return None, None
    lg = log or logging.getLogger("discord-bot.ui")
    what = label or f"#{getattr(channel, 'name', '?')}"
    msg = None
    if message_id:
        try:
            msg = await channel.fetch_message(message_id)
        except discord.NotFound:
            msg = None                     # message supprimé à la main : on en repostera un
        except discord.Forbidden:
            lg.warning("%s : lecture du message épinglé refusée (permissions)", what)
            return None, None
        except discord.HTTPException as e:
            # panne transitoire : NE PAS reposter, on retentera au prochain cycle (sinon
            # on empile les doublons à chaque hoquet de l'API Discord)
            lg.warning("%s : message épinglé illisible (%s)", what, e)
            return None, None

    kw = {}
    if embed is not None:
        kw["embed"] = embed
    if content is not None:
        kw["content"] = content
    if view is not None:
        kw["view"] = view
    try:
        if msg is None:
            if files:
                kw["files"] = list(files)
            msg = await channel.send(**kw)
            try:
                await msg.pin()
            except discord.HTTPException as e:
                lg.info("%s : épinglage impossible (%s) — le message reste posté", what, e)
            return msg, msg.id
        if files:
            kw["attachments"] = list(files)
        elif clear_attachments:
            # liste VIDE et non « absent » : absent = « garde l'existant »
            kw["attachments"] = []
        await msg.edit(**kw)
        return msg, msg.id
    except discord.Forbidden:
        lg.warning("%s : écriture refusée (permissions) — le salon va se figer", what)
    except discord.HTTPException as e:
        lg.warning("%s : publication impossible (%s)", what, e)
    return None, None
