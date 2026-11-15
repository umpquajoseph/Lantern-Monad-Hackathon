"""Layer C: the SQLite cache. Fast, local, rebuildable; never the authority.

One database, data/lantern.db, holds what changes often or is read often:

  missions      one row per mission
  telemetry     pose, estimate, odometry, battery, comms, mode (every 10 m)
  events        what happened, as JSON, indexed by mission, rover and time
  survey        every neutron reading
  reports       one row per science stop: verdict, report and route CIDs,
                dataHash, the find it checks, chain status
  evidence      every stored object: CID, dataHash, sha256, size, kind
  outbox        chain calls with status, attempts, last error, tx, result.
                The id is stable (record id + call), so a call is never
                queued twice, and a retry updates the same row.
  chain_finds   finds read from the contract, keyed by find id, so a restart
                rebuilds what is on chain from the chain itself

Why SQLite: the mission writes thousands of rows a minute. JSON Lines files
grow without an index, and rereading one to answer "where was rover B at
t = 9000 s" scans the whole file. Here those reads hit an index, writes are
batched in one transaction per leg, and WAL mode lets the viewer read while
the mission writes.

The authority for anything published is the chain plus the evidence store.
`reconcile(client)` reads every find from the contract and matches each to
a local report by its dataHash: a find the cache thought was pending, but
which is on chain, is marked posted with its real id. That is what makes a
retry after a crash safe.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import resources  # noqa: E402

DB_PATH = resources.OUT / "lantern.db"
# Bump when the schema changes. The cache is rebuildable, so an old one is
# dropped and recreated rather than migrated.
SCHEMA_VERSION = 3
TABLES = ("missions", "telemetry", "events", "survey", "reports", "evidence", "outbox", "chain_finds")

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
CREATE TABLE IF NOT EXISTS missions (
  mission_id TEXT PRIMARY KEY, created TEXT, seed INTEGER, terrain TEXT,
  planner TEXT, status TEXT, manifest TEXT);
CREATE TABLE IF NOT EXISTS telemetry (
  mission_id TEXT, agency TEXT, t REAL, x REAL, y REAL, heading REAL,
  est_x REAL, est_y REAL, sigma REAL, odom_x REAL, odom_y REAL,
  odometer_m REAL, battery_wh REAL, soc REAL, comms TEXT, mode TEXT);
CREATE INDEX IF NOT EXISTS telemetry_by_time ON telemetry (mission_id, agency, t);
CREATE TABLE IF NOT EXISTS events (
  mission_id TEXT, agency TEXT, t REAL, type TEXT, body TEXT);
CREATE INDEX IF NOT EXISTS events_by_time ON events (mission_id, t);
CREATE INDEX IF NOT EXISTS events_by_type ON events (mission_id, type);
CREATE TABLE IF NOT EXISTS survey (
  mission_id TEXT, agency TEXT, t REAL, x REAL, y REAL, sigma REAL, wt_pct REAL);
CREATE INDEX IF NOT EXISTS survey_by_place ON survey (mission_id, x, y);
CREATE TABLE IF NOT EXISTS reports (
  record_id TEXT, mission_id TEXT, agency TEXT, t REAL, site_id TEXT,
  purpose TEXT, verdict TEXT, reading REAL, x REAL, y REAL, sigma REAL,
  report_cid TEXT, route_cid TEXT, data_hash TEXT, checks_record TEXT,
  chain_call TEXT, chain_status TEXT, find_id INTEGER, tx TEXT, local TEXT,
  PRIMARY KEY (mission_id, record_id));
CREATE INDEX IF NOT EXISTS reports_by_hash ON reports (data_hash);
CREATE TABLE IF NOT EXISTS evidence (
  cid TEXT PRIMARY KEY, data_hash TEXT, sha256 TEXT, bytes INTEGER, kind TEXT,
  storage TEXT, mission_id TEXT, created REAL);
CREATE TABLE IF NOT EXISTS outbox (
  id TEXT, mission_id TEXT, record_id TEXT, call TEXT, args TEXT,
  depends_on TEXT, created_t REAL, earliest_post_t REAL, status TEXT,
  attempts INTEGER DEFAULT 0, last_error TEXT, tx TEXT, result TEXT, updated REAL,
  PRIMARY KEY (mission_id, id));
CREATE INDEX IF NOT EXISTS outbox_by_status ON outbox (mission_id, status);
CREATE TABLE IF NOT EXISTS chain_finds (
  chain TEXT, find_id INTEGER, agency TEXT, x REAL, y REAL, sigma REAL,
  reading REAL, data_hash TEXT, status TEXT, agree TEXT, disagree TEXT,
  time INTEGER, synced REAL, PRIMARY KEY (chain, find_id));
CREATE INDEX IF NOT EXISTS chain_finds_by_hash ON chain_finds (chain, data_hash);
"""


