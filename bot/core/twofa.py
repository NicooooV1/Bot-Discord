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
- Anti-rejeu : un même code TOTP ne peut pas servir deux fois (pyotp ne le fait pas seul,
  et un code reste valide ~30 s — de quoi être rejoué par qui l'aurait vu passer).
"""
import hashlib
import json
import os
import secrets
import time

import pyotp

ISSUER = "Edmine"
BACKUP_COUNT = 8


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
        self._last_code = {}    # uid -> dernier code accepté (anti-rejeu)
        # True si le fichier existe mais est ILLISIBLE (corrompu) : dans ce cas les
        # inscriptions sont inconnues et il ne faut PAS conclure « personne n'est inscrit »
        # (sinon la réconciliation révoquerait Gestion/O à tout le monde). Voir gestion.py.
        self.degraded = False
        # Sessions PERSISTÉES (fichier à part du magasin de secrets) : sans ça, un
        # redémarrage du bot vide les sessions en mémoire -> l'utilisateur qui vient de
        # déverrouiller se voit RE-demander le 2FA. On veut qu'1 h veuille dire 1 h même
        # à travers un restart. Ne contient que des expirations (pas de secret).
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

    def _save(self):
        tmp = self.path + ".tmp"
        # 0600 dès la création : jamais de fenêtre où le secret serait lisible.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self._data, f, indent=2)
        os.replace(tmp, self.path)      # atomique : pas de fichier tronqué si ça coupe

    def _load_sessions(self):
        """Recharge les sessions non expirées après un redémarrage."""
        try:
            with open(self._sessions_path) as f:
                d = json.load(f)
            now = time.time()
            self._sessions = {str(k): float(v) for k, v in d.items() if float(v) > now}
        except Exception:  # noqa: BLE001 — fichier absent/corrompu/JSON non-dict : des
            # sessions perdues se rouvrent d'un /2fa unlock, un boot qui crashe non
            self._sessions = {}

    def _save_sessions(self):
        """Persiste les sessions (best-effort : un échec ne casse pas le déverrouillage —
        au pire la session ne survivra pas à un restart, comportement d'avant)."""
        try:
            tmp = self._sessions_path + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(self._sessions, f)
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
        """Valide le 1er code -> persiste le secret + rend les codes de secours."""
        uid = str(uid)
        secret = self._pending.get(uid)
        if not secret:
            return None
        if not pyotp.TOTP(secret).verify(code, valid_window=1):
            return None
        backup = [secrets.token_hex(4) for _ in range(BACKUP_COUNT)]
        self._data["users"][uid] = {
            "secret": secret,
            "backup": [_hash(b) for b in backup],
            "enrolled_at": int(time.time()),
        }
        self._save()
        self._pending.pop(uid, None)
        self.open_session(uid)
        return backup           # rendus EN CLAIR une seule fois, jamais restituables

    def revoke(self, uid):
        uid = str(uid)
        existed = self._data["users"].pop(uid, None) is not None
        if existed:
            self._save()
        if self._sessions.pop(uid, None) is not None:
            self._save_sessions()
        self._pending.pop(uid, None)
        if existed:
            self._fire(uid)     # plus inscrit -> ses rôles 2FA doivent tomber tout de suite
        return existed

    # ------------------------------------------------------------------ vérification
    def verify(self, uid, code):
        """Accepte un code TOTP ou un code de secours (à usage unique)."""
        uid = str(uid)
        u = self._data["users"].get(uid)
        if not u:
            return False
        code = (code or "").strip().replace(" ", "")

        if code.isdigit() and len(code) == 6:
            if self._last_code.get(uid) == code:
                return False        # anti-rejeu : ce code a déjà servi
            if pyotp.TOTP(u["secret"]).verify(code, valid_window=1):
                self._last_code[uid] = code
                self.open_session(uid)
                return True
            return False

        h = _hash(code)
        if h in u.get("backup", []):
            u["backup"].remove(h)   # usage unique
            self._save()
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
