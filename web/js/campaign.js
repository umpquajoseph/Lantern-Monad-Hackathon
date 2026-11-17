// Campaign runs as web/collect_campaigns.py writes them (docs/handoffs.md,
// "Campaign simulator"): the index of batches and summary rows, one run's
// record, and its pose rows. Nothing here draws.
import { Run } from "./data.js";

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
  // A camera run's own ground (record `ground`, built by web/build_assets.py
  // --run-ground): what its cameras and lidar saw. Else the site's grid.
  const rid = rec.ground && rec.ground.file && rec.run_id;
  if (rid && /^[A-Za-z0-9_-]+$/.test(rid)) {
    try { if ((await fetch(`assets/terrain/runs/${rid}/meta.json`)).ok) return `assets/terrain/runs/${rid}/`; } catch { /* fall back */ }
  }
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

// A full-height page (#page) fills the window below the header and banners, without scrolling.
export function fitPage() {
  const set = () => {
    const top = document.getElementById("page").getBoundingClientRect().top + scrollY;
    document.documentElement.style.setProperty("--page-top", `${Math.ceil(top)}px`);
  };
  set();
  addEventListener("resize", set);
}

// Every banner a record needs: the showcase note (sim/showcase.py), then what is a stand-in.
export function bannersOf(rec) {
  return standinBanner(rec.showcase, "Showcase") + standinBanner(standinOf(rec));
}

export function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

// ------------------------------------------------------------ sortie window
export const DOCKED = 7;            // poses.npz activity: docked (docs/handoffs.md)
const PARK_M = 4;                   // display only: Scouts at the dock are drawn this far apart, beside the pad

function lerpPose(a, b, f) {
  let dh = b[2] - a[2];
  dh = Math.atan2(Math.sin(dh), Math.cos(dh));
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + dh * f];
}

