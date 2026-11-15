"""JSON storage for a mission: everything a run produced, in plain files.

One folder per mission, under LANTERN_OUT (default the git-ignored data/):

    data/missions/<mission_id>/
      mission.json        manifest: what ran, with which planner and terrain,
                          per-rover summary, what is real and what is a stand-in
      telemetry.jsonl     one line per sample: pose, estimate, sigma, battery,
                          comms, mode (the digital twin's history)
      events.jsonl        one line per event: depart, arrive, survey_flag,
                          new_target, science, fault, abort, docked, ...
      survey.jsonl        every neutron spectrometer reading taken while driving
      resource_map.json   survey readings binned into a grid: the resource map
      targets.json        the target board at the end, with each one's status
      science/records/    one JSON record per science stop (sim/science.py)
      outbox.json         chain calls waiting to be posted, in order, with
                          dependencies and the earliest time Earth is in view
      run_log.json        the run in docs/handoffs.md "Run log" format, for
                          the web viewer
      HIDDEN_TRUTH.json   the deposits. For the viewer's labelled reveal only.
      map.png             a top-down picture of the whole mission

Every file is JSON or JSON Lines, written atomically (temp file, then
rename), so a crash never leaves half a file. `MissionStore.latest()` opens
the newest mission; `push_outbox()` posts the outbox with the step 1 client.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import resources  # noqa: E402
import science  # noqa: E402

MISSIONS_DIR = resources.OUT / "missions"
SCHEMA = "lantern.mission/1"
# A find that is not on chain yet is referred to as FIND_REF + its record id.
# Safe in file names on Windows and macOS (no colon).
FIND_REF = "rec_"


def _write_json(path, obj):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, allow_nan=False))
    os.replace(tmp, path)


class MissionStore:
    def __init__(self, mission_id=None, root=MISSIONS_DIR, create=True):
        self.id = mission_id or time.strftime("mission-%Y%m%d-%H%M%S")
        self.dir = Path(root) / self.id
        if create:
            self.dir.mkdir(parents=True, exist_ok=True)
        elif not self.dir.exists():
            raise FileNotFoundError(self.dir)
        self.science = science.ScienceStore(self.dir / "science")
        self._files = {}

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
        ids = cls.list(root)
        if not ids:
            raise FileNotFoundError(f"no missions in {root}")
        return cls.open(ids[-1], root)

    # ---- writing --------------------------------------------------------
    def _append(self, name, row):
        f = self._files.get(name)
        if f is None:
            f = self._files[name] = (self.dir / name).open("a")
        f.write(json.dumps(row, allow_nan=False) + "\n")

    def telemetry(self, row):
        self._append("telemetry.jsonl", row)

    def event(self, row):
        self._append("events.jsonl", row)

    def survey(self, row):
        self._append("survey.jsonl", row)

    def write(self, name, obj):
        _write_json(self.dir / name, obj)

    def flush(self):
        for f in self._files.values():
            f.flush()

    def close(self):
        for f in self._files.values():
            f.close()
        self._files = {}

    # ---- reading --------------------------------------------------------
    def read(self, name):
        return json.loads((self.dir / name).read_text())

    def read_lines(self, name):
        p = self.dir / name
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]

    # ---- outbox -----------------------------------------------------------
    def outbox(self):
        p = self.dir / "outbox.json"
        return json.loads(p.read_text()) if p.exists() else []

    def save_outbox(self, items):
        self.write("outbox.json", items)


def outbox_items_for(record, health):
    """Outbox entries for one science record: the safety zone (posted first,
    so other rovers keep clear), then the find or check. Each waits for
    Earth contact: `earliest_post_t` is the next time the rover can talk."""
    items = []
    t_ready = health.next_contact(record["t"])
    c = record["chain"]
    sz = c.get("safety_zone")
    if sz:
        items.append(dict(id=f"{record['record_id']}:zone", call=sz["call"], args=sz["args"],
                          record_id=record["record_id"], depends_on=None,
                          created_t=record["t"], earliest_post_t=round(t_ready, 1),
                          status="pending", tx=None, result=None))
    if c["call"]:
        dep = None
        args = dict(c["args"])
        if c["call"] == "check_find" and str(args.get("find_id", "")).startswith(FIND_REF):
            dep = args["find_id"][len(FIND_REF):]   # the find this checks, by record id
        items.append(dict(id=f"{record['record_id']}:{c['call']}", call=c["call"], args=args,
                          record_id=record["record_id"], depends_on=dep,
                          created_t=record["t"], earliest_post_t=round(t_ready, 1),
                          status="pending", tx=None, result=None))
    return items


def push_outbox(store, client=None, dry_run=False):
    """Post the outbox in order with the step 1 client (sim/chain.py
    LanternClient). A check waits for the find it checks: its find_id is
    filled in from that find's result. With dry_run, print the calls only.
    Returns the list of items it posted (or would post)."""
    items = store.outbox()
    by_record = {it["record_id"]: it for it in items if it["call"] == "post_find"}
    order = sorted(items, key=lambda i: (i["earliest_post_t"], i["created_t"]))
    done, seen = [], set()
    # Passes until nothing more can go: a check whose find was queued later
    # (its rover was out of contact) goes on the pass after its find.
    while True:
        ready = [it for it in order if it["id"] not in seen and it["status"] != "posted"
                 and _dep_ready(it, by_record, seen, dry_run)]
        if not ready:
            break
        for it in ready:
            seen.add(it["id"])
            _post_one(it, by_record, store, client, dry_run, done, items)
    for it in order:
        if it["status"] != "posted" and it["id"] not in seen:
            it["status"] = "waiting"   # its find is not on chain
    if not dry_run:
        store.save_outbox(items)
    return done


def _dep_ready(it, by_record, seen, dry_run):
    if not it["depends_on"]:
        return True
    find = by_record.get(it["depends_on"])
    if find is None:
        return False
    return find["status"] == "posted" or (dry_run and find["id"] in seen)


def _post_one(it, by_record, store, client, dry_run, done, items):
    args = dict(it["args"])
    if it["depends_on"]:
        find = by_record[it["depends_on"]]
        args["find_id"] = find["result"] if find["status"] == "posted" else f"<find of {it['depends_on']}>"
    if "hold_s" in args:   # safety zone: the contract wants unix seconds, in the future
        args["until"] = int(time.time() + args.pop("hold_s"))
    if dry_run:
        print(f"  t>={it['earliest_post_t']:>9.0f}  {it['call']}({_fmt(args)})")
        done.append(it)
        return
    result = getattr(client, it["call"])(**args)
    log = getattr(client, "tx_log", None)
    it.update(status="posted", result=result,
              tx=log[-1]["tx_hash"] if log else (result if isinstance(result, str) else None))
    if it["call"] in ("post_find", "check_find"):
        r = store.science.load(it["record_id"])
        r["chain"].update(status="posted", tx=it["tx"])
        if it["call"] == "post_find":
            r["chain"]["find_id"] = result
        store.science._path(it["record_id"]).write_text(json.dumps(r, indent=1))
    done.append(it)
    store.save_outbox(items)    # after every post, so a crash loses nothing


def _fmt(args):
    return ", ".join(f"{k}={v!r}" if not isinstance(v, float) else f"{k}={v:g}"
                     for k, v in args.items())
