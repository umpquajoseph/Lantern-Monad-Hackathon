// Loading the terrain assets and the run logs, and reading a run at time t.
// The run-log format is docs/handoffs.md, "Run log". Nothing here draws.

export const AGENCIES = ["A", "B"];

async function fetchOk(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r;
}

// uint16 grid, gzip-compressed by build_assets.py. A host may already have
// undone the gzip (Content-Encoding), so look at the bytes, not the name.
async function fetchU16(url) {
  let buf = new Uint8Array(await (await fetchOk(url)).arrayBuffer());
  if (buf[0] === 0x1f && buf[1] === 0x8b) {
    const stream = new Blob([buf]).stream().pipeThrough(new DecompressionStream("gzip"));
    buf = new Uint8Array(await new Response(stream).arrayBuffer());
  }
  return new Uint16Array(buf.buffer, buf.byteOffset, buf.byteLength >> 1);
}

// float32 table, gzip-compressed by build_assets.py.
export async function fetchF32(url) {
  let buf = new Uint8Array(await (await fetchOk(url)).arrayBuffer());
  if (buf[0] === 0x1f && buf[1] === 0x8b) {
    const stream = new Blob([buf]).stream().pipeThrough(new DecompressionStream("gzip"));
    buf = new Uint8Array(await new Response(stream).arrayBuffer());
  }
  return new Float32Array(buf.buffer, buf.byteOffset, buf.byteLength >> 2);
}

function dequant(q, hmin, hscale) {
  const z = new Float32Array(q.length);
  for (let i = 0; i < q.length; i++) z[i] = hmin + q[i] * hscale;
  return z;
}

// A height grid, rows north: sample [i, j] at (x0 + j*cell, y0 + i*cell).
export class Grid {
  constructor(z, rows, cols, x0, y0, cell) {
    Object.assign(this, { z, rows, cols, x0, y0, cell });
  }
  contains(x, y) {
    const fj = (x - this.x0) / this.cell, fi = (y - this.y0) / this.cell;
    return fj >= 0 && fi >= 0 && fj <= this.cols - 1 && fi <= this.rows - 1;
  }
  at(x, y) {
    let fj = (x - this.x0) / this.cell, fi = (y - this.y0) / this.cell;
    fj = Math.min(Math.max(fj, 0), this.cols - 1.001);
    fi = Math.min(Math.max(fi, 0), this.rows - 1.001);
    const j = Math.floor(fj), i = Math.floor(fi), u = fj - j, v = fi - i;
    const c = this.cols, z = this.z, k = i * c + j;
    return (z[k] * (1 - u) + z[k + 1] * u) * (1 - v) + (z[k + c] * (1 - u) + z[k + c + 1] * u) * v;
  }
}

export class Terrain {
  static async load(base) {
    const t = new Terrain();
    t.base = base;
    t.meta = await (await fetchOk(base + "meta.json")).json();
    const m = t.meta;
    const c = m.coarse;
    t.coarse = new Grid(dequant(await fetchU16(base + c.file), m.hmin, m.hscale),
      c.rows, c.cols, m.x0, m.y0, m.cell * c.step);
    t.tiles = new Map();      // "r_c" -> Grid (0.25 m), loaded on demand
    t.pending = new Map();
    t.tileM = m.tile * m.cell;
    t.ntr = Math.ceil((m.rows - 1) / m.tile);
    t.ntc = Math.ceil((m.cols - 1) / m.tile);
    t.craters = await (await fetchOk(base + m.craters)).json();
    return t;
  }

  // "backdrop" (20 m) or "backdrop_near" (5 m), both real LOLA.
  async loadBackdrop(name = "backdrop") {
    const b = this.meta[name];
    if (!b) return null;
    const z = dequant(await fetchU16(this.base + b.file), b.hmin, b.hscale);
    return new Grid(z, b.rows, b.cols, b.x0, b.y0, b.cell);
  }

  tileKey(x, y) {
    const r = Math.floor((y - this.meta.y0) / this.tileM), c = Math.floor((x - this.meta.x0) / this.tileM);
    if (r < 0 || c < 0 || r >= this.ntr || c >= this.ntc) return null;
    return `${r}_${c}`;
  }

  tileRect(key) {
    const [r, c] = key.split("_").map(Number);
    const x = this.meta.x0 + c * this.tileM, y = this.meta.y0 + r * this.tileM;
    return { r, c, x0: x, y0: y, x1: x + this.tileM, y1: y + this.tileM };
  }

