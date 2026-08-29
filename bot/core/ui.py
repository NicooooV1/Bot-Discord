"""Helpers d'interface partagés : autocomplétion des noms d'invités + message épinglé."""
import asyncio
import logging

import discord
from discord import app_commands


async def _lxc_choices(bot, current, include_host=False, channel=None):
    """Choix d'invités (et d'hôtes) LIMITÉS au serveur du salon `channel`.

    Nico 2026-08-26 : « chaque serveur est séparé, on ne mélange rien ». Avant, la liste
    mêlait l'hôte R820, les nœuds Aveyron et les invités des deux clusters ; désormais un
    salon R820 ne voit que le R820, un salon AVY-NAS que le nœud nas et ses invités."""
    from bot.core import channels as _ch
    srv = _ch.server_of_channel(bot, channel)
    default = getattr(bot.cfg, "server_key", "R820")
    try:
        gm = await asyncio.to_thread(bot.pve.guest_map) if bot.pve.enabled else {}
    except Exception:
        gm = {}
    cur = (current or "").lower()
    out = []
    if include_host and _ch.same_server(srv, None, default) \
            and (not cur or cur in bot.cfg.pve_node.lower()):
        out.append(app_commands.Choice(name=f"{bot.cfg.pve_node} (hôte)", value=bot.cfg.pve_node))
    # nœuds du cluster Aveyron : pseudo-cibles « avy:<nom> » (graphes via RRD) — uniquement
    # depuis les salons DE ce nœud
    if include_host and getattr(bot.pve, "avy_enabled", False):
        for n in (getattr(bot.cfg, "avy_nodes", None) or []):
            if not _ch.same_server(srv, bot.pve.avy_server_key(n), default):
                continue
            label = f"{n} (hôte Aveyron)"
            if not cur or cur in label.lower() or cur in f"avy:{n}":
                out.append(app_commands.Choice(name=label, value=f"avy:{n}"))
    for name, info in sorted(gm.items()):
        if info.get("type") not in ("lxc", "qemu"):
            continue
        if not _ch.same_server(srv, guest_server(bot, name, info), default):
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


def guest_server(bot, name, info=None):
    """Clé serveur d'un invité à partir de son entrée `guest_map` (None = R820).
    Sans I/O : `info` est l'entrée déjà lue (évite un `server_of_name` bloquant)."""
    if not bot.pve.is_avy_name(name):
        return None
    node = (info or {}).get("node")
    return bot.pve.avy_server_key(node) if node else "AVY"


def server_mismatch(bot, interaction, target, target_srv):
    """Message de refus si `target` (serveur `target_srv`, None = R820) n'appartient pas
    au serveur du salon de `interaction` ; None sinon. Garde commune de /ct et /graph."""
    from bot.core import channels as _ch
    default = getattr(bot.cfg, "server_key", "R820")
    srv = _ch.server_of_channel(bot, getattr(interaction, "channel", None))
    if _ch.same_server(srv, target_srv, default):
        return None
    return (f"⛔ `{target}` appartient au serveur **{target_srv or default}**, pas à "
            f"**{srv}** : les serveurs ne se mélangent pas. Relance la commande depuis "
            f"un salon de **{target_srv or default}**.")


async def guard_target(bot, interaction, name):
    """Garde COMMUNE des commandes qui nomment un invité (/ctctl, /backup…) : message de
    refus si `name` n'appartient pas au serveur du salon (donc au serveur dont la porte
    admin_check(scope="channel") a déjà vérifié le rôle), None sinon.

    Audit 2026-08-29 : `/ctctl stop xxx-avy` passait avec un simple rôle M R820 parce
    que la porte ne regardait qu'un rôle global et que la cible n'était jamais rattachée
    à un serveur. Un nom « -avy » dont le nœud est inconnu (guest_map partiel, lien
    Aveyron coupé) est REFUSÉ (« AVY » n'est pas une clé), jamais rattaché au R820."""
    try:
        gm = await asyncio.to_thread(bot.pve.guest_map) if bot.pve.enabled else {}
    except Exception:  # noqa: BLE001 — inventaire indisponible = on ne peut pas situer
        return ("⛔ Inventaire PVE indisponible : impossible de vérifier à quel serveur "
                f"appartient `{name}`. Réessaie dans une minute.")
    info = gm.get(name)
    if info is None:
        return None            # la commande répondra elle-même « introuvable »
    tgt = guest_server(bot, name, info)
    if tgt == "AVY":
        return (f"⏳ Serveur de `{name}` non résolu (supervision AVEYRON en cours de "
                "synchronisation) — réessaie dans une minute.")
    return server_mismatch(bot, interaction, name, tgt)


def _ac_allowed(interaction):
    """L'autocomplétion n'est PAS une porte (la valeur saisie n'est jamais filtrée),
    mais elle livrait l'inventaire (noms, vmid, état) à tout membre ayant une session
    2FA, rôle ou pas (audit 2026-08-29). On ne propose rien à qui ne peut pas lire le
    serveur du salon."""
    from .permissions import can_read, tier_of
    from . import channels as _ch, srvperms
    bot = interaction.client
    try:
        srv = _ch.server_of_channel(bot, getattr(interaction, "channel", None))
        tier = tier_of(bot.cfg, interaction, srv)
        if tier in ("O", "M"):
            return True
        if tier == "G":
            # G voit les cibles dès que l'Owner lui a accordé une capacité (start,
            # refresh, graph…) — pas seulement « read » (2026-08-29 soir)
            return srvperms.tier_has_any_cap(getattr(bot, "state", None), srv, "G")
        return can_read(bot.cfg, interaction, server=srv)      # legacy READ_ROLE_IDS
    except Exception:  # noqa: BLE001 — une autocomplétion ne doit jamais lever
        return False


async def ct_autocomplete(interaction, current):
    if not _ac_allowed(interaction):
        return []
    return await _lxc_choices(interaction.client, current, include_host=False,
                              channel=getattr(interaction, "channel", None))


async def target_autocomplete(interaction, current):
    if not _ac_allowed(interaction):
        return []
    return await _lxc_choices(interaction.client, current, include_host=True,
                              channel=getattr(interaction, "channel", None))


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
