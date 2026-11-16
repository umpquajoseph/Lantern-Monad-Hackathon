// Live, read-only reader of the Lantern contract on Monad testnet.
//
// It reads the record straight from the chain in the judge's browser: every find with
// its status and who checked it, every check, every safety zone, and (as the event logs
// are fetched) the transaction hash behind each one. No keys, nothing is signed, nothing
// reads .env. Address, RPC and ABI come from web/chain/lantern_deployment.json and
// web/chain/lantern_abi.json, copies made by `python web/collect_chain.py` from sim/.
//
// Standalone page: web/chain.html. To wire it into the viewer (index.html, main.js), the
// smallest useful change is a live line under #chain-hint:
//
//     import { LanternReader } from "./chain.js";
//     const live = await LanternReader.load("chain/");
//     live.poll((s) => {
//       $("chain-hint").textContent = `live on Monad testnet: ${s.finds.length} finds, `
//         + `${s.finds.filter(f => f.status === "confirmed").length} confirmed, block ${s.block}`;
//     });
//
// or a replay/live toggle on #feed that renders `s.checks` and `s.finds` with the same
// .ev / .find markup main.js already uses. `renderFeed(snapshot, explorer)` below gives the
// .ev rows ready to drop in.
//
// Units follow sim/chain.py: centimetres on chain become metres, hundredths of wt% become
// wt%, the Status enum becomes "reported" | "confirmed" | "disputed", and an agency's
// address becomes its letter by its index in agencies() (A is 0, B is 1).

import { ethers } from "https://cdn.jsdelivr.net/npm/ethers@6.13.4/dist/ethers.min.js";

export const STATUS = ["reported", "confirmed", "disputed"]; // Lantern.Status order

// The public RPC refuses eth_getLogs over more than 100 blocks ("block range too large"
// at 200, fine at 100; measured 2026-11-15). Logs are fetched in chunks of this many
// blocks. While the history is still being backfilled the page polls fast, a handful of
// chunks per tick, which stays well under the RPC's 20 requests per second; once the
// logs are complete it settles to one poll every POLL_MS.
export const LOGS_MAX_BLOCKS = 100;
export const LOGS_CHUNKS_PER_POLL = 8;
export const POLL_MS = 4000;
export const BACKFILL_MS = 400;

async function fetchJson(url) {
  const r = await fetch(url, { cache: "no-cache" });
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}

const num = (v) => Number(v);            // int64 / uint64 BigInt -> JS number (positions in cm fit easily)
const cm = (v) => Number(v) / 100;        // centimetres -> metres
const wt = (v) => Number(v) / 100;        // hundredths of wt% -> wt%

export class LanternReader {
  /** Load the deployment record and ABI from `base` (relative to the page) and connect. */
  static async load(base = "chain/") {
    const [deployment, abi] = await Promise.all([
      fetchJson(base + "lantern_deployment.json"),
      fetchJson(base + "lantern_abi.json"),
    ]);
    return new LanternReader(deployment, abi);
  }

  constructor(deployment, abi) {
    this.deployment = deployment;
    this.address = deployment.address;
    this.chainId = deployment.chainId;
    this.explorer = deployment.explorer || null;
    this.deployBlock = deployment.deployBlock || 0;
    // staticNetwork: no eth_chainId round trip per call. ethers batches concurrent calls
    // into one JSON-RPC batch request, which the public RPC accepts.
    this.provider = new ethers.JsonRpcProvider(deployment.rpcUrl, this.chainId, { staticNetwork: true });
    this.contract = new ethers.Contract(this.address, abi, this.provider);
    this.letters = new Map();   // lowercased address -> "A" | "B" | ...
    this.names = new Map();     // lowercased address -> display name
    this.agencyList = null;
    // Transaction hashes from the event logs, filled in as syncLogs() walks the chain.
    this.txs = { find: new Map(), check: new Map(), zone: new Map() };
    this.head = null;           // highest block whose logs are in; null before the first poll
    this.tail = null;           // lowest block backfilled so far
    this.logsError = null;
    this._counts = null;
    this._pages = { finds: [], checks: [], zones: [] };
  }

  // ------------------------------------------------------------------ urls
  txUrl(hash) { return this.explorer && hash ? `${this.explorer}/tx/${hash}` : null; }
  addressUrl(addr = this.address) { return this.explorer ? `${this.explorer}/address/${addr}` : null; }

  // ------------------------------------------------------------- agencies
  async agencies() {
    if (this.agencyList) return this.agencyList;
    const [addrs, names] = await this.contract.agencies();
    this.agencyList = addrs.map((a, i) => {
      const letter = String.fromCharCode(65 + i);
      this.letters.set(a.toLowerCase(), letter);
      this.names.set(a.toLowerCase(), names[i]);
      return { letter, name: names[i], address: a };
    });
    return this.agencyList;
  }

  letterOf(addr) { return this.letters.get(String(addr).toLowerCase()) || String(addr); }

