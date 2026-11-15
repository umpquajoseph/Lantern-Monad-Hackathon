"""Evidence: reports and routes stored off-chain, addressed by their own hash.

Lantern keeps three layers of data, each for what it is good at:

  A. chain (Monad, sim/chain.py)   who reported what, when, and a 32-byte
                                   fingerprint of the evidence. Nothing bulky.
  B. evidence (this file)          the reports and routes themselves, as
                                   immutable JSON objects named by their hash.
  C. cache (sim/mission_db.py)     telemetry, events, outbox, chain index. Fast,
                                   local, rebuildable; never the authority.

How A and B link, with no change to the deployed contract: a find's on-chain
`dataHash` is keccak256 of the report's canonical JSON bytes, and the
report's IPFS address is a CIDv1 built from that same digest (codec raw
0x55, multihash keccak-256 0x1b). So anyone holding only the chain can
rebuild the CID, fetch the report, re-hash it, and see that it matches. The
32 bytes on chain are both the integrity check and the pointer.

Objects:
  report  one science stop: mission, site, rover, observation, position
          (world and lat/lon), measurements with units, verdict, confidence,
          route reference, parent report (for a check), simulation flag,
          data sources. No hidden truth, ever.
  route   the rover's driven route to the site, thinned (Douglas-Peucker)
          to within ROUTE_TOLERANCE_M of what it drove. Stored apart from the
          report, so a checking rover can fetch only the route.

Storage backends:
  local   data/evidence/objects/<cid>.json. Write-once: putting the same
          bytes again is a no-op, putting different bytes under a CID is
          impossible. Labelled "local, not pinned": demo data that lives on
          this machine only.
  ipfs    if IPFS_API is set (a Kubo node, e.g. http://127.0.0.1:5001), each
          object is also added there with the same hash settings, and the
          node's CID must equal the local one or the put fails. UNTESTED in
          this repo: no IPFS node was available when it was written.

Usage:
    python sim/evidence.py check    # self-checks: CID encoding, write-once,
                                    # tamper detection, route thinning
    python sim/evidence.py get CID  # print an object, verified
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import resources  # noqa: E402  (for OUT, the git-ignored output root)

EVIDENCE_DIR = resources.OUT / "evidence"
SCHEMA_REPORT = "lantern.report/1"
SCHEMA_ROUTE = "lantern.route/1"

# Multiformats codes (multiformats/multicodec table.csv).
CODEC_RAW = 0x55
MH_SHA2_256 = 0x12
MH_KECCAK_256 = 0x1B

# ASSUMED. A stored route stays within this distance of the driven one.
ROUTE_TOLERANCE_M = 0.25
# IPFS adds a file as one raw block up to its chunk size (256 KiB by
# default); bigger files become a DAG and get a different CID. Objects here
# are kept under that, so the local CID is the CID IPFS gives.
MAX_SINGLE_BLOCK = 256 * 1024


class TamperError(Exception):
    """Stored or fetched bytes do not match the hash in their address."""


# --------------------------------------------------------------------------
# Hashes and CIDs.
# --------------------------------------------------------------------------

def canonical_bytes(obj) -> bytes:
    """The one serialisation everything hashes: sorted keys, no spaces,
    UTF-8, no NaN. The same rules as sim/chain.py canonical_json."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def keccak256(data: bytes) -> bytes:
    try:
        from web3 import Web3
    except ImportError as e:  # sim/chain.py needs web3 too
        raise RuntimeError("keccak256 needs web3: pip install web3") from e
    return bytes(Web3.keccak(data))


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def cid_v1(digest: bytes, mh_code: int, codec: int = CODEC_RAW) -> str:
    """CIDv1, base32 lower-case multibase ("b..."). All codes here are < 128,
    so each varint is one byte."""
    raw = bytes([0x01, codec, mh_code, len(digest)]) + digest
    return "b" + base64.b32encode(raw).decode().lower().rstrip("=")


def parse_cid(cid: str):
    """(codec, multihash code, digest) from a base32 CIDv1."""
    if not cid.startswith("b"):
        raise ValueError(f"not a base32 CIDv1: {cid}")
    s = cid[1:].upper()
    raw = base64.b32decode(s + "=" * (-len(s) % 8))
    if raw[0] != 0x01 or raw[3] != len(raw) - 4:
        raise ValueError(f"unsupported CID layout: {cid}")
    return raw[1], raw[2], raw[4:]


