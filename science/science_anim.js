// Science stop animation: arm out, drill down, dust up, core back, stow.
//
// Plays a science record's `phases` (sim/science.py) on a rover in a
// three.js scene. The web viewer can import this module and call
// rig.update(t) with the replay clock; preview.html shows it on its own.
//
//   import { buildRover, ScienceRig } from "./science_anim.js";
//   const rover = buildRover();               // or the viewer's own rover
//   const rig = new ScienceRig(rover, scene); // adds the arm and the dust
//   rig.setRecord(record);                    // a record or a `science` event
//   rig.update(t);                            // t in simulated seconds
//
// Axes: three.js is y-up. The rover group uses x forward, y up, z right
// (rover frame x forward, y left, z up -> three x, -z, y). Lengths in metres.

import * as THREE from "three";

// Rover body, copied from sim/scene.py _rover_polydata() so both views match.
const BODY = { x: -0.2, z: 0.75, len: 1.7, wid: 1.2, h: 0.5 };
const DECK = { x: -0.2, z: 1.05, len: 1.2, wid: 1.0, h: 0.08 };
const CAMERA_HEIGHT_M = 2.5;       // Atha et al. 2024, sec. V (via sim/scene.py)
const LAMP_BELOW_CAMERA_M = 0.2;   // Atha et al. 2024, sec. V (via sim/scene.py)

// ASSUMED: arm and drill geometry, sized to look right on that body.
const ARM = {
  side: 0.42,                    // metres to the right of the centre line
  shoulder: [0.55, 1.12],        // (forward, up) on the front of the deck
  upper: 0.9, fore: 0.85,        // link lengths
  stowWrist: [-0.05, 2.32],      // drill mast stands upright on the deck
  workWrist: [1.55, 1.45],       // drill mast over open ground ahead
  mastLen: 1.2,                  // drill mast; the bit slides inside it
  bitLen: 1.5,
};
const DRILL_DEPTH_M = 1.0;       // PROSPECT brief p. 5: "1 m drill"
const MOON_G = 1.62;             // lunar surface gravity, m/s^2
// ASSUMED: dust. No air on the Moon, so grains fly on clean ballistic arcs
// and land; nothing billows or hangs.
const DUST = { max: 1400, rate: 220, vMin: 0.4, vMax: 2.2, size: 0.05 };

const VERDICT_COLOR = { ice: 0x58b4ff, trace: 0xb9c7d6, dry: 0x8a7f72 };

// ---------------------------------------------------------------------------

export function buildRover() {
  const g = new THREE.Group();
  const metal = new THREE.MeshStandardMaterial({ color: 0xc9ccd1, metalness: 0.3, roughness: 0.55 });
  const dark = new THREE.MeshStandardMaterial({ color: 0x2b2e33, roughness: 0.8 });
  const box = (cx, cy, cz, dx, dy, dz, m = metal) => {
    const b = new THREE.Mesh(new THREE.BoxGeometry(dx, dz, dy), m);
    b.position.set(cx, cz, -cy);
    b.castShadow = b.receiveShadow = true;
    g.add(b);
    return b;
  };
  box(BODY.x, 0, BODY.z, BODY.len, BODY.wid, BODY.h);
  box(DECK.x, 0, DECK.z, DECK.len, DECK.wid, DECK.h, dark);
  box(0, 0, 1.75, 0.1, 0.1, 1.5);                        // mast
  box(0.02, 0, CAMERA_HEIGHT_M, 0.18, 0.4, 0.14, dark);  // camera head
  for (const x of [0.45, -0.85]) for (const y of [0.72, -0.72]) {
    const w = new THREE.Mesh(new THREE.CylinderGeometry(0.3, 0.3, 0.22, 24), dark);
    w.rotation.x = Math.PI / 2;
    w.position.set(x, 0.3, -y);
    w.castShadow = true;
    g.add(w);
  }
  return g;
}

export function lampPose() {
  // Lamp sits under the camera head, aimed along the camera axis.
  return { x: 0.12, y: CAMERA_HEIGHT_M - LAMP_BELOW_CAMERA_M };
}

// ---------------------------------------------------------------------------

const ease = (u) => u * u * (3 - 2 * u);
const clamp01 = (u) => Math.min(1, Math.max(0, u));

function solveArm(wx, wy) {
  // Two-link inverse kinematics in the rover's x-up plane, elbow up.
  const [sx, sy] = ARM.shoulder, L1 = ARM.upper, L2 = ARM.fore;
  const dx = wx - sx, dy = wy - sy;
  const d = Math.min(Math.hypot(dx, dy), L1 + L2 - 1e-4);
  const c2 = (d * d - L1 * L1 - L2 * L2) / (2 * L1 * L2);
  const t2 = -Math.acos(Math.max(-1, Math.min(1, c2)));
  const t1 = Math.atan2(dy, dx) - Math.atan2(L2 * Math.sin(t2), L1 + L2 * Math.cos(t2));
  return [t1, t2];
}