  // --------------------------------------------------------------- counts
  /** One batched request: find, check and zone counts plus the current block. */
  async counts() {
    const [finds, checks, zones, block] = await Promise.all([
      this.contract.findCount(), this.contract.checkCount(), this.contract.zoneCount(),
      this.provider.getBlockNumber(),
    ]);
    return { finds: num(finds), checks: num(checks), zones: num(zones), block: num(block) };
  }

  // ---------------------------------------------------------------- pages
  async finds(n) {
    if (n === 0) return [];
    const res = await this.contract.getFinds(0, n);
    const [finds, agree, disagree, statuses] = [res[0], res[1], res[2], res[3]];
    return Array.from(finds, (f, i) => ({
      id: i,
      agency: this.letterOf(f[0]),
      address: f[0],
      x: cm(f[1]), y: cm(f[2]), sigma: cm(f[3]),
      reading: wt(f[4]),
      data_hash: f[5],
      time: num(f[6]),
      agree: Array.from(agree[i], (a) => this.letterOf(a)),
      disagree: Array.from(disagree[i], (a) => this.letterOf(a)),
      status: STATUS[num(statuses[i])],
      tx: this.txs.find.get(i)?.tx || null,
      block: this.txs.find.get(i)?.block || null,
    }));
  }

  async checks(n) {
    if (n === 0) return [];
    const checks = await this.contract.getChecks(0, n);
    return Array.from(checks, (c, i) => ({
      id: i,
      find_id: num(c[0]),
      agency: this.letterOf(c[1]),
      address: c[1],
      x: cm(c[2]), y: cm(c[3]), sigma: cm(c[4]),
      reading: wt(c[5]),
      agrees: Boolean(c[6]),
      time: num(c[7]),
      tx: this.txs.check.get(i)?.tx || null,
      block: this.txs.check.get(i)?.block || null,
      status_after: this.txs.check.get(i)?.status || null, // from the FindChecked event
    }));
  }

  async zones(n) {
    if (n === 0) return [];
    const zones = await this.contract.getZones(0, n);
    const now = Date.now() / 1000;
    return Array.from(zones, (z, i) => ({
      id: i,
      agency: this.letterOf(z[0]),
      address: z[0],
      x: cm(z[1]), y: cm(z[2]), radius: cm(z[3]),
      until: num(z[4]),
      purpose: z[5],
      time: num(z[6]),
      active: num(z[4]) > now,
      tx: this.txs.zone.get(i)?.tx || null,
    }));
  }

  // ----------------------------------------------------------------- logs
  /** Walk the event logs: new blocks since the last poll first, then backfill toward the
   *  deploy block, at most `maxChunks` eth_getLogs requests per call. */
  async syncLogs(latest, maxChunks = LOGS_CHUNKS_PER_POLL) {
    let chunks = 0;
    try {
      if (this.head === null) {
        this.head = latest;
        this.tail = latest + 1;
      } else {
        for (let from = this.head + 1; from <= latest && chunks < maxChunks; from += LOGS_MAX_BLOCKS) {
          const to = Math.min(from + LOGS_MAX_BLOCKS - 1, latest);
          await this._logs(from, to);
          this.head = to;
          chunks++;
        }
      }
      while (this.tail > this.deployBlock && chunks < maxChunks) {
        const to = this.tail - 1;
        const from = Math.max(to - LOGS_MAX_BLOCKS + 1, this.deployBlock);
        await this._logs(from, to);
        this.tail = from;
        chunks++;
      }
      this.logsError = null;
    } catch (e) {
      this.logsError = e.shortMessage || e.message || String(e);
    }
    return { head: this.head, tail: this.tail, complete: this.tail <= this.deployBlock, error: this.logsError };
  }

  async _logs(fromBlock, toBlock) {
    const logs = await this.provider.getLogs({ address: this.address, fromBlock, toBlock });
    for (const log of logs) {
      let parsed;
      try { parsed = this.contract.interface.parseLog({ topics: [...log.topics], data: log.data }); }
      catch { continue; }
      if (!parsed) continue;
      const rec = { tx: log.transactionHash, block: log.blockNumber };
      if (parsed.name === "FindPosted") this.txs.find.set(num(parsed.args.id), rec);
      else if (parsed.name === "FindChecked") {
        this.txs.check.set(num(parsed.args.checkId), { ...rec, status: STATUS[num(parsed.args.status)] });
      } else if (parsed.name === "SafetyZonePosted") this.txs.zone.set(num(parsed.args.id), rec);
    }
  }

