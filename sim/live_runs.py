"""The first camera-navigation runs: 3 runs, one per PSR site, in parallel.

    python -m sim.live_runs --dry-run     # print the plan and the expected wall time, run nothing
    python -m sim.live_runs               # run them

Each run: 3 Scouts x 3 sorties, map refinement on, camera navigation
(Settings.nav = "camera", the default), on sites s89_134e, s89_014e and
haynes, world seed --seed. Writes data/campaigns/<batch_id>/<run_id>/
record.json, poses.npz, steps.npz (docs/handoffs.md, "Camera navigation"),
plus summary.csv once all three finish.

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
import time
from multiprocessing import Pool
from pathlib import Path

SITES = ("s89_134e", "s89_014e", "haynes")
BATCH_ID = "live-nav-first3"
# Measured on the test campaign (docs in the commit that sets them): wall
# seconds per Scout-step with one run alone on the machine, and Scout-steps
# taken per planned 2 m of driving.
S_PER_STEP_MEASURED = None
STEPS_PER_PLANNED_MEASURED = None
PARALLEL_SLOWDOWN = None     # measured: s/step with 3 runs at once over s/step alone


def settings_for(site, n_scouts, sorties):
    from sim.settings import Settings
    return Settings(site=site, n_scouts=n_scouts, sorties_per_scout=sorties, map_refinement=True, nav="camera")


def plan(site, seed, n_scouts, sorties):
    """Planned driving per run (m) and the Scout-steps it implies."""
    from sim.campaign import Campaign
    from sim.rover import STEP_M
    s = settings_for(site, n_scouts, sorties)
    c = Campaign(seed, s)
    per_sortie = 2 * c.corridor_len + s.survey_m
    metres = n_scouts * sorties * per_sortie
    steps = metres / STEP_M * (STEPS_PER_PLANNED_MEASURED or 1.0)
    return {"site": site, "run_id": c.run_id, "corridor_m": round(c.corridor_len), "per_sortie_m": round(per_sortie),
            "metres": round(metres), "scout_steps": round(steps)}


def _run(job):
    site, seed, n_scouts, sorties, batch_id = job
    from sim.campaign import Campaign
    t0 = time.perf_counter()
    c = Campaign(seed, settings_for(site, n_scouts, sorties)).run()
    root = c.save(batch_id)
    sm = c.summary()
    (root / "row.json").write_text(json.dumps({"summary": sm, "by_sortie": c.by_sortie(),
                                               "wall_s": time.perf_counter() - t0}, default=str), encoding="utf-8")
    return site, str(root), sm, time.perf_counter() - t0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--scouts", type=int, default=3)
    ap.add_argument("--sorties", type=int, default=3)
    ap.add_argument("--sites", default=",".join(SITES))
    ap.add_argument("--batch-id", default=BATCH_ID)
    ap.add_argument("--dry-run", action="store_true", help="print the plan and expected wall time only")
    a = ap.parse_args()
    sites = a.sites.split(",")
    plans = [plan(site, a.seed, a.scouts, a.sorties) for site in sites]
    print(f"{len(sites)} runs, {a.scouts} Scouts x {a.sorties} sorties each, camera navigation, map refinement on, "
          f"seed {a.seed}, batch {a.batch_id}")
    worst = 0.0
    for p in plans:
        line = (f"  {p['site']:9} {p['run_id']}: descent corridor {p['corridor_m']} m, "
                f"{p['per_sortie_m']} m planned per sortie, about {p['scout_steps']} Scout-steps")
        if S_PER_STEP_MEASURED:
            sec = p["scout_steps"] * S_PER_STEP_MEASURED * (PARALLEL_SLOWDOWN or 1.0)
            worst = max(worst, sec)
            line += f", about {sec / 3600:.1f} h"
        print(line)
    if S_PER_STEP_MEASURED:
        print(f"expected wall time: about {worst / 3600:.1f} h with the runs in parallel "
              f"({S_PER_STEP_MEASURED:.2f} s per Scout-step measured alone, x{PARALLEL_SLOWDOWN or 1.0:.2f} "
              f"for {len(sites)} at once, x{STEPS_PER_PLANNED_MEASURED or 1.0:.2f} steps per planned step)")
    if a.dry_run:
        return
    t0 = time.perf_counter()
    rows = []
    with Pool(len(sites)) as pool:
        for site, root, sm, wall in pool.imap_unordered(
                _run, [(site, a.seed, a.scouts, a.sorties, a.batch_id) for site in sites]):
            print(f"{site}: done in {wall / 3600:.2f} h -> {root}", flush=True)
            rows.append(sm)
    from sim.campaign import CAMPAIGN_DIR
    out = CAMPAIGN_DIR / a.batch_id / "summary.csv"
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, keys)
        w.writeheader()
        w.writerows(rows)
    print(f"all {len(sites)} runs in {(time.perf_counter() - t0) / 3600:.2f} h; wrote {out}")


if __name__ == "__main__":
    main()
