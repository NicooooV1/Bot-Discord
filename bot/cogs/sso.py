"""Supervision du portail SSO (CT123 : LLDAP + Authelia) : /sso + veille de fond.

CE QUE FAIT CE COG
------------------
1. `/sso` (admin) : état de la pile auth (conteneurs Docker de CT123), dernières
   tentatives de connexion (table `authentication_logs` d'Authelia), nombre de
   méthodes 2FA enregistrées, et présence d'un lien de validation en attente
   (`notification.txt` — pas de SMTP : Authelia y écrit les liens d'enrôlement
   TOTP/reset). Un bouton owner-only affiche ce lien en éphémère.
2. `sso_watch` (boucle de fond) : poste dans #alertes chaque événement
   d'authentification NOUVEAU — bannissement (rouge), échec (jaune), connexion
   réussie (discret, débrayable via SSO_LOGIN_NOTIFY=0) — et envoie en DM aux
   admins tout NOUVEAU lien écrit dans notification.txt (le lien ne passe JAMAIS
   par un salon : il permet d'enrôler un 2FA ou de reset un mot de passe).

COMMENT IL LIT CT123
--------------------
Par la même voie que la console du nœud : `nodeshell.run_readonly` (SSH hyperviseur,
clé dédiée) puis `pct exec 123`. Uniquement des LECTURES : sqlite3 en -readonly,
stat/cat, docker ps. Le curseur (`id` du dernier événement vu) et la signature du
dernier lien notifié vivent dans state.json.

⚠️ SOURCE DE VÉRITÉ : Authelia ne journalise PAS les tentatives de connexion dans
authelia.log au niveau info — la table sqlite `authentication_logs` est le seul
enregistrement fiable (vérifié 2026-08-20 sur la 4.38). Ne pas « simplifier » vers
un tail du log : il ne contient que les décisions forward-auth.

⚠️ IP DISTANTES : un client de la MAISON apparaît avec l'IP de la Bbox
(192.168.1.254, hairpin NAT) ou d'une patte Docker (172.18.0.1) — seule une
connexion depuis l'EXTÉRIEUR montre une vraie IP publique. L'affichage l'annote.
"""
import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..core import format as fmt
from ..core.bg import guard_cog_loops
from ..core.gates import GatedView
from ..core.nodeshell import run_readonly
from ..core.permissions import admin_check

log = logging.getLogger("discord-bot.sso")

# Séparateur de colonnes sqlite : un caractère qui ne peut PAS apparaître dans les
# valeurs (username LDAP, IP, type d'auth). Le « | » par défaut est théoriquement
# permis dans un username ; l'unité ASCII 0x1F ne l'est pas.
_SEP = "\x1f"

# IP sources qui ne désignent PAS une machine identifiable : hairpin Bbox et pattes
# Docker internes au CT (cf. bloc-notes d'en-tête).
_LAN_HINTS = {"192.168.1.254": "maison (hairpin Bbox)"}


def _classify_ip(ip):
    """Annotation honnête d'une IP source : on ne prétend pas géolocaliser, on
    distingue juste « la maison » d'une « IP externe » (réel dans ses mots)."""
    if ip in _LAN_HINTS:
        return f"{ip} — {_LAN_HINTS[ip]}"
    if ip.startswith(("172.16.", "172.17.", "172.18.", "172.19.", "10.", "192.168.")):
        return f"{ip} — réseau interne"
    return f"{ip} — externe"


def parse_auth_rows(raw):
    """Sortie sqlite (séparateur 0x1F) -> liste de dicts, du plus ancien au plus
    récent. Une ligne inattendue est ignorée en le disant (log), jamais inventée."""
    rows = []
    for line in (raw or "").splitlines():
        parts = line.split(_SEP)
        if len(parts) != 7:
            if line.strip():
                log.warning("sso: ligne authentication_logs inattendue ignorée: %r",
                            line[:120])
            continue
        rid, ts, user, ok, banned, atype, ip = parts
        try:
            rows.append({
                "id": int(rid),
                # « 2026-08-19 01:08:05.994604607+02:00 » -> minute lisible
                "time": ts[:16],
                "user": user,
                "ok": ok == "1",
                "banned": banned == "1",
                "type": atype,
                "ip": ip,
            })
        except ValueError:
            log.warning("sso: id non numérique ignoré: %r", line[:120])
    return rows


