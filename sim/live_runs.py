"""The first camera-navigation runs: 3 runs, one per PSR site, in parallel.

    python -m sim.live_runs --dry-run     # print the plan and the expected wall time, run nothing
    python -m sim.live_runs               # run them (again: skips finished runs)
    scripts/launch_first3.ps1             # the same, in its own window

Each run: 3 Scouts x 3 sorties, map refinement on, camera navigation
(Settings.nav = "camera", the default), on sites s89_134e, s89_014e and
haynes, world seeds SEEDS (or --seed). Writes data/campaigns/<batch_id>/<run_id>/
record.json, poses.npz, steps.npz (docs/handoffs.md, "Camera navigation"),
plus summary.csv once all three finish. Every line, each run's progress
line (every --progress Scout-steps) included, also goes to
data/campaigns/<batch_id>/launch.log, prefixed with the time and the site.

Resume: a run whose row.json (written last) exists is finished and is
skipped. A run that died part way starts over; its run ground is cached.

The expected wall time is the planned driving distance divided by the step
(sim/rover.py STEP_M), times the measured seconds per Scout-step
(S_PER_STEP_MEASURED), times a factor for detours, turns in place and
skipped waypoints measured on the test campaign (STEPS_PER_PLANNED_MEASURED).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import multiprocessing
import os
import sys
import time
from multiprocessing import Pool
from pathlib import Path

SITES = ("s89_134e", "s89_014e", "haynes")
# World seed per site. Chosen, not drawn: the lowest seed whose world, run
# with the sampled engine on this same plan (3 Scouts x 3 sorties, full
# survey, density 336), makes at least 2 finds and at least 2 searches by a
# second Scout (scan of seeds 1-120 per site, 16 Nov 2026). With 9 sorties
# a run surveys about 0.6 km2, and most seeds' deposits lie elsewhere. The
# camera runs drive their own paths, so finds are likely, not certain.
SEEDS = {"s89_134e": 6, "s89_014e": 9, "haynes": 3}
SEED_RULE = ("the lowest seed whose world, run with the sampled engine on the same plan, makes at least 2 finds "
             "and 2 searches by a second Scout (sim/live_runs.py SEEDS)")
BATCH_ID = "live-nav-first3"
# Measured on the test campaign (docs in the commit that sets them): wall
# seconds per Scout-step with one run alone on the machine, and Scout-steps
# taken per planned 2 m of driving.
# Smoke rerun, 16 Nov 19:16 (realism 455bc93 merged, planner c1021c7): 2,500
# Scout-steps in about 23 min (render 0.38, lidar 0.035, vision 0.007,
# planner 0.010, filter 0.0004 s a step; the rest is logging and labels).
S_PER_STEP_MEASURED = 0.55
# Smoke w00001-bcce1993: 7,725 steps for 3,940 planned 2 m steps (detours,
# turns in place, transit to lanes).
STEPS_PER_PLANNED_MEASURED = 1.96
PARALLEL_SLOWDOWN = None     # not measured: s/step with 3 runs at once over s/step alone


def settings_for(site, n_scouts, sorties, extra=()):
    from sim.campaign import parse_value
    from sim.settings import Settings
    from dataclasses import fields
    types = {f.name: f.type for f in fields(Settings)}
    kw = dict(site=site, n_scouts=n_scouts, sorties_per_scout=sorties, map_refinement=True, nav="camera")
    for p in extra:
        k, v = p.split("=", 1)
        kw[k] = parse_value(types[k], v)
    return Settings(**kw)


def ranges_for(s):
    """The world ranges of these runs: crater density fixed at the lunar
    small-crater equilibrium (sim/terrain.py Params.n_ge10_per_km2, 336 per
    km2 of 10 m and up, Minton et al. 2019 Fit 1), drift fixed at the
    Settings value as a single run does. Every other range as WorldRanges."""
    from sim.settings import WorldRanges
    from sim.terrain import Params
    n = float(Params().n_ge10_per_km2)
    return WorldRanges(crater_n_ge10_per_km2=(n, n), drift_frac=(s.drift_frac, s.drift_frac))


def plan(site, seed, n_scouts, sorties, extra=()):
    """Planned driving per run (m) and the Scout-steps it implies."""
    from sim.campaign import Campaign
    from sim.rover import STEP_M
    s = settings_for(site, n_scouts, sorties, extra)
    c = Campaign(seed, s, ranges_for(s))
    per_sortie = 2 * c.corridor_len + s.survey_m
    metres = n_scouts * sorties * per_sortie
    steps = metres / STEP_M * (STEPS_PER_PLANNED_MEASURED or 1.0)
    return {"site": site, "run_id": c.run_id, "corridor_m": round(c.corridor_len), "per_sortie_m": round(per_sortie),
            "metres": round(metres), "scout_steps": round(steps)}


class _Log:
    """A process's stdout and stderr, line by line, to its console and to
    the batch log, each line prefixed with the time and the run's site."""

    def __init__(self, path, console, lock):
        self.path, self.console, self.lock = path, console, lock
        self.prefix, self.buf = "", ""

    def write(self, text):
        self.buf += text
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            out = f"{time.strftime('%H:%M:%S')} {self.prefix}{line}\n"
            with self.lock:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(out)
                if self.console is not None:
                    self.console.write(out)
                    self.console.flush()
        return len(text)

    def flush(self):
        pass


def _init_worker(lock, log_path):
    import sys
    if log_path:
        sys.stdout = _Log(log_path, sys.__stdout__, lock)
        sys.stderr = sys.stdout