def cid_from_data_hash(data_hash) -> str:
    """The report's CID, rebuilt from a find's on-chain dataHash alone."""
    if isinstance(data_hash, str):
        data_hash = bytes.fromhex(data_hash.removeprefix("0x"))
    return cid_v1(bytes(data_hash), MH_KECCAK_256)


def data_hash_from_cid(cid: str) -> str:
    codec, mh, digest = parse_cid(cid)
    if mh != MH_KECCAK_256:
        raise ValueError("only keccak-256 CIDs map to an on-chain dataHash")
    return "0x" + digest.hex()


# --------------------------------------------------------------------------
# Route thinning.
# --------------------------------------------------------------------------

def thin_route(points, tol=ROUTE_TOLERANCE_M):
    """Douglas-Peucker: the fewest points whose polyline stays within `tol`
    metres of every original point. Straight drives collapse to their ends."""
    pts = [tuple(map(float, p)) for p in points]
    if len(pts) < 3:
        return [list(p) for p in pts]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        worst, wi = -1.0, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            if L2 == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
                d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
            if d > worst:
                worst, wi = d, i
        if worst > tol:
            keep[wi] = True
            stack += [(a, wi), (wi, b)]
    return [[round(p[0], 2), round(p[1], 2)] for p, k in zip(pts, keep) if k]


def max_deviation(original, thinned):
    """Largest distance from any original point to the thinned polyline."""
    th = [tuple(p) for p in thinned]
    worst = 0.0
    for px, py in original:
        best = math.inf
        for (ax, ay), (bx, by) in zip(th[:-1], th[1:]):
            dx, dy = bx - ax, by - ay
            L2 = dx * dx + dy * dy
            t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
            best = min(best, math.hypot(px - (ax + t * dx), py - (ay + t * dy)))
        worst = max(worst, best)
    return worst


# --------------------------------------------------------------------------
# The store.
# --------------------------------------------------------------------------

class EvidenceStore:
    def __init__(self, root=EVIDENCE_DIR):
        self.root = Path(root)
        self.objects = self.root / "objects"
        self.objects.mkdir(parents=True, exist_ok=True)
        self.ipfs_api = os.environ.get("IPFS_API")
        self.storage = ("local + IPFS node " + self.ipfs_api) if self.ipfs_api \
            else "local, not pinned (demo data on this machine only)"

    def _path(self, cid):
        return self.objects / f"{cid}.json"

    def put(self, obj, kind):
        """Store an object; return its reference. Idempotent."""
        data = canonical_bytes(obj)
        if len(data) > MAX_SINGLE_BLOCK:
            raise ValueError(f"{kind} is {len(data)} bytes, over one IPFS block; thin it first")
        k = keccak256(data)
        cid = cid_v1(k, MH_KECCAK_256)
        p = self._path(cid)
        if p.exists():
            if p.read_bytes() != data:   # cannot happen unless the file was edited
                raise TamperError(f"{p} does not match its CID")
        else:
            tmp = p.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, p)
        ref = {"cid": cid, "data_hash": "0x" + k.hex(), "sha256": "0x" + sha256(data).hex(),
               "bytes": len(data), "kind": kind, "storage": self.storage}
        if self.ipfs_api:
            ref["ipfs"] = self._ipfs_add(data, cid)
        return ref

    def get_bytes(self, cid):
        """The object's bytes, verified against its CID. Local first, then
        the IPFS node or gateway if configured."""
        p = self._path(cid)
        if p.exists():
            data = p.read_bytes()
        elif self.ipfs_api or os.environ.get("IPFS_GATEWAY"):
            data = self._ipfs_cat(cid)
        else:
            raise FileNotFoundError(f"{cid} is not in {self.objects} and no IPFS is configured")
        self.verify(cid, data)
        return data

    def get(self, cid):
        return json.loads(self.get_bytes(cid))

    @staticmethod
    def verify(cid, data: bytes):
        codec, mh, digest = parse_cid(cid)
        got = keccak256(data) if mh == MH_KECCAK_256 else sha256(data) if mh == MH_SHA2_256 else None
        if got is None:
            raise ValueError(f"unsupported multihash 0x{mh:x}")
        if got != digest:
            raise TamperError(f"bytes do not hash to {cid}")
        return True

    # ---- IPFS (Kubo HTTP API). UNTESTED: no node was available. ------------
    def _ipfs_add(self, data, want_cid):
        import urllib.request
        boundary = "lantern-evidence-boundary"
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"o.json\"\r\n"
                "Content-Type: application/octet-stream\r\n\r\n").encode() + data + \
            f"\r\n--{boundary}--\r\n".encode()
        url = (f"{self.ipfs_api.rstrip('/')}/api/v0/add?cid-version=1&raw-leaves=true"
               "&hash=keccak-256&pin=true")
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=30) as r:
            got = json.loads(r.read().decode().strip().splitlines()[-1])["Hash"]
        if got != want_cid:
            raise TamperError(f"IPFS node returned {got}, expected {want_cid}")
        return {"pinned": True, "node": self.ipfs_api}

    def _ipfs_cat(self, cid):
        import urllib.request
        if self.ipfs_api:
            req = urllib.request.Request(f"{self.ipfs_api.rstrip('/')}/api/v0/cat?arg={cid}", method="POST")
        else:
            req = urllib.request.Request(f"{os.environ['IPFS_GATEWAY'].rstrip('/')}/ipfs/{cid}")
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read()

    def size(self):
        files = list(self.objects.glob("*.json"))
        return len(files), sum(f.stat().st_size for f in files)