  // ------------------------------------------------------------- snapshot
  /** Everything the page shows, in one object. Pages are refetched only when a count
   *  changed since the last snapshot (or `force`). */
  async snapshot(force = false) {
    await this.agencies();
    const counts = await this.counts();
    const prev = this._counts;
    const changed = force || !prev || prev.finds !== counts.finds || prev.checks !== counts.checks || prev.zones !== counts.zones;
    const logs = await this.syncLogs(counts.block);
    if (changed) {
      const [finds, checks, zones] = await Promise.all([
        this.finds(counts.finds), this.checks(counts.checks), this.zones(counts.zones),
      ]);
      this._pages = { finds, checks, zones };
    } else {
      // Counts unchanged: only the tx hashes from newly fetched logs can differ.
      for (const f of this._pages.finds) { const t = this.txs.find.get(f.id); if (t) { f.tx = t.tx; f.block = t.block; } }
      for (const c of this._pages.checks) { const t = this.txs.check.get(c.id); if (t) { c.tx = t.tx; c.block = t.block; c.status_after = t.status; } }
      for (const z of this._pages.zones) { const t = this.txs.zone.get(z.id); if (t) z.tx = t.tx; }
      const now = Date.now() / 1000;
      for (const z of this._pages.zones) z.active = z.until > now;
    }
    this._counts = counts;
    return {
      block: counts.block,
      counts,
      agencies: this.agencyList,
      finds: this._pages.finds,
      checks: this._pages.checks,
      zones: this._pages.zones,
      logs,
      read_at: new Date(),
      contract: this.address,
      chain_id: this.chainId,
      explorer: this.explorer,
      verified: this.deployment.verified || null,
    };
  }

  /** Poll forever; `onUpdate(snapshot)` after each read, `onError(e)` when a read fails.
   *  Ticks come every `backfillMs` until the event logs are complete, then every `intervalMs`. */
  poll(onUpdate, { intervalMs = POLL_MS, backfillMs = BACKFILL_MS, onError = null } = {}) {
    let stopped = false;
    const tick = async () => {
      if (stopped) return;
      let wait = intervalMs;
      try {
        const snap = await this.snapshot();
        onUpdate(snap);
        if (!snap.logs.complete && !snap.logs.error) wait = backfillMs;
      } catch (e) { if (onError) onError(e); }
      if (!stopped) setTimeout(tick, wait);
    };
    tick();
    return () => { stopped = true; };
  }
}

// --------------------------------------------------------------- rendering helpers
// Markup matches web/js/main.js so the viewer owner can drop these into #feed / #finds.

export const short = (h) => (h ? `${h.slice(0, 10)}…${h.slice(-6)}` : "");
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
export const fmtTime = (unix) => new Date(unix * 1000).toISOString().replace("T", " ").slice(0, 19) + " UTC";

function txCell(reader, hash) {
  if (!hash) return `<span class="tx hint">tx pending in logs</span>`;
  const url = reader.txUrl(hash);
  return url
    ? `<a class="tx" href="${url}" target="_blank" rel="noopener">${short(hash)}</a>`
    : `<span class="tx">${short(hash)}</span>`;
}

/** One .find row per find, newest last, as main.js draws them. */
export function renderFinds(reader, snap) {
  if (!snap.finds.length) return `<div class="hint">No finds on the contract yet.</div>`;
  return snap.finds.map((f) => {
    // Say how thin the evidence is: "disputed by 1 of 1 checks" is one rover at one point.
    const n = f.agree.length + f.disagree.length;
    const who = n
      ? `${f.status} by ${f.status === "disputed" ? f.disagree.length : f.agree.length} of ${n} check${n === 1 ? "" : "s"}: `
        + [...f.agree.map((a) => `${a} agrees`), ...f.disagree.map((a) => `${a} disagrees`)].join(", ")
      : "no checks yet";
    return `<div class="find">` +
      `<span class="id c-${esc(f.agency)}">#${f.id}</span>` +
      `<span>${esc(f.agency)} posted ±${f.sigma.toFixed(1)} m, ${f.reading.toFixed(2)} wt%</span>` +
      `<span class="status ${f.status}">${f.status}</span>` +
      `<span class="meta">(${f.x.toFixed(1)}, ${f.y.toFixed(1)}) m · ${who} · ${fmtTime(f.time)} · ${txCell(reader, f.tx)}</span>` +
      `</div>`;
  }).join("");
}

/** Posts and checks as one feed of .ev rows, newest first. */
export function renderFeed(reader, snap) {
  const evs = [
    ...snap.finds.map((f) => ({ t: f.time, agency: f.agency, tx: f.tx, block: f.block,
      what: `postFind #${f.id} · ±${f.sigma.toFixed(1)} m · reading ${f.reading.toFixed(2)} wt%` })),
    ...snap.checks.map((c) => ({ t: c.time, agency: c.agency, tx: c.tx, block: c.block,
      what: `checkFind #${c.find_id} · ${c.agrees ? "agrees" : "disagrees"} · reading ${c.reading.toFixed(2)} wt%` +
            (c.status_after ? ` · now ${c.status_after}` : "") })),
    ...snap.zones.map((z) => ({ t: z.time, agency: z.agency, tx: z.tx, block: null,
      what: `postSafetyZone #${z.id} · r ${z.radius.toFixed(0)} m · ${esc(z.purpose)}${z.active ? "" : " · ended"}` })),
  ].sort((a, b) => b.t - a.t);
  if (!evs.length) return `<div class="hint">Nothing posted yet.</div>`;
  return evs.map((e) =>
    `<div class="ev"><span class="t">${fmtTime(e.t).slice(11, 19)}</span>` +
    `<span class="who c-${esc(e.agency)}">${esc(e.agency)}</span><span>${e.what}</span>${txCell(reader, e.tx)}</div>`
  ).join("");
}