  loadTile(key) {
    if (this.tiles.has(key)) return Promise.resolve(this.tiles.get(key));
    if (this.pending.has(key)) return this.pending.get(key);
    const m = this.meta;
    const [r, c] = key.split("_").map(Number);
    const info = m.tiles.find((t) => t.i0 === r * m.tile && t.j0 === c * m.tile);
    const p = fetchU16(this.base + info.file).then((q) => {
      const g = new Grid(dequant(q, m.hmin, m.hscale), info.rows, info.cols,
        m.x0 + info.j0 * m.cell, m.y0 + info.i0 * m.cell, m.cell);
      g.key = key;
      this.tiles.set(key, g);
      this.pending.delete(key);
      return g;
    });
    this.pending.set(key, p);
    return p;
  }

  // Keys of every tile within `pad` metres of the points.
  keysNear(points, pad) {
    const keys = new Set();
    for (const [x, y] of points) {
      for (const dx of [-pad, 0, pad]) for (const dy of [-pad, 0, pad]) {
        const k = this.tileKey(x + dx, y + dy);
        if (k) keys.add(k);
      }
    }
    return keys;
  }

  heightAt(x, y) {
    const k = this.tileKey(x, y);
    const g = k && this.tiles.get(k);
    return g ? g.at(x, y) : this.coarse.at(x, y);
  }
}

// ---------------------------------------------------------------------------

// web/runs/index.json: {"runs": [...]} or run_demo.py's plain list. Each
// entry has run_id, label, and path (relative to web/) or dir (in runs/).
export async function loadIndex(base) {
  const j = await (await fetchOk(base + "index.json")).json();
  return (Array.isArray(j) ? j : j.runs).map((r) => ({ ...r, path: r.path || `runs/${r.dir}/log.json` }));
}

export class Run {
  static async load(base, entry) {
    const rel = entry.path.replace(/^runs\//, "");
    const log = await (await fetchOk(base + rel)).json();
    const dir = base + rel.replace(/log\.json$/, "");
    return new Run(log, dir);
  }

  constructor(log, dir) {
    this.log = log;
    this.dir = dir;
    this.steps = log.steps;
    this.times = this.steps.map((s) => s.t);
    this.tEnd = this.times[this.times.length - 1];
    this.agencies = AGENCIES.filter((a) => this.steps[0].rovers[a] && this.steps[0].rovers[a].true_pose);
    this.events = [];
    this.steps.forEach((s, k) => (s.events || []).forEach((e) => this.events.push({ ...e, t: s.t, k })));
    this.tracks = {};
    for (const a of this.agencies) {
      this.tracks[a] = {
        truth: this.steps.map((s) => s.rovers[a].true_pose.slice(0, 2)),
        odom: this.steps.map((s) => (s.rovers[a].odom_pose || s.rovers[a].true_pose).slice(0, 2)),
        est: this.steps.map((s) => s.rovers[a].estimate),
      };
    }
    // What is not real yet. Stand-in runs from web/make_standin_run.py carry
    // `standin`; run_demo.py carries `standins`, one line per part.
    const si = log.standins || log.standin || null;
    this.standin = si && Object.keys(si).length ? si : null;
    const chainLine = si && (si.chain || si.tx);
    this.fakeTx = !!chainLine || !(log.chain && log.chain.explorer);
    this.unit = log.truth && /wt%/.test(log.truth.ice_units || "") ? " wt%" : "";
    this.truth = log.truth || null;
    // What made the estimate and sigma, when the run says (run_demo.py "nav").
    this.nav = typeof log.nav === "string" ? log.nav.replace(/^nav:\s*/, "") : null;
    // Real parts get a top-level line like nav (docs/handoffs.md): the planner.
    this.planner = typeof log.planner === "string" ? log.planner.replace(/^planner:\s*/, "") : null;
    // The navigation filter's particles after each step, when logged
    // (docs/handoffs.md: per rover `particles`, [[x, y, w], ...]).
    this.hasParticles = this.steps.some((s) => this.agencies.some((a) => (s.rovers[a].particles || []).length));
    this.chain = log.chain || {};
    // The rover's real driving speed, from the log: median distance per
    // logged step over dt_s, counting only steps where it moved.
    this.speed = {};
    const dt = log.dt_s || (this.times[1] - this.times[0]);
    for (const a of this.agencies) {
      const v = [];
      for (let k = 1; k < this.steps.length; k++) {
        const p = this.steps[k].rovers[a].true_pose, q = this.steps[k - 1].rovers[a].true_pose;
        const d = Math.hypot(p[0] - q[0], p[1] - q[1]), dtk = this.times[k] - this.times[k - 1];
        if (d > 1e-3 && dtk > 0) v.push(d / dtk);
      }
      v.sort((x, y) => x - y);
      this.speed[a] = v.length ? v[v.length >> 1] : null;
    }
  }

  // Index of the last step at or before t.
  stepAt(t) {
    const ts = this.times;
    let lo = 0, hi = ts.length - 1;
    if (t >= ts[hi]) return hi;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (ts[mid] <= t) lo = mid; else hi = mid - 1;
    }
    return lo;
  }