def _done(root):
    """The run's row.json, written last, or None while the run is not finished."""
    p = Path(root) / "row.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _run(job):
    import os
    import sys
    import traceback
    site, seed, n_scouts, sorties, batch_id, extra = job
    if isinstance(sys.stdout, _Log):
        sys.stdout.prefix = f"[{site}] "
    t0 = time.perf_counter()
    try:
        from sim.campaign import Campaign
        s = settings_for(site, n_scouts, sorties, extra)
        c = Campaign(seed, s, ranges_for(s))
        print(f"start {c.run_id}, seed {seed}, pid {os.getpid()}", flush=True)
        c.run()
        root = c.save(batch_id)
        rec_path = root / "record.json"
        rec = json.loads(rec_path.read_text(encoding="utf-8"))
        if SEEDS.get(site) == seed:
            rec["seed_choice"] = f"seed {seed} chosen for {site}, not drawn: " + SEED_RULE
        else:
            rec["seed_choice"] = f"seed {seed} given on the command line"
        rec_path.write_text(json.dumps(rec, separators=(",", ":")), encoding="utf-8")
        sm = c.summary()
        wall = time.perf_counter() - t0
        (root / "row.json").write_text(json.dumps({"summary": sm, "by_sortie": c.by_sortie(), "wall_s": wall},
                                                  default=str), encoding="utf-8")
        print(f"done in {wall / 3600:.2f} h -> {root}", flush=True)
        return site, str(root), sm, wall
    except Exception:
        wall = time.perf_counter() - t0
        print(f"FAILED after {wall / 3600:.2f} h:\n{traceback.format_exc()}", flush=True)
        return site, None, None, wall


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seed", type=int, default=None, help="one world seed for every site (default: SEEDS)")
    ap.add_argument("--scouts", type=int, default=3)
    ap.add_argument("--sorties", type=int, default=3)
    ap.add_argument("--sites", default=",".join(SITES))
    ap.add_argument("--batch-id", default=BATCH_ID)
    ap.add_argument("--dry-run", action="store_true", help="print the plan and expected wall time only")
    ap.add_argument("--serial", action="store_true", help="one run at a time (about 1 GB) instead of all at once")
    ap.add_argument("--progress", type=int, default=500, help="a progress line every N Scout-steps of each run")
    ap.add_argument("--set", nargs="*", default=[], help="other Settings overrides, name=value (test runs)")
    a = ap.parse_args()
    from sim.campaign import CAMPAIGN_DIR
    batch = CAMPAIGN_DIR / a.batch_id
    log_path = None
    lock = multiprocessing.Lock()
    if not a.dry_run:
        batch.mkdir(parents=True, exist_ok=True)
        log_path = batch / "launch.log"
        _init_worker(lock, log_path)          # this process too: the plan and the outcome go in the log
        print(f"---- launch {time.strftime('%Y-%m-%d %H:%M:%S')}, pid {os.getpid()}, log {log_path}", flush=True)
    sites = a.sites.split(",")
    seed = {site: a.seed if a.seed is not None else SEEDS.get(site, 1) for site in sites}
    plans = [plan(site, seed[site], a.scouts, a.sorties, a.set) for site in sites]
    nav = settings_for(sites[0], a.scouts, a.sorties, a.set).nav
    print(f"{len(sites)} runs, {a.scouts} Scouts x {a.sorties} sorties each, {nav} navigation, map refinement on, "
          f"seeds {seed}, batch {a.batch_id}")
    worst = 0.0
    for p in plans:
        line = (f"  {p['site']:9} {p['run_id']}: descent corridor {p['corridor_m']} m, "
                f"{p['per_sortie_m']} m planned per sortie, about {p['scout_steps']} Scout-steps")
        if S_PER_STEP_MEASURED:
            sec = p["scout_steps"] * S_PER_STEP_MEASURED * (PARALLEL_SLOWDOWN or 1.0)
            worst = max(worst, sec)
            line += f", about {sec / 3600:.1f} h"
        if _done(batch / p["run_id"]):
            line += "  [finished: skipped]"
        print(line)
    if S_PER_STEP_MEASURED:
        print(f"expected wall time: about {worst / 3600:.1f} h with the runs in parallel "
              f"({S_PER_STEP_MEASURED:.2f} s per Scout-step measured alone, x{PARALLEL_SLOWDOWN or 1.0:.2f} "
              f"for {len(sites)} at once, x{STEPS_PER_PLANNED_MEASURED or 1.0:.2f} steps per planned step)")
    if a.dry_run:
        return
    rows, jobs = {}, []
    for site, p in zip(sites, plans):
        row = _done(batch / p["run_id"])
        if row:
            rows[site] = row["summary"]
        else:
            jobs.append((site, seed[site], a.scouts, a.sorties, a.batch_id, a.set))
    os.environ["LANTERN_CAM_PROGRESS"] = str(a.progress)   # sim/campaign_camera.py reads it at import, in each worker
    t0 = time.perf_counter()
    failed = []
    if jobs:
        with Pool(1 if a.serial else len(jobs), initializer=_init_worker, initargs=(lock, log_path),
                  maxtasksperchild=1) as pool:
            for site, root, sm, wall in pool.imap_unordered(_run, jobs):
                if sm is None:
                    failed.append(site)
                else:
                    rows[site] = sm
    done_rows = [rows[x] for x in sites if x in rows]
    out = batch / "summary.csv"
    keys = list(dict.fromkeys(k for r in done_rows for k in r))
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, keys)
        w.writeheader()
        w.writerows(done_rows)
    msg = (f"{len(done_rows)} of {len(sites)} runs finished, {len(jobs)} run this time in "
           f"{(time.perf_counter() - t0) / 3600:.2f} h; wrote {out}")
    if failed:
        msg += f". FAILED: {', '.join(failed)}; the same command again reruns only the unfinished runs"
    print(msg, flush=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