// One sortie's time window as a "Run log" (docs/handoffs.md, "Part 4 reads"):
// every Scout out during it, sampled on one shared timeline, one rover per
// Scout, so world.js and map.js replay it unchanged. Between its own sorties
// a Scout is parked at the dock. Nothing is re-simulated.
//
// believed: draw each Scout at the pose it believed (the sortie replay page,
// truth behind a reveal); otherwise at its true pose with the estimate drawn
// beside it (Mission Control, as the hackathon page drew it).
// pipes: {sortie_id: pipeline doc} from loadPipeline. Each pipeline step's
// time becomes a step here, and that step's rover carries what the Scout's
// pipeline produced: rims ([[x_fwd, y_left]], at that step's true pose),
// rimInfo ([[map_row | null, wrong]] per point), particles ([[x, y, w]]), plan
// ([[x, y]]), lidar (the hazard grid string, HAZ_GRID) and range (the logged
// range image, cm). See loadPipeline.
export class FleetRun extends Run {
  constructor(cr, s, { believed = false, pipes = {} } = {}) {
    const T0 = s.t_start, T1 = s.t_end, c = cr.col, d = cr.rec.dock;
    const over = cr.rec.sorties.filter((x) => x.t_start < T1 && x.t_end > T0);
    const scouts = [s.scout, ...[...new Set(over.map((x) => x.scout))].filter((a) => a !== s.scout).sort()];
    const rowsOf = Object.fromEntries(over.map((x) => [x.sortie_id, cr.rows(x.sortie_id)]));
    const tset = new Set([T0, T1]);
    for (const rows of Object.values(rowsOf)) for (const r of rows) if (r[c.t] >= T0 && r[c.t] <= T1) tset.add(r[c.t]);
    // Pipeline steps, per Scout, in time order; each one's time is a step.
    const pipe = {};
    for (const x of over) {
      const doc = pipes[x.sortie_id];
      if (!doc) continue;
      for (const st of doc.steps) {
        (pipe[x.scout] ||= []).push(st);
        if (st.t >= T0 && st.t <= T1) tset.add(st.t);
      }
    }
    for (const a in pipe) pipe[a].sort((p, q) => p.t - q.t);
    const times = [...tset].sort((a, b) => a - b);
    const park = (i) => [d.x - PARK_M * (i + 1), d.y - PARK_M, 0];

    const sample = (a, i, t) => {
      const so = over.find((x) => x.scout === a && x.t_start <= t && t <= x.t_end);
      const rows = so && rowsOf[so.sortie_id];
      if (!rows || !rows.length) {
        const p = park(i);
        return { tru: p, est: p, b: 0.3, act: DOCKED, energy: null, sortie: null };
      }
      let lo = 0, hi = rows.length - 1;
      if (t <= rows[0][c.t]) hi = 0;
      else while (lo < hi) { const m = (lo + hi + 1) >> 1; if (rows[m][c.t] <= t) lo = m; else hi = m - 1; }
      const r0 = rows[lo], r1 = rows[Math.min(lo + 1, rows.length - 1)];
      const span = r1[c.t] - r0[c.t], f = span > 0 ? Math.min(1, Math.max(0, (t - r0[c.t]) / span)) : 0;
      const tru = lerpPose([r0[c.true_x], r0[c.true_y], r0[c.true_heading]], [r1[c.true_x], r1[c.true_y], r1[c.true_heading]], f);
      const est = lerpPose([r0[c.est_x], r0[c.est_y], r0[c.true_heading]], [r1[c.est_x], r1[c.est_y], r1[c.true_heading]], f);
      return { tru, est, b: r0[c.bound95] + (r1[c.bound95] - r0[c.bound95]) * f, act: r1[c.activity],
        energy: r0[c.energy_kwh] + (r1[c.energy_kwh] - r0[c.energy_kwh]) * f, sortie: so };
    };
    // The latest pipeline step at or before t, inside the Scout's own sortie.
    const pipeAt = (a, t, so) => {
      const L = pipe[a];
      if (!L || !so) return null;
      let lo = 0, hi = L.length - 1, k = -1;
      while (lo <= hi) { const m = (lo + hi) >> 1; if (L[m].t <= t) { k = m; lo = m + 1; } else hi = m - 1; }
      return k >= 0 && L[k].t >= so.t_start ? L[k] : null;
    };

    const poses = { truth: {}, believed: {} };
    for (const a of scouts) { poses.truth[a] = []; poses.believed[a] = []; }
    const steps = times.map((t) => {
      const rovers = {}, info = {};
      scouts.forEach((a, i) => {
        const q = sample(a, i, t);
        const ps = pipeAt(a, t, q.sortie);
        const rims = ps ? ps.rims : [];
        rovers[a] = { true_pose: q.tru, estimate: q.est.slice(0, 2), odom_pose: q.est, sigma: q.b, nav_on: true,
          rims: rims.map((p) => [p[0], p[1]]), rimInfo: rims.map((p) => [p[2], p[3]]),
          particles: ps ? ps.particles : [], plan: ps ? ps.plan : null, lidar: ps ? ps.lidar : null, range: ps ? ps.range : null, pipeT: ps ? ps.t : null };
        info[a] = { act: q.act, energy: q.energy, sortie: q.sortie };
        poses.truth[a].push(q.tru);
        poses.believed[a].push(q.est);
      });
      return { t, rovers, info };
    });
    super({ run_id: `${cr.rec.run_id}:${s.sortie_id}`, steps, truth: { ice: cr.rec.truth.ice } }, "");
    this.agencies = scouts;
    this.tracks = {};
    for (const a of scouts) this.tracks[a] = { truth: poses.truth[a].map((p) => p.slice(0, 2)),
      est: poses.believed[a].map((p) => p.slice(0, 2)), odom: poses.believed[a].map((p) => p.slice(0, 2)) };
    this.poses = poses;
    this.cr = cr;
    this.sortie = s;
    this.over = over;
    this.hasPipeline = Object.keys(pipe).length > 0;
    this.hasParticles = this.hasPipeline;
    const doc = Object.values(pipes)[0];
    this.lidarGrid = doc ? doc.lidar_grid : null;
    this.pipelineNote = doc ? doc.standin || doc.note || null : null;
    this.pipelineStandin = !!(doc && doc.standin);
    this.showTruth(!believed);
    this._finds = new Map();
  }