export class ScienceRig {
  constructor(rover, scene) {
    this.rover = rover;
    this.scene = scene;
    this.record = null;
    this._buildArm();
    this._buildDust();
    this._lastT = null;
    this.setPose(0);
  }

  _buildArm() {
    const m = new THREE.MeshStandardMaterial({ color: 0xe1e3e6, metalness: 0.35, roughness: 0.45 });
    const joint = new THREE.MeshStandardMaterial({ color: 0x3a3e45, roughness: 0.6 });
    const link = (len, r) => {
      const mesh = new THREE.Mesh(new THREE.BoxGeometry(len, r, r), m);
      mesh.position.x = len / 2;
      mesh.castShadow = true;
      return mesh;
    };
    const knuckle = () => {
      const k = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.07, 0.14, 16), joint);
      k.rotation.x = Math.PI / 2;
      return k;
    };
    this.shoulder = new THREE.Group();
    this.shoulder.position.set(ARM.shoulder[0], ARM.shoulder[1], ARM.side);
    this.shoulder.add(knuckle(), link(ARM.upper, 0.09));
    this.elbow = new THREE.Group();
    this.elbow.position.x = ARM.upper;
    this.elbow.add(knuckle(), link(ARM.fore, 0.075));
    this.wrist = new THREE.Group();
    this.wrist.position.x = ARM.fore;
    this.wrist.add(knuckle());

    const mast = new THREE.Mesh(new THREE.BoxGeometry(0.12, ARM.mastLen, 0.12),
      new THREE.MeshStandardMaterial({ color: 0x9aa0a8, metalness: 0.4, roughness: 0.5 }));
    mast.position.y = -ARM.mastLen / 2;
    mast.castShadow = true;
    this.bit = new THREE.Group();
    const rod = new THREE.Mesh(new THREE.CylinderGeometry(0.025, 0.025, ARM.bitLen, 12),
      new THREE.MeshStandardMaterial({ color: 0x6d737b, metalness: 0.8, roughness: 0.3 }));
    rod.position.y = ARM.bitLen / 2;
    const flute = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.012, 0.12, 6),
      new THREE.MeshStandardMaterial({ color: 0x3b3f45, metalness: 0.9, roughness: 0.25 }));
    flute.position.y = 0.06;
    this.core = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.03, 0.3, 12),
      new THREE.MeshStandardMaterial({ color: 0x58b4ff, emissive: 0x000000, roughness: 0.4 }));
    this.core.position.y = 0.27;
    this.core.visible = false;
    this.bit.add(rod, flute, this.core);
    this.bitRest = -ARM.mastLen - 0.05;   // tip just below the mast foot
    this.bit.position.y = this.bitRest;
    this.wrist.add(mast, this.bit);
    this.elbow.add(this.wrist);
    this.shoulder.add(this.elbow);
    this.rover.add(this.shoulder);

    // Spectrometer light on the deck: pulses while the core is read.
    this.specLight = new THREE.Mesh(new THREE.SphereGeometry(0.05, 16, 12),
      new THREE.MeshStandardMaterial({ color: 0x222222, emissive: 0x000000 }));
    this.specLight.position.set(-0.55, DECK.z + 0.09, ARM.side);
    this.rover.add(this.specLight);

    // Drill hole and cuttings pile, on the ground under the work position.
    const hx = ARM.workWrist[0];
    this.hole = new THREE.Mesh(new THREE.CircleGeometry(0.06, 20),
      new THREE.MeshBasicMaterial({ color: 0x050505 }));
    this.hole.rotation.x = -Math.PI / 2;
    this.hole.position.set(hx, 0.012, ARM.side);
    this.pile = new THREE.Mesh(new THREE.ConeGeometry(0.22, 0.06, 24, 1, true),
      new THREE.MeshStandardMaterial({ color: 0x7c7770, roughness: 1, side: THREE.DoubleSide }));
    this.pile.position.set(hx, 0.0, ARM.side);
    this.pile.receiveShadow = true;
    this.rover.add(this.hole, this.pile);
  }

  _buildDust() {
    const n = DUST.max;
    this.dPos = new Float32Array(n * 3);
    this.dVel = new Float32Array(n * 3);
    this.dAge = new Float32Array(n).fill(-1);   // -1 = unused
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(this.dPos, 3));
    this.dust = new THREE.Points(geo, new THREE.PointsMaterial({
      color: 0xb8b2a8, size: DUST.size, transparent: true, opacity: 0.9, depthWrite: false }));
    this.dust.frustumCulled = false;
    this.scene.add(this.dust);
    this._next = 0;
  }

  setRecord(record) {
    // Accepts a full record or a run-log `science` event: both carry phases.
    this.record = record;
    const v = (record.result && record.result.verdict) || record.verdict || "dry";
    this.core.material.color.setHex(VERDICT_COLOR[v] || VERDICT_COLOR.dry);
    this.dAge.fill(-1);
    this._lastT = null;
  }

  phaseAt(t) {
    // Current phase and its progress, from the record's absolute times.
    const ph = this.record ? this.record.phases : [];
    if (!ph.length || t < ph[0].t0) return { name: "idle", u: 0 };
    for (const p of ph) if (t < p.t0 + p.dur) return { name: p.name, u: (t - p.t0) / p.dur };
    return { name: "done", u: 1 };
  }

  start() { return this.record ? this.record.phases[0].t0 : 0; }
  end() {
    const ph = this.record.phases, last = ph[ph.length - 1];
    return last.t0 + last.dur;
  }

  setPose(k) {
    // k = 0 stowed, 1 at work. The wrist rises a little mid-swing to clear the deck.
    const e = ease(clamp01(k));
    const [ax, ay] = ARM.stowWrist, [bx, by] = ARM.workWrist;
    const wx = ax + (bx - ax) * e;
    const wy = ay + (by - ay) * e + 0.35 * Math.sin(Math.PI * e);
    const [t1, t2] = solveArm(wx, wy);
    this.shoulder.rotation.z = t1;
    this.elbow.rotation.z = t2;
    this.wrist.rotation.z = -(t1 + t2);  // keep the drill mast vertical
  }

  update(t) {
    const dt = this._lastT === null ? 0 : Math.max(0, Math.min(0.1, t - this._lastT));
    this._lastT = t;
    const { name, u } = this.phaseAt(t);
    const ground = ARM.workWrist[1] - ARM.mastLen;  // mast foot height above the ground
    let depth = 0, spin = 0, emit = 0, coreOn = false, pile = 0, pulse = 0;

    if (name === "idle" || name === "park") this.setPose(0);
    else if (name === "arm_deploy") this.setPose(u);
    else if (name === "drill") {
      this.setPose(1);
      // First 15%: bit lowers to the ground. Then it plunges to full depth.
      const touch = 0.15;
      depth = u < touch ? (ground - 0.05) * (u / touch)
                        : (ground - 0.05) + DRILL_DEPTH_M * ease((u - touch) / (1 - touch));
      spin = 40;
      emit = u > touch ? DUST.rate : 0;
      pile = clamp01((u - touch) / (1 - touch));
    } else if (name === "sample_lift") {
      this.setPose(1);
      depth = ((ground - 0.05) + DRILL_DEPTH_M) * (1 - ease(u));
      spin = 6 * (1 - u);
      coreOn = u > 0.5;
      pile = 1;
    } else if (name === "analyze") {
      this.setPose(1); coreOn = true; pile = 1;
      pulse = 0.5 + 0.5 * Math.sin(u * Math.PI * 10);
    } else if (name === "arm_stow") {
      this.setPose(1 - u); coreOn = u < 0.6; pile = 1;
    } else { this.setPose(0); pile = 1; }

    this.bit.position.y = this.bitRest - depth;
    this.bit.rotation.y += spin * dt;
    this.core.visible = coreOn;
    this.core.material.emissive.setHex(name === "analyze" ? this.core.material.color.getHex() : 0);
    this.core.material.emissiveIntensity = 0.4 * pulse;
    const lit = name === "analyze" ? pulse : 0;
    this.specLight.material.emissive.setRGB(0.35 * lit, 0.75 * lit, 1.0 * lit);
    this.pile.scale.set(0.3 + 0.7 * pile, 0.05 + pile, 0.3 + 0.7 * pile);
    this.pile.position.y = 0.03 * pile;
    this.hole.visible = pile > 0;
    this.pile.visible = pile > 0;
    this._stepDust(dt, emit);
  }

  _stepDust(dt, rate) {
    // Emit at the hole, in world coordinates, so grains stay put if the rover moves.
    const src = new THREE.Vector3(ARM.workWrist[0], 0.02, ARM.side);
    this.rover.localToWorld(src);
    const groundY = src.y - 0.02;
    let toEmit = rate * dt;
    while (toEmit > 0 && (toEmit >= 1 || Math.random() < toEmit)) {
      const i = this._next; this._next = (this._next + 1) % DUST.max;
      const az = Math.random() * Math.PI * 2;
      const el = (20 + Math.random() * 55) * Math.PI / 180;
      const v = DUST.vMin + Math.random() * (DUST.vMax - DUST.vMin);
      this.dPos.set([src.x, src.y, src.z], i * 3);
      this.dVel.set([v * Math.cos(el) * Math.cos(az), v * Math.sin(el), v * Math.cos(el) * Math.sin(az)], i * 3);
      this.dAge[i] = 0;
      toEmit -= 1;
    }
    for (let i = 0; i < DUST.max; i++) {
      if (this.dAge[i] < 0) { this.dPos[i * 3 + 1] = -999; continue; }
      const k = i * 3;
      if (this.dPos[k + 1] > groundY) {
        this.dVel[k + 1] -= MOON_G * dt;
        this.dPos[k] += this.dVel[k] * dt;
        this.dPos[k + 1] = Math.max(groundY, this.dPos[k + 1] + this.dVel[k + 1] * dt);
        this.dPos[k + 2] += this.dVel[k + 2] * dt;
      }
      this.dAge[i] += dt;
      if (this.dAge[i] > 6) this.dAge[i] = -1;   // landed grains clear after a while
    }
    this.dust.geometry.attributes.position.needsUpdate = true;
  }
}
