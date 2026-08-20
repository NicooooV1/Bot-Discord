"""Rendu texte du cog YouTube : barre de progression, tailles, durées, délais.

Module FRÈRE de `cogs/youtube.py` (2026-08-11). On n'y trouve que des fonctions PURES
(données -> chaîne), sans Discord ni I/O : `_track` et `_flow_playlist` mêlaient le suivi
réseau et la mise en forme, ce qui rendait les deux illisibles. La dépendance est à sens
unique — rien ici n'importe le cog.

⚠️ Ces formats NE SONT PAS ceux de `core.format`, et c'est délibéré :
  - `fmt_bytes` rend « 512 Mio » (entier) et « 1.4 Gio » (une décimale) là où
    `fmt.humanize_bytes` rendrait « 512.0 Mio » et « 900 o ». Cette chaîne est LUE PAR UN
    HUMAIN avant de confirmer plusieurs Gio de téléchargement (règle « annoncer la taille
    avant tout téléchargement ») : on garde le format éprouvé ;
  - `bar` reçoit le pourcentage TEXTUEL de yt-dlp (« 42.3% ») et non un flottant, et fait
    20 caractères de large avec le pourcentage collé, quand `fmt.pct_bar` en fait 12 sans
    le pourcentage.
Les aligner sur core.format changerait ce que voit l'utilisateur : hors périmètre d'une
campagne de simplification.
"""


def fmt_delay(hours):
    """Formatte un délai en heures -> « 1 heure » / « 6 heures » / « 2 jours » / « jamais »."""
    if hours is None:
        return "réglage global"
    if hours < 0:
        return "jamais (garder)"
    if hours < 24:
        return f"{hours} heure" + ("s" if hours > 1 else "")
    d = hours / 24
    return f"{int(d)} jour" + ("s" if d > 1 else "") if hours % 24 == 0 else f"{hours} h"


def bar(pct_str):
    """Barre de 20 blocs à partir du pourcentage TEXTUEL de yt-dlp (« 42.3% »).

    2026-08-20 : un pourcentage illisible n'est PAS 0 % — « 0% » affichait une mesure
    là où on n'en avait aucune. Barre vide + « ?% » quand le parse échoue.
    """
    try:
        p = float((pct_str or "").replace("%", "").strip())
    except ValueError:
        return "░" * 20 + " ?%"
    n = max(0, min(20, round(p / 5)))
    return "█" * n + "░" * (20 - n) + f" {p:.0f}%"


def fmt_bytes(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} Gio"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.0f} Mio"
    return f"{n / 1024:.0f} Kio"


def fmt_dur(s):
    try:
        s = int(s)
    except (TypeError, ValueError):
        return "?"
    h, m = s // 3600, (s % 3600) // 60
    if h:
        return f"{h} h {m:02d}"
    return f"{m} min" if m else f"{s} s"


def progress_text(job, files):
    """Corps du champ « Progression » d'un job en cours (queued / running / paused).

    `job` est la réponse ytgrab telle quelle ; `files` la liste des fichiers DÉJÀ livrés
    (une playlist en cours en a déjà terminé une partie, on le dit).
    """
    # pas de repli « 0% » : un champ absent doit rendre « ?% », pas un faux zéro mesuré
    val = bar(job.get("percent"))
    if job.get("pl_count"):
        val += f"\n📃 Élément **{job.get('pl_idx', '?')}/{job['pl_count']}**" \
               + (f" · {len(files)} terminé(s)" if files else "")
    if job.get("state") == "paused":
        # qui a mis en pause : tout arrêt/pause est ATTRIBUÉ (spec Nico 2026-07-15)
        return val + f"\n⏸️ **En pause** — mis en pause par **{job.get('paused_by', '?')}**"
    extra = []
    if job.get("speed"):
        extra.append(job["speed"])
    if job.get("eta") and job.get("eta") != "Unknown":
        # « Unknown » est la chaîne littérale de yt-dlp : l'afficher telle quelle
        # donnerait « ETA Unknown » dans l'embed.
        extra.append(f"ETA {job['eta']}")
    if job.get("size"):
        extra.append(job["size"])
    if extra:
        val += "\n" + " · ".join(extra)
    if job.get("stage"):
        val += f"\n_{job['stage']}_"
    return val


def size_estimate_text(probe):
    """« ≈ 3.4 Gio (étalonné sur 5 élément(s) réel(s)) » — ou l'aveu d'ignorance.

    Une taille inconnue est annoncée EXPLICITEMENT : la règle « annoncer la taille avant
    tout téléchargement » n'est pas honorée par un silence.
    """
    if not probe.get("size_est"):
        return "❓ inconnue — impossible d'estimer, prudence avant de confirmer"
    txt = f"≈ {fmt_bytes(probe['size_est'])}"
    if probe.get("sampled"):
        return txt + f" (étalonné sur {probe['sampled']} élément(s) réel(s))"
    return txt + " (estimation grossière)"


def entries_preview(probe, count, limit=10):
    """Aperçu « • titre (durée) » des `limit` premiers éléments d'une playlist.

    Chaîne vide s'il n'y a rien à montrer (l'appelant n'ajoute alors pas le champ).
    Tronquée à 1024 caractères : c'est la limite d'un champ d'embed Discord, la dépasser
    fait échouer l'édition entière (donc plus d'annonce de taille du tout).
    """
    lines = []
    for e in (probe.get("entries") or [])[:limit]:
        d = f" ({fmt_dur(e['duration'])})" if e.get("duration") else ""
        lines.append(f"• {str(e.get('title') or '?')[:70]}{d}")
    if count > limit:
        lines.append(f"… et {count - limit} autre(s)")
    return "\n".join(lines)[:1024]
