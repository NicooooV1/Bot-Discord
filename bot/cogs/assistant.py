"""`/assistant` + salon de chat direct (#chat-ia) — assistant IA local (Qwen 35B,
RTX 3090 sur le nœud llm d'Aveyron), avec function-calling sur les invités PVE
(R820 + Aveyron) : consulter et piloter (start/stop/reboot) via langage naturel.
Voir `core/pve.py::_LlmBridge` pour le transport (aucune route réseau directe, tout
passe par guest-agent exec + curl local).

Sécurité : le modèle ne fait QUE proposer des appels d'outils — chaque outil qui
MUTE l'état revérifie is_admin(server=...) sur LE SERVEUR SPÉCIFIQUE de l'invité
ciblé (pas seulement le gate 2FA global du GatedTree, déjà passé pour arriver ici) ;
un refus est renvoyé au modèle comme résultat d'outil (pas d'exception), pour qu'il
l'explique à l'utilisateur plutôt que de planter. Aucun outil destructeur (pas de
suppression de sauvegarde, pas de shell libre) — mêmes verbes que les boutons
existants, jamais plus.

Le salon #chat-ia (catégorie dédiée « 🤖 Assistant IA », réservée au rôle O AVY-LLM,
2026-07-18) répond directement à n'importe quel message, sans /assistant — le
`on_message` construit un contexte adapté (`_MsgCtx`) pour réutiliser TELLE QUELLE
la même logique de permissions/outils que la commande slash."""
import asyncio
import json
import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

from ..core import format as fmt
from ..core.permissions import is_admin, read_check, session_2fa_ok
from ..core.pve import LlmExecError

log = logging.getLogger("discord-bot.assistant")

MAX_ROUNDS = 3          # aller-retours modèle <-> outils avant de forcer une réponse
MAX_HISTORY_CHARS = 6000  # garde le contexte envoyé au modèle raisonnable

SYSTEM_PROMPT = (
    "Tu es l'assistant du homelab de Nico, intégré à son bot Discord Edmine. Nico "
    "n'est PAS forcément la personne qui te parle : plusieurs personnes utilisent ce "
    "bot et l'assistant. Le message système suivant t'indique le pseudo Discord exact "
    "de la personne qui te parle CETTE fois — utilise-le si tu t'adresses à elle "
    "nommément, ne suppose JAMAIS qu'il s'agit de Nico par défaut. "
    "Tu peux consulter et piloter les VM et conteneurs LXC Proxmox du serveur R820 et "
    "du cluster Aveyron (VM/LXC dont le nom finit par « -avy », chacun sur son propre "
    "serveur physique pve/nas/llm) via les outils fournis. Réponds en français, de "
    "façon concise, en disant toujours « VM » ou « conteneur »/« LXC » — N'UTILISE "
    "JAMAIS les mots « invité » ou « guest », même pour parler d'un serveur physique "
    "pve/nas/llm ou d'un hôte : ce ne sont pas des invités, ce sont des serveurs. Si le "
    "nom d'une VM/d'un LXC est ambigu ou introuvable, appelle list_guests puis "
    "demande une précision plutôt que de deviner. N'annonce une action comme faite "
    "que si l'outil correspondant a confirmé un succès — si un outil renvoie un "
    "refus ou une erreur, explique-le clairement à l'utilisateur."
)

# Filet de sécurité : le modèle local (Qwen) n'obéit pas toujours à la consigne
# ci-dessus. Remplacement textuel de secours sur la réponse finale avant envoi
# Discord — demande explicite de Nico (2026-07-18), la formulation "invité" reste
# apparue malgré le system prompt.
_GUEST_WORDING = (
    (re.compile(r"\binvités?\b", re.IGNORECASE), "VM/conteneur"),
    (re.compile(r"\bguests?\b", re.IGNORECASE), "VM/conteneur"),
)


def _scrub_guest_wording(text):
    for pat, repl in _GUEST_WORDING:
        text = pat.sub(repl, text)
    return text

