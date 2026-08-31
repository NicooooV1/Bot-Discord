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
from ..core.gates import GatedView

log = logging.getLogger("discord-bot.transfert")

# 2026-08-30 : les lignes de FIN DE FICHIER de rsync « (xfr#N, ir-chk=…) » portent un ETA
# recalculé sur une autre base (0:06:59 pour 2,3 Tio restants) : relevées entre deux films,
# elles faisaient annoncer « Reste ≈ 10 Go » (question de Nico). On les ignore.
# Une seule commande, une seule connexion SSH par relevé. `tr` : rsync écrit sa
# progression avec des retours CHARIOT (une seule « ligne » de plusieurs Mo sinon).
SONDE = r"""
unite=%(unite)s; journal=%(journal)s; cible=%(cible)s; source=%(source)s
echo "etat=$(systemctl is-active "$unite" 2>&1)"
echo "resultat=$(systemctl show "$unite" -p Result --value 2>/dev/null)"
echo "debut=$(systemctl show "$unite" -p InactiveExitTimestamp --value 2>/dev/null)"
tail -c 20000 "$journal" 2>/dev/null | tr '\r' '\n' \
  | grep -E '^[[:space:]]*[0-9][0-9.,]*[KMGT]?[[:space:]]+[0-9]+%%' | grep -v 'xfr#' | tail -1 \
  | sed 's/^/progres=/'
tail -n 200 "$journal" 2>/dev/null | tr '\r' '\n' \
  | grep -E '^(====|[0-9]{4}-[0-9]{2}-[0-9]{2} )' | tail -3 | sed 's/^/ligne=/'
tail -c 20000 "$journal" 2>/dev/null | tr '\r' '\n' | grep -o 'ir-chk=[0-9/]*\|to-chk=[0-9/]*' \
  | tail -1 | sed 's/^/chk=/'
timeout 3 df -B1 --output=avail "$cible" 2>/dev/null | tail -1 | tr -d ' ' | sed 's/^/libre=/'
# progression RÉELLE (indépendante du compteur de session rsync, remis à 0 à chaque
# redémarrage) : octets présents à l'arrivée / volume de la source. df, pas du : instantané.
timeout 6 df -B1 --output=used "$cible" 2>/dev/null | tail -1 | tr -d ' ' | sed 's/^/utilise=/'
timeout 5 df -B1 --output=used "$source" 2>/dev/null | tail -1 | tr -d ' ' | sed 's/^/total_reel=/'
echo "hote=$(findmnt -n -o SOURCE "$cible" 2>/dev/null | cut -d: -f1)"
echo "chemin=$(cat /run/avy-media.path 2>/dev/null)"
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


def parse_sonde(sortie, reel_cache=None):
    """Sortie brute de SONDE -> dict. Tolère les champs manquants (journal vide,
    montage absent) : c'est justement pendant les ennuis qu'on regarde ce salon.

    `reel_cache` = dernier (utilise, total_reel) connu. Le `df` sur la cible est un
    montage NFS : pendant un creux du lien il dépasse son `timeout` et revient vide,
    ce qui faisait OSCILLER l'affichage entre la vraie progression (717 Go) et le
    compteur de session rsync (21 Go). Quand la mesure du cycle manque, on repart de
    ce cache plutôt que du compteur (2026-08-31)."""
    out = {"etat": "inconnu", "resultat": "", "debut": "", "lignes": [],
           "octets": None, "pct": None, "debit": "", "eta": None, "libre": None,
           "analyse": None,   # True = rsync analyse encore (ir-chk), False = scan fini (to-chk)
           "hote": "", "chemin": "",   # 2026-08-30 : serveur NFS réel + vlan25/mgmt
           "utilise": None, "total_reel": None}   # 2026-08-31 : progression RÉELLE (df)
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
        elif cle == "utilise":
            out["utilise"] = int(val) if val.isdigit() else None
        elif cle == "total_reel":
            out["total_reel"] = int(val) if val.isdigit() else None
        elif cle == "hote":
            out["hote"] = val
        elif cle == "chemin":
            out["chemin"] = val
        elif cle == "chk":
            # rsync affiche « ir-chk » tant que la récursion INCRÉMENTALE analyse encore
            # l'arborescence : son total (donc notre %) ne couvre que le DÉJÀ-analysé et
            # grossit au fil du scan. « to-chk » = arborescence entière connue, chiffres
            # fiables. Sans ce drapeau, l'embed annonçait « 53 % de ~210 Gio » sur une
            # bibliothèque de 3,4 Tio (question de Nico, 2026-08-18).
            out["analyse"] = val.startswith("ir-chk")
        elif cle == "progres":
            m = RE_PROGRES.match(val)
            if m:
                out["octets"] = octets(m.group(1))
                out["pct"] = int(m.group(2))
                out["debit"] = m.group(3)
                out["eta"] = eta_secondes(m.group(4))
    # ----- progression RÉELLE (2026-08-31, demande Nico « les vraies données ») -----
    # Le compteur de session de rsync (out["octets"]) repart de ZÉRO à chaque redémarrage
    # du service : il affichait « 11,7 Go » alors que 715 Go étaient déjà à l'arrivée.
    # La vérité, c'est ce qui est RÉELLEMENT présent sur la cible (df used) rapporté au
    # volume de la source (df used aussi) — deux mesures instantanées, insensibles aux
    # redémarrages. Le débit reste celui, INSTANTANÉ, de la ligne rsync.
    d = debit_octets(out["debit"])
    # df de la cible NFS expiré ce cycle : garder la dernière progression réelle connue
    # au lieu de retomber sur le compteur de session rsync (source /mnt/media est locale,
    # elle expire rarement — mais on complète les deux au besoin)
    if reel_cache:
        cu, ct = reel_cache
        if out["utilise"] is None:
            out["utilise"] = cu
        if not out["total_reel"]:
            out["total_reel"] = ct
    u, t = out.get("utilise"), out.get("total_reel")
    if u is not None and t:
        out["transfere_reel"] = u
        out["total"] = t
        out["pct_fin"] = min(100.0, u / t * 100) if t else None
        out["reste"] = max(0, t - u)
        out["eta"] = int(out["reste"] / d) if d and out["reste"] else None
        # avec une base réelle (df), le % ne dépend plus du scan incrémental de rsync :
        # on neutralise le caveat « provisoire / du volume analysé »
        out["analyse"] = False
    else:
        # repli (df indisponible) sur l'ancienne estimation par le compteur de session
        out["transfere_reel"] = out["octets"]
        reste = out["eta"] * d if (out["eta"] is not None and d) else None
        out["reste"] = reste
        if reste is not None and out["octets"]:
            total = out["octets"] + reste
            out["pct_fin"] = out["octets"] / total * 100 if total else None
            out["total"] = total
        else:
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
        # analyse=None (marqueur pas encore vu) est traité comme « en cours » : au
        # démarrage rsync n'a encore rien imprimé de fiable.
        scan_fini = etat.get("analyse") is False
        titre = f"Progression — {pct:.1f} %"
        if etat.get("total"):
            titre += (f" de ~{fmt.humanize_bytes(etat['total'])}" if scan_fini else
                      f" du volume analysé (~{fmt.humanize_bytes(etat['total'])}, provisoire)")
        elif not scan_fini:
            # 2026-08-20 : sans total estimé, le % brut de rsync ne porte que sur le
            # DÉJÀ-analysé — le dire, sinon « X % » se lit comme un % du transfert entier.
            titre += " du volume analysé (provisoire)"
        emb.add_field(name=titre, value=f"`{barre(pct)}`", inline=False)
        if not scan_fini and cfg.transfert_total_hint:
            emb.add_field(
                name="Bibliothèque complète",
                value=f"≈ {cfg.transfert_total_hint} — tout part ; rsync analyse encore "
                      "l'arborescence, l'estimation s'affine (le déjà-copié est sauté, "
                      "pas renvoyé)", inline=False)
    # « Transféré » = octets RÉELLEMENT présents à l'arrivée (df), pas le compteur de
    # session rsync qui repart de 0 à chaque redémarrage (Nico 2026-08-31)
    transf = etat.get("transfere_reel")
    transf = transf if transf is not None else etat.get("octets")
    if transf is not None:
        emb.add_field(name="Transféré", value=fmt.humanize_bytes(transf))
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
    if etat.get("hote"):
        # 2026-08-30 (Nico : « en passant par sa VLAN25 ») : le nas exporte le pool sur
        # 10.0.25.10 (VLAN25) ET 10.0.10.10 (mgmt) ; l'hôte préfère le VLAN25 et retombe
        # sur le mgmt s'il n'est pas routé — on le DIT, sinon on croit que c'est réglé.
        chemin = etat.get("chemin")
        libelle = {"vlan25": "VLAN25 ✅", "mgmt": "mgmt (repli, VLAN25 injoignable)"}.get(
            chemin, chemin or "?")
        emb.add_field(name="Chemin réseau", value=f"`{etat['hote']}` · {libelle}")
    if etat["debut"]:
        emb.add_field(name="Démarré", value=etat["debut"], inline=False)
    if etat["lignes"]:
        emb.add_field(name="Journal", value="```\n" + "\n".join(etat["lignes"])[-900:] + "\n```",
                      inline=False)
    emb.set_footer(text=f"{cfg.transfert_source} → {cfg.transfert_dest} · "
                        f"relevé toutes les {cfg.transfert_poll_sec} s")
    return emb


class TransfertRefreshView(GatedView):
    """Bouton « Rafraîchir » sous l'embed de suivi (Nico 2026-08-30 : « ajoute aussi un
    bouton pour rafraîchir, sinon 1 minute de délai de base »).

    Porte : `gate = None` volontairement — le salon est PUBLIC et en lecture seule, et le
    bouton ne fait que rejouer la sonde de LECTURE (les mêmes `systemctl show` / `tail`
    que la boucle) puis réécrire l'embed ; il n'y a rien à protéger. Anti-rafale : un
    relevé au plus toutes les `RAFRAICHIR_MIN_SEC` s, sinon on répond « déjà à jour »."""

    gate = None
    gate_reason = ("salon public en lecture seule ; le bouton rejoue la sonde de lecture "
                   "et réécrit l'embed, aucune action — limité à 1 relevé / 10 s")
    RAFRAICHIR_MIN_SEC = 10

    def __init__(self, cog):
        super().__init__(timeout=None)          # persistante : survit au redémarrage
        self.cog = cog

    @discord.ui.button(label="Rafraîchir", emoji="🔄", style=discord.ButtonStyle.secondary,
                       custom_id="transfert:rafraichir")
    async def rafraichir(self, interaction, _button):
        """Rejoue la sonde et réécrit l'embed EN PLACE, sans rien poster (Nico 2026-08-31 :
        « enlève ce message inutile "relevé rafraîchi" »). Un message éphémère n'apparaît
        QUE si le relevé échoue — le succès se voit sur l'embed lui-même, pas dans un accusé.

        `defer()` sans `thinking` = accusé SILENCIEUX (deferred update) : il ferme
        l'interaction sans « … réfléchit » ni message, et n'impose aucun followup. C'est
        ce qui permet un succès muet ; l'ancien `defer(thinking=True)` obligeait à
        répondre, d'où le « ✅ Relevé rafraîchi » superflu."""
        cog = self.cog
        await interaction.response.defer()
        depuis = time.time() - cog._dernier_releve
        if depuis < self.RAFRAICHIR_MIN_SEC:
            # anti-rafale : l'embed a été relevé il y a moins de RAFRAICHIR_MIN_SEC,
            # il est déjà à jour — on ne re-sonde pas et on ne dit rien.
            return
        try:
            ok = await cog.relever()
        except Exception:  # noqa: BLE001 — un relevé raté ne doit pas « échouer l'interaction »
            log.warning("bouton Rafraîchir : relevé en échec", exc_info=True)
            ok = False
        if not ok:
            await interaction.followup.send(
                "⚠️ Hôte injoignable pour l'instant, l'embed n'a pas été modifié.",
                ephemeral=True)


