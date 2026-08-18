"""Salon TEMPORAIRE de suivi du transfert média vers le site distant (Nico 2026-08-18 :
« prévois un salon temporaire accessible pour tous mais sans aucun droit d'écriture
pour suivre le transfert en temps réel »).

CE QUE C'EST. Le premier remplissage du pool de sauvegarde Jellyfin (≈ 3,4 Tio de
/mnt/media vers 10.0.10.10:/data-pool/media, plafonné à 40 Mb/s) dure des JOURS. Il
tourne sous systemd sur l'HYPERVISEUR (`media-backup.service`), donc plus rien à voir
avec une session ouverte quelque part : ce salon en est la fenêtre d'observation.

CYCLE DE VIE (« temporaire », au sens propre) :
  • transfert actif      -> le salon est créé s'il manque, et son message épinglé est
                            réécrit à chaque relevé (progression, débit, ETA) ;
  • transfert terminé    -> un dernier message dit le résultat, puis le salon est
                            SUPPRIMÉ après `TRANSFERT_KEEP_MIN` minutes ;
  • transfert relancé    -> il renaît tout seul au relevé suivant.

DROITS : @everyone voit et LIT, mais `send_messages` est refusé au niveau du salon —
un refus de salon prime sur toute permission de rôle (sauf Administrateur, que Discord
place au-dessus de tout ; c'est aussi vrai partout ailleurs dans ce serveur).

SOURCE DES DONNÉES : une commande de LECTURE SEULE sur l'hôte, par la clé SSH déjà
utilisée par la console du nœud (`restrict,pty,from=10.3.10.106`, host key épinglée).
Aucun privilège nouveau : cette clé ouvre déjà un shell root, on ne fait ici qu'y lire
`systemctl is-active`, la dernière ligne de progression de rsync et l'espace libre.
"""
import asyncio
import logging
import re
import time

import discord
from discord.ext import commands, tasks

from ..core import format as fmt

log = logging.getLogger("discord-bot.transfert")

# Une seule commande, une seule connexion SSH par relevé. `tr` : rsync écrit sa
# progression avec des retours CHARIOT (une seule « ligne » de plusieurs Mo sinon).
SONDE = r"""
unite=%(unite)s; journal=%(journal)s; cible=%(cible)s
echo "etat=$(systemctl is-active "$unite" 2>&1)"
echo "resultat=$(systemctl show "$unite" -p Result --value 2>/dev/null)"
echo "debut=$(systemctl show "$unite" -p InactiveExitTimestamp --value 2>/dev/null)"
tail -c 20000 "$journal" 2>/dev/null | tr '\r' '\n' \
  | grep -E '^[[:space:]]*[0-9][0-9.,]*[KMGT]?[[:space:]]+[0-9]+%%' | tail -1 \
  | sed 's/^/progres=/'
tail -n 200 "$journal" 2>/dev/null | tr '\r' '\n' \
  | grep -E '^(====|[0-9]{4}-[0-9]{2}-[0-9]{2} )' | tail -3 | sed 's/^/ligne=/'
df -B1 --output=avail "$cible" 2>/dev/null | tail -1 | tr -d ' ' | sed 's/^/libre=/'
"""

#: un `Type=oneshot` qui dure reste dans l'état « activating » de bout en bout —
#: le tester avec `== "active"` faisait annoncer « terminé » pendant que rsync tournait.
ETATS_ACTIFS = ("active", "activating", "reloading")

# « 7.47G   0%    4.89MB/s  130:40:02 »
RE_PROGRES = re.compile(r"^\s*([\d.,]+[KMGTP]?)\s+(\d+)%\s+([\d.,]+[KMGTP]?B/s)\s+"
                        r"(\d+:\d\d:\d\d)")
_MULT = {"": 1, "K": 2**10, "M": 2**20, "G": 2**30, "T": 2**40, "P": 2**50}


def octets(txt):
    """« 7.47G » -> 8021254307. Renvoie None sur une valeur inattendue plutôt que de
    lever : cette chaîne vient d'un `tail` sur un journal, elle n'est jamais garantie."""
    m = re.fullmatch(r"([\d.,]+)([KMGTP]?)", (txt or "").strip())
    if not m:
        return None
    try:
        return int(float(m.group(1).replace(",", ".")) * _MULT[m.group(2)])
    except (ValueError, KeyError):
        return None


