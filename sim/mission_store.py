"""Mission storage: the three layers, behind one object the mission writes to.

  evidence  (sim/evidence.py)    reports and routes, content-addressed,
                                 write-once, verified on every read
  cache     (sim/mission_db.py)  data/lantern.db: telemetry, events, survey,
                                 reports index, outbox, chain index
  chain     (sim/chain.py)       posted by push_outbox(), from the outbox

Plus a small folder per mission for people and the viewer:

    data/missions/<mission_id>/
      mission.json        manifest: what ran, planner, terrain, stand-ins,
                          per-rover summary, storage sizes
      reports/<id>.json   the local view of each science stop: its evidence
                          refs (CIDs), chain call, and the hidden truth that
                          never goes into evidence
      outbox.json         the outbox as JSON, rewritten after every push
      targets.json        the target board at the end
      resource_map.json   the survey binned into a grid
      run_log.json        the run in docs/handoffs.md "Run log" format
      HIDDEN_TRUTH.json   the deposits, for the viewer's labelled reveal only
      map.png             the mission from above

What goes where, and why:
  - Nothing bulky goes on chain: a find posts position, sigma, reading and
    the 32-byte dataHash. The dataHash IS the report's address (see
    sim/evidence.py), so the chain needs no CID field.
  - Routes are thinned to 0.25 m and stored once as their own object; a
    report holds the route's CID, not its points.
  - Same bytes, same CID: storing an identical object twice costs nothing.
  - Telemetry and survey readings never leave the cache; they are not
    evidence of a find.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evidence as EV  # noqa: E402
import mission_db  # noqa: E402
import resources  # noqa: E402

MISSIONS_DIR = resources.OUT / "missions"
SCHEMA = "lantern.mission/2"
# A find that is not on chain yet is referred to as FIND_REF + its record id.
# Safe in file names on Windows and macOS (no colon).
FIND_REF = "rec_"


def _write_json(path, obj):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, allow_nan=False))
    os.replace(tmp, path)


class MissionStore:
    def __init__(self, mission_id=None, root=MISSIONS_DIR, create=True, db=None, evidence=None):
        self.id = mission_id or time.strftime("mission-%Y%m%d-%H%M%S")
        self.dir = Path(root) / self.id
        if create:
            (self.dir / "reports").mkdir(parents=True, exist_ok=True)
        elif not self.dir.exists():
            raise FileNotFoundError(self.dir)
        self.db = db or mission_db.MissionDB()
        self.ev = evidence or EV.EvidenceStore()
        if create:
            self.db.clear_mission(self.id)

    # ---- opening ------------------------------------------------------
    @classmethod
    def list(cls, root=MISSIONS_DIR):
        root = Path(root)
        return sorted(p.name for p in root.iterdir() if (p / "mission.json").exists()) \
            if root.exists() else []

    @classmethod
    def open(cls, mission_id, root=MISSIONS_DIR):
        return cls(mission_id, root, create=False)

    @classmethod
    def latest(cls, root=MISSIONS_DIR):
        ids = sorted(cls.list(root), key=lambda m: (Path(root) / m / "mission.json").stat().st_mtime)
        if not ids:
            raise FileNotFoundError(f"no missions in {root}")
        return cls.open(ids[-1], root)

    # ---- high-frequency rows: cache only ---------------------------------
    def telemetry(self, row):
        self.db.telemetry(self.id, row)

    def event(self, row):
        self.db.event(self.id, row)

    def survey(self, row):
        self.db.survey(self.id, row)

    def commit(self):
        self.db.commit()

    # ---- a science stop: evidence, index, outbox ---------------------------
    def publish(self, record, est_track, parent_report_cid=None):
        """Split a science record into its evidence objects and index it.

        route object  -> evidence (thinned estimated track to the site)
        report object -> evidence (refs the route, and the parent report
                         for a check); its keccak is the find's dataHash
        local view    -> reports/<id>.json, with the hidden truth
        """
        route = EV.make_route(self.id, record["agency"], record["target"]["id"], est_track, record["t"],
                              sigma=record["position"]["sigma"])
        route_ref = self.ev.put(route, "route")
        report = EV.make_report(self.id, record, route_ref, parent_report_cid)
        report_ref = self.ev.put(report, "report")
        self.db.evidence(self.id, route_ref)
        self.db.evidence(self.id, report_ref)
        record["evidence"] = {"report": report_ref, "route": route_ref,
                              "route_points_driven": route["points_driven"],
                              "route_points_stored": len(route["points"])}
        record["data_hash"] = report_ref["data_hash"]
        record["hash_alg"] = "keccak256 of the report object's canonical JSON (its CID digest)"
        if record["chain"]["call"] == "post_find":
            record["chain"]["args"]["data_hash"] = report_ref["data_hash"]
        sz = record["chain"].get("safety_zone")
        if sz:   # the zone names its report: unique, so a re-push can find it, and a pointer to the evidence
            sz["args"]["purpose"] = f"drilling at {record['target']['id']}; report {report_ref['cid']}"
        record["route"] = {"length_m": record["route"]["length_m"], "ref": route_ref["cid"],
                           "points": route["points"]}   # thinned, for the local view and preview
        path = self.dir / "reports" / f"{record['record_id']}.json"
        _write_json(path, record)
        self.db.report(self.id, record, path)
        return record

    def queue(self, items):
        for it in items:
            self.db.queue(self.id, it)

    # ---- small JSON files --------------------------------------------------
    def write(self, name, obj):
        _write_json(self.dir / name, obj)

    def read(self, name):
        return json.loads((self.dir / name).read_text())

    def load_report(self, record_id):
        return json.loads((self.dir / "reports" / f"{record_id}.json").read_text())

    def reports(self):
        recs = [json.loads(p.read_text()) for p in (self.dir / "reports").glob("*.json")]
        return sorted(recs, key=lambda r: r["t"])

    def export_outbox(self):
        items = self.db.outbox(self.id)
        self.write("outbox.json", items)
        return items

    def close(self):
        self.db.commit()


def outbox_items_for(record, health):
    """Outbox entries for one science record: the safety zone (posted first,
    so other rovers keep clear), then the find or check. Each waits for
    Earth contact: `earliest_post_t` is the next time the rover can talk.
    Ids are stable (record id + call), so queuing twice queues once."""
    items = []
    t_ready = health.next_contact(record["t"])
    c = record["chain"]
    sz = c.get("safety_zone")
    if sz:
        items.append(dict(id=f"{record['record_id']}:zone", call=sz["call"], args=sz["args"],
                          record_id=record["record_id"], depends_on=None,
                          created_t=record["t"], earliest_post_t=round(t_ready, 1)))
    if c["call"]:
        dep = None
        args = dict(c["args"])
        if c["call"] == "check_find" and str(args.get("find_id", "")).startswith(FIND_REF):
            dep = args["find_id"][len(FIND_REF):]   # the find this checks, by record id
        items.append(dict(id=f"{record['record_id']}:{c['call']}", call=c["call"], args=args,
                          record_id=record["record_id"], depends_on=dep,
                          created_t=record["t"], earliest_post_t=round(t_ready, 1)))
    return items


def push_outbox(store, client=None, dry_run=False, reconcile=True):
    """Post the outbox with the step 1 client (sim/chain.py LanternClient).

    Safe to run again after a crash or a failed call:
      - with `reconcile`, the contract's finds are read first and matched to
        local reports by dataHash, so a find that reached the chain is never
        posted twice, even if the cache lost track of it;
      - a call that fails is marked `failed` with its error and retried on
        the next push; the checks that depend on it wait;
      - a check gets its find's real on-chain id, and goes on the pass after
        its find (a rover out of contact can queue a find late).
    With dry_run, print the calls in order and change nothing.
    """
    db = store.db
    store.checks_on_chain, store.zones_on_chain = set(), set()
    store.chain_key = str(getattr(client, "address", None) or "unknown").lower()
    store.reconciled = False   # only an index refreshed by this push is trusted
    if reconcile and client is not None and not dry_run and hasattr(client, "get_finds"):
        n = db.reconcile(client.get_finds(), store.chain_key)
        store.reconciled = True
        if n:
            print(f"  reconcile: {n} local find(s) were already on chain; not posting them again")
        if hasattr(client, "get_checks"):   # a check has no hash on chain: match (find, agency)
            store.checks_on_chain = {(int(c["find_id"]), str(c["agency"])) for c in client.get_checks()}
        if hasattr(client, "get_zones"):    # a zone's purpose names its report's CID
            store.zones_on_chain = {(str(z["agency"]), z["purpose"]) for z in client.get_zones()}
    items = db.outbox(store.id)
    finds = {it["record_id"]: it for it in items if it["call"] == "post_find"}
    seen, done = set(), []
    while True:
        ready = [it for it in items if it["id"] not in seen and it["status"] != "posted"
                 and _dep_ready(it, finds, seen, dry_run)]
        if not ready:
            break
        for it in ready:
            seen.add(it["id"])
            if _post_one(it, finds, store, client, dry_run):
                done.append(it)
    for it in items:
        if it["status"] not in ("posted", "failed") and it["id"] not in seen and not dry_run:
            db.mark(store.id, it["id"], "waiting")
    if not dry_run:
        store.export_outbox()
    return done


def _dep_ready(it, finds, seen, dry_run):
    if not it["depends_on"]:
        return True
    f = finds.get(it["depends_on"])
    if f is None:
        return False
    return f["status"] == "posted" or (dry_run and f["id"] in seen)


def _post_one(it, finds, store, client, dry_run):
    db = store.db
    args = dict(it["args"])
    if it["depends_on"]:
        f = finds[it["depends_on"]]
        args["find_id"] = f["result"] if f["status"] == "posted" else f"<find of {it['depends_on']}>"
    if "hold_s" in args:   # safety zone: the contract wants unix seconds, in the future
        args["until"] = int(time.time() + args.pop("hold_s"))
    if dry_run:
        print(f"  t>={it['earliest_post_t']:>9.0f}  {it['call']}({_fmt(args)})")
        return True
    if it["call"] == "post_find":
        on_chain = db.find_on_chain(args["data_hash"], store.chain_key) if store.reconciled else None
        if on_chain is not None:   # already there: record it, send nothing
            it.update(status="posted", result=on_chain)
            db.mark(store.id, it["id"], "posted", result=on_chain)
            db.mark_report(store.id, it["record_id"], "posted", find_id=on_chain)
            return False
    if it["call"] == "post_safety_zone" and (args["agency"], args["purpose"]) in store.zones_on_chain:
        it.update(status="posted")
        db.mark(store.id, it["id"], "posted", error="already on chain (found by get_zones)")
        return False
    if it["call"] == "check_find" and (args["find_id"], args["agency"]) in store.checks_on_chain:
        it.update(status="posted")
        db.mark(store.id, it["id"], "posted", error="already on chain (found by get_checks)")
        db.mark_report(store.id, it["record_id"], "posted")
        return False
    try:
        result = getattr(client, it["call"])(**args)
    except Exception as e:   # keep going; this one retries on the next push
        if getattr(e, "name", None) == "AlreadyChecked":   # sim/chain.py LanternRefused
            it.update(status="posted")
            db.mark(store.id, it["id"], "posted", error="contract: AlreadyChecked")
            return False
        it["status"] = "failed"
        db.mark(store.id, it["id"], "failed", error=f"{type(e).__name__}: {e}")
        print(f"  FAILED {it['id']}: {type(e).__name__}: {e}")
        return False
    log = getattr(client, "tx_log", None)
    tx = log[-1]["tx_hash"] if log else (result if isinstance(result, str) else None)
    it.update(status="posted", result=result, tx=tx)
    db.mark(store.id, it["id"], "posted", tx=tx, result=result)
    if it["call"] in ("post_find", "check_find"):
        db.mark_report(store.id, it["record_id"], "posted", tx=tx,
                       find_id=result if it["call"] == "post_find" else None)
        p = store.dir / "reports" / f"{it['record_id']}.json"
        r = json.loads(p.read_text())
        r["chain"].update(status="posted", tx=tx)
        if it["call"] == "post_find":
            r["chain"]["find_id"] = result
        _write_json(p, r)
    return True



def _fmt(args):
    return ", ".join(f"{k}={v!r}" if not isinstance(v, float) else f"{k}={v:g}"
                     for k, v in args.items())