class Transfert(commands.Cog):
    """Salon éphémère de suivi du transfert média (lecture seule, visible de tous)."""

    def __init__(self, bot):
        self.bot = bot
        self.cfg = bot.cfg
        self.enabled = bool(getattr(self.cfg, "transfert_enabled", False)
                            and self.cfg.node_ssh_key)
        self._etat = dict(bot.state.get("transfert", {}) or {})
        self._ssh = None       # connexion SSH RÉUTILISÉE entre les relevés (cf. _run)
        self._dernier_releve = 0.0
        self._reel_cache = None   # dernier (utilise, total_reel) connu (df cible NFS lisse)
        self._verrou = asyncio.Lock()   # un seul relevé à la fois (boucle + bouton)
        self._view = TransfertRefreshView(self)
        if self.enabled:
            self.bot.add_view(self._view)   # boutons persistants au reboot
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
                await msg.edit(embed=emb, view=self._view)
                return
            except discord.NotFound:
                pass
            except discord.HTTPException:
                log.debug("édition du message de suivi échouée", exc_info=True)
                return
        try:
            msg = await ch.send(embed=emb, view=self._view)
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
                       "cible": self.cfg.transfert_dest,
                       "source": self.cfg.transfert_source}
        etat = parse_sonde(await self._run(cmd), reel_cache=self._reel_cache)
        # mémorise la dernière progression RÉELLE (df réussi) pour lisser un df qui expire
        if etat.get("utilise") is not None and etat.get("total_reel"):
            self._reel_cache = (etat["utilise"], etat["total_reel"])
        return etat

    @tasks.loop(seconds=60)
    async def poll(self):
        await self.relever()

    async def relever(self):
        """Un relevé complet (sonde + salon + embed). Appelé par la boucle (60 s par
        défaut) ET par le bouton « Rafraîchir ». Retourne False si l'hôte est muet."""
        guild = self._guild()
        if guild is None:
            return False
        async with self._verrou:
            try:
                etat = await self.sonde()
            except Exception:
                # l'hôte injoignable ne doit ni supprimer le salon ni figer un « terminé » :
                # on ne conclut RIEN et on repasse au cycle suivant.
                log.warning("relevé du transfert impossible", exc_info=True)
                return False
            self._dernier_releve = time.time()
            await self._appliquer(guild, etat)
            return True

    # Relevés INACTIFS consécutifs avant de DÉCLARER l'arrêt (message + compte à rebours
    # de suppression). Un `systemctl restart` — ou tout flap systemd — laisse le service
    # quelques secondes hors des ETATS_ACTIFS : sans ce filtre, chaque redémarrage pour
    # changer un réglage (ex. BWLIMIT le 2026-08-31) postait aussitôt un « 🔴 arrêté …
    # salon supprimé dans 1h » démenti par la reprise. Relevé à 60 s : 2 = ~2 min de grâce.
    INACTIFS_AVANT_FIN = 2

    async def _appliquer(self, guild, etat):
        actif = etat["etat"] in ETATS_ACTIFS
        if actif:
            self._etat["inactif_n"] = 0
            self._etat.pop("fin_ts", None)
            ch = await self._salon(guild, creer=True)
            if ch is not None:
                # reprise après un arrêt DÉJÀ annoncé (flap) : retirer le message
                # « arrêté … » devenu faux, sinon il traîne sous un embed redevenu vert
                await self._retirer_annonce_fin(ch)
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
            # anti-rebond : n'annoncer l'arrêt qu'après INACTIFS_AVANT_FIN relevés
            # inactifs d'affilée. En deçà, on ne touche NI à l'embed (pas de clignotement
            # vert→rouge→vert lors d'un simple redémarrage) NI au salon.
            n = self._etat.get("inactif_n", 0) + 1
            self._etat["inactif_n"] = n
            self._save()
            if n < self.INACTIFS_AVANT_FIN:
                return
            self._etat["fin_ts"] = time.time()
            self._save()
            await self._publier(ch, embed_transfert(etat, self.cfg))
            garde = self.cfg.transfert_keep_min
            try:
                # 2026-08-20 : « terminé » seulement si systemd dit success — l'embed
                # au-dessus fait déjà la distinction, le message final doit la suivre
                # (un `failed` ou un état inconnu n'est pas un transfert « terminé »).
                if etat.get("resultat") == "success":
                    entete = "⏹️ **Transfert terminé** (success)"
                else:
                    entete = (f"🔴 **Transfert arrêté** "
                              f"({etat.get('resultat') or 'état inconnu'})")
                msg = await ch.send(
                    f"{entete} — ce salon temporaire sera supprimé dans "
                    f"{fmt.humanize_duration(garde * 60)}.",
                    allowed_mentions=discord.AllowedMentions.none())
                # mémorisé pour pouvoir le RETIRER si le transfert repart (cf. actif ci-dessus)
                self._etat["stop_msg_id"] = msg.id
                self._save()
            except discord.HTTPException:
                log.debug("message de fin non publié", exc_info=True)
            return
        if time.time() - fin >= self.cfg.transfert_keep_min * 60:
            await self._supprimer(guild)

    async def _retirer_annonce_fin(self, ch):
        """Supprime le message « Transfert arrêté … salon supprimé dans … » quand l'arrêt
        s'est révélé transitoire (le transfert est reparti). Sans ça, un redémarrage pour
        changer un réglage laissait cette annonce sous un embed redevenu vert
        (constat Nico 2026-08-31)."""
        mid = self._etat.pop("stop_msg_id", None)
        if not mid:
            return
        try:
            msg = await ch.fetch_message(mid)
            await msg.delete()
        except discord.NotFound:
            pass
        except discord.HTTPException:
            log.debug("retrait de l'annonce d'arrêt échoué", exc_info=True)

    @poll.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Transfert(bot))