def eta_secondes(hms):
    """« 130:40:02 » -> 470402 s. rsync dépasse allègrement les 24 h ici."""
    try:
        h, m, s = (int(x) for x in hms.split(":"))
        return h * 3600 + m * 60 + s
    except (ValueError, AttributeError):
        return None


def debit_octets(txt):
    """« 4.89MB/s » -> 4890000 (rsync compte ces unités en décimal)."""
    m = re.fullmatch(r"([\d.,]+)([KMGTP]?)B/s", (txt or "").strip())
    if not m:
        return None
    dec = {"": 1, "K": 10**3, "M": 10**6, "G": 10**9, "T": 10**12, "P": 10**15}
    try:
        return float(m.group(1).replace(",", ".")) * dec[m.group(2)]
    except (ValueError, KeyError):
        return None


def parse_sonde(sortie):
    """Sortie brute de SONDE -> dict. Tolère les champs manquants (journal vide,
    montage absent) : c'est justement pendant les ennuis qu'on regarde ce salon."""
    out = {"etat": "inconnu", "resultat": "", "debut": "", "lignes": [],
           "octets": None, "pct": None, "debit": "", "eta": None, "libre": None}
    for ligne in (sortie or "").splitlines():
        cle, _, val = ligne.partition("=")
        val = val.strip()
        if cle == "etat":
            out["etat"] = val
        elif cle == "resultat":
            out["resultat"] = val
        elif cle == "debut":
            out["debut"] = val
        elif cle == "ligne" and val:
            out["lignes"].append(val)
        elif cle == "libre":
            out["libre"] = int(val) if val.isdigit() else None
        elif cle == "progres":
            m = RE_PROGRES.match(val)
            if m:
                out["octets"] = octets(m.group(1))
                out["pct"] = int(m.group(2))
                out["debit"] = m.group(3)
                out["eta"] = eta_secondes(m.group(4))
    reste = None
    d = debit_octets(out["debit"])
    if out["eta"] is not None and d:
        reste = out["eta"] * d
    out["reste"] = reste
    if reste is not None and out["octets"]:
        total = out["octets"] + reste
        out["pct_fin"] = out["octets"] / total * 100 if total else None
        out["total"] = total
    else:
        # repli sur le % ENTIER de rsync : moins précis, mais c'est mieux que rien
        out["pct_fin"] = float(out["pct"]) if out["pct"] is not None else None
        out["total"] = None
    return out


def barre(pct, largeur=20):
    n = max(0, min(largeur, round((pct or 0) / 100 * largeur)))
    return "█" * n + "░" * (largeur - n)


def embed_transfert(etat, cfg):
    actif = etat["etat"] in ETATS_ACTIFS
    ok = etat.get("resultat") == "success"
    emb = discord.Embed(
        title="📦 Transfert des médias vers le site distant",
        description=("🟢 **en cours**" if actif else
                     ("✅ **terminé**" if ok else
                      f"🔴 **arrêté** ({etat.get('resultat') or 'état inconnu'})")),
        color=fmt.BLURPLE if actif else (fmt.GREEN if ok else fmt.RED))
    emb.timestamp = discord.utils.utcnow()
    pct = etat.get("pct_fin")
    if pct is not None:
        titre = f"Progression — {pct:.1f} %"
        if etat.get("total"):
            titre += f" de ~{fmt.humanize_bytes(etat['total'])}"
        emb.add_field(name=titre, value=f"`{barre(pct)}`", inline=False)
    if etat["octets"] is not None:
        emb.add_field(name="Transféré", value=fmt.humanize_bytes(etat["octets"]))
    if etat["debit"]:
        emb.add_field(name="Débit", value=re.sub(r"([\d.,]+)([KMGTP]?B/s)", r"\1 \2",
                                                 etat["debit"]))
    if etat.get("reste") is not None and actif:
        emb.add_field(name="Reste", value="~" + fmt.humanize_bytes(etat["reste"]))
    if etat["eta"] is not None and actif:
        emb.add_field(name="Fin estimée",
                      value=fmt.humanize_duration(etat["eta"]) + " restantes")
    if etat["libre"] is not None:
        emb.add_field(name="Libre à l'arrivée", value=fmt.humanize_bytes(etat["libre"]))
    if etat["debut"]:
        emb.add_field(name="Démarré", value=etat["debut"], inline=False)
    if etat["lignes"]:
        emb.add_field(name="Journal", value="```\n" + "\n".join(etat["lignes"])[-900:] + "\n```",
                      inline=False)
    emb.set_footer(text=f"{cfg.transfert_source} → {cfg.transfert_dest} · "
                        f"relevé toutes les {cfg.transfert_poll_sec} s")
    return emb


