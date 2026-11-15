// Frost patch: the viewer's marker for ground with water ice. White at the
// core, pale blue at a ragged edge, mottled, with crystal speckle and a few
// glints that twinkle. Drawn for people watching only; the rovers' camera
// frames (sim/scene.py) carry no ice. The look is assumed, not measured.
import * as THREE from "three";

export const ICE_HEX = 0xcfeeff;     // rings, labels, ticks: one ice colour
export const ICE_CSS = "#cfeeff";
const LIFT_M = 0.12;                 // above the ground, so the patch does not sink into it
const GRID = 20;                     // draping grid, cells per side
const GLINTS_PER_M2 = 0.5;

function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const hash = (s) => { let h = 2166136261; for (const ch of String(s)) h = Math.imul(h ^ ch.charCodeAt(0), 16777619); return h >>> 0; };

// Smooth value noise on a G x G grid over [0, 1]^2.
function valueNoise(R, G) {
  const g = Array.from({ length: (G + 1) * (G + 1) }, R);
  return (u, v) => {
    const x = u * G, y = v * G, i = Math.min(G - 1, Math.floor(x)), j = Math.min(G - 1, Math.floor(y));
    const fx = x - i, fy = y - j, sx = fx * fx * (3 - 2 * fx), sy = fy * fy * (3 - 2 * fy);
    const s = (a, b) => g[b * (G + 1) + a];
    return (s(i, j) * (1 - sx) + s(i + 1, j) * sx) * (1 - sy) + (s(i, j + 1) * (1 - sx) + s(i + 1, j + 1) * sx) * sy;
  };
}

function frostTexture(seed) {
  const N = 256, R = rng(seed);
  const cv = document.createElement("canvas");
  cv.width = cv.height = N;
  const c = cv.getContext("2d");
  const img = c.createImageData(N, N);
  const waves = Array.from({ length: 5 }, (_, i) => [3 + 2 * i + Math.floor(R() * 3), R() * 6.283, 0.07 / (1 + 0.6 * i)]);
  const n1 = valueNoise(R, 6), n2 = valueNoise(R, 24);
  for (let y = 0; y < N; y++) {
    for (let x = 0; x < N; x++) {
      const u = ((x + 0.5) / N) * 2 - 1, v = ((y + 0.5) / N) * 2 - 1;
      const a = Math.atan2(v, u);
      let edge = 0.84;
      for (const [k, p, amp] of waves) edge += amp * Math.sin(k * a + p);
      const d = Math.hypot(u, v) / edge;
      const n = 0.6 * n1((u + 1) / 2, (v + 1) / 2) + 0.4 * n2((u + 1) / 2, (v + 1) / 2);
      const alpha = Math.min(1, Math.max(0, (1 - d) / 0.22)) * (0.5 + 0.5 * n);
      const t = Math.min(1, d) ** 1.5;            // 0 core (white) .. 1 edge (pale blue)
      const lift = 0.85 + 0.15 * n;
      const o = 4 * (y * N + x);
      img.data[o] = (246 - 110 * t) * lift;
      img.data[o + 1] = (251 - 50 * t) * lift;
      img.data[o + 2] = 255 * lift;
      img.data[o + 3] = 255 * alpha;
    }
  }
  c.putImageData(img, 0, 0);
  // Crystal speckle: bright dots and short needles at 60 degree steps.
  const inside = () => { for (;;) { const u = R() * 2 - 1, v = R() * 2 - 1; if (Math.hypot(u, v) < 0.72) return [((u + 1) / 2) * N, ((v + 1) / 2) * N]; } };
  for (let i = 0; i < 160; i++) {
    const [px, py] = inside();
    c.fillStyle = `rgba(255,255,255,${0.35 + 0.5 * R()})`;
    c.fillRect(px, py, 1 + (R() < 0.2), 1 + (R() < 0.2));
  }
  c.lineWidth = 0.8;
  for (let i = 0; i < 50; i++) {
    const [px, py] = inside(), ang = (Math.floor(R() * 3) * Math.PI) / 3 + 0.2 * (R() - 0.5), L = 3 + 7 * R();
    c.strokeStyle = `rgba(235,248,255,${0.3 + 0.4 * R()})`;
    c.beginPath(); c.moveTo(px - Math.cos(ang) * L, py - Math.sin(ang) * L); c.lineTo(px + Math.cos(ang) * L, py + Math.sin(ang) * L); c.stroke();
  }
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  return tex;
}

