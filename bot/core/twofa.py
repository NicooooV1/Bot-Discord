"""2FA TOTP pour le bot Edmine.

Choix de Nico (2026-07-16) : **toutes** les commandes sont protégées. Le tree refuse
l'exécution tant que l'utilisateur n'a pas ouvert une **session de confiance** avec un
code TOTP ; la session dure `TWOFA_SESSION_MIN` minutes et vit en MÉMOIRE (un
redémarrage du bot la ferme — c'est voulu).

ANTI-VERROUILLAGE — critique, puisque le 2FA barre TOUT :
- Les sous-commandes `/2fa` sont TOUJOURS exemptes : sans ça, personne ne pourrait
  jamais s'inscrire ni déverrouiller (poule et œuf).
- 8 **codes de secours à usage unique** sont remis à l'inscription (téléphone perdu/cassé).
- Break-glass ultime : `TWOFA_ENABLED=false` dans config.env + `systemctl restart discord-bot`.
- `TWOFA_ENABLED` vaut **false par défaut** : on n'active qu'une fois inscrit, sinon le
  premier démarrage barrerait la porte avec la clé à l'intérieur.

SÉCURITÉ :
- Secrets dans `/var/lib/discord-bot/2fa.json`, **0600 discordbot** (PAS state.json, qui
  sert à des données banales et n'a pas la même valeur).
- Les codes de secours sont stockés **hachés** (sha256) : le fichier volé ne les rend pas.
- Anti-rejeu (revu 2026-08-11) : on mémorise le **pas de temps** accepté, pas le code.
  `valid_window=1` rend TROIS codes valides à la fois (~90 s) : ne retenir qu'un seul
  code laissait le précédent rejouable dès qu'un second était accepté. Le compteur est
  monotone (RFC 6238 §5.2) — tout pas ≤ au dernier accepté est refusé — et il est
  persisté à côté des sessions pour qu'un redémarrage ne rouvre pas la fenêtre.
"""
import hashlib
import json
import os
import secrets
import time

import pyotp

ISSUER = "Edmine"
BACKUP_COUNT = 8


class TwoFAStoreError(RuntimeError):
    """Le magasin de secrets n'a PAS pu être écrit (disque plein, droits, ou magasin
    dégradé). Levée par `_save()` pour que chaque appelant décide de son annulation :
    sans elle, une écriture ratée laissait la mémoire et le disque diverger (inscription
    « réussie » en mémoire, absente du fichier — et perdue au redémarrage)."""


def _hash(code):
    return hashlib.sha256(code.encode()).hexdigest()