class Transfert(commands.Cog):
    """Salon éphémère de suivi du transfert média (lecture seule, visible de tous)."""

    def __init__(self, bot):
        self.bot = bot
        self.cfg = bot.cfg
        self.enabled = bool(getattr(self.cfg, "transfert_enabled", False)
                            and self.cfg.node_ssh_key)
        self._etat = dict(bot.state.get("transfert", {}) or {})
        self._ssh = None       # connexion SSH RÉUTILISÉE entre les relevés (cf. _run)
        if self.enabled:
            self.poll.change_interval(seconds=max(30, self.cfg.transfert_poll_sec))
            self.poll.start()
        else:
            log.info("suivi de transfert désactivé (TRANSFERT_ENABLED / clé SSH du nœud)")

    def cog_unload(self):
        self.poll.cancel()
        if self._ssh is not None:
            try:
                self._ssh.close()
            except Exception:  # noqa: BLE001
                pass
            self._ssh = None

    def _save(self):
        self.bot.state.set("transfert", self._etat)

    # ------------------------------------------------------------------ salon

    def _guild(self):
        return self.bot.get_guild(self.cfg.guild_id)

    def _overwrites(self, guild):
        """@everyone : voit et lit, n'écrit RIEN. Le bot : écrit et gère le salon.

        ⚠️ `send_messages=False` ici est un refus AU NIVEAU DU SALON : il l'emporte sur
        la permission « Envoyer des messages » que n'importe quel rôle du serveur
        possède. Les fils sont refusés aussi, sinon on écrit dans le salon par un fil."""
        return {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=True, read_message_history=True,
                send_messages=False, add_reactions=False,
                create_public_threads=False, create_private_threads=False,
                send_messages_in_threads=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, embed_links=True,
                manage_messages=True, manage_channels=True),
        }

    async def _salon(self, guild, creer):
        cid = self._etat.get("channel_id")
        ch = guild.get_channel(cid) if cid else None
        if ch is not None:
            return ch
        if cid:                      # supprimé à la main : on repart de zéro
            self._etat.pop("channel_id", None)
            self._etat.pop("message_id", None)
        if not creer:
            return None
        # adopté par le nom s'il existe déjà (redémarrage du bot, état perdu)
        nom = self.cfg.transfert_channel_name
        ch = discord.utils.get(guild.text_channels, name=nom)
        if ch is None:
            # même catégorie que #général : le salon doit être PUBLIC, donc surtout pas
            # dans « 📊 Supervision R820 » dont _enforce_perms referme tout.
            gen = guild.get_channel(self.cfg.general_channel_id) \
                if getattr(self.cfg, "general_channel_id", 0) else None
            try:
                ch = await guild.create_text_channel(
                    nom, category=getattr(gen, "category", None),
                    overwrites=self._overwrites(guild),
                    topic="Suivi en temps réel du transfert des médias vers le site "
                          "distant. Salon temporaire, en lecture seule.",
                    reason="suivi du transfert média (salon temporaire)")
            except discord.HTTPException:
                log.warning("salon de suivi du transfert non créé", exc_info=True)
                return None
            log.info("salon #%s créé (suivi du transfert)", nom)
        self._etat["channel_id"] = ch.id
        self._save()
        return ch

    async def _publier(self, ch, emb):
        mid = self._etat.get("message_id")
        if mid:
            try:
                msg = await ch.fetch_message(mid)
                await msg.edit(embed=emb)
                return
            except discord.NotFound:
                pass
            except discord.HTTPException:
                log.debug("édition du message de suivi échouée", exc_info=True)
                return
        try:
            msg = await ch.send(embed=emb)
            try:
                await msg.pin()
            except discord.HTTPException:
                pass
            self._etat["message_id"] = msg.id
            self._save()
        except discord.HTTPException:
            log.warning("message de suivi du transfert non publié", exc_info=True)

    async def _supprimer(self, guild):
        cid = self._etat.get("channel_id")
        ch = guild.get_channel(cid) if cid else None
        if ch is not None:
            try:
                await ch.delete(reason="transfert terminé — salon temporaire")
                log.info("salon #%s supprimé (transfert terminé)", ch.name)
                self.bot.audit.record(user="system", action="salon-supprime",
                                      target=ch.name, result="transfert terminé")
            except discord.HTTPException:
                log.warning("suppression du salon de suivi impossible", exc_info=True)
                return
        self._etat = {}
        self._save()

    # ------------------------------------------------------------------ boucle

    async def _run(self, cmd, timeout=25):
        """Exécute `cmd` sur l'hyperviseur en RÉUTILISANT la connexion SSH.

        Une connexion neuve par relevé (revue 2026-08-18) écrivait une paire
        « Accepted publickey / session closed » par minute dans le journal de l'hôte
        — donc dans Loki — pendant les ~5 jours du transfert. On garde la connexion
        ouverte ; si elle est tombée (reboot de l'hôte, sshd rechargé), on la rouvre
        UNE fois et on réessaie. Même clé et même host key épinglée que la console
        du nœud : aucun privilège nouveau."""
        import asyncssh
        for derniere in (False, True):
            if self._ssh is None:
                self._ssh = await asyncssh.connect(
                    self.cfg.node_ssh_host, port=self.cfg.node_ssh_port,
                    username=self.cfg.node_ssh_user,
                    client_keys=[self.cfg.node_ssh_key],
                    known_hosts=self.cfg.node_ssh_known_hosts,
                    connect_timeout=timeout)
            try:
                r = await asyncio.wait_for(self._ssh.run(cmd, check=False),
                                           timeout=timeout)
                return r.stdout or ""
            except Exception:
                try:
                    self._ssh.close()
                except Exception:  # noqa: BLE001
                    pass
                self._ssh = None
                if derniere:
                    raise

    async def sonde(self):
        cmd = SONDE % {"unite": self.cfg.transfert_unit,
                       "journal": self.cfg.transfert_log,
                       "cible": self.cfg.transfert_dest}
        return parse_sonde(await self._run(cmd))

    @tasks.loop(seconds=60)
    async def poll(self):
        guild = self._guild()
        if guild is None:
            return
        try:
            etat = await self.sonde()
        except Exception:
            # l'hôte injoignable ne doit ni supprimer le salon ni figer un « terminé » :
            # on ne conclut RIEN et on repasse au cycle suivant.
            log.warning("relevé du transfert impossible", exc_info=True)
            return
        actif = etat["etat"] in ETATS_ACTIFS
        if actif:
            self._etat.pop("fin_ts", None)
            ch = await self._salon(guild, creer=True)
            if ch is not None:
                await self._publier(ch, embed_transfert(etat, self.cfg))
            self._save()
            return

        # inactif : rien à faire tant qu'aucun salon n'existe (cas normal 23 h sur 24)
        if not self._etat.get("channel_id"):
            return
        ch = await self._salon(guild, creer=False)
        if ch is None:
            self._etat = {}
            self._save()
            return
        fin = self._etat.get("fin_ts")
        if not fin:
            self._etat["fin_ts"] = time.time()
            self._save()
            await self._publier(ch, embed_transfert(etat, self.cfg))
            garde = self.cfg.transfert_keep_min
            try:
                await ch.send(
                    f"⏹️ **Transfert terminé** ({etat.get('resultat') or '?'}) — "
                    f"ce salon temporaire sera supprimé dans "
                    f"{fmt.humanize_duration(garde * 60)}.",
                    allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                log.debug("message de fin non publié", exc_info=True)
            return
        if time.time() - fin >= self.cfg.transfert_keep_min * 60:
            await self._supprimer(guild)

    @poll.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Transfert(bot))
