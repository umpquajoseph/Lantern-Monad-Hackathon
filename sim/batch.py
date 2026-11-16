"""Run a batch of campaigns on all cores, then the hand checks and Figures 1a, 1b.

    python -m sim.batch --batch-id small50 --worlds 50 \\
        --sweep n_scouts=1,2,3,4,5 --sweep check_policy=fixed,p95,before_work

Every design choice runs on the same worlds (seeds seed0 .. seed0+worlds-1).
Writes to data/campaigns/<batch_id>/:

- <run_id>/record.json, <run_id>/poses.npz   one per run (sim/campaign.py)
- summary.csv      one row per run (docs/handoffs.md "Summary row")
- by_sortie.csv    one row per run and sortie number (Figure 1b)
- drills.csv       one row per drill (Figure 1a pools drill-site error by density bin)
- report.md        hand checks against the draft, timings, what is a stand-in
- fig1a.png, fig1b.png
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import os
import time
from dataclasses import asdict, fields
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from sim import campaign as C
from sim.settings import Settings, WorldRanges


def _types():
    return {f.name: f.type for f in fields(Settings)}


def parse_sweeps(items):
    types = _types()
    axes = []
    for it in items or []:
        k, vs = it.split("=", 1)
        if k not in types:
            raise SystemExit(f"unknown setting {k}")
        axes.append((k, [C.parse_value(types[k], v) for v in vs.split(",")]))
    return axes


def _row_path(out_root, batch_id, seed, overrides):
    return Path(out_root) / batch_id / f"w{seed:05d}-{Settings(**overrides).key()}" / "row.json"


def _run_one(job):
    """One run. Writes record.json, poses.npz (unless --no-records) and
    row.json (its summary row, by-sortie rows and drills), so a batch that is
    stopped keeps every finished run and a re-run skips them."""
    seed, overrides, batch_id, out_root, keep, world_kw = job
    s = Settings(**overrides)
    t0 = time.perf_counter()
    c = C.Campaign(seed, s, WorldRanges(**world_kw)).run()
    t_sim = time.perf_counter() - t0
    if keep:
        c.save(batch_id, out_root)
    drills = [{"run_id": c.run_id, "sortie_id": e["sortie_id"], "true_err_m": e["true_err_m"],
               "miss_m": e["miss_m"], "hit": e["hit"]} for e in c.events if e["type"] == "drill"]
    out = {"summary": c.summary(), "by_sortie": c.by_sortie(), "drills": drills, "t_sim": t_sim}
    rp = _row_path(out_root, batch_id, seed, overrides)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(out, default=str), encoding="utf-8")
    return t_sim


def write_csv(path, rows):
    rows = list(rows)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = list(rows[0].keys())
    for r in rows[1:]:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in keys})


# =================================================================== hand checks

def hand_checks(s: Settings, seed: int, out_root, batch_id) -> tuple[str, dict]:
    """Spec 4a item 2: the draft's own arithmetic, computed by the engine's own
    functions, then a one-Scout run with no faults and no refinement."""
    lines = ["## Hand checks (spec 4a item 2)", ""]
    # --- Table 1 (draft 4.2): spacing = (limit - e_fix95) / 0.02.
    lines += ["### Table 1: fix spacing, e(d) = e_fix + 0.02 d", "",
              "| Row | e_fix95 engine | draft | 5 m limit engine | draft | 10 m limit engine | draft |",
              "|---|---|---|---|---|---|---|"]
    rows = [("3 (best)", s.fix_err_good[1], 2.8, "108", "358"),
            ("2", s.fix_err_good[0], 3.0, "98", "348"),
            ("1 (false matches)", s.fix_err_wrong_match, 8.4, "not achievable", "80")]
    out = {}
    for name, row, d_e, d5, d10 in rows:
        e95 = C.e95_rows([row], s.fix_z95)[0]
        sp = {lim: (lim - e95) / 0.02 for lim in (5.0, 10.0)}
        f = lambda v: f"{v:.1f} m" if v > 0 else "not achievable"
        lines.append(f"| {name} | {e95:.3f} m | {d_e} m | {f(sp[5.0])} | {d5} | {f(sp[10.0])} | {d10} |")
        out[name] = sp
    e_fm = C.e95_rows([s.fix_err_wrong_match], s.fix_z95)[0]
    lines += ["",
              f"Row 1 at 10 m gives {(10 - e_fm) / 0.02:.1f} m from the unrounded {e_fm:.3f} m; the draft's 80 m "
              f"comes from the rounded 8.4 m ((10 - 8.4) / 0.02 = 80). Within rounding of e_fix, not of the spacing.",
              f"Fixes per 7 km sortie: 7000 / {(10 - e_fm) / 0.02:.1f} = {7000 / ((10 - e_fm) / 0.02):.1f} "
              f"(draft 'about 88'); 7000 / {out['3 (best)'][10.0]:.1f} = {7000 / out['3 (best)'][10.0]:.1f} "
              f"(draft 'about 20'). 88 fixes x 5 min = {88 * 300 / 3600:.2f} h (draft 7.3 h).",
              f"The engine's 'fixed' policy uses {C.fixed_spacing(s):.1f} m at drill_limit_m = {s.drill_limit_m:g} "
              f"(fixed_row = {s.fixed_row}).", ""]
    # --- Stop budget (draft 4.1).
    dist = 2 * s.descent_m + s.survey_m
    lines += ["### Stop budget (draft 4.1)", "",
              f"Sortie {dist / 1000:g} km in {s.sortie_h:g} h (brief p.6).", "",
              "| Top speed | moving engine | draft | budget engine | draft |", "|---|---|---|---|---|"]
    for v, dm, db in ((0.10, 19.4, 2.6), (0.15, 13.0, 9.0), (0.20, 9.7, 12.3)):
        mv = dist / v / 3600
        lines.append(f"| {v * 100:.0f} cm/s | {mv:.2f} h | {dm} h | {s.sortie_h - mv:.2f} h | {db} h |")
    lines += ["", f"Average speed {dist / (s.sortie_h * 3600) * 100:.2f} cm/s (draft 8.8 cm/s).", ""]
    # --- Loads (draft 4.6).
    dock_w = s.dock_power_kw * s.dock_charge_h / s.sortie_h * 1000
    batt_w = s.battery_kwh * (1 - s.reserve_frac) / s.sortie_h * 1000
    lines += ["### Average load (draft 4.6, item 3)", "",
              f"- Dock: {s.dock_power_kw:g} kW x {s.dock_charge_h:g} h / {s.sortie_h:g} h = {dock_w:.1f} W (draft 205 W).",
              f"- Battery: {s.battery_kwh:g} kWh x {1 - s.reserve_frac:g} / {s.sortie_h:g} h = {batt_w:.1f} W "
              f"(draft 270 W; 269.3 rounds to 269, so the draft rounds to the nearest 10 W).", ""]
    # --- A one-Scout run, no faults, no refinement.
    s1 = s.with_(n_scouts=1, p_fail_camera=0.0, p_fail_lamp=0.0, p_fail_neutron=0.0, map_refinement=False,
                 check_policy="fixed")
    c = C.Campaign(seed, s1, WorldRanges(drift_frac=(0.02, 0.02))).run()
    c.save(batch_id, out_root)
    sr = c.sorties
    km = np.mean([r["distance_m"] for r in sr]) / 1000
    dur = np.mean([(r["t_end"] - r["t_start"]) for r in sr]) / 3600
    drive = np.mean([r["time_s"]["drive"] for r in sr]) / 3600
    chk = np.mean([r["checks"] for r in sr])
    used = np.mean([s1.battery_kwh - r["energy_end_kwh"] for r in sr])
    avg_w = used / dur * 1000
    pre = [e["true_err_before_m"] for e in c.events if e["type"] == "check" and e["fixed"]]
    sp = C.fixed_spacing(s1)
    fixes = np.mean([r["fixes"] for r in sr])
    dens = c.world.draws["crater_n_ge10_per_km2"]
    lines += [f"### One-Scout run ({c.run_id}: 1 Scout, no faults, no refinement, 'fixed' policy, drift 2%, "
              f"{dens:.0f} craters >= 10 m per km^2)", "",
              "| Quantity | Run | By hand | Draft |", "|---|---|---|---|",
              f"| Distance per sortie | {km:.2f} km | | 7 km |",
              f"| Moving time per sortie | {drive:.2f} h | {km * 1000 / s1.top_speed_mps / 3600:.2f} h at "
              f"{s1.top_speed_mps * 100:g} cm/s | 13.0 h for 7 km |",
              f"| Sortie duration | {dur:.2f} h | | 22 h |",
              f"| Checks per sortie | {chk:.1f} | {km * 1000 / sp:.1f} (distance / {sp:.1f} m) | 88 per 7 km |",
              f"| Check stops per sortie | {chk * s1.check_stop_s / 3600:.2f} h | | 7.3 h for 88 |",
              f"| Average load | {avg_w:.1f} W | | 205 W |",
              f"| True error just before a fix, 95th pct | {np.percentile(pre, 95):.2f} m | <= {s1.drill_limit_m:g} m "
              f"(Table 1 design) | 10 m limit |" if pre else "| no fixes | | | |",
              f"| Wrong-match rate | {sum(r['wrong_matches'] for r in sr) / max(sum(r['fixes'] for r in sr), 1):.3f} | | |",
              ""]
    n_drill = sum(1 for e in c.events if e["type"] == "drill")
    lines += [f"- Distance: the plan is {dist / 1000:g} km; the run adds {km - dist / 1000:.2f} km of transit "
              f"between the descent end and the lanes.",
              f"- Checks: a Scout stops to check only where its own map shows {s1.fix_min_craters}+ craters within "
              f"{s1.fix_range_m:g} m, so a due check waits for such a spot. Mean distance between fixes "
              f"{km * 1000 / max(fixes, 1e-9):.1f} m against the {sp:.1f} m schedule.",
              f"- Load: draws are scaled so the {dist / 1000:g} km baseline with {s1.drill_sites_max} drills of "
              f"{s1.drill_h:g} h averages {s1.avg_load_w:g} W (energy scale {c.energy['scale']:.3f}). "
              f"This run drilled {n_drill} times; one Scout cannot confirm a find, so it never drills.", ""]
    return "\n".join(lines), {"one_scout_run": c.run_id, "one_scout_wall_s": c.wall_s}


# =================================================================== main

def main():
    ap = argparse.ArgumentParser(description="Run a batch of campaigns.")
    ap.add_argument("--batch-id", required=True)
    ap.add_argument("--worlds", type=int, default=10)
    ap.add_argument("--seed0", type=int, default=1)
    ap.add_argument("--sweep", action="append", default=[], help="name=v1,v2,...  (repeatable)")
    ap.add_argument("--set", nargs="*", default=[], help="fixed overrides name=value for every run")
    ap.add_argument("--world-set", nargs="*", default=[],
                    help="WorldRanges overrides for every run, name=value or name=lo:hi. Runs are named by "
                         "seed and Settings only, so give each world variant its own --batch-id")
    ap.add_argument("--jobs", type=int, default=0, help="worker processes (0 = half the cores; each "
                                                       "holds one world in memory)")
    ap.add_argument("--no-records", action="store_true", help="skip per-run record.json / poses.npz")
    ap.add_argument("--out", default=None, help="root folder (default data/campaigns)")
    a = ap.parse_args()

    base = C.settings_from(a.set)
    world_kw = {}
    for it in a.world_set:
        k, v = it.split("=", 1)
        vals = tuple(float(x) for x in v.split(":"))
        world_kw[k] = vals if len(vals) > 1 else vals[0]
        if isinstance(getattr(WorldRanges(), k), tuple) and len(vals) == 1:
            world_kw[k] = (vals[0], vals[0])
    axes = parse_sweeps(a.sweep)
    out_root = Path(a.out) if a.out else C.CAMPAIGN_DIR
    bdir = out_root / a.batch_id
    bdir.mkdir(parents=True, exist_ok=True)
    combos = list(itertools.product(*[vs for _, vs in axes])) if axes else [()]
    jobs = []
    base_kw = asdict(base)
    for seed in range(a.seed0, a.seed0 + a.worlds):
        for combo in combos:
            kw = dict(base_kw)
            kw.update({k: v for (k, _), v in zip(axes, combo)})
            kw = {k: (tuple(map(tuple, v)) if isinstance(v, list) and v and isinstance(v[0], list)
                      else tuple(v) if isinstance(v, list) else v) for k, v in kw.items()}
            jobs.append((seed, kw, a.batch_id, out_root, not a.no_records, world_kw))
    n_jobs = a.jobs or max(1, (os.cpu_count() or 2) // 2)
    todo = [j for j in jobs if not _row_path(out_root, a.batch_id, j[0], j[1]).exists()]
    print(f"{len(jobs)} runs ({a.worlds} worlds x {len(combos)} design choices), {len(jobs) - len(todo)} "
          f"already done, on {n_jobs} processes -> {bdir}")
    # Build each site's descent route once, here, so workers load it from disk.
    sites = dict(axes).get("site", [base.site])
    for site in sites:
        C.Campaign(a.seed0, base.with_(site=site), WorldRanges(**world_kw))
    t0 = time.perf_counter()
    t_sims = []
    if todo:
        with Pool(n_jobs, maxtasksperchild=20) as pool:
            for i, t_sim in enumerate(pool.imap_unordered(_run_one, todo, chunksize=1)):
                t_sims.append(t_sim)
                if (i + 1) % max(1, len(todo) // 20) == 0 or i + 1 == len(todo):
                    print(f"  {i + 1}/{len(todo)} runs, {(time.perf_counter() - t0) / 60:.1f} min", flush=True)
    wall = time.perf_counter() - t0
    summaries, bys, drills = [], [], []
    for j in jobs:
        r = json.loads(_row_path(out_root, a.batch_id, j[0], j[1]).read_text(encoding="utf-8"))
        summaries.append(r["summary"]); bys.extend(r["by_sortie"]); drills.extend(r["drills"])
        if not todo:
            t_sims.append(r["t_sim"])
    summaries.sort(key=lambda r: r["run_id"])
    bys.sort(key=lambda r: (r["run_id"], r["sortie_n"]))
    drills.sort(key=lambda r: (r["run_id"], r["sortie_id"]))
    write_csv(bdir / "summary.csv", summaries)
    write_csv(bdir / "by_sortie.csv", bys)
    write_csv(bdir / "drills.csv", drills)

    hc_text, hc = hand_checks(base, a.seed0, out_root, a.batch_id)
    from sim import figures
    figs = figures.make(bdir)

    standins = ["world: Shoemaker 5 m window (sim/campaign.py standin_world)"] if "shoemaker" in sites else []
    lines = [f"# Batch {a.batch_id}", "",
             f"{len(jobs)} runs: worlds {a.seed0}..{a.seed0 + a.worlds - 1}, sweeps "
             + (", ".join(f"{k} = {vs}" for k, vs in axes) or "none")
             + f", overrides {a.set or 'none'}, world {a.world_set or 'default ranges'}.", "",
             f"Wall time {wall / 60:.1f} min on {n_jobs} processes for {len(todo)} new runs; per run {np.median(t_sims):.1f} s median, "
             f"{np.max(t_sims):.1f} s max (simulation only, one process each, cores shared).", "",
             "Stand-ins in these runs: " + ("; ".join(standins) or "none"), "",
             hc_text, "",
             "## Figures", ""] + [f"- `{f.name}`" for f in figs]
    (bdir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (bdir / "batch.json").write_text(json.dumps({"batch_id": a.batch_id, "worlds": a.worlds, "seed0": a.seed0,
                                                 "sweeps": axes, "set": a.set, "world_set": a.world_set, "runs": len(jobs),
                                                 "wall_s": wall, **hc}, default=str, indent=1), encoding="utf-8")
    print(f"done in {wall / 60:.1f} min -> {bdir / 'report.md'}")


if __name__ == "__main__":
    main()
