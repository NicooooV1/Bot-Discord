"""Humanize helpers, embed colors and small formatting utilities."""

# Discord palette (blends with the dark client theme)
BLURPLE = 0x5865F2
GREEN = 0x57F287
YELLOW = 0xFEE75C
RED = 0xED4245
GREY = 0x4F545C
ORANGE = 0xE67E22


def humanize_bytes(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "—"
    for unit in ("o", "Kio", "Mio", "Gio", "Tio", "Pio"):
        if abs(n) < 1024.0:
            return f"{n:.0f} {unit}" if unit == "o" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} Eio"


def humanize_rate(bytes_per_s):
    return humanize_bytes(bytes_per_s) + "/s"


def humanize_duration(seconds):
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return "—"
    if s < 0:
        return "—"
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, _ = divmod(s, 60)
    if d:
        return f"{d}j {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def pct_bar(pct, width=12):
    try:
        p = max(0.0, min(100.0, float(pct)))
    except (TypeError, ValueError):
        return "—"
    filled = int(round(p / 100.0 * width))
    return "█" * filled + "░" * (width - filled)


def pct_of(used, total, decimals=0):
    """'62 % · 1.2 Gio / 2.0 Gio' — pourcentage AVEC les valeurs absolues (octets).
    Sert à rendre chaque % lisible (« 60 % de RAM » = combien exactement)."""
    try:
        u = float(used)
        t = float(total)
    except (TypeError, ValueError):
        return "—"
    p = (u / t * 100.0) if t else 0.0
    return f"{p:.{decimals}f} % · {humanize_bytes(u)} / {humanize_bytes(t)}"


def pct_of_num(used, total, unit="", decimals=0):
    """Comme pct_of mais pour des quantités NON-octets (ex. '45 % · 9 / 20 …')."""
    try:
        u = float(used)
        t = float(total)
    except (TypeError, ValueError):
        return "—"
    p = (u / t * 100.0) if t else 0.0
    suf = f" {unit}" if unit else ""
    return f"{p:.{decimals}f} % · {u:.{decimals}f} / {t:.{decimals}f}{suf}"


def status_emoji(running):
    return "🟢" if running else "🔴"


# emojis de statut préfixant les noms de salons « {emoji}-{nom} » (invités et nœud)
STATUS_EMOJI = ("🟢", "🟠", "🔴")


def strip_status_emoji(nm):
    """Retire l'emoji de statut en tête d'un nom de salon pour retrouver le nom de base."""
    for e in STATUS_EMOJI:
        if nm.startswith(e):
            return nm[len(e):].lstrip("-_ ")
    return nm


def level_emoji(level):
    return {"crit": "🔥", "warn": "⚠️", "ok": "✅", None: "✅"}.get(level, "•")


def health_color(pct, warn=80, crit=90):
    try:
        p = float(pct)
    except (TypeError, ValueError):
        return GREY
    if p >= crit:
        return RED
    if p >= warn:
        return YELLOW
    return GREEN


def slug(name):
    """Nom de guest PVE -> nom de salon Discord valide (minuscules, sans espace).

    Produit exactement ce que Discord stocke (déjà normalisé) -> la comparaison
    `ch.name == f"{emoji}-{slug(name)}"` est STABLE et n'entraîne pas de boucle de
    renommage (Discord limite à 2 renommages / 10 min / salon)."""
    s = "".join(c if (c.isalnum() or c in "-_") else "-" for c in str(name).strip().lower())
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-")[:90] or "guest"


def outcome_text(outcome, done_label="terminé"):
    """Rend le verdict d'un suivi de tâche PVE (`_poll`).

    Partagé par cogs/actions.py et cogs/ct_channels.py (2026-08-11) : les deux avaient
    leur propre rendu, et celui de ct_channels affichait « ⚠️ lost » — illisible.

    « lost » n'est PAS un échec : c'est NOTRE suivi qui s'est interrompu, la tâche
    continue côté PVE. L'annoncer comme un échec ferait croire à une sauvegarde ratée.
    """
    if outcome == "OK":
        return f"✅ {done_label}"
    if outcome == "running":
        return "⏳ encore en cours"
    if outcome == "lost":
        return ("⚠️ suivi interrompu (API PVE injoignable) — **la tâche continue côté "
                "Proxmox**, son sort réel est dans `/tasks`")
    return f"⚠️ {outcome}"