TOOLS = [
    {"type": "function", "function": {
        "name": "list_guests",
        "description": "Liste toutes les VM/conteneurs connus avec leur statut "
                       "(running/stopped) et le serveur physique qui les héberge.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "guest_status",
        "description": "Statut détaillé d'une VM ou d'un LXC précis (CPU, RAM, uptime).",
        "parameters": {"type": "object",
                       "properties": {"name": {"type": "string",
                                                "description": "Nom exact de la VM/du LXC"}},
                       "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "start_guest",
        "description": "Démarre une VM ou un conteneur arrêté.",
        "parameters": {"type": "object",
                       "properties": {"name": {"type": "string"}},
                       "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "stop_guest",
        "description": "Arrête (shutdown propre) une VM ou un conteneur.",
        "parameters": {"type": "object",
                       "properties": {"name": {"type": "string"}},
                       "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "reboot_guest",
        "description": "Redémarre une VM ou un conteneur.",
        "parameters": {"type": "object",
                       "properties": {"name": {"type": "string"}},
                       "required": ["name"]}}},
]


class _MsgCtx:
    """Adapte un `discord.Message` à la forme attendue par is_admin/session_2fa_ok
    (qui visent normalement une Interaction) : .user/.guild/.guild_id/.client."""

    def __init__(self, message, bot):
        self.user = message.author
        self.guild = message.guild
        self.guild_id = message.guild.id if message.guild else None
        self.client = bot


class Assistant(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ------------------------------------------------------------ implémentation outils

    def _resolve(self, name):
        """(vmid, gtype, server) ou (None, None, None) si introuvable."""
        pve = self.bot.pve
        g = pve.guest_map().get(name)
        if not g:
            return None, None, None
        return g.get("vmid"), g.get("type"), pve.server_of_name(name)

    def _tool_list_guests(self):
        gm = self.bot.pve.guest_map()
        rows = [{"name": n, "type": i.get("type"), "status": i.get("status"),
                 "server": self.bot.pve.server_of_name(n) or "R820"}
                for n, i in sorted(gm.items())]
        return json.dumps(rows, ensure_ascii=False)

    def _tool_guest_status(self, name):
        vmid, gtype, _ = self._resolve(name)
        if vmid is None:
            return json.dumps({"error": f"VM/LXC « {name} » introuvable"})
        try:
            st = self.bot.pve.guest_status(vmid, gtype)
        except Exception as e:
            return json.dumps({"error": str(e)[:200]})
        return json.dumps({
            "name": name, "status": st.get("status"),
            "uptime_s": st.get("uptime"),
            "cpu_pct": round((st.get("cpu") or 0) * 100, 1),
            "ram_pct": (round((st.get("mem") or 0) / st["maxmem"] * 100, 1)
                       if st.get("maxmem") else None),
        }, ensure_ascii=False)

    async def _tool_action(self, ctx, name, verb, label):
        """verb: start/stop/reboot — vérifie les droits SUR LE SERVEUR de CET invité
        avant d'agir, journalise, renvoie un résultat structuré pour le modèle.
        `ctx` = Interaction OU _MsgCtx, peu importe (même forme .user/.guild)."""
        bot = self.bot
        vmid, gtype, server = self._resolve(name)
        if vmid is None:
            return json.dumps({"error": f"VM/LXC « {name} » introuvable"})
        if not is_admin(bot.cfg, ctx, server=server):
            return json.dumps({
                "error": f"refusé : droits insuffisants sur {server or 'R820'} "
                         f"pour {ctx.user.display_name}"})
        if not bot.pve.actions_enabled:
            return json.dumps({"error": "jeton d'action PVE non configuré"})
        lxc = {"start": bot.pve.start_ct, "stop": bot.pve.shutdown_ct,
               "reboot": bot.pve.reboot_ct}
        vm = {"start": bot.pve.start_vm, "stop": bot.pve.shutdown_vm,
              "reboot": bot.pve.reboot_vm}
        who = f"{ctx.user}({ctx.user.id})"
        try:
            upid = await asyncio.to_thread((vm if gtype == "qemu" else lxc)[verb], vmid)
        except Exception as e:
            bot.audit.record(user=who, action=f"assistant:{verb}",
                             target=f"{name}/{vmid}", result=f"error:{e}")
            return json.dumps({"error": f"échec : {e}"[:200]})
        bot.audit.record(user=who, action=f"assistant:{verb}", target=f"{name}/{vmid}",
                         result="submitted", upid=str(upid))
        return json.dumps({"ok": True, "action": label, "name": name,
                           "server": server or "R820"}, ensure_ascii=False)

    async def _call_tool(self, ctx, tc):
        fn = tc["function"]["name"]
        try:
            args = json.loads(tc["function"].get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        name = args.get("name", "")
        if fn == "list_guests":
            return self._tool_list_guests()
        if fn == "guest_status":
            return self._tool_guest_status(name)
        if fn == "start_guest":
            return await self._tool_action(ctx, name, "start", "démarré")
        if fn == "stop_guest":
            return await self._tool_action(ctx, name, "stop", "arrêté")
        if fn == "reboot_guest":
            return await self._tool_action(ctx, name, "reboot", "redémarré")
        return json.dumps({"error": f"outil inconnu: {fn}"})

    # ---------------------------------------------------------- boucle partagée

    async def converse(self, ctx, message_text):
        """Boucle tool-calling complète. Renvoie (texte, [lignes d'actions]).
        Lève LlmExecError si l'assistant est indisponible — à l'appelant d'afficher
        l'erreur (le rendu diffère entre /assistant et le salon de chat)."""
        # pseudo Discord EXACT de qui parle CETTE fois (jamais supposé « Nico » par
        # défaut : plusieurs personnes utilisent le bot/l'assistant, demande Nico
        # 2026-07-18) — message système séparé, à chaque appel, pas mémorisé plus loin.
        who_prompt = (f"La personne qui te parle s'appelle « {ctx.user.display_name} » "
                      f"sur ce serveur Discord. Adresse-toi à elle par ce pseudo si "
                      f"besoin, ce n'est pas forcément Nico.")
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "system", "content": who_prompt},
                    {"role": "user", "content": message_text}]
        actions = []
        for _ in range(MAX_ROUNDS):
            reply = await asyncio.to_thread(self.bot.pve.llm_chat, messages, TOOLS)
            tool_calls = reply.get("tool_calls") or []
            if not tool_calls:
                text = (reply.get("content") or "").strip() or "(réponse vide)"
                return _scrub_guest_wording(text), actions
            messages.append({"role": "assistant", "content": reply.get("content") or "",
                             "tool_calls": tool_calls})
            for tc in tool_calls:
                result = await self._call_tool(ctx, tc)
                try:
                    rd = json.loads(result)
                    if rd.get("ok"):
                        actions.append(f"✅ {rd['action']} **{rd['name']}** ({rd['server']})")
                    elif rd.get("error"):
                        actions.append(f"⚠️ {rd['error']}")
                except Exception:
                    pass
                messages.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                                 "content": result})
            # garde-fou : contexte borné avant le prochain aller-retour
            if sum(len(m.get("content") or "") for m in messages) > MAX_HISTORY_CHARS:
                break
        return "(trop d'étapes, réponse interrompue — précise ta demande)", actions

    # ------------------------------------------------------------------- commande

    @app_commands.command(
        description="Assistant IA local (Qwen) : consulter/piloter les VM en langage naturel.")
    @app_commands.describe(message="Ta demande, en langage naturel")
    @read_check()
    async def assistant(self, itx: discord.Interaction, message: str):
        if not self.bot.pve.llm_enabled:
            await itx.response.send_message(
                "Assistant IA local non configuré.", ephemeral=True)
            return
        await itx.response.defer()
        try:
            text, actions = await self.converse(itx, message)
        except LlmExecError as e:
            await itx.followup.send(f"❌ Assistant IA indisponible : `{e}`")
            return
        except Exception:
            log.exception("assistant: échec inattendu")
            await itx.followup.send("❌ Erreur inattendue de l'assistant.")
            return
        emb = discord.Embed(title="🤖 Assistant", description=text[:4000], color=fmt.BLURPLE)
        if actions:
            emb.add_field(name="Actions", value="\n".join(actions)[:1024], inline=False)
        await itx.followup.send(embed=emb)

    # --------------------------------------------------- salon de chat direct

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """#chat-ia (catégorie « 🤖 Assistant IA », réservée au rôle O AVY-LLM) :
        répond à TOUT message posté là, sans /assistant. La visibilité du salon est
        déjà restreinte côté Discord (overwrites, provision.py) ; on revérifie quand
        même ici le rôle + la session 2FA (même défense en profondeur que partout
        ailleurs dans le bot — un overwrite mal appliqué ne doit jamais suffire à
        déclencher des actions)."""
        if message.author.bot or not message.guild:
            return
        cid = getattr(self.bot.cfg, "assistant_chat_channel_id", 0)
        if not cid or message.channel.id != cid:
            return
        if not self.bot.pve.llm_enabled:
            return
        ctx = _MsgCtx(message, self.bot)
        if not is_admin(self.bot.cfg, ctx, server=self.bot.cfg.avy_llm_server_key):
            await message.reply("🔒 Ce salon est réservé au rôle O AVY-LLM.",
                                mention_author=False)
            return
        if not session_2fa_ok(ctx):
            from ..views.twofa_view import UnlockView
            emb = discord.Embed(
                title="🔐 Vérification requise",
                description="Déverrouille ta session avant de discuter ici.",
                color=0xE5A50A)
            await message.reply(embed=emb, view=UnlockView(message.author.id),
                                mention_author=False)
            return
        content = (message.content or "").strip()
        if not content:
            return
        async with message.channel.typing():
            try:
                text, actions = await self.converse(ctx, content)
            except LlmExecError as e:
                await message.reply(f"❌ Assistant IA indisponible : `{e}`",
                                    mention_author=False)
                return
            except Exception:
                log.exception("chat-ia: échec inattendu")
                await message.reply("❌ Erreur inattendue de l'assistant.",
                                    mention_author=False)
                return
        reply = text[:1900]
        if actions:
            reply += "\n\n" + "\n".join(actions)
        await message.reply(reply[:2000], mention_author=False)


async def setup(bot):
    await bot.add_cog(Assistant(bot))
