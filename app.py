"""Lantern, one command: python app.py

Builds whatever is missing, runs the demo if there are no runs yet, and opens
the web viewer on the result. Every step is skipped when its output is newer
than its inputs, so a second launch takes a few seconds.

    python app.py              build what is missing, open the viewer
    python app.py --fresh      rerun both demo runs first (about 5 minutes)
    python app.py --no-open    do not open a browser
    python app.py --port 8001
    python app.py --site DIR   also copy the finished static site to DIR, ready to host

Pieces, in order (owners in docs/WORKING.md):
    1. terrain       sim/terrain.py build          -> data/terrain/shoemaker.npz
    2. display layer sim/terrain_display.py build  -> data/terrain/display/
    3. web assets    web/build_assets.py           -> web/assets/terrain/
    4. demo runs     run_demo.py                   -> data/runs/<run_id>/
    5. viewer runs   (this file)                   -> web/runs/  (copies, viewer index format)
    6. viewer        web/serve.py                  -> http://localhost:<port>

Data lives in the main checkout (C:\\Monad-Hackathon-Project\\data), or in
LANTERN_DATA if set, as sim/terrain.py reads it. The chain is a stand-in
until sim/chain.py has a deployed contract; the viewer labels it so.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("LANTERN_DATA", r"C:\Monad-Hackathon-Project\data"))
TERRAIN = DATA / "terrain" / "shoemaker.npz"
DISPLAY = DATA / "terrain" / "display" / "display.json"
RUNS = DATA / "runs"
WEB = ROOT / "web"
ASSETS_META = WEB / "assets" / "terrain" / "meta.json"
WEB_RUNS = WEB / "runs"
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


def build(fresh: bool):
    if not TERRAIN.exists():
        run(["sim/terrain.py", "build"], "terrain")
    else:
        say(f"terrain: {TERRAIN} (kept)")

    if stale(DISPLAY, TERRAIN):
        run(["sim/terrain_display.py", "build"], "display layer")
    else:
        say(f"display layer: {DISPLAY.parent} (kept)")

    if stale(ASSETS_META, TERRAIN, DISPLAY, WEB / "build_assets.py"):
        run(["web/build_assets.py"], "web assets")
    else:
        say(f"web assets: {ASSETS_META.parent} (kept)")

    if fresh or not (RUNS / "index.json").exists():
        run(["run_demo.py"], "demo runs")
    else:
        say(f"demo runs: {RUNS / 'index.json'} (kept; --fresh reruns them)")


def sync_runs():
    """Copy the latest run of each kind into web/runs/ and write the viewer's index.

    run_demo.py writes data/runs/index.json as a list of {run_id, label, dir,
    status}; the viewer reads web/runs/index.json as {"runs": [{run_id, label,
    path}]}. The copies are what a static host serves.
    """
    index = json.loads((RUNS / "index.json").read_text(encoding="utf8"))
    WEB_RUNS.mkdir(parents=True, exist_ok=True)
    entries = []
    for r in index:
        src = RUNS / r["dir"]
        dst = WEB_RUNS / r["dir"]
        if stale(dst / "log.json", src / "log.json"):
            if dst.exists():
                shutil.rmtree(dst)  # one literal run folder, a copy this file made
            shutil.copytree(src, dst)
            say(f"viewer runs: copied {r['dir']}")
        entries.append({"run_id": r["run_id"], "label": r["label"], "status": r.get("status"),
                        "path": f"runs/{r['dir']}/log.json"})
    (WEB_RUNS / "index.json").write_text(json.dumps({"runs": entries}, indent=1), encoding="utf8")
    say(f"viewer runs: {', '.join(e['run_id'] + ' (' + str(e['status']) + ')' for e in entries)}")


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
    say(f"viewer: {url}   (Ctrl+C stops it)")
    if open_browser:
        webbrowser.open(url)
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()


def main():
    ap = argparse.ArgumentParser(description="Build what is missing, run the demo, open the viewer.")
    ap.add_argument("--fresh", action="store_true", help="rerun both demo runs")
    ap.add_argument("--no-open", action="store_true", help="do not open a browser")
    ap.add_argument("--no-serve", action="store_true", help="build and sync only")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--site", type=Path, help="copy the finished static site here")
    a = ap.parse_args()

    build(a.fresh)
    sync_runs()
    if a.site:
        export_site(a.site)
    if not a.no_serve:
        serve(a.port, not a.no_open)


if __name__ == "__main__":
    main()