let starTex = null;
function glintTexture() {
  if (starTex) return starTex;
  const N = 64, cv = document.createElement("canvas");
  cv.width = cv.height = N;
  const c = cv.getContext("2d");
  const g = c.createRadialGradient(N / 2, N / 2, 0, N / 2, N / 2, N / 2);
  g.addColorStop(0, "rgba(255,255,255,1)"); g.addColorStop(0.15, "rgba(220,240,255,0.6)"); g.addColorStop(1, "rgba(200,230,255,0)");
  c.fillStyle = g; c.fillRect(0, 0, N, N);
  c.strokeStyle = "rgba(255,255,255,0.9)"; c.lineWidth = 1.5;
  c.beginPath(); c.moveTo(N / 2, 2); c.lineTo(N / 2, N - 2); c.moveTo(2, N / 2); c.lineTo(N - 2, N / 2); c.stroke();
  starTex = new THREE.CanvasTexture(cv);
  return starTex;
}

// A frost patch of `radius` metres at world (x, y), draped on the terrain.
// `seed` (any value) picks the patch's shape, so each site looks its own.
export function frostPatch(world, x, y, radius, seed = 0) {
  const R = rng(hash(seed));
  const group = new THREE.Group();
  const geo = new THREE.PlaneGeometry(2 * radius, 2 * radius, GRID, GRID);
  const pos = geo.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    const v = world.groundL(x + pos.getX(i), y + pos.getY(i), LIFT_M);
    pos.setXYZ(i, v.x, v.y, v.z);
  }
  geo.computeVertexNormals();
  geo.computeBoundingSphere();
  const mesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({
    map: frostTexture(hash(seed)), transparent: true, depthWrite: false, toneMapped: false,
    polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2,
  }));
  mesh.renderOrder = 2;

  // Glints: additive points whose colour pulses, so black means unseen.
  const n = Math.max(6, Math.round(GLINTS_PER_M2 * Math.PI * radius * radius));
  const gp = new Float32Array(3 * n), gc = new Float32Array(3 * n), phase = [], rate = [];
  for (let i = 0; i < n; i++) {
    const r = radius * 0.75 * Math.sqrt(R()), a = R() * 6.283;
    const v = world.groundL(x + r * Math.cos(a), y + r * Math.sin(a), LIFT_M + 0.05);
    gp.set([v.x, v.y, v.z], 3 * i);
    phase.push(R() * 6.283); rate.push(0.8 + 1.6 * R());
  }
  const pg = new THREE.BufferGeometry();
  pg.setAttribute("position", new THREE.BufferAttribute(gp, 3));
  pg.setAttribute("color", new THREE.BufferAttribute(gc, 3));
  const glints = new THREE.Points(pg, new THREE.PointsMaterial({
    map: glintTexture(), size: 0.45, sizeAttenuation: true, vertexColors: true,
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, toneMapped: false,
  }));
  glints.renderOrder = 3;
  glints.onBeforeRender = () => {
    const s = performance.now() / 1000;
    for (let i = 0; i < n; i++) {
      const k = Math.max(0, Math.sin(s * rate[i] + phase[i])) ** 8;
      gc[3 * i] = 0.85 * k; gc[3 * i + 1] = 0.95 * k; gc[3 * i + 2] = k;
    }
    pg.attributes.color.needsUpdate = true;
  };
  group.add(mesh, glints);
  return group;
}
