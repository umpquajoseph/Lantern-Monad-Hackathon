"""Figures 1a and 1b from a batch folder (summary.csv, by_sortie.csv, drills.csv).

    python -m sim.figures data/campaigns/<batch_id>

1a: share of sorties home with the reserve, and 95th-percentile drill-site
    error, against crater density, one line per check policy. Two panels on
    one x axis (never two y scales on one panel).
1b: wrong-match rate and checks per sortie against sortie number, with and
    without crater-map refinement, one column per check policy.
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# Categorical slots 1-4 in fixed order (dataviz reference palette, validated
# light: CVD worst adjacent dE 9.1; aqua and yellow under 3:1 contrast, so
# every line carries a direct label and its own marker).
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
MARKERS = ["o", "s", "^", "D"]
INK, INK2, GRID = "#1f1f1d", "#5f5e58", "#e4e3de"
POLICY_ORDER = ["fixed", "p95", "before_work", "fixed_time", "filter"]
MIN_DRILLS = 20     # a density bin with fewer pooled drills is drawn hollow


def _read(path):
    if not path.exists() or path.stat().st_size == 0:
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _style(ax):
    ax.grid(True, color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color(INK2)
    ax.tick_params(colors=INK2, labelsize=9)


def _label_end(ax, x, y, text, color):
    ok = np.isfinite(y)
    if ok.any():
        i = np.flatnonzero(ok)[-1]
        ax.annotate(text, (x[i], y[i]), xytext=(6, 0), textcoords="offset points", va="center",
                    fontsize=8.5, color=INK)


def fig1a(bdir: Path, runs, drills):
    pols = [p for p in POLICY_ORDER if any(r["check_policy"] == p for r in runs)]
    sel = [r for r in runs if int(r["n_scouts"]) >= 2 and r["map_refinement"] == "True"] or runs
    dens = np.array([float(r["crater_n_ge10_per_km2"]) for r in sel])
    edges = np.logspace(2, 4, 9)
    mids = np.sqrt(edges[:-1] * edges[1:])
    by_run = {r["run_id"]: r for r in sel}
    lim = float(sel[0]["drill_limit_m"]) if sel else 10.0
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(7.2, 6.4), sharex=True, constrained_layout=True)
    for k, pol in enumerate(pols):
        rs = [r for r in sel if r["check_policy"] == pol]
        ok = np.full(len(mids), np.nan)
        err = np.full(len(mids), np.nan)
        nd = np.zeros(len(mids), int)
        for b in range(len(mids)):
            inb = [r for r in rs if edges[b] <= float(r["crater_n_ge10_per_km2"]) < edges[b + 1]]
            if inb:
                ok[b] = sum(int(r["sorties_ok"]) for r in inb) / sum(int(r["sorties"]) for r in inb)
            ids = {r["run_id"] for r in inb}
            e = [float(d["true_err_m"]) for d in drills if d["run_id"] in ids]
            nd[b] = len(e)
            if e:
                err[b] = np.percentile(e, 95)
        c, m = SERIES[k % 4], MARKERS[k % 4]
        a1.plot(mids, ok, color=c, lw=2, marker=m, ms=7, mec="white", mew=1.2, label=pol)
        a2.plot(mids, err, color=c, lw=2, label=pol)
        full = nd >= MIN_DRILLS
        a2.plot(mids[full], err[full], ls="none", marker=m, ms=7, color=c, mec="white", mew=1.2)
        a2.plot(mids[~full], err[~full], ls="none", marker=m, ms=7, mfc="white", mec=c, mew=1.5)
        _label_end(a1, mids, ok, pol, c)
        _label_end(a2, mids, err, pol, c)
    a2.axhline(lim, color=INK2, lw=1, ls=(0, (4, 3)))
    a2.annotate(f"drill-site limit {lim:g} m", (edges[0], lim), xytext=(2, 4), textcoords="offset points",
                fontsize=8.5, color=INK2)
    for ax in (a1, a2):
        _style(ax)
        ax.set_xscale("log")
        ax.set_xlim(edges[0], edges[-1] * 1.6)
    a1.set_ylabel("Share of sorties home with the 25% reserve", color=INK, fontsize=9.5)
    a1.set_ylim(min(0.0, np.nanmin([a1.get_ylim()[0]])), 1.02)
    a2.set_ylabel("95th-percentile drill-site error (m)", color=INK, fontsize=9.5)
    a2.set_xlabel("Craters ≥ 10 m per km² (world draw, log bins)", color=INK, fontsize=9.5)
    a2.set_yscale("log")
    a1.legend(frameon=False, fontsize=8.5, loc="lower right", title="check policy", title_fontsize=8.5)
    fig.suptitle(f"Figure 1a. {len(sel)} runs, fleets of 2+ with refinement; hollow marker = under "
                 f"{MIN_DRILLS} drills in the bin", fontsize=9.5, color=INK2)
    out = bdir / "fig1a.png"
    fig.savefig(out, dpi=150, facecolor="white")
    plt.close(fig)
    return out


def fig1b(bdir: Path, runs, bys):
    meta = {r["run_id"]: r for r in runs}
    pols = [p for p in POLICY_ORDER if any(r["check_policy"] == p for r in runs)]
    refs = [v for v in ("True", "False") if any(r["map_refinement"] == v for r in runs)]
    fig, axes = plt.subplots(2, len(pols), figsize=(3.4 * len(pols) + 0.6, 6.0), sharex=True, sharey="row",
                             squeeze=False, constrained_layout=True)
    for j, pol in enumerate(pols):
        for k, ref in enumerate(refs):
            agg = defaultdict(lambda: [0, 0, 0, 0])
            for b in bys:
                m = meta.get(b["run_id"])
                if not m or m["check_policy"] != pol or m["map_refinement"] != ref:
                    continue
                a = agg[int(b["sortie_n"])]
                a[0] += int(b["checks"]); a[1] += int(b["wrong_matches"])
                a[2] += int(b["fixes"]); a[3] += int(b["sorties"])
            if not agg:
                continue
            n = np.array(sorted(agg))
            wr = np.array([agg[i][1] / agg[i][2] if agg[i][2] else np.nan for i in n])
            cps = np.array([agg[i][0] / agg[i][3] for i in n])
            c, mk = SERIES[k], MARKERS[k]
            lab = "refinement on" if ref == "True" else "refinement off"
            for ax, y in ((axes[0, j], wr), (axes[1, j], cps)):
                ax.plot(n, y, color=c, lw=2, marker=mk, ms=6, mec="white", mew=1.0, label=lab)
                _label_end(ax, n, y, lab.split()[1], c)
        axes[0, j].set_title(pol, fontsize=10, color=INK)
        axes[1, j].set_xlabel("Sortie number (per Scout)", color=INK, fontsize=9.5)
        for i in range(2):
            _style(axes[i, j])
            axes[i, j].set_xlim(0.5, axes[i, j].get_xlim()[1] + 2)
    axes[0, 0].set_ylabel("Wrong-match rate (wrong / fixes)", color=INK, fontsize=9.5)
    axes[1, 0].set_ylabel("Checks per sortie", color=INK, fontsize=9.5)
    axes[0, 0].legend(frameon=False, fontsize=8.5, loc="upper right")
    for ax in axes[0]:
        ax.set_ylim(bottom=0)
    for ax in axes[1]:
        ax.set_ylim(bottom=0)
    nr = len({b["run_id"] for b in bys})
    fig.suptitle(f"Figure 1b. {nr} runs, all fleet sizes pooled", fontsize=9.5, color=INK2)
    out = bdir / "fig1b.png"
    fig.savefig(out, dpi=150, facecolor="white")
    plt.close(fig)
    return out


def make(bdir) -> list[Path]:
    bdir = Path(bdir)
    runs = _read(bdir / "summary.csv")
    bys = _read(bdir / "by_sortie.csv")
    drills = _read(bdir / "drills.csv")
    if not runs:
        return []
    return [fig1a(bdir, runs, drills), fig1b(bdir, runs, bys)]


if __name__ == "__main__":
    for p in make(sys.argv[1]):
        print(p)
