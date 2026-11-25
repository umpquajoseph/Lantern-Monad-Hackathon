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
import { regolith, lampIntensity, fillIntensity, fillAmbient, TONE_GLSL } from "./regolith.js";
import { fetchF32 } from "./data.js";
import { frostPatch } from "./frost.js";

// As sim/scene.py: camera on the Scout's 1.6 m mast (sim/settings.py
// camera_height_m, brief p.6), lamp 0.2 m below it (lamp_below_camera_m,
// Atha et al. 2024 sec. IV). The hackathon rover used 2.5 m.
export const CAMERA_HEIGHT_M = 1.6;
const LAMP_BELOW_CAMERA_M = 0.2;
// The rest match sim/scene.py's assumed values so both views agree.
export const CAMERA_PITCH_DEG = 15;   // scene.py CAMERA_PITCH_DEG, assumed
export const CAMERA_HFOV_DEG = 60;    // scene.py HFOV_DEG (Atha et al. 2024 sec. IV)
const LAMP_INNER_DEG = 15;     // scene.py:76 (step-6-scene 2c8af7e), assumed
const LAMP_OUTER_DEG = 32;     // scene.py:77 (2c8af7e), assumed; was 30/45
const LAMP_FADE_END_M = 80;    // scene.py, assumed
const CHASE_BACK_M = 14, CHASE_UP_M = 8;  // scene.py, assumed
// Exposure as sim/scene.py (step-6-scene 2c8af7e), assumed there: flat ground
// 5 m ahead of the rover shows at 0.55 on screen through the tone curve.
const EXPOSURE_REF_RANGE_M = 5, EXPOSURE_REF_LEVEL = 0.55;  // scene.py:80-81 (2c8af7e)
const LAMP_RGB = [1.0, 0.93, 0.8];   // assumed (linear)
const FILL_RGB = [0.62, 0.68, 0.82]; // assumed (linear)
const FILL_LEVEL = 0.22;                    // sim/scene.py:156, assumed: display-linear, before gamma
const FILL_AZ_DEG = 200, FILL_EL_DEG = 35;  // sim/scene.py:157, assumed: where the fill comes from (fixed)

// Viewer only, assumed. "low" is for weak GPUs (?quality=low).
const QUALITY = {
  // patch: the fine height patch ahead of each rover, `res` m spacing over a
  // square `half` m each way about a point PATCH_AHEAD_M ahead.
  high: { fineStride: 1, shadow: 4096, nearStride: 1, farStride: 2, rocks: true, patch: { res: 0.05, half: 16 } },
  low: { fineStride: 2, shadow: 2048, nearStride: 2, farStride: 4, rocks: true, patch: null },
};
const PATCH_AHEAD_M = 9;       // patch centre ahead of the rover, where the lamp pool is
const PATCH_MOVE_M = 4;        // patches along the track this far apart (a patch covers the pool within ~6 m)
const PATCH_PREFETCH = 4;      // build this many patches ahead along the logged track
const PATCH_WORKERS = 2;
const PATCH_TAPER_M = 0.75;    // blend to the tile surface over the patch's outer edge
const COARSE_STRIDE = 2;       // coarse map drawn at 2 m
const FINE_PAD_M = 70;         // load 0.25 m tiles within this of each rover
const FINE_VIEW_M = 150;       // viewer only, assumed: fine tiles and patches draw within this of the camera (0.25 m is ~1.5 px there)
const DRAPE_M = 0.18;          // lines float this far above the ground
const LABEL_GAP_PX = 6;       // viewer only, assumed: a rover tag hides when a find label is this close
const PARTICLE_SIZE = 0.3;    // viewer only, assumed: point size for an average-weight particle
const PARTICLE_MIN = 0.5, PARTICLE_MAX = 3;   // size limits, as multiples of PARTICLE_SIZE
const PARTICLE_LIFT_M = 0.08; // above the ground, so the 5 cm patch's lips do not hide them

export const COLORS = {
  A: 0x5aa7ff, B: 0xf2994a,
  reported: 0xd9b44a, confirmed: 0x4fbf72, disputed: 0xe5574f,
  truth: 0xd8f3ff, track: 0xe9ecef, odom: 0x9aa0a6,
};

