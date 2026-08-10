"""Matplotlib (Agg) graph engine -> discord.File PNG. Dark theme blends with Discord.

Rules: Agg backend; the object-oriented Figure API (NOT pyplot) so renders are safe
from the concurrent asyncio.to_thread worker threads the callers use — pyplot's
global state is not thread-safe. Figures are created standalone (Figure()) and never
registered with pyplot, so they need no explicit close (GC reclaims them). The dark
theme is applied by hand on each axes since we no longer rely on a global rc style.
"""
import io

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
import discord  # noqa: E402

BG = "#2b2d31"
FG = "#dbdee1"
GRID = "#404249"
SERIES = ["#5865F2", "#57F287", "#FEE75C", "#EB459E", "#ED4245", "#00A8FC"]


def _new_fig(title, ylabel):
    fig = Figure(figsize=(8, 3.2), dpi=130)
    ax = fig.subplots()
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.tick_params(colors=FG)
    ax.yaxis.label.set_color(FG)
    ax.title.set_color(FG)
    ax.grid(True, color=GRID, linewidth=0.6, alpha=0.7)
    ax.set_title(title, fontsize=11, fontweight="bold")
    ax.set_ylabel(ylabel, fontsize=9)
    return fig, ax


def _file(fig, filename):
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", facecolor=BG)
    buf.seek(0)
    return discord.File(buf, filename=filename)


def timeseries(title, ylabel, series, filename="graph.png", pct=False):
    """series = {label: (times, values)}. Returns discord.File (or None if all empty)."""
    # drop None points so matplotlib never sees a non-finite value
    clean = {}
    for label, (ts, vals) in series.items():
        pts = [(t, v) for t, v in zip(ts or [], vals or []) if v is not None]
        if pts:
            clean[label] = ([t for t, _ in pts], [v for _, v in pts])
    if not clean:
        return None
    fig, ax = _new_fig(title, ylabel)
    multi = len(clean) > 1
    for i, (label, (ts, vals)) in enumerate(clean.items()):
        c = SERIES[i % len(SERIES)]
        ax.plot(ts, vals, color=c, linewidth=1.6, label=label)
        ax.fill_between(ts, vals, color=c, alpha=0.12)
    if pct:
        ax.set_ylim(0, 100)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    fig.autofmt_xdate(rotation=0, ha="center")
    if multi:
        ax.legend(facecolor=BG, edgecolor=GRID, labelcolor=FG, fontsize=8, loc="upper left")
    return _file(fig, filename)


def barchart(title, ylabel, labels, values, warn=None, crit=None, filename="bar.png"):
    if not values:
        return None
    fig, ax = _new_fig(title, ylabel)
    colors = []
    for v in values:
        if crit is not None and v is not None and v >= crit:
            colors.append("#ED4245")
        elif warn is not None and v is not None and v >= warn:
            colors.append("#FEE75C")
        else:
            colors.append("#57F287")
    ax.bar(range(len(values)), [v or 0 for v in values], color=colors)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=35, ha="right", fontsize=8)
    if warn is not None:
        ax.axhline(warn, color="#FEE75C", linewidth=0.8, linestyle="--", alpha=0.6)
    if crit is not None:
        ax.axhline(crit, color="#ED4245", linewidth=0.8, linestyle="--", alpha=0.6)
    return _file(fig, filename)
