"""Analyse et rendu des durées saisies par l'utilisateur (2026-08-14).

Un SEUL analyseur pour les deux endroits où Nico choisit une durée à la volée : la modale
d'inactivité du terminal (`cogs/terminal.py`) et la session de confiance 2FA
(`cogs/twofa.py`). Avant, seul le terminal savait lire « 1h30 », et uniquement en
minutes/heures bornées — d'où la duplication qui aurait suivi l'ajout du 2FA.

CONVENTION CENTRALE : **0 minute = illimité**. Elle vient de la conf existante
(`NODE_TERMINAL_MAX_MIN` documenté « 0 = illimité ») et est reprise partout, y compris
pour les PLAFONDS : un plafond à 0 veut dire « pas de plafond », donc « illimité
autorisé ». Conséquence à ne pas perdre de vue côté appelant : `if minutes:` distingue
correctement borné/illimité, mais `x or defaut` écrase un 0 volontaire — d'où
`None` (= « prends le défaut ») partout où l'absence de choix doit rester distincte.
"""
import re

UNLIMITED = 0

# « 45 », « 45m », « 45 min », « 2h », « 1h30 », « 1 h 30 »
_DUREE = re.compile(
    r"^(?:(?P<h>\d{1,3})\s*h\s*(?P<hm>\d{1,2})?|(?P<m>\d{1,5})\s*(?:m(?:in(?:utes?)?)?)?)$",
    re.I)

# Mots qui veulent dire « pas de limite ». Volontairement larges : la modale est tapée au
# pouce sur téléphone, et un refus renverrait l'utilisateur ouvrir une session bornée
# alors qu'il en voulait une infinie. Les accents sont retirés avant comparaison.
_INFINI = {"illimite", "illimitee", "indefini", "indefinie", "infini", "infinie",
           "unlimited", "indefinite", "never", "jamais", "aucune", "aucun", "sans",
           "toujours", "inf", "infini(e)", "-", "--", "0", "oo", "00"}


def _deaccent(s: str) -> str:
    return (s.replace("é", "e").replace("è", "e").replace("ê", "e")
             .replace("î", "i").replace("ï", "i").replace("û", "u").replace("à", "a"))


def parse_duration(raw: str) -> int:
    """« 45 », « 45 min », « 2h », « 1h30 », « illimité », « ∞ » -> minutes (0 = illimité).

    Lève ValueError si la saisie n'est pas reconnue. Les BORNES ne sont pas appliquées
    ici : l'appelant (terminal guest, terminal nœud, 2FA) les connaît et doit pouvoir
    citer la valeur refusée dans son message d'erreur.
    """
    s = _deaccent((raw or "").strip().lower())
    if s in {"∞", "♾", "♾️"} or s in _INFINI:
        return UNLIMITED
    m = _DUREE.match(s)
    if not m:
        raise ValueError(raw)
    if m.group("h") is not None:
        return int(m.group("h")) * 60 + int(m.group("hm") or 0)
    return int(m.group("m"))


def fmt_duration(minutes, *, unlimited="illimité") -> str:
    """Rendu FR d'une durée en minutes : « illimité », « 45 min », « 2 h », « 1 h 30 »."""
    m = int(minutes or 0)
    if m <= 0:
        return unlimited
    if m < 60:
        return f"{m} min"
    h, r = divmod(m, 60)
    return f"{h} h" if not r else f"{h} h {r:02d}"


def clamp_duration(value, default, maximum):
    """Applique « vide -> défaut » puis le plafond. Renvoie des minutes (0 = illimité).

    - `value is None` -> `default` (l'absence de choix n'est PAS un choix d'illimité) ;
    - `maximum` à 0 = pas de plafond : l'illimité et n'importe quelle valeur passent ;
    - sinon un `value` illimité (0) ou trop grand est ramené AU PLAFOND — jamais au
      défaut : rendre 10 min à qui a demandé l'infini serait le contraire de l'intention.
    """
    if value is None:
        value = default
    value = max(0, int(value))
    if not maximum:                      # plafond absent = tout est permis
        return value
    if value == UNLIMITED or value > maximum:
        return maximum
    return value


def bounds_hint(default, maximum) -> str:
    """Texte d'aide de la modale : ce qui est accepté, et jusqu'où."""
    haut = "illimité" if not maximum else fmt_duration(maximum)
    return (f"vide = {fmt_duration(default)} · ex. 45, 2h, 1h30"
            + (" ou « illimité »" if not maximum else f" · max {haut}"))
