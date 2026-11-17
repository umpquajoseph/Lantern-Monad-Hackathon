"""Science at a target: arm out, drill, sample, result, stored for the chain.

When a rover arrives at a target (sim/resources.py), it parks, swings its arm
out, drills a core, and its mass spectrometer reads the core. The result is
one JSON record on disk, with everything the chain step needs to post it.

    record = run_science(agency, target, deposits, t,
                         true_pose, estimate, sigma,
                         est_track, true_track)
    store = ScienceStore()
    store.save(record)           # data/science/records/<record_id>.json
    store.pending()              # records not on chain yet
    store.mark_posted(record_id, tx_hash, find_id)

What a record holds:
  position   where the rover THINKS it is (its estimate and sigma), in world
             metres and as latitude and longitude. This is what is reported.
  route      the last ROUTE_LENGTH_M of the rover's estimated track.
  sample     the drill core's water profile with depth, and ilmenite.
  result     the verdict (ice, trace, dry) and the reading to post.
  phases     the timed steps of the arm and drill, for the animation.
  chain      the exact post_find / check_find call (docs/handoffs.md step 1),
             its status, and the tx hash once posted.
  HIDDEN_TRUTH  the true pose and route, for the viewer's labelled reveal
             only. Nothing here is posted.

Usage:
    python sim/science.py demo      # stand-in drive, one find, one check
    python sim/science.py list      # what is stored, and what is pending

Sources:
  [PB] PROSPECT Team Brief, RASC-AL 2027 Theme 3, 13 Oct 2026
       (docs/sources/PROSPECT-Team-Brief-RASC-AL-2027-Theme-3-1.pdf)
Every other number is a named parameter marked ASSUMED.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import resources  # noqa: E402
import terrain  # noqa: E402  (Joseph's step 2; read only)

SCIENCE_DIR = resources.OUT / "science"
SCHEMA = "lantern.science/1"

# ASSUMED. The route kept with each record: the last quarter kilometre.
ROUTE_LENGTH_M = 250.0
# ASSUMED. Route points are thinned to about this spacing before storing.
ROUTE_POINT_SPACING_M = 2.0

# [PB] p. 5: "1 m drill + mass spectrometer" measures "water and other gases
# from drilled samples".
DRILL_DEPTH_M = resources.DRILL_DEPTH_M
# ASSUMED. The core is read in bins this deep.
CORE_BIN_M = 0.1
# ASSUMED. Mass spectrometer noise on water, wt% (1-sigma), and on ilmenite.
WATER_NOISE_WT_PCT = 0.15
ILMENITE_NOISE_WT_PCT = 0.5
# ASSUMED, [PB] p. 9 calls ~1 wt% "the ice threshold we assumed" and asks for
# a source. Used by classify_sample.
ICE_THRESHOLD_WT_PCT = 1.0
# ASSUMED. Bins in a row over the threshold for a core to count as ice
# (3 x 10 cm = a 30 cm layer). Fewer is "trace".
MIN_ICE_LAYER_BINS = 3
# ASSUMED. A core whose mean water is at least this fraction of the threshold
# is "trace" even with no bin over it.
TRACE_FRACTION = 0.5
# ASSUMED. Radius of the safety zone a rover announces around its drill site
# while it works (Artemis Accords section 11, spec "Upgrades"), and how long.
SAFETY_ZONE_RADIUS_M = 10.0
SAFETY_ZONE_HOLD_S = 600.0

# ASSUMED. Seconds of each step. These are demo timings for the animation,
# far shorter than a real drill, and the viewer may play them faster still.
PHASES = (
    ("park", 2.0),          # stop, settle
    ("arm_deploy", 4.0),    # arm swings out and down to the ground
    ("drill", 10.0),        # bit spins down to DRILL_DEPTH_M, dust kicks up
    ("sample_lift", 3.0),   # core comes up
    ("analyze", 5.0),       # mass spectrometer reads it
    ("arm_stow", 4.0),      # arm back on the deck
)

# ASSUMED. Moon radius for the polar stereographic to lat/lon conversion. The
# LOLA polar stereographic products use a sphere; check this against the
# GeoTIFF's CRS before quoting a coordinate (_archive/docs/moon-ledger-spec.md, Part 4, item 4).
MOON_RADIUS_M = 1_737_400.0


# --------------------------------------------------------------------------
# Small pieces.
# --------------------------------------------------------------------------

def south_polar_to_latlon(x, y, R=MOON_RADIUS_M):
    """South polar stereographic (true scale at the pole) -> (lat, lon) deg."""
    rho = math.hypot(x, y)
    lat = math.degrees(2.0 * math.atan(rho / (2.0 * R)) - math.pi / 2)
    lon = math.degrees(math.atan2(x, y))
    return lat, lon


def last_route(track, length_m=ROUTE_LENGTH_M, spacing=ROUTE_POINT_SPACING_M):
    """The tail of `track` ((K, 2) xy) covering `length_m`, thinned to about
    `spacing`, oldest point first."""
    pts = np.asarray(track, float).reshape(-1, 2)
    if len(pts) < 2:
        return pts.tolist(), 0.0
    seg = np.hypot(*np.diff(pts, axis=0).T)
    back = np.concatenate([[0.0], np.cumsum(seg[::-1])])  # distance from the end
    keep = back <= length_m
    tail = pts[::-1][keep][::-1]
    out, run = [tail[0]], 0.0
    for a, b in zip(tail[:-1], tail[1:]):
        run += float(np.hypot(*(b - a)))
        if run >= spacing:
            out.append(b)
            run = 0.0
    if not np.array_equal(out[-1], tail[-1]):
        out.append(tail[-1])
    return np.round(np.array(out), 2).tolist(), float(min(back[keep].max(), length_m))


def canonical_json(obj):
    # Same rules as sim/chain.py canonical_json (step 1), so the hashes match.
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def canonical_hash(obj):
    """(0x hash, algorithm) of canonical JSON. keccak256 when web3 is
    installed, which is what sim/chain.py hash_raw() uses, so anyone can
    re-hash the stored sample and compare it with the chain. Without web3 it
    falls back to sha256 and says so."""
    blob = canonical_json(obj)
    try:
        from web3 import Web3
        return "0x" + Web3.keccak(text=blob).hex().removeprefix("0x"), "keccak256"
    except ImportError:
        return "0x" + hashlib.sha256(blob.encode()).hexdigest(), "sha256 (web3 not installed)"


def drill_core(deposits, x, y, rng):
    """Simulated core at TRUE (x, y): water by depth bin, and ilmenite."""
    c_ice, over = resources.concentration(deposits, "water_ice", x, y)
    c_ilm, _ = resources.concentration(deposits, "ilmenite", x, y)
    profile = []
    for k in range(int(round(DRILL_DEPTH_M / CORE_BIN_M))):
        top, bot = k * CORE_BIN_M, (k + 1) * CORE_BIN_M
        frac_ice = np.clip((bot - over) / CORE_BIN_M, 0.0, 1.0)  # part of the bin below the overburden
        w = c_ice * frac_ice + rng.normal(0, WATER_NOISE_WT_PCT)
        profile.append({"depth_m": round(bot, 2), "water_wt_pct": round(max(w, 0.0), 3)})
    p = resources.Params()
    ilm = round(max(p.ilmenite_background_wt_pct + c_ilm + rng.normal(0, ILMENITE_NOISE_WT_PCT), 0.0), 2)
    return {
        "instrument": "1 m drill + mass spectrometer (simulated)",
        "drill_depth_m": DRILL_DEPTH_M,
        "profile": profile,
        "water_wt_pct": round(max(b["water_wt_pct"] for b in profile), 3),
        "ilmenite_wt_pct": ilm,
        "regolith_class": resources.classify_regolith(ilm),
    }


# --------------------------------------------------------------------------
# The verdict. This is the rule that decides what goes on the chain.
# --------------------------------------------------------------------------

def classify_sample(sample, target):
    """Decide what a drill core means.

    sample: drill_core()'s dict. `profile` is a list of
            {"depth_m", "water_wt_pct"} from the surface down, each with
            WATER_NOISE_WT_PCT of noise; `water_wt_pct` is the profile's max.
    target: the target dict; target["kind"] is "water_ice" or "ilmenite".

    Return {"verdict": "ice" | "trace" | "dry", "reading": float,
            "ice_top_m": float | None}
      verdict  "ice" is a find (posted to chain, or a check that agrees).
               Anything else is not a find (a check that disagrees).
      reading  the number posted on chain as the find's reading.
      ice_top_m  depth where ice starts, or None.
    """
    # A find goes on a public record that no one can delete, so one noisy bin
    # must not make one. Ice needs MIN_ICE_LAYER_BINS bins in a row over the
    # threshold: a buried layer is continuous, noise is not. The reading is
    # that layer's mean, which is less noisy than the single highest bin.
    w = [b["water_wt_pct"] for b in sample["profile"]]
    best, run_start = (0, None), None
    for k, v in enumerate(w + [-1.0]):          # sentinel closes the last run
        if v >= ICE_THRESHOLD_WT_PCT:
            run_start = k if run_start is None else run_start
        elif run_start is not None:
            if k - run_start > best[0]:
                best = (k - run_start, run_start)
            run_start = None
    n, start = best
    if n >= MIN_ICE_LAYER_BINS:
        layer = w[start:start + n]
        return {"verdict": "ice", "reading": round(float(np.mean(layer)), 3),
                "ice_top_m": round(start * CORE_BIN_M, 2)}
    # Some hydrogen, but no layer: worth recording, not worth a claim.
    if n > 0 or np.mean(w) >= TRACE_FRACTION * ICE_THRESHOLD_WT_PCT:
        return {"verdict": "trace", "reading": round(float(max(w)), 3), "ice_top_m": None}
    return {"verdict": "dry", "reading": round(float(max(w)), 3), "ice_top_m": None}


# --------------------------------------------------------------------------
# One science stop.
# --------------------------------------------------------------------------

# ASSUMED. The Prospector's fuller instrument sequence: a near-infrared
# volatiles scan before the arm goes out, a longer drill and a longer read
# ([PB] p. 5 lists NIR volatiles spectrometer, drill and mass spectrometer).
PROSPECTOR_PHASES = (
    ("park", 2.0), ("nir_scan", 5.0), ("arm_deploy", 4.0), ("drill", 12.0),
    ("sample_lift", 3.0), ("analyze", 8.0), ("arm_stow", 4.0),
)


# ---- The Surveyor: no arm, no drill -----------------------------------------
# The Surveyor carries a mast camera and a neutron spectrometer, and leaves
# drilling to the Prospector. At a site it parks short of it, faces it, maps
# the ground with the camera, sits still for a neutron dwell, and takes a
# photo. A dwell averages many readings, so it is steadier than the readings
# taken on the move, but it still only sees hydrogen averaged over the top
# metre ([PB] p. 5): it says "worth drilling", the Prospector's core says how
# much and how deep.
# ASSUMED, demo timings, as PHASES.
SURVEY_PHASES = (
    ("park", 2.0),            # stop short of the site, facing it
    ("mast_pan", 6.0),        # camera pans across the site
    ("terrain_map", 10.0),    # stereo map of the ground: slopes, rocks
    ("neutron_dwell", 10.0),  # sit still and count neutrons
    ("photo", 3.0),           # a still of the site, for the report
)
# ASSUMED. A new obstacle on the way: stop, face it, map it, photograph it,
# flag it in the route so the next rover plans around it.
HAZARD_PHASES = (
    ("stop", 2.0), ("mast_pan", 5.0), ("terrain_map", 10.0), ("photo", 3.0), ("flag", 3.0),
)
# ASSUMED. Neutron readings per point in a dwell, and the points: the site
# centre and six around it at this radius.
DWELL_READINGS = 6
DWELL_RING_M = 3.0


def site_survey_sample(deposits, x, y, rng, terrain_stats):
    """The Surveyor's dwell at TRUE site centre (x, y), plus its camera's map
    of the ground. No core: nothing below the surface is sampled."""
    pts = [(0.0, 0.0)] + [(DWELL_RING_M * math.cos(a), DWELL_RING_M * math.sin(a))
                          for a in np.linspace(0, 2 * math.pi, 6, endpoint=False)]
    dwell = []
    for dx, dy in pts:
        w = np.mean([resources.neutron_reading(deposits, x + dx, y + dy, rng) for _ in range(DWELL_READINGS)])
        dwell.append([round(dx, 2), round(dy, 2), round(float(w), 3)])
    return {
        "kind": "site_survey",
        "instrument": "mast camera + neutron spectrometer dwell (simulated)",
        "dwell": dwell,
        "dwell_readings_per_point": DWELL_READINGS,
        "water_wt_pct": round(float(np.mean([d[2] for d in dwell])), 3),
        "water_wt_pct_peak": round(float(max(d[2] for d in dwell)), 3),
        "terrain": terrain_stats,
        "unit": "wt% water-equivalent hydrogen, top 1 m",
    }


def classify_survey(sample):
    """Neutron dwell -> verdict, with the same 1 wt% threshold as a core. The
    dwell mean over the site is the reading."""
    w = sample["water_wt_pct"]
    if w >= ICE_THRESHOLD_WT_PCT:
        return {"verdict": "ice", "reading": w, "ice_top_m": None}
    if w >= TRACE_FRACTION * ICE_THRESHOLD_WT_PCT:
        return {"verdict": "trace", "reading": w, "ice_top_m": None}
    return {"verdict": "dry", "reading": w, "ice_top_m": None}


def run_site_survey(agency, target, deposits, t, site_true, site_est, sigma, true_pose,
                    est_track, true_track, terrain_stats, seed=None):
    """The Surveyor's record for a site: same shape as run_science's, with a
    dwell and a terrain map in place of a core."""
    rng = np.random.default_rng(seed)
    sample = site_survey_sample(deposits, site_true[0], site_true[1], rng, terrain_stats)
    result = classify_survey(sample)
    data_hash, hash_alg = canonical_hash(sample)
    lat, lon = south_polar_to_latlon(site_est[0], site_est[1])
    route, route_len = last_route(est_track)
    true_route, _ = last_route(true_track)
    phases, t0 = [], float(t)
    for name, dur in SURVEY_PHASES:
        phases.append({"name": name, "t0": round(t0, 2), "dur": dur})
        t0 += dur
    x, y, s = round(float(site_est[0]), 2), round(float(site_est[1]), 2), round(float(sigma), 2)
    if result["verdict"] == "ice":
        chain = {"call": "post_find", "args": dict(agency=agency, x=x, y=y, sigma=s, reading=result["reading"],
                                                   data_hash=data_hash)}
    else:
        chain = {"call": None, "args": None}
    chain.update(status="pending" if chain["call"] else "not_for_chain", tx=None, find_id=target.get("find_id"))
    w = sample["water_wt_pct"]
    summary = {"ice": f"neutron dwell {w:.2f} wt% over the site: worth a core",
               "trace": f"neutron dwell {w:.2f} wt%: some hydrogen, below the threshold",
               "dry": f"neutron dwell {w:.2f} wt%: dry"}[result["verdict"]]
    return {
        "schema": SCHEMA,
        "record_id": f"{agency}-{target['id']}-t{int(round(t)):06d}",
        "agency": agency,
        "t": round(float(t), 2),
        "target": {k: target.get(k) for k in ("id", "kind", "purpose", "find_id", "source")},
        "position": {"x": x, "y": y, "sigma": s, "lat": round(lat, 6), "lon": round(lon, 6),
                     "frame": "world metres (south polar stereographic on LOLA)",
                     "source": "rover estimate plus the camera's range to the site"},
        "route": {"length_m": round(route_len, 1), "points": route, "source": "rover estimate"},
        "sample": sample,
        "result": dict(result, summary=summary),
        "phases": phases,
        "attempts": 1,
        "data_hash": data_hash,
        "hash_alg": f"{hash_alg} of canonical JSON (sorted keys) of `sample`",
        "chain": chain,
        "HIDDEN_TRUTH": {
            "true_pose": [round(float(v), 3) for v in true_pose],
            "site_true": [round(float(v), 3) for v in site_true],
            "position_error_m": round(math.hypot(site_true[0] - site_est[0], site_true[1] - site_est[1]), 2),
            "true_route": true_route,
        },
    }


def run_science(agency, target, deposits, t, true_pose, estimate, sigma,
                est_track, true_track, seed=None, attempts=1, phases_spec=PHASES):
    """Run the arm-and-drill sequence at a target and return the record.

    true_pose  (x, y, heading): where the drill really is. Used ONLY to read
               the hidden deposits and for HIDDEN_TRUTH.
    estimate   (x, y) and sigma: where the rover thinks it is. This is what
               the record reports and what goes on chain.
    est_track, true_track: (K, 2) xy histories, oldest first.
    """
    rng = np.random.default_rng(seed)
    sample = drill_core(deposits, true_pose[0], true_pose[1], rng)
    result = classify_sample(sample, target)
    data_hash, hash_alg = canonical_hash(sample)
    lat, lon = south_polar_to_latlon(estimate[0], estimate[1])
    route, route_len = last_route(est_track)
    true_route, _ = last_route(true_track)

    phases, t0 = [], float(t)
    for name, dur in phases_spec:
        phases.append({"name": name, "t0": round(t0, 2), "dur": dur})
        t0 += dur

    x, y, s = round(float(estimate[0]), 2), round(float(estimate[1]), 2), round(float(sigma), 2)
    if target.get("purpose") == "check":
        chain = {"call": "check_find", "args": dict(
            agency=agency, find_id=target["find_id"], x=x, y=y, sigma=s,
            reading=result["reading"], agrees=result["verdict"] == "ice")}
    elif result["verdict"] == "ice":
        chain = {"call": "post_find", "args": dict(
            agency=agency, x=x, y=y, sigma=s, reading=result["reading"],
            data_hash=data_hash)}
    else:
        chain = {"call": None, "args": None}
    chain.update(status="pending" if chain["call"] else "not_for_chain",
                 tx=None, find_id=target.get("find_id"))
    # Announced while drilling, so other rovers route around the site.
    # The contract's `until` is unix seconds and must be in the future when
    # posted (contracts/src/Lantern.sol, ZoneAlreadyEnded), so the args carry
    # how long to hold it; the outbox sets until = post time + hold_s.
    chain["safety_zone"] = {"call": "post_safety_zone", "args": dict(
        agency=agency, x=x, y=y, radius=SAFETY_ZONE_RADIUS_M, hold_s=SAFETY_ZONE_HOLD_S,
        purpose=f"drilling at target {target['id']}"),
        "until_sim_t": round(phases[-1]["t0"] + phases[-1]["dur"] + SAFETY_ZONE_HOLD_S, 1),
        "status": "pending", "tx": None}

    record_id = f"{agency}-{target['id']}-t{int(round(t)):06d}"
    return {
        "schema": SCHEMA,
        "record_id": record_id,
        "agency": agency,
        "t": round(float(t), 2),
        "target": {k: target.get(k) for k in ("id", "kind", "purpose", "find_id", "source")},
        "position": {"x": x, "y": y, "sigma": s, "lat": round(lat, 6), "lon": round(lon, 6),
                     "frame": "world metres (south polar stereographic on LOLA)",
                     "source": "rover estimate"},
        "route": {"length_m": round(route_len, 1), "points": route, "source": "rover estimate"},
        "sample": sample,
        "result": dict(result, summary=summary_line(result, sample, target)),
        "phases": phases,
        "attempts": attempts,
        "data_hash": data_hash,
        "hash_alg": f"{hash_alg} of canonical JSON (sorted keys) of `sample`",
        "chain": chain,
        "HIDDEN_TRUTH": {
            "true_pose": [round(float(v), 3) for v in true_pose],
            "position_error_m": round(math.hypot(true_pose[0] - estimate[0],
                                                 true_pose[1] - estimate[1]), 2),
            "true_route": true_route,
        },
    }


def summary_line(result, sample, target):
    if result["verdict"] == "ice":
        s = f"Water ice: {result['reading']:.2f} wt% in the core"
        if result.get("ice_top_m") is not None:
            s += f", starting {result['ice_top_m'] * 100:.0f} cm down"
    elif result["verdict"] == "trace":
        s = f"Trace water: {result['reading']:.2f} wt%, below the find threshold"
    else:
        s = f"Dry: {sample['water_wt_pct']:.2f} wt% water, no find"
    if target.get("kind") == "ilmenite":
        s += f". Ilmenite {sample['ilmenite_wt_pct']:.1f} wt%, {sample['regolith_class']}"
    return s


def run_log_event(record):
    """A compact `science` event for the run log's `events` list. The viewer
    plays `phases` as the arm-and-drill animation and links the record."""
    return {"type": "science", "agency": record["agency"], "record_id": record["record_id"],
            "target_id": record["target"]["id"], "x": record["position"]["x"],
            "y": record["position"]["y"], "sigma": record["position"]["sigma"],
            "verdict": record["result"]["verdict"], "reading": record["result"]["reading"],
            "summary": record["result"]["summary"], "phases": record["phases"],
            "chain_call": record["chain"]["call"], "observation": record.get("observation"),
            "kind": record["target"].get("kind"),
            "report_cid": (record.get("evidence") or {}).get("report", {}).get("cid"),
            "route_cid": (record.get("evidence") or {}).get("route", {}).get("cid"),
            "data_hash": record.get("data_hash")}


# --------------------------------------------------------------------------
# Storage: where the chain step finds what to post.
# --------------------------------------------------------------------------

class ScienceStore:
    """One JSON file per record under data/science/records/, plus an
    append-only index.jsonl of every save and post, oldest first."""

    def __init__(self, root=SCIENCE_DIR):
        self.root = Path(root)
        self.records = self.root / "records"
        self.records.mkdir(parents=True, exist_ok=True)
        self.index = self.root / "index.jsonl"

    def _path(self, record_id):
        return self.records / f"{record_id}.json"

    def _log(self, **entry):
        with self.index.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    def save(self, record):
        self._path(record["record_id"]).write_text(json.dumps(record, indent=1))
        self._log(event="saved", record_id=record["record_id"], t=record["t"],
                  chain_status=record["chain"]["status"])
        return self._path(record["record_id"])

    def load(self, record_id):
        return json.loads(self._path(record_id).read_text())

    def all(self):
        recs = [json.loads(p.read_text()) for p in self.records.glob("*.json")]
        return sorted(recs, key=lambda r: r["t"])

    def pending(self):
        """Records waiting for the chain, oldest first. Post finds before the
        checks that point at them."""
        return [r for r in self.all() if r["chain"]["status"] == "pending"]

    def mark_posted(self, record_id, tx, find_id=None):
        r = self.load(record_id)
        r["chain"].update(status="posted", tx=tx)
        if find_id is not None:
            r["chain"]["find_id"] = find_id
        self._path(record_id).write_text(json.dumps(r, indent=1))
        self._log(event="posted", record_id=record_id, tx=tx, find_id=find_id)
        return r


def push_pending(chain, store=None):
    """Post every pending record with the step 1 client (sim/chain.py).

    `chain` is the module or object with post_find / check_find from
    docs/handoffs.md step 1. A check whose target is a stand-in find id
    (from the demo) is skipped, because that find is not on chain.
    """
    store = store or ScienceStore()
    done = []
    for r in store.pending():
        c, a = r["chain"]["call"], r["chain"]["args"]
        if c == "post_find":
            find_id = chain.post_find(a["agency"], a["x"], a["y"], a["sigma"],
                                      a["reading"], a["data_hash"])
            log = getattr(chain, "tx_log", None)   # LanternClient keeps one (sim/chain.py)
            done.append(store.mark_posted(r["record_id"], tx=log[-1]["tx_hash"] if log else None,
                                          find_id=find_id))
        elif c == "check_find":
            if str(a["find_id"]).startswith("standin"):
                continue
            tx = chain.check_find(a["agency"], a["find_id"], a["x"], a["y"], a["sigma"],
                                  a["reading"], a["agrees"])
            done.append(store.mark_posted(r["record_id"], tx=tx))
    return done


# --------------------------------------------------------------------------
# Demo: a STAND-IN drive so this file can be seen working on its own.
# The real planner (step 4) and filter (step 3) replace the drive below.
# --------------------------------------------------------------------------

def _standin_drive(start, goal, step=0.5):
    """STAND-IN, not the planner: a straight line from start to goal."""
    d = math.hypot(goal[0] - start[0], goal[1] - start[1])
    n = max(2, int(d / step))
    return np.linspace(start, goal, n)


def demo(seed=3):
    rng = np.random.default_rng(seed)
    deposits, board = resources.load_site()
    store = ScienceStore()
    sig_a = 0.8  # STAND-IN filter: true pose plus noise at this sigma
    # Rover A starts 300 m from a water ice target, so the route has 250 m.
    ice_targets = [t for t in board.open("water_ice")]
    tgt = ice_targets[0]
    ang = rng.uniform(0, 2 * math.pi)
    start = (tgt["x"] + 300 * math.cos(ang), tgt["y"] + 300 * math.sin(ang))
    nxt = board.nearest(*start, kind="water_ice", agency="A")
    board.claim(nxt["id"], "A")
    true_tr = _standin_drive(start, (nxt["x"], nxt["y"]))
    est_tr = true_tr + rng.normal(0, sig_a, true_tr.shape)
    t = len(true_tr) * 5.0  # STAND-IN clock: 0.5 m per 5 s, 10 cm/s
    heading = math.atan2(*(true_tr[-1] - true_tr[-2])[::-1])
    rec_a = run_science("A", nxt, deposits, t, (*true_tr[-1], heading),
                        est_tr[-1], sig_a, est_tr, true_tr, seed=seed)
    board.complete(nxt["id"], rec_a["record_id"])
    store.save(rec_a)
    print_record(rec_a)

    # Rover B checks A's find, if A made one.
    if rec_a["result"]["verdict"] == "ice":
        find = dict(id="standin-1", agency="A", x=rec_a["position"]["x"],
                    y=rec_a["position"]["y"], sigma=rec_a["position"]["sigma"])
        chk = board.add_check_target(find)
        board.claim(chk["id"], "B")
        b_start = (chk["x"] - 120.0, chk["y"] + 40.0)
        true_b = _standin_drive(b_start, (chk["x"], chk["y"]))
        est_b = true_b + rng.normal(0, 0.9, true_b.shape)
        rec_b = run_science("B", chk, deposits, t + 600, (*true_b[-1], 0.0),
                            est_b[-1], 0.9, est_b, true_b, seed=seed + 1)
        board.complete(chk["id"], rec_b["record_id"])
        store.save(rec_b)
        print_record(rec_b)

    # A copy for the animation preview (science/preview.html).
    out = Path(__file__).resolve().parent.parent / "science" / "sample_record.json"
    out.write_text(json.dumps(rec_a, indent=1))
    print(f"\nstored in {store.records}\npreview copy {out}")


def print_record(r):
    p, c = r["position"], r["chain"]
    print(f"\n[{r['record_id']}] target {r['target']['id']} ({r['target']['kind']}, "
          f"{r['target']['purpose']})")
    print(f"  at x {p['x']:.1f} y {p['y']:.1f} +/- {p['sigma']} m   "
          f"lat {p['lat']:.5f} lon {p['lon']:.5f}")
    print(f"  route: last {r['route']['length_m']:.0f} m, {len(r['route']['points'])} points")
    print(f"  {r['result']['summary']}")
    print(f"  chain: {c['call']} -> {c['status']}")
    print(f"  hidden truth: position error {r['HIDDEN_TRUTH']['position_error_m']} m")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo")
    sub.add_parser("list")
    a = ap.parse_args()
    if a.cmd == "demo":
        demo()
    else:
        s = ScienceStore()
        for r in s.all():
            print(f"{r['record_id']:28s} {r['result']['verdict']:6s} "
                  f"{r['chain']['call'] or '-':11s} {r['chain']['status']}")
        print(f"{len(s.pending())} pending for the chain")


if __name__ == "__main__":
    main()
