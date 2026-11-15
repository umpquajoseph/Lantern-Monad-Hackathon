"""Copy the runs run_demo.py wrote into web/runs/ so the page can serve them.

    python web/collect_runs.py                  # real runs only
    python web/collect_runs.py --with-standins  # also list web/make_standin_run.py's runs

Reads C:\\Monad-Hackathon-Project\\data\\runs\\index.json (a list of
{run_id, label, dir, status}) and writes web/runs/index.json.
"""
import argparse
import json
import shutil
from pathlib import Path

SRC = Path(r"C:\Monad-Hackathon-Project\data\runs")
DST = Path(__file__).resolve().parent / "runs"
STANDINS = ["run1-standin", "run2-standin"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-standins", action="store_true")
    args = ap.parse_args()
    DST.mkdir(parents=True, exist_ok=True)
    runs = []
    for r in json.loads((SRC / "index.json").read_text()):
        shutil.copytree(SRC / r["dir"], DST / r["dir"], dirs_exist_ok=True)
        runs.append({"run_id": r["run_id"], "label": r["label"], "path": f"runs/{r['dir']}/log.json",
                     "status": r.get("status")})
        print("copied", r["dir"])
    if args.with_standins:
        for rid in STANDINS:
            log = DST / rid / "log.json"
            if log.exists():
                runs.append({"run_id": rid, "label": json.loads(log.read_text())["label"] + " (stand-in)",
                             "path": f"runs/{rid}/log.json"})
    (DST / "index.json").write_text(json.dumps({"runs": runs}, indent=1))
    print(f"{len(runs)} runs -> {DST / 'index.json'}")


if __name__ == "__main__":
    main()
