// Campaign runs as web/collect_campaigns.py writes them (docs/handoffs.md,
// "Campaign simulator"): the index of batches and summary rows, one run's
// record, and its pose rows. Nothing here draws.

export const SCOUT_COLOR = { S1: "#5aa7ff", S2: "#f2994a", S3: "#b58cff", S4: "#4fd1b5", S5: "#f06a9a" };
export const STATUS_COLOR = { find: "#e0b84a", confirmed: "#4fbf72", unconfirmed: "#8a9099", drilled: "#58c8ff" };
export const STATUS_WORD = { find: "find, not yet checked", confirmed: "confirmed by a second Scout", unconfirmed: "not confirmed", drilled: "drilled" };
// poses.npz `activity` codes (docs/handoffs.md).
export const ACTIVITY = ["descent", "survey", "return", "position check", "reading", "drilling", "searching a find", "docked", "waiting at dock"];
export const TIME_KINDS = [
  ["drive", "driving", "var(--t-drive)"], ["check", "position checks", "var(--t-check)"],
  ["reading", "readings", "var(--t-reading)"], ["drill", "drilling", "var(--t-drill)"], ["wait", "dock queue", "var(--t-wait)"],
];
export const OUTCOME_WORD = { showcase: "showcase run", ok: "home with reserve", below_reserve: "home below reserve", fault_return: "turned back on a fault", lost: "lost" };
export const OUTCOME_SHORT = { showcase: "showcase", ok: "", below_reserve: "below reserve", fault_return: "fault", lost: "lost" };

async function fetchOk(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r;
}

export async function loadIndex(base = "campaigns/") {
  return (await (await fetchOk(base + "index.json")).json()).batches || [];
}

export class CampaignRun {
  static async load(batch, runId, base = "campaigns/") {
    const dir = `${base}${encodeURIComponent(batch)}/${encodeURIComponent(runId)}/`;
    const [rec, idx, buf] = await Promise.all([
      fetchOk(dir + "record.json").then((r) => r.json()),
      fetchOk(dir + "poses.json").then((r) => r.json()),
      fetchOk(dir + "poses.bin").then((r) => r.arrayBuffer()),
    ]);
    const run = new CampaignRun(batch, rec, idx, new Float32Array(buf));
    run.dir = dir;
    return run;
  }

  constructor(batch, rec, idx, flat) {
    this.batch = batch;
    this.rec = rec;
    this.ncol = idx.columns.length;
    this.col = Object.fromEntries(idx.columns.map((c, i) => [c, i]));
    this.poses = {};
    for (const [sid, [row, n]] of Object.entries(idx.sorties)) {
      this.poses[sid] = flat.subarray(row * this.ncol, (row + n) * this.ncol);
    }
    // Sorties in the order they end: the slider steps through them.
    this.sorties = rec.sorties.slice().sort((a, b) => a.t_end - b.t_end || a.sortie_id.localeCompare(b.sortie_id));
    this.sortieById = Object.fromEntries(rec.sorties.map((s) => [s.sortie_id, s]));
    this.events = rec.events.slice().sort((a, b) => a.t - b.t);
    // The ice map's events only: a record can hold tens of thousands of sightings and checks.
    this.iceEvents = this.events.filter((e) => e.type === "find" || e.type === "search_end" || e.type === "drill" || e.type === "search_start");
    this.addedBy = {};
    for (const c of rec.crater_map.added || []) (this.addedBy[c.after_sortie] ||= []).push(c);
  }

  rows(sid) {
    const a = this.poses[sid], n = this.ncol, out = [];
    if (!a) return out;
    for (let i = 0; i < a.length; i += n) out.push(a.subarray(i, i + n));
    return out;
  }

  // What the dock knows after the first k sorties have ended (k = 0: nothing
  // flown). An event counts once its sortie is home; dock events (crater
  // added) carry the sortie just ended. Campaign-wide events go by time.
  doneSet(k) { return new Set(this.sorties.slice(0, k).map((s) => s.sortie_id)); }
  timeAfter(k) { return k > 0 ? this.sorties[k - 1].t_end : 0; }
  eventsKnown(k, events = this.events) {
    const done = this.doneSet(k), T = this.timeAfter(k);
    return events.filter((e) => (e.sortie_id ? done.has(e.sortie_id) : e.t <= T));
  }

  // The ice map: every find with its status, from find / search_end / drill.
  findsFrom(events) {
    const m = new Map();
    for (const e of events) {
      if (e.type === "find") m.set(e.find_id, { ...e, status: "find", searches: [], drill: null });
      const f = m.get(e.find_id);
      if (!f) continue;
      if (e.type === "search_end") { f.status = e.result; f.searches.push(e); }
      if (e.type === "drill") { f.status = "drilled"; f.drill = e; }
    }
    return [...m.values()];
  }