  // True pose at t, interpolated between steps so the 3D view moves smoothly.
  poseAt(a, t) {
    const k = this.stepAt(t);
    const p0 = this.steps[k].rovers[a].true_pose;
    if (k >= this.steps.length - 1) return p0.slice();
    const p1 = this.steps[k + 1].rovers[a].true_pose;
    const f = Math.min(1, Math.max(0, (t - this.times[k]) / (this.times[k + 1] - this.times[k])));
    let dh = p1[2] - p0[2];
    dh = Math.atan2(Math.sin(dh), Math.cos(dh));
    return [p0[0] + (p1[0] - p0[0]) * f, p0[1] + (p1[1] - p0[1]) * f, p0[2] + dh * f];
  }

  rover(a, k) { return this.steps[k].rovers[a]; }

  // The most recent first-person frame at or before step k.
  frameAt(a, k) {
    for (let i = k; i >= 0 && i > k - 50; i--) {
      const f = this.steps[i].rovers[a].fp_frame;
      if (f) return { src: this.dir + f, k: i };
    }
    return null;
  }

  // The recorded frame nearest time t, so the camera panel and the 3D rover
  // (at its interpolated pose for t) show the same moment: they differ by at
  // most half a stop. Returns {src, k}, k being the step the frame belongs to.
  // With fp_sub (docs/handoffs.md: frames between step k and k+1 at fraction
  // f of the way, about every 0.5 m), the candidates are step k's frame at
  // f = 0, its sub-frames, and step k+1's frame at f = 1; the nearest to the
  // 3D view's fraction wins. `sub` is false for a step's own frame, the only
  // kind the logged rim points belong to.
  frameNear(a, t) {
    const k0 = this.stepAt(t);
    const k1 = Math.min(k0 + 1, this.steps.length - 1);
    const span = this.times[k1] - this.times[k0];
    const frac = span > 0 ? Math.min(1, Math.max(0, (t - this.times[k0]) / span)) : 0;
    const r0 = this.steps[k0].rovers[a], r1 = this.steps[k1].rovers[a];
    const cand = [];
    if (r0.fp_frame) cand.push({ f: 0, src: r0.fp_frame, k: k0, sub: false });
    for (const [f, src] of r0.fp_sub || []) cand.push({ f, src, k: k0, sub: true });
    if (k1 !== k0 && r1.fp_frame) cand.push({ f: 1, src: r1.fp_frame, k: k1, sub: false });
    let best = null;
    for (const c of cand) if (!best || Math.abs(c.f - frac) < Math.abs(best.f - frac)) best = c;
    if (best) return { ...best, src: this.dir + best.src };
    return this.frameAt(a, k0);
  }

  // Metres driven between recorded frames, from the log: the rover's speed
  // times dt_s, split by the sub-frames per step when there are any.
  frameSpacing(a) {
    const stepM = this.speed[a] && this.log.dt_s ? this.speed[a] * this.log.dt_s : null;
    if (!stepM) return null;
    const subs = this.steps.map((s) => (s.rovers[a].fp_sub || []).length).filter((n) => n > 0);
    if (!subs.length) return stepM;
    subs.sort((x, y) => x - y);
    return stepM / (subs[subs.length >> 1] + 1);
  }

  eventsUpTo(k) { return this.events.filter((e) => e.k <= k); }

  // Finds and their status after the events up to step k, using the
  // contract's rule: disputed if any agency disagrees, confirmed if at least
  // one agrees and none disagree, else reported (spec, contract functions).
  findsAt(k) {
    const finds = new Map();
    for (const e of this.eventsUpTo(k)) {
      if (e.type === "find") finds.set(e.find_id, { ...e, checks: [], status: "reported" });
      else if (e.type === "check" && finds.has(e.find_id)) finds.get(e.find_id).checks.push(e);
    }
    for (const f of finds.values()) {
      if (f.checks.some((c) => !c.agrees)) f.status = "disputed";
      else if (f.checks.some((c) => c.agrees)) f.status = "confirmed";
    }
    return [...finds.values()];
  }
}

export function fmtClock(t) {
  t = Math.max(0, Math.round(t));
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  return `T+${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}
