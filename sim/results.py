"""Mission results: one master file per run, with what every rover found and where it drove.

Reads the run log that run_demo.py writes (docs/handoffs.md, "Run log") and builds

    data/runs/<run_id>/results.json

with, per run:
  - the path each rover believes it drove (its estimate, or odometry when nav is off),
    with its 1-sigma uncertainty along the way, plus a downsampled copy for the chain;
  - every ice find: world x, y and sigma, latitude and longitude, the reading (wt%
    water-equivalent), a modelled ice quantity (area, depth, mass) and modelled volatiles;
  - every check by another agency, and the find's status by the contract's rule;
  - totals for the run;
  - keccak256 hashes: of each path, of each assay, and of the whole file (`results_hash`).

sim/survey.py posts the paths, assays and the results hash to LanternSurvey on Monad.
The finds and checks themselves are posted by the rovers during the run (sim/chain.py).

Hashing. `results_hash` covers everything except the `truth` block (the hidden ice map,
which no rover knows) and the `chain` block (transaction hashes added after posting). So
anyone holding the file can strip those two keys, re-hash and compare with the chain.

Every model number below names its source or is marked ASSUMED.

Sources:
  [C10]  Colaprete et al. 2010, Detection of Water in the LCROSS Ejecta Plume, Science
         330, 463. Table 2 (p. 467): column densities relative to H2O(g).
  [PB]   PROSPECT Team Brief, RASC-AL 2027 Theme 3-1 (docs/sources/), p. 5: the neutron
         spectrometer senses hydrogen "down to ~1 m"; p. 12: LCROSS ~5.6 % water.
  [Hu21] arXiv 2109.00421, section 2: "1.66 g/cc lunar regolith".
  [PGDA] pgda.gsfc.nasa.gov/products/90: LOLA polar DEMs are in south polar
         stereographic X/Y metres, MOON_ME frame.

Usage:
    python sim/results.py build data/runs/<run_id>         # -> results.json, prints totals
    python sim/results.py build data/runs/<run_id>/log.json
    python sim/results.py selftest                          # builds from a synthetic log
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from web3 import Web3

SCHEMA = "lantern-results/1"
ROOT = Path(__file__).resolve().parents[1]
# Same data root as sim/terrain.py: the main checkout's git-ignored data/ folder.
DATA = Path(os.environ.get("LANTERN_DATA", r"C:\Monad-Hackathon-Project\data"))

# Order matters: it is the order of LanternSurvey.volatilesPpm and volatileNames().
VOLATILES = ("H2S", "NH3", "SO2", "C2H4", "CO2", "CH3OH", "CH4", "OH")
# [C10] Table 2, "% relative to H2O(g)", as fractions (molecule counts, not mass).
C10_RATIO = {"H2S": 0.1675, "NH3": 0.0603, "SO2": 0.0319, "C2H4": 0.0312,
             "CO2": 0.0217, "CH3OH": 0.0155, "CH4": 0.0065, "OH": 0.0003}
# Molar masses, g/mol (standard atomic weights).
MOLAR = {"H2O": 18.015, "H2S": 34.08, "NH3": 17.031, "SO2": 64.06, "C2H4": 28.054,
         "CO2": 44.009, "CH3OH": 32.042, "CH4": 16.043, "OH": 17.007}


@dataclass
class Model:
    """Parameters of the ice-quantity and volatiles model. Change here, nowhere else."""
    # [PB] p. 5: the neutron spectrometer senses hydrogen to about 1 m depth.
    sense_depth_m: float = 1.0
    # [Hu21] section 2: lunar regolith bulk density 1.66 g/cc.
    regolith_density_kg_m3: float = 1660.0
    # ASSUMED. Radius of ground one neutron reading averages over. Used as the deposit
    # radius when the log has no per-step readings to measure the extent from.
    footprint_radius_m: float = 1.5
    # ASSUMED. A reading at or above this is "ice" when measuring a deposit's extent
    # along the track. [PB] p. 9 asks for a source for its ~1 wt% threshold; none yet.
    extent_threshold_wt_pct: float = 1.0
    # ASSUMED. The LCROSS plume's volatile mix [C10] scaled to each find's water. The
    # plume sampled one spot in Cabeus; using it everywhere is a modelling choice.
    volatile_ratios: dict = field(default_factory=lambda: dict(C10_RATIO))
    # Sphere for the stereographic inverse. ASSUMED: the IAU mean lunar radius, 1737.4 km,
    # the sphere LOLA products use; check against data/lola/README.txt.
    moon_radius_m: float = 1_737_400.0
    # ASSUMED. Most waypoints a traverse may put on chain (LanternSurvey.MAX_WAYPOINTS = 512).
    chain_waypoints: int = 120


# ------------------------------------------------------------------ hashing helpers

def canonical_json(obj: Any) -> str:
    """Sorted keys, no spaces, floats as Python repr. Same rule as sim/chain.py."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def keccak_hex(obj: Any) -> str:
    return "0x" + bytes(Web3.keccak(text=canonical_json(obj))).hex()


