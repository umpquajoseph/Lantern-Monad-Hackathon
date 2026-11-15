// The live third-person 3D view, drawn on the viewer's own GPU.
// World frame (docs/handoffs.md): metres, x east, y north, z up. Everything is
// drawn relative to the map centre, because LOLA stereographic metres (~1e5)
// lose centimetres in float32.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DRenderer, CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";
import { Line2 } from "three/addons/lines/Line2.js";
import { LineGeometry } from "three/addons/lines/LineGeometry.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";

// ShadowNav (Atha et al. 2024, sections IV and V), as in sim/scene.py:
// camera 2.5 m above the ground, lamp 0.2 m below it.
const CAMERA_HEIGHT_M = 2.5;
const LAMP_BELOW_CAMERA_M = 0.2;
// The rest match sim/scene.py's assumed values so both views agree.
const CAMERA_PITCH_DEG = 15;   // scene.py, assumed
const LAMP_INNER_DEG = 30;     // scene.py, assumed
const LAMP_OUTER_DEG = 45;     // scene.py, assumed
const LAMP_FADE_END_M = 80;    // scene.py, assumed
const CHASE_BACK_M = 14, CHASE_UP_M = 8;  // scene.py, assumed

// Viewer-only, assumed.
const FINE_STRIDE = 2;         // fine tiles drawn at 0.5 m (every 2nd 0.25 m sample)
const COARSE_STRIDE = 2;       // coarse map drawn at 2 m
const FINE_PAD_M = 70;         // load 0.25 m tiles within this of each rover
const DRAPE_M = 0.18;          // lines float this far above the ground
const LAMP_INTENSITY = 90;     // candela-ish, tuned by eye against scene.py's look
const MOON_R = 1737400;        // m, for the backdrop's horizon drop

export const COLORS = {
  A: 0x5aa7ff, B: 0xf2994a,
  reported: 0xd9b44a, confirmed: 0x4fbf72, disputed: 0xe5574f,
  truth: 0xd8f3ff, track: 0xe9ecef, odom: 0x9aa0a6,
};

export class World {
  constructor(el, terrain) {
    this.el = el;
    this.terrain = terrain;
    const m = terrain.meta;
    this.o = new THREE.Vector3(m.x0 + 0.5 * (m.cols - 1) * m.cell, m.y0 + 0.5 * (m.rows - 1) * m.cell,
      0.5 * (m.hmin + m.hmin + 65535 * m.hscale));

    const r = (this.renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" }));
    r.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    r.shadowMap.enabled = true;
    r.shadowMap.type = THREE.PCFSoftShadowMap;
    r.toneMapping = THREE.ACESFilmicToneMapping;
    r.toneMappingExposure = 1.0;
    el.appendChild(r.domElement);

    this.labels = new CSS2DRenderer();
    Object.assign(this.labels.domElement.style, { position: "absolute", inset: "0", pointerEvents: "none" });
    el.appendChild(this.labels.domElement);

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x020304);
    this.camera = new THREE.PerspectiveCamera(50, 1, 0.3, 30000);
    this.camera.up.set(0, 0, 1);
    this.camera.position.set(0, -60, 40);
    this.controls = new OrbitControls(this.camera, r.domElement);
    this.controls.enableDamping = true;
    this.controls.maxPolarAngle = Math.PI * 0.49;
    this.controls.minDistance = 3;
    this.controls.maxDistance = 4000;

    // Fill light: only so a viewer can see the ground. The rovers' cameras
    // never get it (sim/scene.py draws the first-person view lamp only).
    // A low cool key from one side brings out crater relief, like a raking light.
    this.fill = new THREE.HemisphereLight(0x9fb0d0, 0x101114, 0.35);
    this.scene.add(this.fill);
    this.fillDir = new THREE.DirectionalLight(0xa8b6d4, 0.9);
    this.fillDir.position.set(-0.8, 0.45, 0.32);
    this.scene.add(this.fillDir);

    this.stars();
    this.terrainMaterial();
    this.fineMeshes = new Map();
    this.rovers = {};
    this.overlay = new THREE.Group();
    this.scene.add(this.overlay);
    this.follow = "A";
    this.lastFollowPos = null;
    this.lineMats = [];

    this.resize();
    new ResizeObserver(() => this.resize()).observe(el);
  }