class MissionDB:
    def __init__(self, path=DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.c = sqlite3.connect(self.path)
        self.c.row_factory = sqlite3.Row
        if self.c.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            for t in TABLES:
                self.c.execute(f"DROP TABLE IF EXISTS {t}")
        self.c.executescript(SCHEMA)
        self.c.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    # ---- writing (callers batch with `with db.batch():`) ------------------
    def batch(self):
        return self.c   # sqlite3 connection as a context manager = one transaction

    def mission(self, mission_id, **kw):
        cols = ", ".join(["mission_id", *kw])
        q = ", ".join("?" * (len(kw) + 1))
        self.c.execute(f"INSERT OR REPLACE INTO missions ({cols}) VALUES ({q})",
                       (mission_id, *[json.dumps(v) if isinstance(v, (dict, list)) else v
                                      for v in kw.values()]))
        self.c.commit()

    def clear_mission(self, mission_id):
        """Rerunning a mission id replaces its rows, never adds to them."""
        with self.c:
            for t in ("telemetry", "events", "survey", "reports", "outbox"):
                self.c.execute(f"DELETE FROM {t} WHERE mission_id = ?", (mission_id,))

    def telemetry(self, mid, row):
        self.c.execute("INSERT INTO telemetry VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            mid, row["agency"], row["t"], row["x"], row["y"], row["heading"], row["est_x"],
            row["est_y"], row["sigma"], row["odom_x"], row["odom_y"], row["odometer_m"],
            row["battery_wh"], row["soc"], row["comms"], row["mode"]))

    def event(self, mid, row):
        body = {k: v for k, v in row.items() if k not in ("t", "agency", "type")}
        self.c.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                       (mid, row["agency"], row["t"], row["type"], json.dumps(body)))

    def survey(self, mid, row):
        self.c.execute("INSERT INTO survey VALUES (?,?,?,?,?,?,?)",
                       (mid, row["agency"], row["t"], row["x"], row["y"], row["sigma"], row["wt_pct"]))

    def evidence(self, mid, ref):
        self.c.execute("INSERT OR IGNORE INTO evidence VALUES (?,?,?,?,?,?,?,?)",
                       (ref["cid"], ref["data_hash"], ref["sha256"], ref["bytes"], ref["kind"],
                        ref["storage"], mid, time.time()))

    def report(self, mid, rec, local_path):
        ev, p, c = rec["evidence"], rec["position"], rec["chain"]
        self.c.execute("INSERT OR REPLACE INTO reports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            rec["record_id"], mid, rec["agency"], rec["t"], rec["target"]["id"],
            rec["target"]["purpose"], rec["result"]["verdict"], rec["result"]["reading"],
            p["x"], p["y"], p["sigma"], ev["report"]["cid"], ev["route"]["cid"],
            ev["report"]["data_hash"], rec.get("checks_record"), c["call"], c["status"],
            None, None, str(local_path)))

    def queue(self, mid, item):
        """Add an outbox item once. Re-queuing the same id changes nothing."""
        self.c.execute("INSERT OR IGNORE INTO outbox (id, mission_id, record_id, call, args, depends_on,"
                       " created_t, earliest_post_t, status, updated) VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (item["id"], mid, item["record_id"], item["call"], json.dumps(item["args"]),
                        item["depends_on"], item["created_t"], item["earliest_post_t"],
                        "pending", time.time()))

    def commit(self):
        self.c.commit()

    # ---- outbox state ------------------------------------------------------
    def outbox(self, mid):
        rows = self.c.execute("SELECT * FROM outbox WHERE mission_id = ? ORDER BY earliest_post_t, created_t",
                              (mid,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["args"] = json.loads(d["args"])
            d["result"] = json.loads(d["result"]) if d["result"] else None
            out.append(d)
        return out

    def mark(self, mid, item_id, status, tx=None, result=None, error=None):
        self.c.execute("UPDATE outbox SET status = ?, tx = COALESCE(?, tx), result = COALESCE(?, result),"
                       " last_error = ?, attempts = attempts + (? IS NOT NULL OR ? = 'posted'),"
                       " updated = ? WHERE mission_id = ? AND id = ?",
                       (status, tx, json.dumps(result) if result is not None else None, error,
                        error, status, time.time(), mid, item_id))
        self.c.commit()

    def mark_report(self, mid, record_id, status, tx=None, find_id=None):
        self.c.execute("UPDATE reports SET chain_status = ?, tx = COALESCE(?, tx),"
                       " find_id = COALESCE(?, find_id) WHERE mission_id = ? AND record_id = ?",
                       (status, tx, find_id, mid, record_id))
        self.c.commit()

    # ---- chain index and reconcile -----------------------------------------
    def reconcile(self, finds, chain="unknown"):
        """Replace the index of `chain` (a contract address) with finds just
        read from it (sim/chain.py get_finds() dicts), and match them to
        local reports by dataHash. Returns how many local reports this
        showed to be on chain already. The index is per contract and fully
        refreshed, so a stale or foreign index can never hide a find."""
        now, matched = time.time(), 0
        with self.c:
            self.c.execute("DELETE FROM chain_finds WHERE chain = ?", (chain,))
            for f in finds:
                dh = f.get("data_hash")
                dh = ("0x" + bytes(dh).hex()) if isinstance(dh, (bytes, bytearray)) else (dh or "").lower()
                self.c.execute("INSERT OR REPLACE INTO chain_finds VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    chain, f["id"], f["agency"], f["x"], f["y"], f["sigma"], f["reading"], dh,
                    f.get("status"), json.dumps(f.get("agree", [])), json.dumps(f.get("disagree", [])),
                    f.get("time"), now))
                # The dataHash covers the whole report, mission id included,
                # so it names one report in one mission.
                for r in self.c.execute("SELECT mission_id, record_id FROM reports WHERE lower(data_hash) = ?"
                                        " AND chain_status != 'posted'", (dh,)).fetchall():
                    self.c.execute("UPDATE reports SET chain_status = 'posted', find_id = ?"
                                   " WHERE mission_id = ? AND record_id = ?",
                                   (f["id"], r["mission_id"], r["record_id"]))
                    self.c.execute("UPDATE outbox SET status = 'posted', result = ?, last_error = NULL"
                                   " WHERE mission_id = ? AND record_id = ? AND call = 'post_find'",
                                   (json.dumps(f["id"]), r["mission_id"], r["record_id"]))
                    matched += 1
        return matched

    def find_on_chain(self, data_hash, chain="unknown"):
        r = self.c.execute("SELECT find_id FROM chain_finds WHERE chain = ? AND data_hash = ?",
                           (chain, data_hash.lower())).fetchone()
        return r["find_id"] if r else None

    # ---- reading -------------------------------------------------------------
    def events(self, mid, etype=None):
        q = "SELECT agency, t, type, body FROM events WHERE mission_id = ?"
        args = [mid]
        if etype:
            q += " AND type = ?"
            args.append(etype)
        rows = self.c.execute(q + " ORDER BY t, rowid", args).fetchall()
        return [dict(agency=r["agency"], t=r["t"], type=r["type"], **json.loads(r["body"])) for r in rows]

    def where(self, mid, agency, t):
        """The last telemetry sample at or before t: an indexed lookup."""
        r = self.c.execute("SELECT * FROM telemetry WHERE mission_id = ? AND agency = ? AND t <= ?"
                           " ORDER BY t DESC LIMIT 1", (mid, agency, t)).fetchone()
        return dict(r) if r else None

    def counts(self, mid):
        out = {}
        for t in ("telemetry", "events", "survey", "reports", "outbox"):
            out[t] = self.c.execute(f"SELECT COUNT(*) FROM {t} WHERE mission_id = ?", (mid,)).fetchone()[0]
        out["evidence"] = self.c.execute("SELECT COUNT(*) FROM evidence WHERE mission_id = ?",
                                         (mid,)).fetchone()[0]
        return out

    def close(self):
        self.c.commit()
        self.c.close()