export class World {
  constructor(el, terrain, quality = "high") {
    this.el = el;
    this.terrain = terrain;
    this.quality = quality in QUALITY ? quality : "high";
    this.q = QUALITY[this.quality];
    const m = terrain.meta;
    // display.json params (Xu et al. 2020 Table 1, old highland), via build_assets.py.
    this.hapke = m.hapke || { hapke_w: 0.48, hapke_b: 0.232, hapke_bs0: 1.5867, hapke_hs: 0.0626 };
    this.moonR = (m.display && m.display.moon_radius_m) || 1737400;
    this.o = new THREE.Vector3(m.x0 + 0.5 * (m.cols - 1) * m.cell, m.y0 + 0.5 * (m.rows - 1) * m.cell,
      0.5 * (m.hmin + m.hmin + 65535 * m.hscale));

    const r = (this.renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" }));
    r.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    r.shadowMap.enabled = true;
    r.shadowMap.type = THREE.PCFSoftShadowMap;
    // sim/scene.py's tone curve, then the sRGB display curve.
    THREE.ShaderChunk.tonemapping_pars_fragment = THREE.ShaderChunk.tonemapping_pars_fragment.replace(
      "vec3 CustomToneMapping( vec3 color ) { return color; }", TONE_GLSL);
    r.toneMapping = THREE.CustomToneMapping;
    r.toneMappingExposure = 1.0;
    el.appendChild(r.domElement);

    this.labels = new CSS2DRenderer();
    Object.assign(this.labels.domElement.style, { position: "absolute", inset: "0", pointerEvents: "none" });
    el.appendChild(this.labels.domElement);

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x000000);
    this.camera = new THREE.PerspectiveCamera(50, 1, 0.3, 30000);
    this.camera.up.set(0, 0, 1);
    this.camera.position.set(0, -60, 40);
    this.controls = new OrbitControls(this.camera, r.domElement);
    this.controls.enableDamping = true;
    this.controls.maxPolarAngle = Math.PI * 0.49;
    this.controls.minDistance = 3;
    this.controls.maxDistance = 4000;

    // Fill light: the one cheat, labelled on screen and switchable. It is
    // there only so a viewer can see the ground; the rovers' cameras never
    // get it. Level, colour and wrap as sim/scene.py's chase view.
    const fc = new THREE.Color().setRGB(...FILL_RGB, THREE.LinearSRGBColorSpace);
    this.fill = new THREE.DirectionalLight(fc, fillIntensity(FILL_LEVEL));
    this.scene.add(this.fill, this.fill.target);
    // scene.py's wrap term: part of the fill reaches every surface.
    this.ambient = new THREE.AmbientLight(fc, fillAmbient(FILL_LEVEL));
    this.scene.add(this.ambient);
    this.lampI = lampIntensity(this.hapke, { camH: CAMERA_HEIGHT_M, lampBelow: LAMP_BELOW_CAMERA_M,
      pitchDeg: CAMERA_PITCH_DEG, refRange: EXPOSURE_REF_RANGE_M, refLevel: EXPOSURE_REF_LEVEL });
    // No stars: a camera exposed for lamp-lit ground cannot see them.
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

  // ------------------------------------------------------------- terrain
  terrainMaterial() {
    const m = this.terrain.meta;
    const base = this.terrain.base;
    const loader = new THREE.TextureLoader();
    const tex = (file, repeat) => {
      const t = loader.load(base + file);
      t.colorSpace = THREE.NoColorSpace;
      t.anisotropy = 8;
      if (repeat) t.wrapS = t.wrapT = THREE.RepeatWrapping;
      return t;
    };
    const strength = (m.display && m.display.strength) ?? 1;
    const albedo = m.albedo ? tex(m.albedo.file) : null;
    const detailN = m.detail ? tex(m.detail.normal, true) : null;
    const detailA = m.detail ? tex(m.detail.albedo, true) : null;
    this.mask = new THREE.DataTexture(new Uint8Array(this.terrain.ntr * this.terrain.ntc), this.terrain.ntc, this.terrain.ntr,
      THREE.RedFormat, THREE.UnsignedByteType);
    this.mask.needsUpdate = true;
    const drive = { hapke: this.hapke, albedo, normalMap: detailN, detailAlb: detailA, strength };
    // The fine patch draws with fineMat; the 0.25 m tiles are not drawn,
    // nor cast shadows, inside a patch.
    this.fineMat = regolith(drive);
    const far = () => new THREE.Vector4(1e9, 1e9, -1e9, -1e9);
    this.patchRects = [far(), far()];
    const rectU = { uP0: { value: this.patchRects[0] }, uP1: { value: this.patchRects[1] } };
    const inRect = `(vXY.x > uP0.x && vXY.y > uP0.y && vXY.x < uP0.z && vXY.y < uP0.w) ||
                    (vXY.x > uP1.x && vXY.y > uP1.y && vXY.x < uP1.z && vXY.y < uP1.w)`;
    this.tileMat = regolith({ ...drive, uniforms: rectU, discard: `if (${inRect}) discard;` });
    this.tileDepthMat = new THREE.MeshDepthMaterial({ depthPacking: THREE.RGBADepthPacking });
    this.tileDepthMat.onBeforeCompile = (sh) => {
      Object.assign(sh.uniforms, rectU);
      sh.vertexShader = sh.vertexShader.replace("#include <common>", `#include <common>
varying vec2 vXY;`)
        .replace("#include <begin_vertex>", `#include <begin_vertex>
vXY = (modelMatrix * vec4(position, 1.0)).xy;`);
      sh.fragmentShader = sh.fragmentShader.replace("#include <common>", `#include <common>
varying vec2 vXY;
uniform vec4 uP0;
uniform vec4 uP1;`)
        .replace("void main() {", `void main() {
 if (${inRect}) discard;`);
    };
    // The coarse map is not drawn where a fine tile is loaded.
    this.coarseMat = regolith({ ...drive,
      uniforms: {
        uMask: { value: this.mask },
        uGridMin: { value: new THREE.Vector2(m.x0 - this.o.x, m.y0 - this.o.y) },
        uTileM: { value: this.terrain.tileM },
        uTiles: { value: new THREE.Vector2(this.terrain.ntc, this.terrain.ntr) },
      },
      discard: `vec2 tij = floor((vXY - uGridMin) / uTileM);
        if (all(greaterThanEqual(tij, vec2(0.0))) && all(lessThan(tij, uTiles))) {
          if (texelFetch(uMask, ivec2(tij), 0).r > 0.5) discard;
        }` });
    this.farNormal = m.far_detail ? tex(m.far_detail.normal, true) : null;
  }

  gridGeometry(grid, stride, { uv = true, drop = null, uvPeriod = null } = {}) {
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
    const per = uvPeriod || (m.detail ? m.detail.period_m : 8);
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

  // Real LOLA round the drive area: the 5 m near backdrop over the 20 m far
  // one, each with a hole where a finer layer is drawn. Lowered 0.5 m so the
  // seams hide, and by r^2 / 2R for the horizon (display.json, "backdrop").
  async buildBackdrop() {
    const m = this.terrain.meta;
    const ox = this.o.x, oy = this.o.y, R = this.moonR;
    const drop = (x, y) => 0.5 + ((x - ox) ** 2 + (y - oy) ** 2) / (2 * R);
    const rect = (g) => ({ x0: g.x0, y0: g.y0, x1: g.x0 + (g.cols - 1) * g.cell, y1: g.y0 + (g.rows - 1) * g.cell });
    const hole = (b, pad) => ({ lo: new THREE.Vector2(b.x0 - ox + pad, b.y0 - oy + pad), hi: new THREE.Vector2(b.x1 - ox - pad, b.y1 - oy - pad) });
    const drive = rect({ x0: m.x0, y0: m.y0, rows: m.rows, cols: m.cols, cell: m.cell });
    const per = m.far_detail ? m.far_detail.period_m : 512;
    const near = await this.terrain.loadBackdrop("backdrop_near");
    const far = await this.terrain.loadBackdrop("backdrop");
    const layers = [];
    if (near) layers.push({ g: near, stride: this.q.nearStride, hole: hole(drive, 2) });
    if (far) layers.push({ g: far, stride: this.q.farStride, hole: near ? hole(rect(near), 40) : hole(drive, 2) });
    for (const L of layers) {
      const geo = this.gridGeometry(L.g, L.stride, { drop, uvPeriod: per });
      const mat = regolith({ hapke: this.hapke, normalMap: this.farNormal,
        uniforms: { uLo: { value: L.hole.lo }, uHi: { value: L.hole.hi } },
        discard: "if (all(greaterThan(vXY, uLo)) && all(lessThan(vXY, uHi))) discard;" });
      const mesh = new THREE.Mesh(geo, mat);
      mesh.receiveShadow = true;
      this.scene.add(mesh);
    }
  }

  // Rocks from the terrain worker's rocks.npy (made up, and listed as made up
  // in display.json): one instanced mesh per loaded fine tile.
  async loadRocks() {
    const r = this.terrain.meta.rocks;
    if (!r || !this.q.rocks) return;
    const a = await fetchF32(this.terrain.base + r.file);
    const T = this.terrain, n = a.length / 7;
    this.rocksByTile = new Map();
    for (let i = 0; i < n; i++) {
      const x = a[i * 7] + T.meta.x0, y = a[i * 7 + 1] + T.meta.y0;
      const k = T.tileKey(x, y);
      if (!k) continue;
      if (!this.rocksByTile.has(k)) this.rocksByTile.set(k, []);
      this.rocksByTile.get(k).push([x, y, a[i * 7 + 2], a[i * 7 + 3], a[i * 7 + 4], a[i * 7 + 5], a[i * 7 + 6]]);
    }
    this.rockMat = regolith({ hapke: this.hapke, color: r.rock_albedo, flat: true });
    this.rockSink = r.sink_frac;
    // A lumpy unit rock: z from 0 (base) to 1 (top), x and y about [-1, 1].
    const g = new THREE.IcosahedronGeometry(1, 1);
    const p = g.attributes.position;
    for (let i = 0; i < p.count; i++) {
      const x = p.getX(i), y = p.getY(i), z = p.getZ(i);
      const k = 1 + 0.18 * Math.sin(3.1 * x + 1.7 * y) + 0.12 * Math.sin(5.3 * y - 2.1 * z) + 0.08 * Math.sin(7.7 * z + x);
      p.setXYZ(i, x * k, y * k, Math.max(0, (z * k + 1) / 2));
    }
    g.computeVertexNormals();
    this.rockGeo = g;
    for (const [key, mesh] of this.fineMeshes) if (mesh && !mesh.userData.rocks) this.addRocks(key, mesh);
  }

  addRocks(key, tileMesh) {
    const list = this.rocksByTile && this.rocksByTile.get(key);
    if (!list || !list.length) return;
    const im = new THREE.InstancedMesh(this.rockGeo, this.rockMat, list.length);
    const M = new THREE.Matrix4(), Q = new THREE.Quaternion(), S = new THREE.Vector3();
    const zAxis = new THREE.Vector3(0, 0, 1), c = new THREE.Color();
    list.forEach(([x, y, lng, sht, h, yaw, seed], i) => {
      Q.setFromAxisAngle(zAxis, yaw);
      S.set(lng / 2, sht / 2, h);
      M.compose(this.groundL(x, y, -this.rockSink * h), Q, S);
      im.setMatrixAt(i, M);
      // Brightness varies about 10% per rock, as sim/terrain_display.py.
      im.setColorAt(i, c.setScalar(1 + 0.1 * Math.sin(seed * 12.9898 + 4.1) * 1.4));
    });
    im.castShadow = true;
    im.receiveShadow = true;
    tileMesh.add(im);
    tileMesh.userData.rocks = im;
  }

  // Load 0.25 m tiles near the rovers, drop the rest.
  updateFine(points) {
    const want = this.terrain.keysNear(points, FINE_PAD_M);
    for (const key of want) {
      if (this.fineMeshes.has(key)) continue;
      this.fineMeshes.set(key, null);
      this.terrain.loadTile(key).then((g) => {
        if (!this.fineMeshes.has(key)) return;
        const mesh = new THREE.Mesh(this.gridGeometry(g, this.q.fineStride), this.tileMat);
        mesh.customDepthMaterial = this.tileDepthMat;
        mesh.castShadow = true;
        mesh.receiveShadow = true;
        this.scene.add(mesh);
        this.fineMeshes.set(key, mesh);
        this.setMask(key, 1);
        if (this.rockGeo) this.addRocks(key, mesh);
      });
    }
    for (const [key, mesh] of this.fineMeshes) {
      if (want.has(key) || !mesh) continue;
      // Keep tiles a little longer than needed so scrubbing back is instant.
      if (this.fineMeshes.size <= 12) continue;
      this.scene.remove(mesh);
      mesh.geometry.dispose();
      if (mesh.userData.rocks) mesh.userData.rocks.dispose();
      this.fineMeshes.delete(key);
      this.setMask(key, 0);
    }
  }

  // ------------------------------------------------------- fine patch
  // Patches are built ahead along the logged track, so at fast playback the
  // lamp pool is already on a fine patch when the rover gets there.
  async initPatches() {
    const P = this.q.patch, lm = this.terrain.meta.lips;
    if (!P || !lm) return;
    const lips = await fetchF32(this.terrain.base + lm.file);
    this.patchWorkers = [];
    for (let i = 0; i < PATCH_WORKERS; i++) {
      const w = new Worker(new URL("./patch_worker.js", import.meta.url), { type: "module" });
      w.postMessage({ init: { lips, x0: this.terrain.meta.x0, y0: this.terrain.meta.y0 } });
      w.onmessage = (e) => { w.busy = false; this.patchReady(e.data); this.pumpPatches(); };
      this.patchWorkers.push(w);
    }
    this.patches = {};      // agency -> Map(key -> {mesh, center, rect})
    this.patchActive = {};  // agency -> key
    this.patchQueue = [];
    this.patchInFlight = new Set();
    const n = Math.round((2 * P.half) / P.res) + 1;
    const idx = new Uint32Array((n - 1) * (n - 1) * 6);
    let t = 0;
    for (let a = 0; a < n - 1; a++) for (let b = 0; b < n - 1; b++) {
      const v = a * n + b;
      idx[t++] = v; idx[t++] = v + 1; idx[t++] = v + n;
      idx[t++] = v + 1; idx[t++] = v + n + 1; idx[t++] = v + n;
    }
    this.patchIndex = new THREE.BufferAttribute(idx, 1);
    this.patchN = n;
  }

  // Where the lamp pool is: PATCH_AHEAD_M ahead of the rover.
  poolCenter([x, y, h]) { return [x + PATCH_AHEAD_M * Math.cos(h), y + PATCH_AHEAD_M * Math.sin(h)]; }

  // The pool centres from t onward, PATCH_MOVE_M apart along the logged track.
  poolCentersAhead(run, a, t) {
    const out = [this.poolCenter(run.poseAt(a, t))];
    for (let k = run.stepAt(t) + 1; k < run.steps.length && out.length < PATCH_PREFETCH + 1; k++) {
      const c = this.poolCenter(run.steps[k].rovers[a].true_pose), last = out[out.length - 1];
      if (Math.hypot(c[0] - last[0], c[1] - last[1]) >= PATCH_MOVE_M) out.push(c);
    }
    return out;
  }

  nearestPatch(a, c) {
    let best = null, bd = Infinity;
    for (const [key, p] of this.patches[a]) {
      const d = Math.hypot(c[0] - p.center[0], c[1] - p.center[1]);
      if (d < bd) { bd = d; best = key; }
    }
    return { key: best, d: bd };
  }

  updatePatches(run, t) {
    if (!this.patches) return;
    for (const a of run.agencies) {
      if (!this.patches[a]) this.patches[a] = new Map();
      const want = this.poolCentersAhead(run, a, t);
      // Show the built patch nearest the pool now; hold the old one until a nearer one is ready.
      const { key } = this.nearestPatch(a, want[0]);
      if (key && key !== this.patchActive[a]) this.showPatch(a, key);
      // Queue what is missing, nearest in time first.
      want.forEach((c, i) => {
        const near = this.nearestPatch(a, c);
        if (near.d < PATCH_MOVE_M * 0.75) return;
        const id = `${a}:${Math.round(c[0] / PATCH_MOVE_M)}:${Math.round(c[1] / PATCH_MOVE_M)}`;
        if (this.patchInFlight.has(id) || this.patchQueue.some((q) => q.id === id)) return;
        this.patchQueue.push({ id, a, c, pri: i });
      });
      // Forget patches the rover has passed.
      const keep = new Set(want.map((c) => this.nearestPatch(a, c).key));
      keep.add(this.patchActive[a]);
      if (this.patches[a].size > PATCH_PREFETCH + 3) {
        for (const [k2, p] of this.patches[a]) {
          if (keep.has(k2)) continue;
          p.mesh.geometry.dispose(); this.scene.remove(p.mesh); this.patches[a].delete(k2);
        }
      }
    }
    // Drop queued jobs that are no longer wanted (scrubbing), keep order by priority.
    this.patchQueue = this.patchQueue.filter((q) => {
      const want = this.poolCentersAhead(run, q.a, t);
      return want.some((c) => Math.hypot(c[0] - q.c[0], c[1] - q.c[1]) < PATCH_MOVE_M * 0.5);
    }).sort((p, q) => p.pri - q.pri);
    this.pumpPatches();
  }

  pumpPatches() {
    for (const w of this.patchWorkers) {
      if (w.busy) continue;
      let job = null;
      while (this.patchQueue.length && !job) {
        const q = this.patchQueue.shift();
        job = this.patchJob(q);
      }
      if (!job) return;
      w.busy = true;
      this.patchInFlight.add(job.id);
      w.postMessage(job, [job.base.buffer]);
    }
  }

  // The worker's input for a patch centred near c, or null if its tiles are not loaded yet.
  patchJob({ id, a, c }) {
    const P = this.q.patch, T = this.terrain, m = T.meta;
    // Snap to the patch spacing so vertices land on the same world points.
    const px0 = m.x0 + Math.round((c[0] - P.half - m.x0) / P.res) * P.res;
    const py0 = m.y0 + Math.round((c[1] - P.half - m.y0) / P.res) * P.res;
    const span = (this.patchN - 1) * P.res;
    // Every 0.25 m tile under it must be loaded, or the base would be the 1 m map.
    const corners = [[px0, py0], [px0 + span, py0], [px0, py0 + span], [px0 + span, py0 + span]];
    if (corners.some(([u, v]) => { const k = T.tileKey(u, v); return !k || !T.tiles.has(k); })) {
      corners.forEach(([u, v]) => { const k = T.tileKey(u, v); if (k) T.loadTile(k); });
      return null;
    }
    const cell = m.cell;
    const bx0 = m.x0 + (Math.floor((px0 - m.x0) / cell) - 2) * cell, by0 = m.y0 + (Math.floor((py0 - m.y0) / cell) - 2) * cell;
    const bnx = Math.ceil(span / cell) + 6, bny = bnx;
    const base = new Float32Array(bnx * bny);
    for (let i = 0; i < bny; i++) for (let j = 0; j < bnx; j++) base[i * bnx + j] = T.heightAt(bx0 + j * cell, by0 + i * cell);
    const ax0 = m.x0 - m.cell / 2, ay0 = m.y0 - m.cell / 2;
    return { id, a, center: c, base, bx0, by0, bnx, bny, cell, px0, py0, res: P.res, n: this.patchN,
      taper: PATCH_TAPER_M, o: [this.o.x, this.o.y, this.o.z], alb: [ax0, ay0, m.cols * m.cell, m.rows * m.cell],
      per: m.detail ? m.detail.period_m : 8 };
  }

  patchReady({ id, a, center, pos, nrm, uv0, uv1, px0, py0, span }) {
    this.patchInFlight.delete(id);
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setAttribute("normal", new THREE.BufferAttribute(nrm, 3));
    g.setAttribute("uv", new THREE.BufferAttribute(uv0, 2));
    g.setAttribute("uv1", new THREE.BufferAttribute(uv1, 2));
    g.setIndex(this.patchIndex);
    g.computeBoundingSphere();
    const mesh = new THREE.Mesh(g, this.fineMat);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    mesh.visible = false;
    this.scene.add(mesh);
    const key = `${px0.toFixed(2)}_${py0.toFixed(2)}`;
    const old = this.patches[a].get(key);
    if (old) { this.scene.remove(old.mesh); old.mesh.geometry.dispose(); }
    this.patches[a].set(key, { mesh, center, rect: [px0, py0, span] });
    if (old && this.patchActive[a] === key) this.showPatch(a, key);
    // Show it now if it is nearer the pool than the one showing.
    if (this.run && this.run.agencies.includes(a)) {
      const c = this.poolCenter(this.run.poseAt(a, this.currentT || 0));
      if (this.nearestPatch(a, c).key === key) this.showPatch(a, key);
    }
  }

  showPatch(a, key) {
    const prev = this.patchActive[a] && this.patches[a].get(this.patchActive[a]);
    if (prev) prev.mesh.visible = false;
    const p = this.patches[a].get(key);
    p.mesh.visible = true;
    this.patchActive[a] = key;
    const [px0, py0, span] = p.rect;
    this.patchRects[a === "A" ? 0 : 1].set(px0 - this.o.x, py0 - this.o.y, px0 + span - this.o.x, py0 + span - this.o.y);
  }

  // Far from the camera, the 0.25 m tiles and the 5 cm patches go sub-pixel
  // and their rim lips alias into dark speckle with a hard outline where
  // they stop. Beyond FINE_VIEW_M they hide and the coarse map draws there.
  applyDistanceLod() {
    const cam = this.camera.position;
    for (const [key, mesh] of this.fineMeshes) {
      if (!mesh) continue;
      if (!mesh.geometry.boundingBox) mesh.geometry.computeBoundingBox();
      const near = mesh.geometry.boundingBox.distanceToPoint(cam) < FINE_VIEW_M;
      if (mesh.visible !== near) { mesh.visible = near; this.setMask(key, near ? 1 : 0); }
    }
    for (const a of Object.keys(this.patchActive || {})) {
      const p = this.patchActive[a] && this.patches[a].get(this.patchActive[a]);
      if (!p || !p.mesh) continue;
      if (!p.mesh.geometry.boundingBox) p.mesh.geometry.computeBoundingBox();
      const near = p.mesh.geometry.boundingBox.distanceToPoint(cam) < FINE_VIEW_M;
      if (p.mesh.visible === near) continue;
      p.mesh.visible = near;
      const [px0, py0, span] = p.rect, r = this.patchRects[a === "A" ? 0 : 1];
      if (near) r.set(px0 - this.o.x, py0 - this.o.y, px0 + span - this.o.x, py0 + span - this.o.y);
      else r.set(1e9, 1e9, -1e9, -1e9);
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
    // three.js smoothsteps the cone from the outer edge in to
    // cos(outer * (1 - penumbra)) = cos(inner), and falls off as 1/d^2 to 80 m.
    const lamp = new THREE.SpotLight(new THREE.Color().setRGB(...LAMP_RGB, THREE.LinearSRGBColorSpace), this.lampI,
      LAMP_FADE_END_M, THREE.MathUtils.degToRad(LAMP_OUTER_DEG), 1 - LAMP_INNER_DEG / LAMP_OUTER_DEG, 2);
    lamp.position.set(0.12, 0, CAMERA_HEIGHT_M - LAMP_BELOW_CAMERA_M);
    const p = THREE.MathUtils.degToRad(CAMERA_PITCH_DEG);
    lamp.target.position.set(0.12 + 10 * Math.cos(p), 0, CAMERA_HEIGHT_M - LAMP_BELOW_CAMERA_M - 10 * Math.sin(p));
    lamp.castShadow = true;
    lamp.shadow.mapSize.set(this.q.shadow, this.q.shadow);
    lamp.shadow.camera.near = 0.3;
    lamp.shadow.camera.far = LAMP_FADE_END_M;
    lamp.shadow.bias = -0.0001;
    lamp.shadow.normalBias = 0.03;
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
    this.rovers[a] = { g, lamp, tag };
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
    // The navigation filter's particles, when the run logs them.
    this.particles = {};
    if (run.hasParticles) for (const a of run.agencies) {
      const P = new THREE.Points(new THREE.BufferGeometry(), this.particleMat(COLORS[a]));
      P.frustumCulled = false;
      P.userData.k = null;
      this.overlay.add(P);
      this.particles[a] = P;
    }
    // Real ice, hidden from the rovers (stand-in runs carry it).
    this.truthObjs = [];
    for (const [i, ice] of ((run.truth && run.truth.ice) || []).entries()) {
      const d = new THREE.Mesh(new THREE.OctahedronGeometry(0.3), new THREE.MeshBasicMaterial({ color: COLORS.truth }));
      d.position.copy(this.groundL(ice.x, ice.y, 0.8));
      d.scale.set(1, 1, 1.6);
      // Marker only: the sidebar legend names it, and a text label here
      // would cover the find label at the same spot.
      const ring = this.ring(ice.x, ice.y, ice.radius, this.lineMat(COLORS.truth, 1.5, { dashed: true, opacity: 0.8 }));
      const frost = frostPatch(this, ice.x, ice.y, ice.radius, `${run.log.run_id}:${i}`);
      this.overlay.add(d, ring, frost);
      this.truthObjs.push(d, ring, frost);
    }
    // Mission sites a run log called ice: a frost
    // patch from the moment of that verdict. It is the find, not hidden truth,
    // so it shows whatever the truth toggle says.
    this.iceSites = [];
    const verdictT = {};
    for (const st of run.steps) for (const e of st.mission_events || []) {
      if (e.type === "site" && e.verdict === "ice" && !(e.site_id in verdictT)) verdictT[e.site_id] = st.t;
    }
    for (const site of (run.log.mission && run.log.mission.sites) || []) {
      if (site.verdict !== "ice") continue;
      const frost = frostPatch(this, site.x, site.y, site.radius || 6, `${run.log.run_id}:${site.id}`);
      frost.visible = false;
      this.overlay.add(frost);
      this.iceSites.push({ obj: frost, t: verdictT[site.id] ?? 0 });
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

  // Particles as round dots on the ground, one draw per rover. Dot diameter
  // grows with sqrt(w * n), so dot area follows the particle's weight and an
  // evenly weighted cloud has equal dots.
  particleMat(color) {
    const m = new THREE.PointsMaterial({ color, size: PARTICLE_SIZE, sizeAttenuation: true });
    m.onBeforeCompile = (sh) => {
      sh.vertexShader = "attribute float pSize;\n" + sh.vertexShader.replace("gl_PointSize = size;", "gl_PointSize = size * pSize;");
      sh.fragmentShader = sh.fragmentShader.replace("#include <color_fragment>", `#include <color_fragment>
        vec2 pc = gl_PointCoord - 0.5;
        float rr = dot(pc, pc);
        if (rr > 0.25) discard;
        if (rr > 0.13) diffuseColor.rgb *= 0.2;`);
    };
    return m;
  }

  fillParticles(P, list) {
    const n = list.length;
    let g = P.geometry;
    if (!g.attributes.position || g.attributes.position.count < n) {
      g.dispose();
      g = new THREE.BufferGeometry();
      const cap = Math.max(n, 128);
      g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(cap * 3), 3));
      g.setAttribute("pSize", new THREE.BufferAttribute(new Float32Array(cap), 1));
      P.geometry = g;
    }
    const pos = g.attributes.position.array, sz = g.attributes.pSize.array;
    for (let i = 0; i < n; i++) {
      const [x, y, w] = list[i];
      const v = this.groundL(x, y, PARTICLE_LIFT_M);
      pos[3 * i] = v.x; pos[3 * i + 1] = v.y; pos[3 * i + 2] = v.z;
      sz[i] = Math.min(PARTICLE_MAX, Math.max(PARTICLE_MIN, Math.sqrt(Math.max(w, 0) * n)));
    }
    g.attributes.position.needsUpdate = true;
    g.attributes.pSize.needsUpdate = true;
    g.setDrawRange(0, n);
  }

  setVisibility({ fill, tracks, truth }) {
    this.fill.visible = fill;
    this.ambient.visible = fill;
    // hideOdom: a page may leave the odometry-alone line out of the 3D view (Mission Control does).
    for (const t of Object.values(this.tracks || {})) for (const [k, l] of Object.entries(t)) l.line.visible = tracks && !(k === "odom" && this.hideOdom);
    for (const o of this.truthObjs || []) o.visible = truth;
    for (const o of Object.values(this.estDots || {})) o.visible = tracks;
    for (const o of Object.values(this.sigmaRings || {})) if (o) o.visible = tracks;
  }

  // ----------------------------------------------------------- per frame
  update(t, opts) {
    const run = this.run;
    if (!run) return;
    const k = run.stepAt(t);
    for (const s of this.iceSites || []) s.obj.visible = t >= s.t;
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
      // The cloud the filter held after step k, until step k + 1.
      const P = this.particles[a];
      if (P) {
        if (P.userData.k !== k) { this.fillParticles(P, r.particles || []); P.userData.k = k; }
        P.visible = !!(opts.tracks && opts.particles) && P.geometry.drawRange.count > 0;
      }
    }
    this.updateFine(pts);
    this.updatePatches(run, t);

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

  // After the label pass: a find label that would run off the right edge
  // flips to above-left (as map.js does), and a rover's tag hides while a
  // find label covers it. Takes effect from the next frame.
  declutterLabels() {
    const box = this.el.getBoundingClientRect();
    const finds = [];
    for (const obj of (this.findObjs || new Map()).values()) {
      const lbl = obj.userData.lbl, el = lbl.element;
      if (!obj.visible || el.style.display === "none") continue;
      const r = el.getBoundingClientRect();
      if (!r.width) continue;
      const anchorX = lbl.center.x === 0 ? r.left : r.right;
      const wantLeft = anchorX + r.width > box.right - 4;
      if ((lbl.center.x === 1) !== wantLeft) lbl.center.set(wantLeft ? 1 : 0, 1);
      finds.push(r);
    }
    for (const { tag } of Object.values(this.rovers)) {
      if (!tag) continue;
      const r = tag.getBoundingClientRect();
      const pad = LABEL_GAP_PX;
      const hit = finds.some((f) => r.left < f.right + pad && r.right > f.left - pad && r.top < f.bottom + pad && r.bottom > f.top - pad);
      tag.style.visibility = hit ? "hidden" : "";
    }
  }

  render() {
    this.applyDistanceLod();
    this.controls.update();
    // The fill comes from a fixed direction, as scene.py's; shaded Lambert,
    // so no opposition blob when it is behind the camera.
    const t = this.controls.target;
    const az = THREE.MathUtils.degToRad(FILL_AZ_DEG);
    const el = THREE.MathUtils.degToRad(FILL_EL_DEG);
    this.fill.target.position.copy(t);
    this.fill.position.set(t.x + 100 * Math.cos(az) * Math.cos(el), t.y + 100 * Math.sin(az) * Math.cos(el), t.z + 100 * Math.sin(el));
    this.renderer.render(this.scene, this.camera);
    this.labels.render(this.scene, this.camera);
    this.declutterLabels();
  }
}