  showTruth(on) {
    const P = on ? this.poses.truth : this.poses.believed;
    for (const a of this.agencies) this.steps.forEach((st, k) => { st.rovers[a].true_pose = P[a][k]; });
  }

  // The ice map as the Scouts had logged it by step k, every Scout's finds
  // with their status. world.js draws each with its 95% bound.
  findsAt(k) {
    if (this._finds.has(k)) return this._finds.get(k);
    const T = this.times[k];
    const list = this.cr.findsFrom(this.cr.iceEvents.filter((e) => e.t <= T)).map((f) => ({
      find_id: f.find_id, agency: f.scout, x: f.est[0], y: f.est[1], sigma: f.bound95, status: f.status, checks: [],
      reading: f.reading_wt, sortie_id: f.sortie_id, searches: f.searches, drill: f.drill, tx: f.x, ty: f.y,
    }));
    this._finds.set(k, list);
    return list;
  }
}

// Per-step pipeline data for some sorties: the run's steps.npz (docs/handoffs.md,
// "Camera navigation"), served as steps/<sortie_id>.bin + .json by
// web/collect_campaigns.py convert_steps. Returns {sortie_id: doc}, doc =
// {steps: [{t, rims: [[fwd, left, map_row | null, wrong]], particles: [[x, y, w]],
// plan: [[x, y]], haz: [[fwd, left, kind]], range: Uint16Array (rows x cols, cm),
// lidar: hazard grid string (see HAZ_GRID)}], lidar_grid, standin}. A sortie
// without data is left out.
// HAZ_GRID: sim/lidar.py's hazard cells (HAZ_CELL_M 0.5, HAZ_RANGE_M 2-25 m,
// HAZ_HALF_WIDTH_M 15) as one char per cell, row-major (i forward, j left):
// 0 not seen, 1 seen by the lidar with no hazard, 2 crater floor, 3 slope or rock.
export const HAZ_GRID = { cell_m: 0.5, fwd0_m: 2, half_width_m: 15, nx: 46, ny: 60 };
const HAZ_KIND_CELL = { 0: 2, 1: 3, 2: 3 };   // steps.npz haz_kind: 0 crater, 1 slope, 2 rock
const TYPED = { float32: Float32Array, int32: Int32Array, uint16: Uint16Array, uint8: Uint8Array };
export async function loadPipeline(cr, sortieIds) {
  let index;
  try {
    const r = await fetch(`${cr.dir}steps/index.json`);
    if (!r.ok) return {};
    index = await r.json();
  } catch { return {}; }
  const el = index.lidar_el_deg.map((d) => (d * Math.PI) / 180), az = index.lidar_az_deg.map((d) => (d * Math.PI) / 180);
  const g = { ...HAZ_GRID, el_deg: index.lidar_el_deg, az_deg: index.lidar_az_deg };
  const out = {};
  await Promise.all(sortieIds.filter((sid) => index.sorties.includes(sid)).map(async (sid) => {
    const [mr, br] = await Promise.all([fetch(`${cr.dir}steps/${encodeURIComponent(sid)}.json`), fetch(`${cr.dir}steps/${encodeURIComponent(sid)}.bin`)]);
    if (!mr.ok || !br.ok) return;
    const meta = await mr.json(), buf = await br.arrayBuffer();
    const A = {};
    for (const [name, [dtype, shape, off]] of Object.entries(meta)) {
      A[name] = { a: new TYPED[dtype](buf, off, shape.reduce((p, q) => p * q, 1)), shape };
    }
    const K = A.step.shape[0], NP = A.particles.shape[1], [LR, LC] = A.lidar.shape.slice(1);
    const steps = [];
    for (let k = 0; k < K; k++) {
      const S = A.step.a.subarray(k * 12, k * 12 + 12);
      const ex = S[4], ey = S[5];
      const particles = [];
      for (let i = 0; i < NP; i++) { const o = (k * NP + i) * 3; particles.push([ex + A.particles.a[o], ey + A.particles.a[o + 1], A.particles.a[o + 2]]); }
      const rims = [];
      for (let i = A.rim_o.a[k]; i < A.rim_o.a[k + 1]; i++) {
        const m = A.rim_map.a[i];
        rims.push([A.rims.a[2 * i], A.rims.a[2 * i + 1], m >= 0 ? m : null, A.rim_wrong.a[i] === 1]);
      }
      const plan = [];
      for (let i = A.plan_o.a[k]; i < A.plan_o.a[k + 1]; i++) plan.push([A.plan.a[2 * i], A.plan.a[2 * i + 1]]);
      const range = A.lidar.a.subarray(k * LR * LC, (k + 1) * LR * LC);
      // The grid: cells a return fell in are seen; hazard cells take their kind.
      const cells = new Uint8Array(g.nx * g.ny);
      const cell = (f, l) => { const i = Math.floor((f - g.fwd0_m) / g.cell_m), j = Math.floor((l + g.half_width_m) / g.cell_m); return i >= 0 && i < g.nx && j >= 0 && j < g.ny ? i * g.ny + j : -1; };
      for (let r = 0; r < LR; r++) for (let c = 0; c < LC; c++) {
        const cm = range[r * LC + c];
        if (!cm) continue;
        const d = (cm / 100) * Math.cos(el[r]), q = cell(d * Math.cos(az[c]), d * Math.sin(az[c]));
        if (q >= 0 && !cells[q]) cells[q] = 1;
      }
      const haz = [];
      for (let i = A.haz_o.a[k]; i < A.haz_o.a[k + 1]; i++) {
        const f = A.haz.a[2 * i], l = A.haz.a[2 * i + 1], kind = A.haz_kind.a[i];
        haz.push([f, l, kind]);
        const q = cell(f, l);
        if (q >= 0) cells[q] = Math.max(cells[q], HAZ_KIND_CELL[kind]);
      }
      steps.push({ t: S[0], est_heading: S[6], goal: [S[10], S[11]], rims, particles, plan, haz, range, lidar: String.fromCharCode(...cells.map((v) => 48 + v)) });
    }
    out[sid] = { steps, lidar_grid: { ...g, rows: LR, cols: LC }, standin: index.standin || null };
  }));
  return out;
}