  L(x, y, z) { return new THREE.Vector3(x - this.o.x, y - this.o.y, z - this.o.z); }
  groundL(x, y, lift = 0) { return this.L(x, y, this.terrain.heightAt(x, y) + lift); }

  resize() {
    const w = this.el.clientWidth, h = this.el.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h);
    this.labels.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    for (const m of this.lineMats) m.resolution.set(w, h);
  }

  stars() {
    const n = 2500, p = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      const u = Math.random() * 2 - 1, th = Math.random() * Math.PI * 2;
      const z = Math.abs(u) * 0.9 + 0.05, s = Math.sqrt(1 - z * z);
      p.set([Math.cos(th) * s * 20000, Math.sin(th) * s * 20000, z * 20000], i * 3);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(p, 3));
    const s = new THREE.Points(g, new THREE.PointsMaterial({ color: 0xffffff, size: 1.2, sizeAttenuation: false, transparent: true, opacity: 0.7 }));
    s.frustumCulled = false;
    this.starField = s;
    this.scene.add(s);
  }

  // ------------------------------------------------------------- terrain
  terrainMaterial() {
    const m = this.terrain.meta;
    const base = this.terrain.base;
    const loader = new THREE.TextureLoader();
    // Display albedo and close-up detail from the terrain worker, if built.
    const opts = { color: 0x9a9a9a, roughness: 1, metalness: 0 };
    if (m.albedo) {
      const t = loader.load(base + m.albedo.file);
      t.colorSpace = THREE.NoColorSpace;
      t.anisotropy = 8;
      opts.map = t;
      opts.color = 0xb8b8b8;
    }
    if (m.detail) {
      const n = loader.load(base + m.detail.normal);
      n.wrapS = n.wrapT = THREE.RepeatWrapping;
      n.channel = 1;
      n.colorSpace = THREE.NoColorSpace;
      opts.normalMap = n;
      opts.normalScale = new THREE.Vector2(1, 1);
    }
    this.mask = new THREE.DataTexture(new Uint8Array(this.terrain.ntr * this.terrain.ntc), this.terrain.ntc, this.terrain.ntr,
      THREE.RedFormat, THREE.UnsignedByteType);
    this.mask.needsUpdate = true;
    this.fineMat = new THREE.MeshStandardMaterial(opts);
    this.coarseMat = new THREE.MeshStandardMaterial(opts);
    // The coarse map is not drawn where a fine tile is loaded.
    const gridMin = new THREE.Vector2(m.x0 - this.o.x, m.y0 - this.o.y);
    this.coarseMat.onBeforeCompile = (sh) => {
      sh.uniforms.uMask = { value: this.mask };
      sh.uniforms.uGridMin = { value: gridMin };
      sh.uniforms.uTileM = { value: this.terrain.tileM };
      sh.uniforms.uTiles = { value: new THREE.Vector2(this.terrain.ntc, this.terrain.ntr) };
      sh.vertexShader = sh.vertexShader.replace("#include <common>", "#include <common>\nvarying vec2 vXY;")
        .replace("#include <begin_vertex>", "#include <begin_vertex>\nvXY = position.xy;");
      sh.fragmentShader = sh.fragmentShader.replace("#include <common>",
        "#include <common>\nvarying vec2 vXY;\nuniform sampler2D uMask;\nuniform vec2 uGridMin;\nuniform float uTileM;\nuniform vec2 uTiles;")
        .replace("void main() {", `void main() {
          vec2 tij = floor((vXY - uGridMin) / uTileM);
          if (all(greaterThanEqual(tij, vec2(0.0))) && all(lessThan(tij, uTiles))) {
            if (texelFetch(uMask, ivec2(tij), 0).r > 0.5) discard;
          }`);
    };
  }

  gridGeometry(grid, stride, { uv = true, drop = null } = {}) {
    const ri = [], cj = [];
    for (let i = 0; i < grid.rows; i += stride) ri.push(i);
    if (ri[ri.length - 1] !== grid.rows - 1) ri.push(grid.rows - 1);
    for (let j = 0; j < grid.cols; j += stride) cj.push(j);
    if (cj[cj.length - 1] !== grid.cols - 1) cj.push(grid.cols - 1);
    const nr = ri.length, nc = cj.length;
    const pos = new Float32Array(nr * nc * 3);
    const uv0 = uv ? new Float32Array(nr * nc * 2) : null;
    const uv1 = uv ? new Float32Array(nr * nc * 2) : null;
    const m = this.terrain.meta;
    // Albedo covers the sim grid's cells: [x0 - cell/2, x0 + cols*cell - cell/2].
    const ax0 = m.x0 - m.cell / 2, ay0 = m.y0 - m.cell / 2, aw = m.cols * m.cell, ah = m.rows * m.cell;
    const per = m.detail ? m.detail.period_m : 8;
    let p = 0, q = 0;
    for (let a = 0; a < nr; a++) {
      const i = ri[a], y = grid.y0 + i * grid.cell;
      for (let b = 0; b < nc; b++) {
        const j = cj[b], x = grid.x0 + j * grid.cell;
        let z = grid.z[i * grid.cols + j];
        if (drop) z -= drop(x, y);
        pos[p++] = x - this.o.x; pos[p++] = y - this.o.y; pos[p++] = z - this.o.z;
        if (uv) {
          uv0[q] = (x - ax0) / aw; uv0[q + 1] = (y - ay0) / ah;
          // Detail tile: world metres modulo its period, done in doubles.
          uv1[q] = (x - Math.floor(m.x0 / per) * per) / per; uv1[q + 1] = (y - Math.floor(m.y0 / per) * per) / per;
          q += 2;
        }
      }
    }
    const idx = new Uint32Array((nr - 1) * (nc - 1) * 6);
    let t = 0;
    for (let a = 0; a < nr - 1; a++) for (let b = 0; b < nc - 1; b++) {
      const v = a * nc + b;
      idx[t++] = v; idx[t++] = v + 1; idx[t++] = v + nc;
      idx[t++] = v + 1; idx[t++] = v + nc + 1; idx[t++] = v + nc;
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    if (uv) {
      g.setAttribute("uv", new THREE.BufferAttribute(uv0, 2));
      g.setAttribute("uv1", new THREE.BufferAttribute(uv1, 2));
    }
    g.setIndex(new THREE.BufferAttribute(idx, 1));
    g.computeVertexNormals();
    return g;
  }

  buildTerrain() {
    const coarse = new THREE.Mesh(this.gridGeometry(this.terrain.coarse, COARSE_STRIDE), this.coarseMat);
    coarse.receiveShadow = true;
    this.scene.add(coarse);
    this.coarse = coarse;
  }

  async buildBackdrop() {
    const b = await this.terrain.loadBackdrop();
    if (!b) return;
    const m = this.terrain.meta;
    const da = { x0: m.x0 - 1, y0: m.y0 - 1, x1: m.x0 + m.cols * m.cell + 1, y1: m.y0 + m.rows * m.cell + 1 };
    const ox = this.o.x, oy = this.o.y;
    // Lowered 0.5 m so seams hide, and by r^2 / 2R for the horizon
    // (terrain worker's display.json, "backdrop").
    const geo = this.gridGeometry(b, 2, { uv: false, drop: (x, y) => 0.5 + ((x - ox) ** 2 + (y - oy) ** 2) / (2 * MOON_R) });
    const mat = new THREE.MeshStandardMaterial({ color: 0x8a8a8a, roughness: 1, metalness: 0 });
    const lo = new THREE.Vector2(da.x0 - ox, da.y0 - oy), hi = new THREE.Vector2(da.x1 - ox, da.y1 - oy);
    mat.onBeforeCompile = (sh) => {
      sh.uniforms.uLo = { value: lo };
      sh.uniforms.uHi = { value: hi };
      sh.vertexShader = sh.vertexShader.replace("#include <common>", "#include <common>\nvarying vec2 vXY;")
        .replace("#include <begin_vertex>", "#include <begin_vertex>\nvXY = position.xy;");
      sh.fragmentShader = sh.fragmentShader.replace("#include <common>", "#include <common>\nvarying vec2 vXY;\nuniform vec2 uLo;\nuniform vec2 uHi;")
        .replace("void main() {", "void main() {\n if (all(greaterThan(vXY, uLo + 2.0)) && all(lessThan(vXY, uHi - 2.0))) discard;");
    };
    const mesh = new THREE.Mesh(geo, mat);
    mesh.receiveShadow = false;
    this.scene.add(mesh);
  }

  // Load 0.25 m tiles near the rovers, drop the rest.
  updateFine(points) {
    const want = this.terrain.keysNear(points, FINE_PAD_M);
    for (const key of want) {
      if (this.fineMeshes.has(key)) continue;
      this.fineMeshes.set(key, null);
      this.terrain.loadTile(key).then((g) => {
        if (!this.fineMeshes.has(key)) return;
        const mesh = new THREE.Mesh(this.gridGeometry(g, FINE_STRIDE), this.fineMat);
        mesh.castShadow = true;
        mesh.receiveShadow = true;
        this.scene.add(mesh);
        this.fineMeshes.set(key, mesh);
        this.setMask(key, 1);
      });
    }
    for (const [key, mesh] of this.fineMeshes) {
      if (want.has(key) || !mesh) continue;
      // Keep tiles a little longer than needed so scrubbing back is instant.
      if (this.fineMeshes.size <= 12) continue;
      this.scene.remove(mesh);
      mesh.geometry.dispose();
      this.fineMeshes.delete(key);
      this.setMask(key, 0);
    }
  }

  setMask(key, v) {
    const [r, c] = key.split("_").map(Number);
    this.mask.image.data[r * this.terrain.ntc + c] = v ? 255 : 0;
    this.mask.needsUpdate = true;
  }

  // -------------------------------------------------------------- rovers
  makeRover(a) {
    const g = new THREE.Group();
    const body = new THREE.MeshStandardMaterial({ color: COLORS[a], roughness: 0.6, metalness: 0.1 });
    const dark = new THREE.MeshStandardMaterial({ color: 0x2a2c30, roughness: 0.8 });
    const metal = new THREE.MeshStandardMaterial({ color: 0xc9ccd1, roughness: 0.4, metalness: 0.5 });
    const box = (mat, cx, cy, cz, dx, dy, dz) => {
      const m = new THREE.Mesh(new THREE.BoxGeometry(dx, dy, dz), mat);
      m.position.set(cx, cy, cz);
      m.castShadow = true;
      g.add(m);
    };
    // Dimensions from sim/scene.py _rover_polydata.
    box(body, -0.2, 0, 0.75, 1.7, 1.2, 0.5);
    box(metal, -0.2, 0, 1.05, 1.2, 1.0, 0.08);
    box(metal, 0, 0, 1.75, 0.1, 0.1, 1.5);
    box(dark, 0.02, 0, CAMERA_HEIGHT_M, 0.18, 0.4, 0.14);
    box(new THREE.MeshBasicMaterial({ color: 0xfff1d6 }), 0.1, 0, CAMERA_HEIGHT_M - LAMP_BELOW_CAMERA_M, 0.06, 0.3, 0.08);
    for (const x of [0.45, -0.85]) for (const y of [0.72, -0.72]) {
      const w = new THREE.Mesh(new THREE.CylinderGeometry(0.3, 0.3, 0.22, 20), dark);
      w.position.set(x, y, 0.3);
      w.castShadow = true;
      g.add(w);
    }
    // The lamp: 0.2 m below the camera, aimed along it, 15 deg down.
    const lamp = new THREE.SpotLight(0xffeedd, LAMP_INTENSITY, LAMP_FADE_END_M, THREE.MathUtils.degToRad(LAMP_OUTER_DEG),
      1 - LAMP_INNER_DEG / LAMP_OUTER_DEG, 2);
    lamp.position.set(0.12, 0, CAMERA_HEIGHT_M - LAMP_BELOW_CAMERA_M);
    const p = THREE.MathUtils.degToRad(CAMERA_PITCH_DEG);
    lamp.target.position.set(0.12 + 10 * Math.cos(p), 0, CAMERA_HEIGHT_M - LAMP_BELOW_CAMERA_M - 10 * Math.sin(p));
    lamp.castShadow = true;
    lamp.shadow.mapSize.set(2048, 2048);
    lamp.shadow.camera.near = 0.3;
    lamp.shadow.camera.far = LAMP_FADE_END_M;
    lamp.shadow.bias = -0.0004;
    lamp.shadow.normalBias = 0.04;
    g.add(lamp, lamp.target);

    const tag = document.createElement("div");
    tag.className = "lbl3d";
    tag.style.color = "#" + COLORS[a].toString(16).padStart(6, "0");
    tag.innerHTML = `<span>Rover ${a}</span>`;
    const lbl = new CSS2DObject(tag);
    lbl.position.set(0, 0, 3.6);
    lbl.center.set(1, 1);  // above-left of the rover; find labels go above-right
    g.add(lbl);
    this.scene.add(g);
    this.rovers[a] = { g, lamp };
  }

  // ------------------------------------------------------------ overlays
  lineMat(color, width, { dashed = false, opacity = 1 } = {}) {
    const m = new LineMaterial({ color, linewidth: width, dashed, dashSize: 0.8, gapSize: 0.8, transparent: opacity < 1, opacity, worldUnits: false });
    m.resolution.set(this.el.clientWidth, this.el.clientHeight);
    this.lineMats.push(m);
    return m;
  }

  // A polyline draped on the ground. Returns the line and, for each input
  // point, the index of its first segment, so the line can grow with time.
  drape(points, mat, step = 1.0) {
    const pos = [], at = [];
    for (let i = 0; i < points.length; i++) {
      at.push(Math.max(0, pos.length / 3 - 1));
      const [x, y] = points[i];
      if (i > 0) {
        const [px, py] = points[i - 1];
        const n = Math.max(1, Math.ceil(Math.hypot(x - px, y - py) / step));
        for (let s = 1; s < n; s++) {
          const f = s / n, v = this.groundL(px + (x - px) * f, py + (y - py) * f, DRAPE_M);
          pos.push(v.x, v.y, v.z);
        }
      }
      const v = this.groundL(x, y, DRAPE_M);
      pos.push(v.x, v.y, v.z);
    }
    if (pos.length < 6) pos.push(...pos.slice(0, 3));
    const g = new LineGeometry();
    g.setPositions(pos);
    const line = new Line2(g, mat);
    line.computeLineDistances();
    line.frustumCulled = false;
    return { line, at };
  }

  ring(x, y, r, mat, n = 72) {
    const pts = [];
    for (let i = 0; i <= n; i++) {
      const a = (i / n) * Math.PI * 2;
      pts.push([x + r * Math.cos(a), y + r * Math.sin(a)]);
    }
    return this.drape(pts, mat, Math.max(0.5, r / 12)).line;
  }

  label(html, color) {
    const d = document.createElement("div");
    d.className = "lbl3d";
    d.style.color = color;
    d.innerHTML = html;
    return new CSS2DObject(d);
  }

  clearOverlay() {
    for (const o of [...this.overlay.children]) {
      this.overlay.remove(o);
      o.traverse((c) => {
        if (c.geometry) c.geometry.dispose();
        if (c.element) c.element.remove();
      });
    }
  }

  // Called once per run, after the tiles under its tracks are loaded.
  setRun(run) {
    this.run = run;
    this.clearOverlay();
    for (const a of run.agencies) if (!this.rovers[a]) this.makeRover(a);
    this.tracks = {};
    for (const a of run.agencies) {
      const T = run.tracks[a];
      const t = {
        truth: this.drape(T.truth, this.lineMat(COLORS.track, 2, { opacity: 0.85 })),
        odom: this.drape(T.odom, this.lineMat(COLORS.odom, 1.5, { dashed: true, opacity: 0.55 })),
        est: this.drape(T.est, this.lineMat(COLORS[a], 3)),
      };
      for (const l of Object.values(t)) this.overlay.add(l.line);
      this.tracks[a] = t;
    }
    this.sigmaRings = {};
    this.sigmaKey = {};
    this.estDots = {};
    this.sigmaMats = {};
    for (const a of run.agencies) {
      this.sigmaMats[a] = this.sigmaMats[a] || this.lineMat(COLORS[a], 2, { opacity: 0.95 });
      const dot = new THREE.Mesh(new THREE.SphereGeometry(0.35, 16, 12), new THREE.MeshBasicMaterial({ color: COLORS[a] }));
      this.overlay.add(dot);
      this.estDots[a] = dot;
    }
    // Real ice, hidden from the rovers (stand-in runs carry it).
    this.truthObjs = [];
    for (const ice of (run.truth && run.truth.ice) || []) {
      const d = new THREE.Mesh(new THREE.OctahedronGeometry(0.3), new THREE.MeshBasicMaterial({ color: COLORS.truth }));
      d.position.copy(this.groundL(ice.x, ice.y, 0.8));
      d.scale.set(1, 1, 1.6);
      // Marker only: the sidebar legend names it, and a text label here
      // would cover the find label at the same spot.
      const ring = this.ring(ice.x, ice.y, ice.radius, this.lineMat(COLORS.truth, 1.5, { dashed: true, opacity: 0.8 }));
      this.overlay.add(d, ring);
      this.truthObjs.push(d, ring);
    }
    this.findObjs = new Map();
    this.lastFollowPos = null;
    this.placeCamera(this.follow, true);
  }

  findMarker(f) {
    const col = COLORS[f.status];
    const g = new THREE.Group();
    const p = this.groundL(f.x, f.y);
    const beam = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.05, 6, 8),
      new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: 0.8 }));
    beam.rotation.x = Math.PI / 2;
    beam.position.set(p.x, p.y, p.z + 3);
    const ring = this.ring(f.x, f.y, f.sigma, this.lineMat(col, 2.5));
    const lbl = this.label("", "#" + col.toString(16).padStart(6, "0"));
    lbl.position.set(p.x, p.y, p.z + 6.4);
    lbl.center.set(0, 1);
    g.add(beam, ring, lbl);
    for (const c of f.checks) {
      const q = this.groundL(c.x, c.y, 0.3);
      const m = new THREE.Mesh(new THREE.SphereGeometry(0.4, 14, 10), new THREE.MeshBasicMaterial({ color: COLORS[c.agency] }));
      m.position.copy(q);
      g.add(m);
    }
    g.userData = { lbl, key: `${f.status}:${f.checks.length}` };
    this.overlay.add(g);
    return g;
  }

  setVisibility({ fill, tracks, truth }) {
    this.fill.visible = fill;
    this.fillDir.visible = fill;
    for (const t of Object.values(this.tracks || {})) for (const l of Object.values(t)) l.line.visible = tracks;
    for (const o of this.truthObjs || []) o.visible = truth;
    for (const o of Object.values(this.estDots || {})) o.visible = tracks;
    for (const o of Object.values(this.sigmaRings || {})) if (o) o.visible = tracks;
  }

  // ----------------------------------------------------------- per frame
  update(t, opts) {
    const run = this.run;
    if (!run) return;
    const k = run.stepAt(t);
    const pts = [];
    for (const a of run.agencies) {
      const [x, y, h] = run.poseAt(a, t);
      const { g } = this.rovers[a];
      g.position.copy(this.groundL(x, y));
      g.rotation.set(0, 0, h);
      pts.push([x, y]);
      // Grow the truth line with the rover between steps; the estimate and
      // odometry lines only change at logged steps.
      const tr = this.tracks[a];
      const f = k < run.steps.length - 1 ? (t - run.times[k]) / (run.times[k + 1] - run.times[k]) : 0;
      for (const key of ["truth", "odom", "est"]) {
        const l = tr[key];
        const n = key === "truth" && k + 1 < l.at.length ? l.at[k] + Math.floor((l.at[k + 1] - l.at[k]) * f) : l.at[k];
        l.line.geometry.instanceCount = Math.max(1, n);
      }
      const r = run.rover(a, k);
      const [ex, ey] = r.estimate;
      this.estDots[a].position.copy(this.groundL(ex, ey, 0.35));
      const sk = `${k}`;
      if (this.sigmaKey[a] !== sk) {
        if (this.sigmaRings[a]) { this.overlay.remove(this.sigmaRings[a]); this.sigmaRings[a].geometry.dispose(); }
        const ring = this.ring(ex, ey, r.sigma, this.sigmaMats[a]);
        ring.visible = opts.tracks;
        this.overlay.add(ring);
        this.sigmaRings[a] = ring;
        this.sigmaKey[a] = sk;
      }
    }
    this.updateFine(pts);

    // Finds, keyed by status so a marker is rebuilt when its status changes.
    const finds = run.findsAt(k);
    const live = new Set();
    for (const f of finds) {
      live.add(f.find_id);
      const key = `${f.status}:${f.checks.length}`;
      let obj = this.findObjs.get(f.find_id);
      if (obj && obj.userData.key !== key) { this.dropObj(obj); obj = null; }
      if (!obj) { obj = this.findMarker(f); this.findObjs.set(f.find_id, obj); }
      obj.userData.lbl.element.innerHTML = `<span>Find ${f.find_id} by ${f.agency}, ±${f.sigma.toFixed(1)} m</span> · ${f.status}`;
    }
    for (const [id, obj] of this.findObjs) if (!live.has(id)) { this.dropObj(obj); this.findObjs.delete(id); }

    this.placeCamera(this.follow, false);
  }

  dropObj(obj) {
    this.overlay.remove(obj);
    obj.traverse((c) => { if (c.geometry) c.geometry.dispose(); if (c.element) c.element.remove(); });
  }

  setFollow(mode) {
    this.follow = mode;
    this.lastFollowPos = null;
    this.placeCamera(mode, true);
  }

  // Follow: the orbit target rides on the rover and the camera keeps its
  // offset, so a viewer can orbit and zoom while the rover drives.
  placeCamera(mode, reset) {
    const run = this.run;
    if (!run) return;
    const c = this.controls;
    if (mode === "site") {
      if (!reset) return;
      const all = run.agencies.flatMap((a) => run.tracks[a].truth);
      const xs = all.map((p) => p[0]), ys = all.map((p) => p[1]);
      const cx = (Math.min(...xs) + Math.max(...xs)) / 2, cy = (Math.min(...ys) + Math.max(...ys)) / 2;
      const span = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys));
      const tgt = this.groundL(cx, cy);
      c.target.copy(tgt);
      this.camera.position.set(tgt.x, tgt.y - span * 0.75, tgt.z + span * 0.6);
      c.update();
      return;
    }
    if (!run.agencies.includes(mode)) return;  // free camera
    const t = this.currentT || 0;
    const [x, y, h] = run.poseAt(mode, t);
    const p = this.groundL(x, y, 1.0);
    if (reset || !this.lastFollowPos) {
      c.target.copy(p);
      this.camera.position.set(p.x - CHASE_BACK_M * Math.cos(h), p.y - CHASE_BACK_M * Math.sin(h), p.z + CHASE_UP_M);
    } else {
      const d = p.clone().sub(this.lastFollowPos);
      c.target.add(d);
      this.camera.position.add(d);
    }
    this.lastFollowPos = p;
    c.update();
  }

  render() {
    this.controls.update();
    this.starField.position.copy(this.camera.position);
    this.renderer.render(this.scene, this.camera);
    this.labels.render(this.scene, this.camera);
  }
}
