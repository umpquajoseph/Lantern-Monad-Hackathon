"""Copy campaign runs into web/campaigns/ in a shape the browser can read.

Reads only what docs/handoffs.md, "Campaign simulator", defines: each batch's
summary.csv and each run's record.json and poses.npz, under
<DATA>/campaigns/<batch_id>/. Writes, under web/campaigns/:

    index.json                    {"batches": [{batch_id, standin, runs: [summary row + "dir"]}]}
    <batch_id>/<run_id>/record.json   copied as is
    <batch_id>/<run_id>/poses.bin     every sortie's pose rows, float32, back to back
    <batch_id>/<run_id>/poses.json    {"columns": [...], "sorties": {sortie_id: [first_row, n_rows]}}

A browser cannot open .npz, so poses become one flat float32 file; the values
are the record's own, not resampled. Runs whose record is missing are listed
in the summary but skipped here, with a line saying so.

    python web/collect_campaigns.py              # every batch, every run
    python web/collect_campaigns.py --max-runs 50
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sim.terrain import DATA  # noqa: E402

SRC = DATA / "campaigns"
DST = ROOT / "web" / "campaigns"
POSE_COLUMNS = ["t", "true_x", "true_y", "true_heading", "est_x", "est_y", "bound95", "energy_kwh", "activity"]


def typed(v: str):
    if v == "":
        return None
    if v in ("True", "False"):
        return v == "True"
    try:
        f = float(v)
        return int(f) if f.is_integer() and "." not in v and "e" not in v.lower() else f
    except ValueError:
        return v


def newer(src: Path, dst: Path) -> bool:
    return not dst.exists() or src.stat().st_mtime > dst.stat().st_mtime


def collect_run(src: Path, dst: Path) -> bool:
    rec, npz = src / "record.json", src / "poses.npz"
    if not rec.exists() or not npz.exists():
        return False
    dst.mkdir(parents=True, exist_ok=True)
    if newer(rec, dst / "record.json"):
        shutil.copy2(rec, dst / "record.json")
    if newer(npz, dst / "poses.bin"):
        z = np.load(npz)
        index, parts, row = {}, [], 0
        for key in z.files:
            a = np.asarray(z[key], np.float32)
            if a.ndim != 2 or a.shape[1] != len(POSE_COLUMNS):
                raise ValueError(f"{npz} {key}: shape {a.shape}, want (n, {len(POSE_COLUMNS)})")
            index[key] = [row, int(a.shape[0])]
            parts.append(a)
            row += a.shape[0]
        flat = np.concatenate(parts) if parts else np.zeros((0, len(POSE_COLUMNS)), np.float32)
        (dst / "poses.bin").write_bytes(flat.astype("<f4").tobytes())
        (dst / "poses.json").write_text(json.dumps({"columns": POSE_COLUMNS, "sorties": index}), encoding="utf8")
    return True


def collect(max_runs: int | None = None, say=print) -> list[dict]:
    batches = []
    for bdir in sorted(p for p in SRC.glob("*") if (p / "summary.csv").exists()) if SRC.exists() else []:
        with open(bdir / "summary.csv", newline="", encoding="utf8") as fh:
            rows = [{k: typed(v) for k, v in r.items()} for r in csv.DictReader(fh)]
        runs, skipped = [], 0
        for r in rows[: max_runs or None]:
            if collect_run(bdir / r["run_id"], DST / bdir.name / r["run_id"]):
                runs.append(r)
            else:
                skipped += 1
        standin = None
        if runs:
            first = json.loads((DST / bdir.name / runs[0]["run_id"] / "record.json").read_text(encoding="utf8"))
            standin = first.get("standin")
        batches.append({"batch_id": bdir.name, "standin": standin, "runs": runs})
        say(f"campaigns: {bdir.name}, {len(runs)} runs" + (f", {skipped} without a record skipped" if skipped else "")
            + (" (stand-in)" if standin else ""))
    DST.mkdir(parents=True, exist_ok=True)
    (DST / "index.json").write_text(json.dumps({"batches": batches}, separators=(",", ":")), encoding="utf8")
    if not batches:
        say(f"campaigns: none under {SRC}. Run python web/make_standin_campaign.py for a labelled stand-in.")
    return batches


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-runs", type=int, help="host at most this many runs per batch")
    collect(ap.parse_args().max_runs)