def summarize_batch(rows):
    """Regroupe un lot d'événements en messages à poster : chaque bannissement
    individuellement (rare et grave), les échecs agrégés par (user, ip), les
    succès agrégés par (user, ip, type). Renvoie une liste de tuples
    (niveau, titre, description) — niveau dans {"ban", "fail", "ok"}."""
    out = []
    fails, oks = {}, {}
    for r in rows:
        if r["banned"]:
            out.append(("ban", "⛔ SSO : utilisateur banni",
                        f"**{r['user']}** banni par la régulation Authelia "
                        f"(3 échecs / 2 min → 1 h) le {r['time']}\n"
                        f"Source : {_classify_ip(r['ip'])}"))
        elif not r["ok"]:
            k = (r["user"], r["ip"])
            fails.setdefault(k, []).append(r)
        else:
            k = (r["user"], r["ip"], r["type"])
            oks.setdefault(k, []).append(r)
    for (user, ip), rs in fails.items():
        n = len(rs)
        out.append(("fail", "🟡 SSO : échec de connexion",
                    f"**{n}** échec(s) {rs[0]['type']} pour **{user}** "
                    f"entre {rs[0]['time']} et {rs[-1]['time']}\n"
                    f"Source : {_classify_ip(ip)}"))
    for (user, ip, atype), rs in oks.items():
        out.append(("ok", "🔓 SSO : connexion réussie",
                    f"**{user}** — {atype} le {rs[-1]['time']}\n"
                    f"Source : {_classify_ip(ip)}"))
    return out


class SsoPanelView(GatedView):
    """Bouton « afficher le lien en attente » — tier « mod » (rôle Gestion + 2FA),
    même exigence que /sso. La réponse est éphémère : le lien permet d'enrôler un
    2FA ou de reset un mot de passe, il ne doit pas rester lisible dans le salon."""

    gate = "mod"

    def __init__(self, cog):
        super().__init__(timeout=300)
        self.cog = cog

    @discord.ui.button(label="📬 Afficher le lien en attente",
                       style=discord.ButtonStyle.secondary)
    async def show_link(self, itx: discord.Interaction, _b: discord.ui.Button):
        await itx.response.defer(ephemeral=True)
        try:
            content = await self.cog._ct_read_notification()
        except Exception as e:  # noqa: BLE001 — SSH/CT injoignable, on le dit
            await itx.followup.send(f"CT123 injoignable : `{e}`", ephemeral=True)
            return
        if not content.strip():
            await itx.followup.send("Aucun lien en attente (notification.txt vide).",
                                    ephemeral=True)
            return
        await itx.followup.send(
            f"Contenu de `notification.txt` (ne le partage pas) :\n"
            f">>> {content.strip()[:1800]}", ephemeral=True)


