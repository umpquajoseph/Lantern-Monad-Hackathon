"""The site's index of campaign runs, and browser-readable copies of a run.

Reads only what docs/handoffs.md, "Campaign simulator", defines, under
<DATA>/campaigns/<batch_id>/: summary.csv if the batch has one, else each
run's record.json `summary`; and each run's record.json and poses.npz.

    web/campaigns/index.json     {"batches": [{batch_id, standin, runs: [summary row]}]}
    web/campaigns/<batch>/rows.json   cache of summary rows read from records, by mtime

Locally, web/serve.py serves record.json straight from <DATA> and converts
poses.npz on first request (convert_poses below), so nothing is copied.
With --copy (and python app.py --site), every listed run is copied too:

    web/campaigns/<batch>/<run>/record.json   as is
    web/campaigns/<batch>/<run>/poses.bin     every sortie's pose rows, float32, back to back
    web/campaigns/<batch>/<run>/poses.json    {"columns": [...], "sorties": {sortie_id: [first_row, n_rows]}}

A browser cannot open .npz, so poses become one flat float32 file; the values
are the record's own, not resampled. Only runs with a record are listed.
Each distinct terrain window gets a height grid for the 3D world
(web/build_windows.py).

    python web/collect_campaigns.py              # index every batch
    python web/collect_campaigns.py --copy       # and copy every run, for a static host
    python web/collect_campaigns.py --max-runs 50
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web"))
from sim.terrain import DATA  # noqa: E402

SRC = DATA / "campaigns"
DST = ROOT / "web" / "campaigns"
POSE_COLUMNS = ["t", "true_x", "true_y", "true_heading", "est_x", "est_y", "bound95", "energy_kwh", "activity"]
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


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


def stale(src: Path, dst: Path) -> bool:
    return not dst.exists() or src.stat().st_mtime > dst.stat().st_mtime


def convert_poses(npz: Path, dst: Path) -> None:
    """poses.npz -> poses.bin + poses.json in dst, if the copy is older than the source."""
    if not stale(npz, dst / "poses.bin") and (dst / "poses.json").exists():
        return
    dst.mkdir(parents=True, exist_ok=True)
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
    tmp = dst / "poses.bin.tmp"
    tmp.write_bytes(flat.astype("<f4").tobytes())
    tmp.replace(dst / "poses.bin")
    (dst / "poses.json").write_text(json.dumps({"columns": POSE_COLUMNS, "sorties": index}), encoding="utf8")


def copy_run(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    if stale(src / "record.json", dst / "record.json"):
        shutil.copy2(src / "record.json", dst / "record.json")
    convert_poses(src / "poses.npz", dst)
    if (src / "fp").is_dir():   # the showcase run's first-person frames (sim/showcase.py)
        shutil.copytree(src / "fp", dst / "fp", dirs_exist_ok=True)


def has_record(d: Path) -> bool:
    return (d / "record.json").exists() and (d / "poses.npz").exists()


def record_rows(bdir: Path, runs: list[Path], say) -> tuple[list[dict], dict]:
    """Summary rows from each record, cached by mtime; plus what the batch's records say about stand-ins and terrain."""
    cache_p = DST / bdir.name / "rows.json"
    cache = json.loads(cache_p.read_text(encoding="utf8")) if cache_p.exists() else {}
    rows, meta, read = [], {}, 0
    for d in runs:
        mt = (d / "record.json").stat().st_mtime
        c = cache.get(d.name)
        if not c or c["mtime"] != mt:
            rec = json.loads((d / "record.json").read_text(encoding="utf8"))
            c = {"mtime": mt, "row": rec.get("summary") or {"run_id": rec["run_id"], "seed": rec.get("seed")},
                 "standin": standin_text(rec), "terrain": rec.get("terrain"), "showcase": rec.get("showcase")}
            cache[d.name] = c
            read += 1
            if read % 50 == 0:
                say(f"campaigns: {bdir.name}, read {read} records")
        rows.append(c["row"])
        meta.setdefault("standin", c["standin"])
        meta.setdefault("showcase", c.get("showcase"))
        meta.setdefault("terrains", {})[json.dumps(c["terrain"], sort_keys=True)] = c["terrain"]
    if read:
        cache_p.parent.mkdir(parents=True, exist_ok=True)
        cache_p.write_text(json.dumps(cache), encoding="utf8")
    return rows, meta


def standin_text(rec: dict) -> str | None:
    """What a record says is not real yet: this site's stand-in sentence, and the engine's `standins` list."""
    parts = []
    if rec.get("standin"):
        parts.append(str(rec["standin"]))
    si = rec.get("standins")
    if isinstance(si, list):
        parts += [str(s) for s in si]
    elif isinstance(si, dict):
        parts += [f"{k}: {v}" for k, v in si.items()]
    return " ".join(parts) or None


def collect(max_runs: int | None = None, copy: bool = False, say=print) -> list[dict]:
    from build_windows import build as build_window
    batches = []
    bdirs = sorted(p for p in SRC.glob("*") if p.is_dir() and NAME.match(p.name) and not p.name.startswith("_")) if SRC.exists() else []
    for bdir in bdirs:
        runs = sorted(d for d in bdir.iterdir() if d.is_dir() and has_record(d))
        if not runs:
            continue
        # summary.csv when the batch has one (it lists runs without records too; keep those with one).
        by_name = {d.name: d for d in runs}
        _, meta = record_rows(bdir, runs[:1], say)
        if (bdir / "summary.csv").exists():
            with open(bdir / "summary.csv", newline="", encoding="utf8") as fh:
                rows = [{k: typed(v) for k, v in r.items()} for r in csv.DictReader(fh)]
            missing = sum(1 for r in rows if r["run_id"] not in by_name)
            rows = [r for r in rows if r["run_id"] in by_name]
        else:
            rows, meta = record_rows(bdir, runs, say)
            missing = 0
        rows = rows[: max_runs or None]
        if copy:
            for r in rows:
                copy_run(by_name[r["run_id"]], DST / bdir.name / r["run_id"])
        for t in meta.get("terrains", {}).values():
            build_window(t, say=say)
        if rows:
            batches.append({"batch_id": bdir.name, "standin": meta.get("standin"), "showcase": meta.get("showcase"), "runs": rows})
        say(f"campaigns: {bdir.name}, {len(rows)} runs" + (f" ({missing} in summary.csv have no record)" if missing else "")
            + (f"; stand-ins: {meta['standin'][:90]}" if meta.get("standin") else ""))
    DST.mkdir(parents=True, exist_ok=True)
    (DST / "index.json").write_text(json.dumps({"batches": batches}, separators=(",", ":")), encoding="utf8")
    if not batches:
        say(f"campaigns: none under {SRC}. Run python web/make_standin_campaign.py for a labelled stand-in.")
    return batches


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-runs", type=int, help="list at most this many runs per batch")
    ap.add_argument("--copy", action="store_true", help="copy every listed run into web/campaigns/ (static host)")
    a = ap.parse_args()
    collect(a.max_runs, a.copy)
