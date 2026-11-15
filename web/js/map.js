// The 2D map: north up, the site's hillshade, the three tracks per rover, the
// uncertainty circle, finds with status, and the real ice when the log has it.
import { COLORS } from "./world.js";

const hex = (c) => "#" + c.toString(16).padStart(6, "0");

export class MapView {
  constructor(canvas, terrain) {
    this.cv = canvas;
    this.ctx = canvas.getContext("2d");
    this.terrain = terrain;
    this.img = new Image();
    this.img.onload = () => this.draw();
    this.img.src = terrain.base + terrain.meta.map.file;
    // The planner's no-go ground, red on clear (build_assets.py, from sim/rover.py).
    if (terrain.meta.nogo) {
      this.nogo = new Image();
      this.nogo.onload = () => this.draw();
      this.nogo.src = terrain.base + terrain.meta.nogo.file;
    }
    this.mode = "site";
    this.zoom = 1;
    this.pan = [0, 0];
    this.opts = { tracks: true, truth: true };
    new ResizeObserver(() => this.draw()).observe(canvas);
    canvas.addEventListener("wheel", (e) => {
      e.preventDefault();
      this.zoom = Math.min(40, Math.max(0.5, this.zoom * (e.deltaY > 0 ? 1 / 1.2 : 1.2)));
      this.draw();
    }, { passive: false });
    let drag = null;
    canvas.addEventListener("pointerdown", (e) => { drag = [e.clientX, e.clientY]; canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener("pointermove", (e) => {
      if (!drag || !this.view) return;
      const s = this.view.s;
      this.pan[0] -= (e.clientX - drag[0]) / s;
      this.pan[1] += (e.clientY - drag[1]) / s;
      drag = [e.clientX, e.clientY];
      this.draw();
    });
    canvas.addEventListener("pointerup", () => (drag = null));
  }

  setMode(mode) {
    this.mode = mode;
    this.zoom = 1;
    this.pan = [0, 0];
    this.draw();
  }

  setRun(run) { this.run = run; this.pan = [0, 0]; this.zoom = 1; }

  // World window for the current mode: centre and metres across.
  window() {
    const run = this.run;
    if (this.mode === "site") {
      const all = run.agencies.flatMap((a) => [...run.tracks[a].truth, ...run.tracks[a].odom]);
      const xs = all.map((p) => p[0]), ys = all.map((p) => p[1]);
      const span = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) + 60;
      return { cx: (Math.min(...xs) + Math.max(...xs)) / 2, cy: (Math.min(...ys) + Math.max(...ys)) / 2, span };
    }
    const [x, y] = run.poseAt(this.mode, this.t);
    return { cx: x, cy: y, span: 90 };
  }

  update(t) { this.t = t; this.draw(); }

  draw() {
    const cv = this.cv, run = this.run;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const W = cv.clientWidth, H = cv.clientHeight;
    if (!W || !H) return;
    if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) {
      cv.width = Math.round(W * dpr);
      cv.height = Math.round(H * dpr);
    }
    const c = this.ctx;
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.fillStyle = "#0d0e10";
    c.fillRect(0, 0, W, H);
    if (!run) return;

    const w = this.window();
    const s = (Math.min(W, H) / w.span) * this.zoom;
    const cx = w.cx + this.pan[0], cy = w.cy + this.pan[1];
    this.view = { s };
    const X = (x) => W / 2 + (x - cx) * s;
    const Y = (y) => H / 2 - (y - cy) * s;

    // Hillshade: pixel j at x0 + j*1 m; the image is north up.
    const m = this.terrain.meta, g = this.terrain.coarse;
    if (this.img.complete && this.img.naturalWidth) {
      const x0 = g.x0 - g.cell / 2, y1 = g.y0 + (g.rows - 0.5) * g.cell;
      c.imageSmoothingEnabled = s < 2;
      c.globalAlpha = 0.85;
      c.drawImage(this.img, X(x0), Y(y1), g.cols * g.cell * s, g.rows * g.cell * s);
      if (this.opts.nogo && this.nogo && this.nogo.complete && this.nogo.naturalWidth) {
        c.globalAlpha = 0.42;
        // One pixel per 0.25 m sim cell, centred on the grid points.
        const nx0 = m.x0 - m.cell / 2, ny1 = m.y0 + (m.rows - 0.5) * m.cell;
        c.drawImage(this.nogo, X(nx0), Y(ny1), m.cols * m.cell * s, m.rows * m.cell * s);
        c.globalAlpha = 1;
      }
      c.globalAlpha = 1;
    }

    // Mapped craters (the rover's orbital map) once zoomed in enough to read.
    if (s > 2.2) {
      c.strokeStyle = "rgba(160, 200, 255, 0.28)";
      c.lineWidth = 1;
      const x0 = cx - W / 2 / s, x1 = cx + W / 2 / s, y0 = cy - H / 2 / s, y1 = cy + H / 2 / s;
      c.beginPath();
      for (const [x, y, d] of this.terrain.craters) {
        if (x < x0 - d || x > x1 + d || y < y0 - d || y > y1 + d) continue;
        c.moveTo(X(x) + (d / 2) * s, Y(y));
        c.arc(X(x), Y(y), (d / 2) * s, 0, Math.PI * 2);
      }
      c.stroke();
    }

    const k = run.stepAt(this.t);
    const path = (pts, n) => {
      c.beginPath();
      for (let i = 0; i <= n && i < pts.length; i++) {
        const [x, y] = pts[i];
        if (i === 0) c.moveTo(X(x), Y(y)); else c.lineTo(X(x), Y(y));
      }
      c.stroke();
    };

    if (this.opts.tracks) {
      for (const a of run.agencies) {
        const T = run.tracks[a];
        c.lineJoin = "round";
        c.setLineDash([5, 4]);
        c.strokeStyle = hex(COLORS.odom);
        c.lineWidth = 1.5;
        path(T.odom, k);
        c.setLineDash([]);
        c.strokeStyle = "rgba(233, 236, 239, 0.8)";
        c.lineWidth = 1.5;
        path(T.truth, k);
        c.strokeStyle = hex(COLORS[a]);
        c.lineWidth = 2;
        path(T.est, k);
      }
    }

    // Real ice, hidden from the rovers.
    if (this.opts.truth && run.truth) {
      for (const ice of run.truth.ice || []) {
        const px = X(ice.x), py = Y(ice.y), r = Math.max(5, ice.radius * s);
        c.strokeStyle = hex(COLORS.truth);
        c.setLineDash([3, 3]);
        c.lineWidth = 1;
        c.beginPath(); c.arc(px, py, r, 0, Math.PI * 2); c.stroke();
        c.setLineDash([]);
        c.fillStyle = hex(COLORS.truth);
        c.beginPath(); c.moveTo(px, py - 6); c.lineTo(px + 5, py); c.lineTo(px, py + 6); c.lineTo(px - 5, py); c.closePath(); c.fill();
      }
    }

    // Finds: circle of the finder's sigma, coloured by status.
    c.font = "600 11px Helvetica Neue, Helvetica, Arial, sans-serif";
    for (const f of run.findsAt(k)) {
      const col = hex(COLORS[f.status]);
      const px = X(f.x), py = Y(f.y), r = Math.max(4, f.sigma * s);
      c.fillStyle = col + "26";
      c.strokeStyle = col;
      c.lineWidth = 2;
      c.beginPath(); c.arc(px, py, r, 0, Math.PI * 2); c.fill(); c.stroke();
      c.beginPath(); c.arc(px, py, 3, 0, Math.PI * 2); c.fillStyle = col; c.fill();
      for (const ch of f.checks) {
        c.fillStyle = hex(COLORS[ch.agency]);
        c.beginPath(); c.arc(X(ch.x), Y(ch.y), 3.5, 0, Math.PI * 2); c.fill();
      }
      const text = `${f.agency}'s find ±${f.sigma.toFixed(1)} m · ${f.status}`;
      c.fillStyle = "rgba(10,11,13,0.8)";
      const tw = c.measureText(text).width;
      // Put the label on whichever side has room.
      const lx = px + r + 4 + tw + 10 < W ? px + r + 4 : px - r - 4 - tw - 10;
      c.fillRect(lx, py - 9, tw + 10, 17);
      c.fillStyle = col;
      c.fillText(text, lx + 5, py + 4);
    }

    // Rovers: estimate with its sigma circle, and a triangle at the true pose.
    for (const a of run.agencies) {
      const r = run.rover(a, k);
      const col = hex(COLORS[a]);
      if (this.opts.tracks) {
        const [ex, ey] = r.estimate;
        c.strokeStyle = col;
        c.fillStyle = col + "22";
        c.lineWidth = 1.5;
        c.beginPath(); c.arc(X(ex), Y(ey), Math.max(3, r.sigma * s), 0, Math.PI * 2); c.fill(); c.stroke();
      }
      const [x, y, h] = run.poseAt(a, this.t);
      c.save();
      c.translate(X(x), Y(y));
      c.rotate(-h);
      c.fillStyle = col;
      c.strokeStyle = "#0d0e10";
      c.lineWidth = 1.5;
      c.beginPath(); c.moveTo(9, 0); c.lineTo(-6, 6); c.lineTo(-3, 0); c.lineTo(-6, -6); c.closePath();
      c.fill(); c.stroke();
      c.restore();
      c.fillStyle = col;
      c.fillText(a, X(x) + 9, Y(y) - 8);
    }

    // Scale bar.
    const target = 80 / s;
    const nice = [1, 2, 5, 10, 20, 50, 100, 200, 500].find((v) => v >= target) || 1000;
    c.fillStyle = "#c9cdd2";
    c.fillRect(12, H - 16, nice * s, 2);
    c.fillText(`${nice} m`, 12, H - 22);
  }
}