def hashed_body(results: dict) -> dict:
    """The part of a results file that results_hash covers."""
    return {k: v for k, v in results.items() if k not in ("truth", "chain", "results_hash")}


# ------------------------------------------------------------------ geometry

def stereo_to_latlon(x: float, y: float, R: float) -> tuple[float, float]:
    """South polar stereographic (true scale at the pole) to degrees.

    rho = 2 R tan(pi/4 + lat/2), x = rho sin(lon), y = rho cos(lon)  [PGDA: S polar stereo].
    """
    rho = math.hypot(x, y)
    lat = math.degrees(2.0 * math.atan(rho / (2.0 * R)) - math.pi / 2.0)
    lon = math.degrees(math.atan2(x, y)) % 360.0
    return lat, lon


def path_length(pts: list[list[float]]) -> float:
    return sum(math.dist(pts[i][:2], pts[i + 1][:2]) for i in range(len(pts) - 1))


def downsample(pts: list, n_max: int) -> list:
    """Evenly spaced by index, always keeping the first and last point."""
    if len(pts) <= n_max:
        return list(pts)
    idx = sorted({round(i * (len(pts) - 1) / (n_max - 1)) for i in range(n_max)})
    return [pts[i] for i in idx]


# ------------------------------------------------------------------ models

def volatiles_ppm(reading_wt_pct: float, m: Model) -> dict[str, float]:
    """ppm by mass of regolith for each species, from the water reading and [C10] ratios."""
    water_ppm = reading_wt_pct * 1e4
    return {s: round(water_ppm * m.volatile_ratios[s] * MOLAR[s] / MOLAR["H2O"], 1)
            for s in VOLATILES}


def deposit_extent(find: dict, track: list[dict], m: Model) -> tuple[float, str]:
    """Deposit radius in metres, and how it was got.

    With per-step readings on the finder's track: the longest run of consecutive steps at
    or above the threshold that passes within 3 sigma of the find, half its length (a
    chord through the deposit, so a lower bound). Never below the footprint radius.
    """
    if not track or all(p.get("reading") is None for p in track):
        return m.footprint_radius_m, "footprint (no per-step readings in log)"
    best = 0.0
    run: list[dict] = []

    def close(run):
        nonlocal best
        if len(run) >= 2:
            near = any(math.dist((p["x"], p["y"]), (find["x"], find["y"])) <=
                       3 * (find["sigma"] + p["sigma"]) for p in run)
            if near:
                best = max(best, path_length([[p["x"], p["y"]] for p in run]))

    for p in track:
        r = p.get("reading")
        if r is not None and r >= m.extent_threshold_wt_pct:
            run.append(p)
        else:
            close(run)
            run = []
    close(run)
    r = max(best / 2.0, m.footprint_radius_m)
    how = "chord of readings >= threshold along track" if best / 2.0 > m.footprint_radius_m \
        else "footprint (chord shorter than footprint)"
    return r, how


