// Builds the fine height patch ahead of a rover, off the main thread.
//
// The 0.25 m tiles carry the rim lips sampled at 0.25 m, but a lip is a few
// cm to tens of cm wide (lips.bin, from the terrain worker's lips.npy), so
// its lamp shadow, the arc ShadowNav navigates on, breaks up into dashes.
// Here, as in sim/terrain_display.py Display.patch: base heights cubic-
// interpolated from the sim grid, plus each lip evaluated at the patch's own
// spacing, h += lh * exp(-((r - R) / lw)^2).

let lips = null;   // Float32Array, rows [x - x0, y - y0, D, lh, lw]
let gx0 = 0, gy0 = 0;

// Add every lip to grid h (rows ny, cols nx) at (x0 + j*d, y0 + i*d), times sign.
function stampLips(h, nx, ny, x0, y0, d, sign) {
  const xmax = x0 + (nx - 1) * d, ymax = y0 + (ny - 1) * d;
  for (let c = 0; c < lips.length; c += 5) {
    const cx = lips[c] + gx0, cy = lips[c + 1] + gy0, R = lips[c + 2] / 2, lh = lips[c + 3], lw = lips[c + 4];
    if (!(lh > 0) || !(lw > 0)) continue;
    const reach = R + 3 * lw;
    if (cx + reach < x0 || cx - reach > xmax || cy + reach < y0 || cy - reach > ymax) continue;
    const j0 = Math.max(0, Math.floor((cx - reach - x0) / d)), j1 = Math.min(nx - 1, Math.ceil((cx + reach - x0) / d));
    const i0 = Math.max(0, Math.floor((cy - reach - y0) / d)), i1 = Math.min(ny - 1, Math.ceil((cy + reach - y0) / d));
    const k = sign * lh, inv = 1 / lw, lo = Math.max(0, R - 3 * lw);
    for (let i = i0; i <= i1; i++) {
      const dy = y0 + i * d - cy, dy2 = dy * dy, row = i * nx;
      for (let j = j0; j <= j1; j++) {
        const dx = x0 + j * d - cx;
        const r = Math.sqrt(dx * dx + dy2);
        if (r < lo || r > R + 3 * lw) continue;
        const u = (r - R) * inv;
        h[row + j] += k * Math.exp(-u * u);
      }
    }
  }
}

// Catmull-Rom weights.
function cr(t, w) {
  const t2 = t * t, t3 = t2 * t;
  w[0] = -0.5 * t3 + t2 - 0.5 * t;
  w[1] = 1.5 * t3 - 2.5 * t2 + 1;
  w[2] = -1.5 * t3 + 2 * t2 + 0.5 * t;
  w[3] = 0.5 * t3 - 0.5 * t2;
}

self.onmessage = (e) => {
  const m = e.data;
  if (m.init) { lips = m.init.lips; gx0 = m.init.x0; gy0 = m.init.y0; return; }
  const { id, a, base, bx0, by0, bnx, bny, cell, px0, py0, res, n, taper, o, alb, per } = m;
  // Lip-free base: the tiles have the lips baked in at 0.25 m.
  const b = Float32Array.from(base);
  if (lips) stampLips(b, bnx, bny, bx0, by0, cell, -1);
  const h = new Float32Array(n * n);
  const wx = new Float64Array(4), wy = new Float64Array(4);
  const span = (n - 1) * res;
  for (let i = 0; i < n; i++) {
    const y = py0 + i * res, fy = (y - by0) / cell, iy = Math.floor(fy);
    cr(fy - iy, wy);
    for (let j = 0; j < n; j++) {
      const x = px0 + j * res, fx = (x - bx0) / cell, ix = Math.floor(fx);
      cr(fx - ix, wx);
      let s = 0;
      for (let q = 0; q < 4; q++) {
        const row = (iy - 1 + q) * bnx + ix - 1;
        s += wy[q] * (wx[0] * b[row] + wx[1] * b[row + 1] + wx[2] * b[row + 2] + wx[3] * b[row + 3]);
      }
      h[i * n + j] = s;
    }
  }
  if (lips) stampLips(h, n, n, px0, py0, res, 1);
  // Blend to the tile surface (bilinear, lips as baked) over the outer `taper`
  // metres, so the patch meets the 0.25 m mesh without a step.
  for (let i = 0; i < n; i++) {
    const y = py0 + i * res, fy = (y - by0) / cell, iy = Math.floor(fy), v = fy - iy;
    for (let j = 0; j < n; j++) {
      const edge = Math.min(i, j, n - 1 - i, n - 1 - j) * res;
      if (edge >= taper) continue;
      const x = px0 + j * res, fx = (x - bx0) / cell, ix = Math.floor(fx), u = fx - ix;
      const k = iy * bnx + ix;
      const t = (base[k] * (1 - u) + base[k + 1] * u) * (1 - v) + (base[k + bnx] * (1 - u) + base[k + bnx + 1] * u) * v;
      let w = edge / taper; w = w * w * (3 - 2 * w);
      h[i * n + j] = t + w * (h[i * n + j] - t);
    }
  }
  const pos = new Float32Array(n * n * 3), nrm = new Float32Array(n * n * 3);
  const uv0 = new Float32Array(n * n * 2), uv1 = new Float32Array(n * n * 2);
  const px = Math.floor(gx0 / per) * per, py = Math.floor(gy0 / per) * per;
  for (let i = 0; i < n; i++) {
    const y = py0 + i * res;
    for (let j = 0; j < n; j++) {
      const x = px0 + j * res, k = i * n + j;
      pos[3 * k] = x - o[0]; pos[3 * k + 1] = y - o[1]; pos[3 * k + 2] = h[k] - o[2];
      const jl = Math.max(j - 1, 0), jr = Math.min(j + 1, n - 1), il = Math.max(i - 1, 0), ir = Math.min(i + 1, n - 1);
      const gx = (h[i * n + jr] - h[i * n + jl]) / ((jr - jl) * res), gy = (h[ir * n + j] - h[il * n + j]) / ((ir - il) * res);
      const l = 1 / Math.sqrt(gx * gx + gy * gy + 1);
      nrm[3 * k] = -gx * l; nrm[3 * k + 1] = -gy * l; nrm[3 * k + 2] = l;
      uv0[2 * k] = (x - alb[0]) / alb[2]; uv0[2 * k + 1] = (y - alb[1]) / alb[3];
      uv1[2 * k] = (x - px) / per; uv1[2 * k + 1] = (y - py) / per;
    }
  }
  self.postMessage({ id, a, pos, nrm, uv0, uv1, px0, py0, span }, [pos.buffer, nrm.buffer, uv0.buffer, uv1.buffer]);
};