class Sso(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        guard_cog_loops(self)
        self.sso_watch.change_interval(seconds=self.bot.cfg.sso_poll_seconds)
        self.sso_watch.start()

    async def cog_unload(self):
        self.sso_watch.cancel()

    # ------------------------------------------------------------- lectures CT123
    def _pct(self, inner):
        return f"pct exec {self.bot.cfg.sso_ct_id} -- {inner}"

    async def _ct_auth_rows(self, since_id, limit=50):
        sql = ("select id,time,username,successful,banned,auth_type,remote_ip "
               f"from authentication_logs where id > {int(since_id)} "
               f"order by id limit {int(limit)};")
        raw = await run_readonly(self.bot.cfg, self._pct(
            f"sqlite3 -readonly -separator '{_SEP}' {self.bot.cfg.sso_db} \"{sql}\""))
        return parse_auth_rows(raw)

    async def _ct_last_rows(self, n=8):
        """Les n derniers événements, du plus ancien au plus récent (panneau /sso)."""
        sql = ("select id,time,username,successful,banned,auth_type,remote_ip "
               "from (select * from authentication_logs order by id desc "
               f"limit {int(n)}) order by id;")
        raw = await run_readonly(self.bot.cfg, self._pct(
            f"sqlite3 -readonly -separator '{_SEP}' {self.bot.cfg.sso_db} \"{sql}\""))
        return parse_auth_rows(raw)

    async def _ct_max_auth_id(self):
        raw = await run_readonly(self.bot.cfg, self._pct(
            f"sqlite3 -readonly {self.bot.cfg.sso_db} "
            "\"select coalesce(max(id),0) from authentication_logs;\""))
        try:
            return int(raw.strip())
        except ValueError:
            return 0

    async def _ct_twofa_counts(self):
        raw = await run_readonly(self.bot.cfg, self._pct(
            f"sqlite3 -readonly -separator '{_SEP}' {self.bot.cfg.sso_db} "
            "\"select (select count(*) from totp_configurations),"
            "(select count(*) from webauthn_credentials);\""))
        parts = (raw.strip().split(_SEP) + ["?", "?"])[:2]
        return parts[0], parts[1]

    async def _ct_stack(self):
        # NB: séparateur « | » et pas \t — les templates Go de docker n'interprètent
        # pas les séquences d'échappement, un \t sortirait littéralement.
        raw = await run_readonly(self.bot.cfg, self._pct(
            "docker ps -a --format '{{.Names}}|{{.Status}}'"))
        lines = []
        for line in raw.splitlines():
            if "|" not in line:
                continue
            name, status = line.split("|", 1)
            emoji = "🟢" if status.startswith("Up") else "🔴"
            lines.append(f"{emoji} `{name}` — {status}")
        return lines

    async def _ct_notification_sig(self):
        """« taille:mtime » du fichier de notification, « 0:0 » s'il est absent."""
        raw = await run_readonly(self.bot.cfg, self._pct(
            f"sh -c 'stat -c %s:%Y {self.bot.cfg.sso_notif_file} "
            "2>/dev/null || echo 0:0'"))
        return raw.strip() or "0:0"

    async def _ct_read_notification(self):
        return await run_readonly(self.bot.cfg, self._pct(
            f"sh -c 'cat {self.bot.cfg.sso_notif_file} 2>/dev/null || true'"))

    # ------------------------------------------------------------------- /sso
    @app_commands.command(
        name="sso",
        description="Portail SSO (Authelia CT123) : pile, connexions, 2FA, lien en attente.")
    @admin_check(require_admin_channel=False)
    async def sso(self, itx: discord.Interaction):
        await itx.response.defer()
        emb = discord.Embed(title="🔐 Portail SSO — auth.nicov1.fr", color=fmt.GREEN)
        try:
            stack = await self._ct_stack()
        except Exception as e:  # noqa: BLE001
            await itx.followup.send(f"CT123 injoignable via l'hyperviseur : `{e}`")
            return
        down = [l for l in stack if l.startswith("🔴")]
        if down:
            emb.color = fmt.RED
        emb.add_field(name="Pile (CT123)", value="\n".join(stack) or "—", inline=False)

        try:
            totp, webauthn = await self._ct_twofa_counts()
            rows = await self._ct_last_rows(8)
            lignes = []
            for r in rows:
                ico = "⛔" if r["banned"] else ("✅" if r["ok"] else "❌")
                lignes.append(f"{ico} {r['time']} — **{r['user']}** ({r['type']}) "
                              f"· {_classify_ip(r['ip'])}")
            emb.add_field(name="Dernières tentatives (base Authelia)",
                          value="\n".join(lignes) or "aucune enregistrée",
                          inline=False)
            emb.add_field(name="Méthodes 2FA enrôlées",
                          value=f"TOTP : {totp} · Passkeys/WebAuthn : {webauthn}",
                          inline=True)
        except Exception as e:  # noqa: BLE001
            emb.add_field(name="Base Authelia", value=f"lecture impossible : `{e}`",
                          inline=False)

        view = None
        try:
            sig = await self._ct_notification_sig()
            size = int(sig.split(":")[0] or 0)
            if size > 0:
                emb.add_field(name="Lien de validation en attente",
                              value="📬 `notification.txt` n'est pas vide "
                                    "(enrôlement 2FA ou reset en cours)",
                              inline=True)
                view = SsoPanelView(self)
            else:
                emb.add_field(name="Lien de validation en attente",
                              value="aucun", inline=True)
        except Exception:  # noqa: BLE001 — l'indicateur est optionnel
            pass

        emb.set_footer(text="Lecture seule via l'hyperviseur · liens de validation "
                            "en DM admin (pas de SMTP)")
        await itx.followup.send(embed=emb, view=view)

    # ------------------------------------------------------------------- veille
    @tasks.loop(seconds=120)
    async def sso_watch(self):
        cfg = self.bot.cfg
        st = self.bot.state

        # -- événements d'authentification -------------------------------------
        last = st.get("sso_last_auth_id")
        if last is None:
            # Premier démarrage : on se cale sur l'existant SANS rejouer
            # l'historique (sinon chaque déploiement re-posterait tout).
            st.set("sso_last_auth_id", await self._ct_max_auth_id())
        else:
            rows = await self._ct_auth_rows(last)
            if rows:
                st.set("sso_last_auth_id", rows[-1]["id"])
                ch = None
                if cfg.alert_channel_id:
                    ch = self.bot.get_channel(cfg.alert_channel_id)
                if ch is not None:
                    for level, title, desc in summarize_batch(rows):
                        if level == "ok" and not cfg.sso_login_notify:
                            continue
                        color = {"ban": fmt.RED, "fail": fmt.YELLOW}.get(level, fmt.GREEN)
                        await ch.send(embed=discord.Embed(
                            title=title, description=desc, color=color))

        # -- lien de validation (notification.txt) -----------------------------
        sig = await self._ct_notification_sig()
        size = int(sig.split(":")[0] or 0)
        prev = st.get("sso_notif_sig", "")
        if prev == "":
            # premier passage : mémoriser sans notifier (un lien déjà consommé
            # peut traîner dans le fichier)
            st.set("sso_notif_sig", sig)
        elif sig != prev:
            st.set("sso_notif_sig", sig)
            if size > 0:
                content = (await self._ct_read_notification()).strip()
                if content:
                    await self._dm_admins(
                        "🔐 **Authelia a émis un lien de validation** (enrôlement "
                        "2FA ou reset de mot de passe). Il n'y a pas de SMTP : le "
                        "voici, ne le partage pas.\n"
                        f">>> {content[:1700]}")

    async def _dm_admins(self, text):
        sent = False
        for uid in self.bot.cfg.admin_ids:
            try:
                user = await self.bot.fetch_user(uid)
                await user.send(text)
                sent = True
            except Exception as e:  # noqa: BLE001 — DM fermés / id invalide
                log.warning("sso: DM admin %s impossible: %s", uid, e)
        if not sent and self.bot.cfg.alert_channel_id:
            # Repli SANS le lien : on signale qu'un lien attend, sans le publier
            # dans un salon (il ouvre un enrôlement 2FA).
            ch = self.bot.get_channel(self.bot.cfg.alert_channel_id)
            if ch is not None:
                await ch.send(embed=discord.Embed(
                    title="🔐 SSO : lien de validation en attente",
                    description="Aucun DM admin n'a pu partir — le lien reste dans "
                                "`notification.txt` sur CT123 (voir /sso).",
                    color=fmt.YELLOW))

    @sso_watch.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Sso(bot))