def ice_quantity(reading_wt_pct: float, radius_m: float, m: Model) -> dict:
    area = math.pi * radius_m ** 2
    mass = reading_wt_pct / 100.0 * m.regolith_density_kg_m3 * area * m.sense_depth_m
    return {"radius_m": round(radius_m, 3), "area_m2": round(area, 2),
            "depth_m": m.sense_depth_m, "mass_kg": round(mass, 1)}


def status_of(n_agree: int, n_disagree: int) -> str:
    """Lantern's rule (contracts/src/Lantern.sol): any disagreement disputes; else one
    agreement confirms; else reported."""
    if n_disagree > 0:
        return "disputed"
    return "confirmed" if n_agree > 0 else "reported"


# ------------------------------------------------------------------ build

def load_log(path: str | os.PathLike) -> tuple[dict, Path]:
    p = Path(path)
    if p.is_dir():
        p = p / "log.json"
    return json.loads(p.read_text()), p.parent


def tracks_from_log(log: dict) -> dict[str, list[dict]]:
    """Per agency, the path the rover believes it drove: estimate if nav gave one, else
    odometry. `reading` is kept if the log carries one per step."""
    tracks: dict[str, list[dict]] = {}
    for step in log.get("steps", []):
        for ag, r in (step.get("rovers") or {}).items():
            if not r:
                continue
            est = r.get("estimate")
            odom = r.get("odom_pose")
            if est is not None:
                x, y, src = est[0], est[1], "estimate"
            elif odom is not None:
                x, y, src = odom[0], odom[1], "odometry"
            else:
                continue
            tracks.setdefault(ag, []).append({
                "t": step.get("t", 0.0), "x": float(x), "y": float(y),
                "sigma": float(r.get("sigma") or 0.0), "source": src,
                "nav_on": r.get("nav_on"),
                # run_demo.py logs the per-step reading as `ice_reading` (handoffs.md).
                "reading": r.get("ice_reading", r.get("reading")),
            })
    return tracks


