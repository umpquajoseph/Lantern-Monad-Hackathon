"""Lantern, one command: python app.py

Builds whatever is missing, collects the campaign runs and opens the local
site on them: the runs list, each run's top-down map, and a 3D replay of any
sortie. Every step is skipped when its output is newer than its inputs, so a
second launch takes a few seconds.

    python app.py              build what is missing, open the site
    python app.py --standin    remake the labelled stand-in batch first
    python app.py --no-open    do not open a browser
    python app.py --port 8001
    python app.py --max-runs N host at most N runs per batch
    python app.py --site DIR   also copy the finished static site to DIR

Pieces, in order:
    1. terrain       sim/terrain.py build           -> data/terrain/shoemaker.npz
    2. display layer sim/terrain_display.py build   -> data/terrain/display/
    3. web assets    web/build_assets.py            -> web/assets/terrain/
                     web/build_assets.py --run-ground, per camera run -> web/assets/terrain/runs/<run_id>/
    4. campaigns     the campaign engine            -> data/campaigns/<batch_id>/
                     (none yet: web/make_standin_campaign.py writes a labelled stand-in)
    5. site index    web/collect_campaigns.py       -> web/campaigns/index.json (+ terrain windows)
    6. site          web/serve.py                   -> http://localhost:<port>

Data lives where sim/terrain.py says: LANTERN_DATA if set, else the main
checkout (C:/Monad-Hackathon-Project/data), else ./data. Set
LANTERN_DATA=lantern_data to use the committed terrain. Hosting is off: the
site runs locally only.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from sim.terrain import DATA, LOLA_SHOEMAKER  # noqa: E402  LANTERN_DATA, else the main checkout, else ./data
TERRAIN = DATA / "terrain" / "shoemaker.npz"
DISPLAY = DATA / "terrain" / "display" / "display.json"
CAMPAIGNS = DATA / "campaigns"
WEB = ROOT / "web"
ASSETS_META = WEB / "assets" / "terrain" / "meta.json"
PY = sys.executable


def say(msg):
    print(f"[lantern] {msg}", flush=True)


def run(args, what):
    say(f"{what}: {' '.join(str(a) for a in args)}")
    t0 = time.perf_counter()
    subprocess.run([PY, *map(str, args)], cwd=ROOT, check=True)
    say(f"{what}: done in {time.perf_counter() - t0:.0f} s")


def mtime(p: Path) -> float:
    return p.stat().st_mtime if p.exists() else 0.0


def stale(out: Path, *inputs: Path) -> bool:
    return not out.exists() or any(mtime(i) > mtime(out) for i in inputs)


def build(standin: bool):
    if not TERRAIN.exists():
        run(["sim/terrain.py", "build"], "terrain")
    else:
        say(f"terrain: {TERRAIN} (kept)")

    # A git checkout stamps files in checkout order, so a committed display layer can look
    # older than the terrain. It cannot be rebuilt without the LOLA tile; keep it then.
    lola = Path(str(LOLA_SHOEMAKER).format("surf"))
    if DISPLAY.exists() and stale(DISPLAY, TERRAIN) and not lola.exists():
        say(f"display layer: {DISPLAY.parent} (kept; no LOLA tile to rebuild it from)")
    elif stale(DISPLAY, TERRAIN):
        run(["sim/terrain_display.py", "build"], "display layer")
    else:
        say(f"display layer: {DISPLAY.parent} (kept)")

    if stale(ASSETS_META, TERRAIN, DISPLAY, WEB / "build_assets.py"):
        run(["web/build_assets.py"], "web assets")
    else:
        say(f"web assets: {ASSETS_META.parent} (kept)")

    have = any(CAMPAIGNS.glob("*/summary.csv")) if CAMPAIGNS.exists() else False
    if standin or not have:
        # No batch from the campaign engine yet: a stand-in in the same format, labelled on every page.
        run(["web/make_standin_campaign.py"], "stand-in campaign")
    else:
        say(f"campaigns: {', '.join(p.parent.name for p in sorted(CAMPAIGNS.glob('*/summary.csv')))}")


def run_grounds():
    """Each camera run's own ground (record `ground`, docs/handoffs.md) -> web/assets/terrain/runs/<run_id>/.

    Mission Control draws a camera run on the ground its cameras saw; without
    these assets it falls back to the Shoemaker stand-in grid, which is the
    wrong place for a PSR run. Camera runs are the ones with steps.npz.
    """
    import json
    built = kept = 0
    for steps in sorted(CAMPAIGNS.glob("*/*/steps.npz")):
        rdir = steps.parent
        try:
            g = json.loads((rdir / "record.json").read_text()).get("ground") or {}
        except (OSError, ValueError):
            continue
        src = DATA / "terrain" / g["file"] if g.get("file") else None
        if src is None or not src.exists():
            continue
        meta = WEB / "assets" / "terrain" / "runs" / rdir.name / "meta.json"
        if stale(meta, src, WEB / "build_assets.py"):
            run(["web/build_assets.py", "--run-ground", f"{rdir.parent.name}/{rdir.name}"], f"run ground {rdir.name}")
            built += 1
        else:
            kept += 1
    say(f"run grounds: {built} built, {kept} kept")


def sync_runs(max_runs, copy=False):
    """Index the batches. Locally web/serve.py reads the runs from DATA; --site copies them."""
    sys.path.insert(0, str(WEB))
    from collect_campaigns import collect  # noqa: E402  web/collect_campaigns.py
    collect(max_runs, copy=copy, say=say)


def export_site(dest: Path):
    """The static site, as a host would get it: web/ without the dev scripts."""
    if dest.exists() and any(dest.iterdir()):
        sys.exit(f"--site {dest} is not empty; give an empty or new folder")
    skip = shutil.ignore_patterns("*.py", "__pycache__", ".gitignore")
    shutil.copytree(WEB, dest, ignore=skip, dirs_exist_ok=True)
    size = sum(p.stat().st_size for p in dest.rglob("*") if p.is_file()) / 1e6
    say(f"site: {dest} ({size:.0f} MB), upload this folder as it is")


def serve(port: int, open_browser: bool):
    url = f"http://localhost:{port}/"
    proc = subprocess.Popen([PY, str(WEB / "serve.py"), str(port)], cwd=WEB)
    for _ in range(50):
        try:
            urllib.request.urlopen(url, timeout=0.5)
            break
        except OSError:
            time.sleep(0.1)
    say(f"site: {url}   (Ctrl+C stops it)")
    if open_browser:
        webbrowser.open(url)
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()


def main():
    ap = argparse.ArgumentParser(description="Build what is missing, collect the campaign runs, open the site.")
    ap.add_argument("--standin", action="store_true", help="remake the labelled stand-in batch")
    ap.add_argument("--max-runs", type=int, help="host at most this many runs per batch")
    ap.add_argument("--no-open", action="store_true", help="do not open a browser")
    ap.add_argument("--no-serve", action="store_true", help="build and sync only")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--site", type=Path, help="copy the finished static site here")
    a = ap.parse_args()

    build(a.standin)
    run_grounds()
    sync_runs(a.max_runs, copy=bool(a.site))
    if a.site:
        export_site(a.site)
    if not a.no_serve:
        serve(a.port, not a.no_open)


if __name__ == "__main__":
    main()