# --------------------------------------------------------------------------
# Building the objects.
# --------------------------------------------------------------------------

DATA_SOURCES = [
    "terrain: NASA LOLA 5 m/px south pole tile near Shoemaker (Barker et al. 2021, PGDA product 78), "
    "with simulated small craters (sim/terrain.py)",
    "deposits and all measurements: SIMULATED (sim/resources.py, sim/science.py); not NASA data",
    "instrument and rover numbers: PROSPECT Team Brief, RASC-AL 2027 Theme 3 (docs/sources/)",
]


def make_route(mission_id, agency, target_id, est_track, t_end, sigma=0.0):
    """The route object: what the rover drove to reach the site, by its own
    estimate (the only position it knows), thinned.

    Thinned to the larger of ROUTE_TOLERANCE_M and the rover's own position
    sigma: the estimate wanders by about sigma anyway, so finer detail is
    noise, and storing it would cost bytes and mislead a reader."""
    pts = [list(map(float, p)) for p in est_track]
    tol = max(ROUTE_TOLERANCE_M, float(sigma))
    thin = thin_route(pts, tol)
    return {
        "schema": SCHEMA_ROUTE, "mission_id": mission_id, "rover": agency, "site_id": target_id,
        "kind": "completed traversed route", "frame": "world metres, x east, y north",
        "position_source": "rover estimate", "position_sigma_m": round(float(sigma), 2),
        "simulated": True, "t_end": round(float(t_end), 1),
        "tolerance_m": round(tol, 2), "points_driven": len(pts), "points": thin,
    }


def make_report(mission_id, record, route_ref, parent_ref=None):
    """The report object from a science record (sim/science.py). Everything a
    reader needs, nothing hidden."""
    p, s, r = record["position"], record["sample"], record["result"]
    observation = "check of another agency's find" if record["target"]["purpose"] == "check" \
        else "survey drill"
    return {
        "schema": SCHEMA_REPORT, "mission_id": mission_id, "report_id": record["record_id"],
        "site_id": record["target"]["id"], "site_source": record["target"].get("source"),
        "rover": record["agency"], "observation": observation, "simulated": True,
        "t": record["t"],
        "position": {k: p[k] for k in ("x", "y", "sigma", "lat", "lon", "frame", "source")},
        "measurements": {
            "instrument": s["instrument"], "drill_depth_m": s["drill_depth_m"],
            "water_wt_pct_by_depth": [[b["depth_m"], b["water_wt_pct"]] for b in s["profile"]],
            "water_wt_pct_max": s["water_wt_pct"], "ilmenite_wt_pct": s["ilmenite_wt_pct"],
            "regolith_class": s["regolith_class"],
            "units": {"depth": "m", "water": "wt% water", "ilmenite": "wt%"},
        },
        "result": {"verdict": r["verdict"], "reading_wt_pct": r["reading"],
                   "ice_top_m": r.get("ice_top_m"), "summary": r["summary"],
                   "confidence": confidence(record)},
        "drill_attempts": record.get("attempts", 1),
        "refs": {"route": route_ref["cid"], "parent_report": parent_ref},
        "data_sources": DATA_SOURCES,
    }