def build_results(log: dict, m: Model | None = None) -> dict:
    m = m or Model()
    run_id = log["run_id"]
    tracks = tracks_from_log(log)

    rovers = {}
    for ag, tr in sorted(tracks.items()):
        pts = [[round(p["t"], 3), round(p["x"], 3), round(p["y"], 3), round(p["sigma"], 3)]
               for p in tr]
        path = {"columns": ["t", "x", "y", "sigma"], "points": pts}
        chain_pts = downsample(pts, m.chain_waypoints)
        rovers[ag] = {
            "path_source": sorted({p["source"] for p in tr}),
            "nav_on": sorted({str(p["nav_on"]) for p in tr}),
            "n_points": len(pts),
            "length_m": round(path_length([p[1:3] for p in pts]), 2),
            "max_sigma_m": round(max((p[3] for p in pts), default=0.0), 3),
            "final_sigma_m": pts[-1][3] if pts else None,
            "path": path,
            "path_hash": keccak_hex(path),
            "chain_path": {"n": len(chain_pts), "xy": [p[1:3] for p in chain_pts]},
        }

    # Finds and checks from the step events.
    finds: dict[Any, dict] = {}
    order: list[Any] = []
    for step in log.get("steps", []):
        for ev in step.get("events") or []:
            if ev.get("type") == "find":
                fid = ev["find_id"]
                lat, lon = stereo_to_latlon(ev["x"], ev["y"], m.moon_radius_m)
                finds[fid] = {
                    "find_id": fid, "agency": ev["agency"], "t": step.get("t"),
                    "x": ev["x"], "y": ev["y"], "sigma": ev["sigma"],
                    "lat": round(lat, 6), "lon": round(lon, 6),
                    "reading_wt_pct": ev["reading"], "tx": ev.get("tx"),
                    "checks": [],
                }
                order.append(fid)
            elif ev.get("type") == "check":
                fid = ev["find_id"]
                if fid in finds:
                    finds[fid]["checks"].append({
                        "agency": ev["agency"], "t": step.get("t"), "x": ev["x"],
                        "y": ev["y"], "sigma": ev["sigma"], "reading_wt_pct": ev["reading"],
                        "agrees": bool(ev["agrees"]), "tx": ev.get("tx"),
                    })

    out_finds = []
    for fid in order:
        f = finds[fid]
        radius, how = deposit_extent(f, tracks.get(f["agency"], []), m)
        f["ice"] = {**ice_quantity(f["reading_wt_pct"], radius, m), "extent_from": how}
        f["volatiles_ppm"] = volatiles_ppm(f["reading_wt_pct"], m)
        bulk = f["ice"]["area_m2"] * f["ice"]["depth_m"] * m.regolith_density_kg_m3
        f["volatiles_kg"] = {s: round(v * 1e-6 * bulk, 2) for s, v in f["volatiles_ppm"].items()}
        n_a = sum(c["agrees"] for c in f["checks"])
        n_d = sum(not c["agrees"] for c in f["checks"])
        f["status"] = status_of(n_a, n_d)
        assay = {k: f[k] for k in ("find_id", "agency", "x", "y", "sigma", "lat", "lon",
                                   "reading_wt_pct", "ice", "volatiles_ppm")}
        assay["run_id"] = run_id
        f["assay_hash"] = keccak_hex(assay)
        out_finds.append(f)

    totals = {
        "n_finds": len(out_finds),
        "by_status": {s: sum(f["status"] == s for f in out_finds)
                      for s in ("reported", "confirmed", "disputed")},
        "ice_mass_kg": round(sum(f["ice"]["mass_kg"] for f in out_finds), 1),
        "confirmed_ice_mass_kg": round(sum(f["ice"]["mass_kg"] for f in out_finds
                                           if f["status"] == "confirmed"), 1),
        "volatiles_kg": {s: round(sum(f["volatiles_kg"][s] for f in out_finds), 2)
                         for s in VOLATILES},
        "distance_m": {ag: r["length_m"] for ag, r in rovers.items()},
    }

    results = {
        "schema": SCHEMA,
        "run_id": run_id,
        "label": log.get("label", ""),
        "terrain": log.get("terrain", {}),
        "frame": {"xy": "south polar stereographic metres, MOON_ME [PGDA]",
                  "latlon": "degrees, sphere R = moon_radius_m"},
        "model": {**asdict(m), "sources": __doc__.split("Sources:")[1].split("Usage:")[0].strip()},
        "volatile_species": list(VOLATILES),
        "rovers": rovers,
        "finds": out_finds,
        "totals": totals,
        "truth": copy.deepcopy(log.get("truth", {})),
    }
    results["results_hash"] = keccak_hex(hashed_body(results))
    return results


def rehash(results: dict) -> dict:
    """Recompute every assay hash and results_hash, after find ids or tx hashes change."""
    for f in results["finds"]:
        assay = {k: f[k] for k in ("find_id", "agency", "x", "y", "sigma", "lat", "lon",
                                   "reading_wt_pct", "ice", "volatiles_ppm")}
        assay["run_id"] = results["run_id"]
        f["assay_hash"] = keccak_hex(assay)
    results["results_hash"] = keccak_hex(hashed_body(results))
    return results


def write_results(results: dict, run_dir: Path) -> Path:
    out = run_dir / "results.json"
    out.write_text(json.dumps(results, indent=1, sort_keys=False))
    return out


def summary(results: dict) -> str:
    t = results["totals"]
    lines = [f"{results['run_id']}  {results['label']}",
             f"  results_hash {results['results_hash']}"]
    for ag, r in results["rovers"].items():
        lines.append(f"  rover {ag}: {r['length_m']:.0f} m, {r['n_points']} pts "
                     f"({'/'.join(r['path_source'])}), max sigma {r['max_sigma_m']:.2f} m")
    for f in results["finds"]:
        lines.append(f"  find {f['find_id']} by {f['agency']}: ({f['x']:.1f}, {f['y']:.1f}) "
                     f"+-{f['sigma']:.1f} m = {f['lat']:.4f}, {f['lon']:.4f}  "
                     f"{f['reading_wt_pct']:.2f} wt%  {f['ice']['mass_kg']:.0f} kg H2O  "
                     f"[{f['status']}]")
    lines.append(f"  total {t['ice_mass_kg']:.0f} kg H2O, confirmed {t['confirmed_ice_mass_kg']:.0f} kg; "
                 f"H2S {t['volatiles_kg']['H2S']:.0f} kg, NH3 {t['volatiles_kg']['NH3']:.0f} kg, "
                 f"CO2 {t['volatiles_kg']['CO2']:.0f} kg")
    return "\n".join(lines)