class TwoFA:
    def __init__(self, path, session_min=15, log=None):
        self.path = path
        self.session_min = max(1, int(session_min))
        self.log = log
        self._data = {"users": {}}
        self._pending = {}      # uid -> secret en cours d'inscription (jamais persisté)
        self._sessions = {}     # uid -> expiration (epoch)
        # uid -> DERNIER PAS DE TEMPS TOTP accepté (anti-rejeu monotone). Retenir le
        # dernier *code* ne suffisait pas : valid_window=1 accepte 3 pas simultanément,
        # donc un 2e code accepté rendait le 1er rejouable jusqu'à la fin de sa fenêtre.
        self._last_step = {}
        # True si le fichier existe mais est ILLISIBLE (corrompu) : dans ce cas les
        # inscriptions sont inconnues et il ne faut PAS conclure « personne n'est inscrit »
        # (sinon la réconciliation révoquerait Gestion/O à tout le monde). Voir gestion.py.
        self.degraded = False
        # Sessions PERSISTÉES (fichier à part du magasin de secrets) : sans ça, un
        # redémarrage du bot vide les sessions en mémoire -> l'utilisateur qui vient de
        # déverrouiller se voit RE-demander le 2FA. On veut qu'1 h veuille dire 1 h même
        # à travers un restart. Ne contient que des expirations et le dernier pas TOTP
        # accepté (aucun secret, aucun code).
        self._sessions_path = os.path.join(os.path.dirname(path), "2fa-sessions.json")
        # Callback optionnel `fn(uid: str)` : prévenu quand une session s'OUVRE, se FERME
        # ou EXPIRE (et à la désinscription). Le cog gestion s'y branche pour retirer/
        # remettre IMMÉDIATEMENT les rôles liés à la session (« 2FA Complet », « O <srv> »)
        # au lieu d'attendre la boucle de réconciliation.
        self.on_change = None
        self._load()
        self._load_sessions()

    # ------------------------------------------------------------------ stockage
    def _load(self):
        try:
            with open(self.path) as f:
                d = json.load(f)
            if isinstance(d, dict) and isinstance(d.get("users"), dict):
                self._data = d
            else:
                self.degraded = True   # contenu inattendu = ne pas révoquer en masse
        except FileNotFoundError:
            pass                       # aucun inscrit encore = état NORMAL, pas dégradé
        except Exception as e:  # noqa: BLE001
            self.degraded = True       # corrompu/illisible -> la réconciliation s'abstient
            if self.log:
                self.log.error("2fa: fichier illisible (%s) — inscriptions ignorées, "
                               "réconciliation des rôles gelée par sécurité", e)

    def reload(self):
        """Retente la lecture du magasin après un état dégradé. Renvoie True si le
        magasin est de nouveau lisible.

        ⚠️ N'écrase `_data` QUE sur une lecture VALIDE : une lecture ratée doit laisser
        l'état dégradé intact, sinon une corruption transitoire se transformerait en
        « personne n'est inscrit » — donc en révocation de masse des rôles, exactement ce
        que le drapeau `degraded` sert à empêcher. Un fichier DISPARU ne lève pas non plus
        le gel : à ce stade il vaut mieux figer les rôles que les dépouiller tous.
        (2026-08-11 : sans cette méthode le gel durait jusqu'au redémarrage du processus.)
        """
        if not self.degraded:
            return True
        try:
            with open(self.path) as f:
                d = json.load(f)
        except Exception as e:  # noqa: BLE001 — absent, illisible, JSON cassé : on reste gelé
            if self.log:
                self.log.debug("2fa: relecture du magasin encore impossible (%s)", e)
            return False
        if isinstance(d, dict) and isinstance(d.get("users"), dict):
            self._data = d
            self.degraded = False
            if self.log:
                self.log.warning("2fa: magasin de nouveau lisible — gel levé")
            return True
        return False

    def _save(self):
        """Persiste le magasin de secrets. Lève TwoFAStoreError en cas d'échec — l'appelant
        DOIT annuler sa mutation mémoire, sinon mémoire et disque divergent."""
        # 2026-08-11 : refus d'écrire par-dessus un magasin ILLISIBLE. `_data` est alors
        # vide : le remplacer changerait une corruption peut-être réparable à la main en
        # perte DÉFINITIVE de tous les secrets (os.replace est atomique et sans retour).
        if self.degraded:
            raise TwoFAStoreError("magasin 2FA dégradé : écriture refusée")
        tmp = self.path + ".tmp"
        try:
            # 0600 dès la création : jamais de fenêtre où le secret serait lisible.
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp, self.path)  # atomique : pas de fichier tronqué si ça coupe
        except OSError as e:
            try:
                os.unlink(tmp)          # pas de .tmp fantôme à 0600 derrière nous
            except OSError:
                pass
            if self.log:
                self.log.error("2fa: magasin NON écrit (%s)", e)
            raise TwoFAStoreError(str(e)) from e

    def _load_sessions(self):
        """Recharge les sessions non expirées (et l'anti-rejeu) après un redémarrage.

        Deux formats acceptés : l'ANCIEN `{uid: expiration}` (fichiers d'avant le
        2026-08-11) et le nouveau `{"sessions": {...}, "last_step": {...}}` — sans quoi
        une mise à jour du bot fermerait toutes les sessions en cours.
        """
        try:
            with open(self._sessions_path) as f:
                d = json.load(f)
            now = time.time()
            if isinstance(d, dict) and isinstance(d.get("sessions"), dict):
                raw, steps = d["sessions"], (d.get("last_step") or {})
            else:
                raw, steps = d, {}
            self._sessions = {str(k): float(v) for k, v in raw.items() if float(v) > now}
            self._last_step = {str(k): int(v) for k, v in steps.items()}
        except Exception:  # noqa: BLE001 — fichier absent/corrompu/JSON non-dict : des
            # sessions perdues se rouvrent d'un /2fa unlock, un boot qui crashe non
            self._sessions = {}
            self._last_step = {}

    def _save_sessions(self):
        """Persiste les sessions + l'anti-rejeu (best-effort : un échec ne casse pas le
        déverrouillage — au pire ils ne survivront pas à un restart, comportement d'avant.
        Bloquer ici créerait un verrouillage total, contraire à la doctrine du module)."""
        try:
            tmp = self._sessions_path + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump({"sessions": self._sessions, "last_step": self._last_step}, f)
            os.replace(tmp, self._sessions_path)
        except OSError as e:
            if self.log:
                self.log.warning("2fa: sessions non persistées: %s", e)

    # ------------------------------------------------------------------ inscription
    def enrolled(self, uid):
        return str(uid) in self._data["users"]

    def begin_enroll(self, uid, label):
        """Génère un secret PROVISOIRE (non persisté tant que le code n'est pas confirmé :
        sinon un abandon en cours de route laisserait un compte inscrit mais inutilisable)."""
        secret = pyotp.random_base32()
        self._pending[str(uid)] = secret
        uri = pyotp.TOTP(secret).provisioning_uri(name=label, issuer_name=ISSUER)
        return secret, uri

    def confirm_enroll(self, uid, code):
        """Valide le 1er code -> persiste le secret + rend les codes de secours.

        Renvoie None si le code est refusé. Lève **TwoFAStoreError** si le magasin n'a
        pas pu être écrit : l'appelant doit alors dire « inscription NON enregistrée »,
        surtout pas « code invalide » — le diagnostic enverrait l'utilisateur régler
        l'heure de son téléphone pour une panne de disque (2026-08-11)."""
        uid = str(uid)
        secret = self._pending.get(uid)
        if not secret:
            return None
        prev_step = self._last_step.get(uid)        # None = aucun pas encore consommé
        step = self._totp_step(secret, code, prev_step if prev_step is not None else -1)
        if step is None:
            return None
        backup = [secrets.token_hex(4) for _ in range(BACKUP_COUNT)]
        self._data["users"][uid] = {
            "secret": secret,
            "backup": [_hash(b) for b in backup],
            "enrolled_at": int(time.time()),
        }
        self._last_step[uid] = step
        try:
            self._save()
        except TwoFAStoreError:
            # ANNULATION : sans elle `enrolled()` répondrait True alors que rien n'est sur
            # le disque — /2fa status annoncerait « 8/8 codes de secours » que personne
            # n'a jamais vus, et l'inscription disparaîtrait au redémarrage.
            self._data["users"].pop(uid, None)
            # pas de temps rendu : le MÊME code doit rester réessayable une fois le disque
            # réparé, sinon l'utilisateur lirait « code invalide » pour une panne d'écriture
            if prev_step is None:
                self._last_step.pop(uid, None)
            else:
                self._last_step[uid] = prev_step
            # `_pending` reste INTACT : un nouveau clic « Confirmer » suffit, sans rescan
            raise
        self._pending.pop(uid, None)
        self.open_session(uid)
        return backup           # rendus EN CLAIR une seule fois, jamais restituables

    def revoke(self, uid):
        """Désinscrit un utilisateur. Lève TwoFAStoreError si le magasin n'a pas pu être
        écrit — la désinscription est alors ANNULÉE en mémoire (fail-closed : mieux vaut
        un 2FA toujours actif qu'un compte réputé désinscrit qui redevient inscrit au
        prochain redémarrage)."""
        uid = str(uid)
        entry = self._data["users"].pop(uid, None)
        existed = entry is not None
        if existed:
            try:
                self._save()
            except TwoFAStoreError:
                self._data["users"][uid] = entry
                raise
        # avant _save_sessions : rien à garder de l'anti-rejeu d'un compte désinscrit
        self._last_step.pop(uid, None)
        if self._sessions.pop(uid, None) is not None:
            self._save_sessions()
        self._pending.pop(uid, None)
        if existed:
            self._fire(uid)     # plus inscrit -> ses rôles 2FA doivent tomber tout de suite
        return existed

    # ------------------------------------------------------------------ vérification
    def _totp_step(self, secret, code, last):
        """Renvoie le PAS DE TEMPS qui valide `code`, ou None.

        Équivalent de `verify(code, valid_window=1)` mais fenêtre par fenêtre, pour
        pouvoir refuser tout pas déjà consommé (`<= last`, compteur monotone RFC 6238).
        `t.interval` plutôt que 30 en dur : le pas doit suivre l'intervalle réel.
        Effet de bord assumé : après un code « en avance », un téléphone légèrement en
        retard devra attendre la fenêtre suivante — d'où le message « code invalide ou
        déjà utilisé » côté cog, qui couvre les deux cas."""
        t = pyotp.TOTP(secret)
        interval = int(getattr(t, "interval", 30) or 30)
        now = int(time.time()) // interval
        for d in (-1, 0, 1):
            s = now + d
            if s <= last:
                continue            # anti-rejeu : ce pas a déjà servi (ou est antérieur)
            if t.verify(code, for_time=s * interval):
                return s
        return None

    def verify(self, uid, code):
        """Accepte un code TOTP ou un code de secours (à usage unique)."""
        uid = str(uid)
        u = self._data["users"].get(uid)
        if not u:
            return False
        code = (code or "").strip().replace(" ", "")

        if code.isdigit() and len(code) == 6:
            step = self._totp_step(u["secret"], code, self._last_step.get(uid, -1))
            if step is None:
                return False
            self._last_step[uid] = step
            self.open_session(uid)      # persiste aussi _last_step (même fichier)
            return True

        h = _hash(code)
        if h in u.get("backup", []):
            u["backup"].remove(h)   # usage unique
            try:
                self._save()
            except TwoFAStoreError:
                # ANTI-VERROUILLAGE : on ouvre quand même la session. Un code de secours
                # sert quand le téléphone est perdu — refuser ici enfermerait la clé à
                # l'intérieur pour une panne d'écriture. Le code redeviendra utilisable
                # au prochain redémarrage (non consommé sur le disque) : c'est le moindre
                # mal, et c'est journalisé par _save().
                if self.log:
                    self.log.error("2fa: code de secours consommé en mémoire seulement "
                                   "(magasin non écrit) — session ouverte malgré tout")
            self.open_session(uid)
            return True
        return False

    def backup_left(self, uid):
        u = self._data["users"].get(str(uid))
        return len(u.get("backup", [])) if u else 0

    # ------------------------------------------------------------------ sessions
    def _fire(self, uid):
        """Prévient l'abonné on_change. Best-effort : une erreur du callback ne doit
        jamais remonter jusqu'au déverrouillage/à la vérification qui l'a déclenché."""
        cb = self.on_change
        if cb is None:
            return
        try:
            cb(str(uid))
        except Exception:  # noqa: BLE001
            if self.log:
                self.log.exception("2fa: callback on_change en échec")

    def _expire(self, uid):
        """Purge une session arrivée à expiration + prévient l'abonné (retrait des rôles)."""
        if self._sessions.pop(str(uid), None) is not None:
            self._save_sessions()
            self._fire(uid)

    def expire_stale(self):
        """Purge TOUTES les sessions échues et rend leurs uid. À appeler périodiquement :
        sans balayage actif, une session expirée resterait dans le dict (et les rôles sur
        le membre) tant que personne n'appellerait trusted() pour cet uid."""
        now = time.time()
        stale = [u for u, exp in self._sessions.items() if exp <= now]
        for u in stale:
            self._expire(u)
        return stale

    def open_session(self, uid):
        self._sessions[str(uid)] = time.time() + self.session_min * 60
        self._save_sessions()          # survit à un redémarrage du bot
        self._fire(uid)                # rôles de session remis sans attendre la boucle

    def trusted(self, uid):
        exp = self._sessions.get(str(uid))
        if not exp:
            return False
        if exp < time.time():
            self._expire(uid)
            return False
        return True

    def session_left(self, uid):
        exp = self._sessions.get(str(uid))
        return max(0, int(exp - time.time())) if exp else 0

    def close_session(self, uid):
        if self._sessions.pop(str(uid), None) is not None:
            self._save_sessions()
            self._fire(uid)
