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

  async loadBackdrop() {
    const b = this.meta.backdrop;
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

export async function loadIndex(base) {
  return (await (await fetchOk(base + "index.json")).json()).runs;
}

export class Run {
  static async load(base, entry) {
    const log = await (await fetchOk(base + entry.path.replace(/^runs\//, ""))).json();
    const dir = base + entry.path.replace(/^runs\//, "").replace(/log\.json$/, "");
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
    this.standin = log.standin || null;
    this.truth = log.truth || null;
    this.chain = log.chain || {};
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