# ------------------------------------------------------------------ synthetic log

def synthetic_log(run_id="selftest-run1", nav_on=True) -> dict:
    """A small log in the handoffs.md format: A drives east and finds ice, B checks it."""
    x0, y0 = 73250.0, 36000.0
    steps = []
    ice = (x0 + 60.0, y0 + 40.0)
    for k in range(121):
        t = k * 10.0
        ax, ay = x0 + k * 1.0, y0 + 40.0
        bx, by = x0 + 60.0, y0 + k * 0.5
        sig_a = 0.8 if nav_on else 0.8 + 0.02 * k
        ra = max(0.0, 5.6 * math.exp(-((ax - ice[0]) ** 2 + (ay - ice[1]) ** 2) / (2 * 4.0 ** 2)))
        steps.append({"t": t, "rovers": {
            "A": {"true_pose": [ax, ay, 0.0], "odom_pose": [ax, ay, 0.0],
                  "estimate": [ax + 0.3, ay - 0.2] if nav_on else None, "sigma": sig_a,
                  "nav_on": nav_on, "reading": round(ra, 3)},
            "B": {"true_pose": [bx, by, 1.57], "odom_pose": [bx, by, 1.57],
                  "estimate": [bx, by], "sigma": 0.9, "nav_on": True, "reading": None},
        }, "events": []})
    steps[60]["events"].append({"type": "find", "agency": "A", "find_id": 0, "x": ice[0] + 0.3,
                                "y": ice[1] - 0.2, "sigma": steps[60]["rovers"]["A"]["sigma"],
                                "reading": 5.6, "tx": None})
    steps[80]["events"].append({"type": "check", "agency": "B", "find_id": 0, "x": ice[0],
                                "y": ice[1], "sigma": 0.9, "reading": 5.4, "agrees": True,
                                "tx": None})
    return {"run_id": run_id,
            "label": "Selftest: crater fixes on" if nav_on else "Selftest: A on odometry only",
            "terrain": {"file": "synthetic", "x0": x0, "y0": y0, "cell": 0.25},
            "chain": {}, "truth": {"ice": [{"x": ice[0], "y": ice[1], "radius": 8.0, "peak": 5.6}]},
            "steps": steps}


def _selftest() -> int:
    res = build_results(synthetic_log())
    print(summary(res))
    f = res["finds"][0]
    lat, lon = stereo_to_latlon(0.0, -100_000.0, 1_737_400.0)
    # 100 km from the pole is 100/1737.4 rad = 3.298 deg of arc, so about -86.70 deg.
    assert abs(lon - 180.0) < 1e-9 and abs(lat - (-90 + math.degrees(1e5 / 1_737_400.0))) < 0.01, (lat, lon)
    assert f["status"] == "confirmed"
    assert f["ice"]["radius_m"] > Model().footprint_radius_m, f["ice"]
    # The hash ignores truth and chain, and catches any other change.
    h = res["results_hash"]
    res2 = copy.deepcopy(res)
    res2["truth"] = {"tampered": True}
    res2["chain"] = {"tx": "0x1"}
    assert keccak_hex(hashed_body(res2)) == h
    res2["finds"][0]["reading_wt_pct"] = 9.9
    assert keccak_hex(hashed_body(res2)) != h
    print("selftest ok")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build results.json from a run log")
    b.add_argument("run", help="data/runs/<run_id> or its log.json")
    sub.add_parser("selftest")
    a = ap.parse_args(argv)
    if a.cmd == "selftest":
        return _selftest()
    log, run_dir = load_log(a.run)
    res = build_results(log)
    out = write_results(res, run_dir)
    print(summary(res))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