def confidence(record):
    """How sure the verdict is, from the core itself: the layer's margin over
    the threshold in units of the instrument noise. Not a probability of ice
    being there; a measure of how clearly this core says what it says."""
    import science
    w = [b["water_wt_pct"] for b in record["sample"]["profile"]]
    thr, noise = science.ICE_THRESHOLD_WT_PCT, science.WATER_NOISE_WT_PCT
    if record["result"]["verdict"] == "ice":
        margin = (record["result"]["reading"] - thr) / noise
    else:
        margin = (thr - max(w)) / noise
    return {"margin_sigma": round(margin, 2),
            "level": "high" if margin >= 3 else "medium" if margin >= 1 else "low",
            "basis": "core reading vs the 1 wt% threshold, in units of instrument noise (simulated)"}


# --------------------------------------------------------------------------
# Self-checks.
# --------------------------------------------------------------------------

def check():
    import tempfile
    ok = True

    def say(name, cond, detail=""):
        nonlocal ok
        ok &= bool(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")

    print("evidence self-checks")
    # Known vector: CIDv1 raw sha2-256 of the empty file, as `ipfs add
    # --cid-version 1 --raw-leaves` gives it.
    empty = cid_v1(sha256(b""), MH_SHA2_256)
    say("CID encoding matches IPFS for the empty file",
        empty == "bafkreihdwdcefgh4dqkjv67uzcmw7ojee6xedzdetojuzjevtenxquvyku", empty)
    k = keccak256(b"abc")
    say("keccak256('abc') is Ethereum's keccak",
        k.hex() == "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45")
    c = cid_v1(k, MH_KECCAK_256)
    say("dataHash -> CID -> dataHash round-trips", data_hash_from_cid(c) == "0x" + k.hex(), c)
    with tempfile.TemporaryDirectory() as d:
        st = EvidenceStore(d)
        ref1 = st.put({"b": 1, "a": [1.5, "x"]}, "test")
        ref2 = st.put({"a": [1.5, "x"], "b": 1}, "test")
        say("same content, any key order -> same CID, stored once",
            ref1["cid"] == ref2["cid"] and st.size()[0] == 1)
        say("get returns the object, verified", st.get(ref1["cid"]) == {"a": [1.5, "x"], "b": 1})
        p = st._path(ref1["cid"])
        p.write_bytes(p.read_bytes().replace(b"1.5", b"9.5"))
        try:
            st.get(ref1["cid"])
            say("an edited object is refused on read", False)
        except TamperError:
            say("an edited object is refused on read", True)
    import random
    rnd = random.Random(1)
    pts, x, y, h = [], 0.0, 0.0, 0.0
    for _ in range(600):   # 300 m of 0.5 m steps with gentle turns
        h += rnd.uniform(-0.02, 0.02)
        x, y = x + 0.5 * math.cos(h), y + 0.5 * math.sin(h)
        pts.append((x, y))
    th = thin_route(pts)
    dev = max_deviation(pts, th)
    say(f"route thinning keeps within {ROUTE_TOLERANCE_M} m", dev <= ROUTE_TOLERANCE_M + 0.006,
        f"{len(pts)} -> {len(th)} points, worst {dev:.3f} m")
    print("all passed" if ok else "SOME FAILED")
    return ok


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "check":
        sys.exit(0 if check() else 1)
    if len(sys.argv) >= 3 and sys.argv[1] == "get":
        print(json.dumps(EvidenceStore().get(sys.argv[2]), indent=1))
        return
    print(__doc__)


if __name__ == "__main__":
    main()