// A sortie runs kilometres past the 1 km drive area. Outside it, heights come
// from the real LOLA backdrops, lowered exactly as world.js draws them
// (buildBackdrop: 0.5 m plus r^2 / 2R), so the Scouts sit on the drawn ground.
// Call before World is built: a 5 m window outside the near backdrop takes
// the near backdrop's place (world.js buildBackdrop draws it).
export async function groundEverywhere(terrain, rec) {
  const m = terrain.meta;
  const win = await windowFor(rec);
  if (win) {
    const n = m.backdrop_near;
    const inside = n && win.x0 >= n.x0 && win.y0 >= n.y0 && win.x0 + win.cols * win.cell <= n.x0 + n.cols * n.cell &&
      win.y0 + win.rows * win.cell <= n.y0 + n.rows * n.cell;
    if (!inside) m.backdrop_near = win;
  }
  const near = await terrain.loadBackdrop("backdrop_near"), far = await terrain.loadBackdrop("backdrop");
  const ox = m.x0 + 0.5 * (m.cols - 1) * m.cell, oy = m.y0 + 0.5 * (m.rows - 1) * m.cell;
  const R = (m.display && m.display.moon_radius_m) || 1737400;
  const x1 = m.x0 + (m.cols - 1) * m.cell, y1 = m.y0 + (m.rows - 1) * m.cell;
  const inner = terrain.heightAt.bind(terrain);
  terrain.heightAt = (x, y) => {
    if (x >= m.x0 && x <= x1 && y >= m.y0 && y <= y1) return inner(x, y);
    const drop = 0.5 + ((x - ox) ** 2 + (y - oy) ** 2) / (2 * R);
    if (near && near.contains(x, y)) return near.at(x, y) - drop;
    if (far && far.contains(x, y)) return far.at(x, y) - drop;
    return inner(x, y);
  };
  return { near, far };
}