  // Craters in the shared map after k sorties.
  addedAfter(k) {
    const done = this.doneSet(k);
    return (this.rec.crater_map.added || []).filter((c) => done.has(c.after_sortie));
  }

  bounds() {
    const b = this.rec.terrain && this.rec.terrain.bounds;
    if (b && b.length === 4) return b;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    const { true_x, true_y } = this.col;
    for (const a of Object.values(this.poses)) for (let i = 0; i < a.length; i += this.ncol) {
      x0 = Math.min(x0, a[i + true_x]); x1 = Math.max(x1, a[i + true_x]);
      y0 = Math.min(y0, a[i + true_y]); y1 = Math.max(y1, a[i + true_y]);
    }
    return [x0, y0, x1, y1];
  }
}

// ---------------------------------------------------------------- formatting
export function fmtNum(v, d = 1) {
  if (v === null || v === undefined || v === "") return "–";
  if (typeof v === "boolean") return v ? "on" : "off";
  if (typeof v !== "number") return String(v);
  if (!Number.isFinite(v)) return "NaN";   // kv() rows drop it; never shown as a number
  return Number.isInteger(v) && d <= 1 ? String(v) : v.toFixed(d);
}

// Campaign time as "day 3, 14:05" from the first launch.
export function fmtDay(t) {
  t = Math.max(0, t);
  const d = Math.floor(t / 86400), h = Math.floor((t % 86400) / 3600), m = Math.floor((t % 3600) / 60);
  return `day ${d + 1}, ${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

export function fmtHM(s) {
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return `${h} h ${String(m).padStart(2, "0")} m`;
}

export function timeBar(time_s, scale, big = false) {
  const total = Object.values(time_s || {}).reduce((a, b) => a + b, 0);
  const segs = TIME_KINDS.map(([k, word, c]) => {
    const v = (time_s && time_s[k]) || 0;
    return v > 0 ? `<i style="width:${(100 * v) / total}%;background:${c}" title="${word}: ${fmtHM(v)}"></i>` : "";
  }).join("");
  return `<div class="tbar${big ? " big" : ""}" style="width:${Math.min(100, (100 * total) / scale)}%" title="${fmtHM(total)} in all">${segs}</div>`;
}

export function timeLegend(time_s) {
  return `<div class="tlegend">${TIME_KINDS.map(([k, word, c]) =>
    `<span><i class="sw sq" style="--c:${c}"></i>${word} <b>${(((time_s && time_s[k]) || 0) / 3600).toFixed(1)} h</b></span>`).join("")}</div>`;
}

// What a record says is not real yet: this site's stand-in sentence and the
// engine's `standins` list (docs/handoffs.md), in one line.
export function standinOf(rec) {
  const parts = [];
  if (rec.standin) parts.push(String(rec.standin));
  const si = rec.standins;
  if (Array.isArray(si)) parts.push(...si.map(String));
  else if (si && typeof si === "object") parts.push(...Object.entries(si).map(([k, v]) => `${k}: ${v}`));
  return parts.join(" ") || null;
}

// The 3D and map assets for a run's terrain site: assets/terrain/<site>/ when
// web/build_assets.py --site built them, else the Shoemaker drive area's.
export async function assetsBase(rec) {
  const site = rec.terrain && rec.terrain.site;
  if (site && /^[A-Za-z0-9_-]+$/.test(site) && site !== "shoemaker") {
    try { if ((await fetch(`assets/terrain/${site}/meta.json`)).ok) return `assets/terrain/${site}/`; } catch { /* fall back */ }
  }
  return "assets/terrain/";
}

// The 5 m terrain window under a run (web/build_windows.py), if the site has one.
export async function windowFor(rec, base = "assets/terrain/") {
  try {
    const list = await (await fetch(base + "windows.json")).json();
    const [x0, y0, x1, y1] = rec.terrain.bounds;
    return list.find((w) => w.x0 - w.cell <= x0 && w.y0 - w.cell <= y0 &&
      w.x0 + w.cols * w.cell >= x1 && w.y0 + w.rows * w.cell >= y1) || null;
  } catch { return null; }
}

export function standinBanner(text, tag = "Stand-in") {
  if (!text) return "";
  const esc = String(text).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);
  return `<div class="standin" role="note"><b>${tag}</b>${esc}</div>`;
}

// Every banner a record needs: the showcase note (sim/showcase.py), then what is a stand-in.
export function bannersOf(rec) {
  return standinBanner(rec.showcase, "Showcase") + standinBanner(standinOf(rec));
}

export function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}
