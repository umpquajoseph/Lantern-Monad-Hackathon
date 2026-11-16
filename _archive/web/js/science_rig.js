// Science stop rig: arm out, drill down, dust up, core back, stow.
//
// Mounts on a rover group and plays a science event's `phases` (sim/science.py
// run_log_event) against the replay clock. Works in a y-up scene (the
// preview page) or a z-up one (world.js, where the rover frame is x forward,
// y left, z up), chosen by `up`.
//
//   const rig = new ScienceRig(roverGroup, scene, { up: "z" });
//   rig.setEvent(ev);   // a science event, or null
//   rig.update(t);      // sim seconds
import * as THREE from "three";

// Rover body and deck, as sim/scene.py _rover_polydata().
const DECK_Z = 1.05;
// ASSUMED: arm and drill geometry, sized to look right on that body.
const ARM = {
  side: 0.42,                    // metres to the right of the centre line
  shoulder: [0.55, 1.12],        // (forward, up) on the front of the deck
  upper: 0.9, fore: 0.85,
  stowWrist: [-0.05, 2.32],      // drill mast upright on the deck
  workWrist: [1.55, 1.45],       // drill mast over open ground ahead
  mastLen: 1.2, bitLen: 1.5,
};
export const DRILL_DEPTH_M = 1.0;   // PROSPECTOR brief p. 5: "1 m drill"
const MOON_G = 1.62;                // lunar surface gravity, m/s^2
// ASSUMED: dust. No air on the Moon: grains fly on clean ballistic arcs and land.
const DUST = { max: 1400, rate: 240, vMin: 0.4, vMax: 2.2, size: 0.05 };
export const VERDICT_COLOR = { ice: 0x58b4ff, trace: 0xb9c7d6, dry: 0x8a7f72 };

const ease = (u) => u * u * (3 - 2 * u);
const clamp01 = (u) => Math.min(1, Math.max(0, u));

function solveArm(wx, wy) {
  // Two-link inverse kinematics in the rover's forward-up plane, elbow up.
  const [sx, sy] = ARM.shoulder, L1 = ARM.upper, L2 = ARM.fore;
  const dx = wx - sx, dy = wy - sy;
  const d = Math.min(Math.hypot(dx, dy), L1 + L2 - 1e-4);
  const c2 = (d * d - L1 * L1 - L2 * L2) / (2 * L1 * L2);
  const t2 = -Math.acos(Math.max(-1, Math.min(1, c2)));
  const t1 = Math.atan2(dy, dx) - Math.atan2(L2 * Math.sin(t2), L1 + L2 * Math.cos(t2));
  return [t1, t2];
}

// The rig is built y-up (x forward, y up, z right); `mount` turns it into the host frame.
export class ScienceRig {
  constructor(rover, scene, { up = "y" } = {}) {
    this.rover = rover;
    this.scene = scene;
    this.upAxis = up === "z" ? 2 : 1;
    this.mount = new THREE.Group();
    if (up === "z") this.mount.rotation.x = Math.PI / 2;   // rig y (up) -> host z, rig z (right) -> host -y
    rover.add(this.mount);
    this.ev = null;
    this._lastT = null;
    this._buildArm();
    this._buildDust();
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
      new THREE.MeshStandardMaterial({ color: VERDICT_COLOR.ice, emissive: 0x000000, roughness: 0.4 }));
    this.core.position.y = 0.27;
    this.core.visible = false;
    this.bit.add(rod, flute, this.core);
    this.bitRest = -ARM.mastLen - 0.05;
    this.bit.position.y = this.bitRest;
    this.wrist.add(mast, this.bit);
    this.elbow.add(this.wrist);
    this.shoulder.add(this.elbow);
    this.mount.add(this.shoulder);

    // NIR scanner light on the mast head and the spectrometer light on the deck.
    this.specLight = new THREE.Mesh(new THREE.SphereGeometry(0.06, 16, 12),
      new THREE.MeshStandardMaterial({ color: 0x222222, emissive: 0x000000 }));
    this.specLight.position.set(-0.55, DECK_Z + 0.1, ARM.side);
    this.nirBeam = new THREE.Mesh(new THREE.ConeGeometry(1.6, 3.2, 24, 1, true),
      new THREE.MeshBasicMaterial({ color: 0xa070ff, transparent: true, opacity: 0.18, side: THREE.DoubleSide,
        depthWrite: false }));
    this.nirBeam.position.set(1.7, 0.9, 0);
    this.nirBeam.rotation.z = Math.PI / 2 + 0.6;
    this.nirBeam.visible = false;
    this.mount.add(this.specLight, this.nirBeam);

    const hx = ARM.workWrist[0];
    this.hole = new THREE.Mesh(new THREE.CircleGeometry(0.06, 20), new THREE.MeshBasicMaterial({ color: 0x050505 }));
    this.hole.rotation.x = -Math.PI / 2;
    this.hole.position.set(hx, 0.02, ARM.side);
    this.pile = new THREE.Mesh(new THREE.ConeGeometry(0.22, 0.06, 24, 1, true),
      new THREE.MeshStandardMaterial({ color: 0x7c7770, roughness: 1, side: THREE.DoubleSide }));
    this.pile.position.set(hx, 0.0, ARM.side);
    this.mount.add(this.hole, this.pile);
  }

  _buildDust() {
    const n = DUST.max;
    this.dPos = new Float32Array(n * 3);
    this.dVel = new Float32Array(n * 3);
    this.dAge = new Float32Array(n).fill(-1);
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(this.dPos, 3));
    this.dust = new THREE.Points(geo, new THREE.PointsMaterial({
      color: 0xc9c2b6, size: DUST.size, transparent: true, opacity: 0.95, depthWrite: false }));
    this.dust.frustumCulled = false;
    this.scene.add(this.dust);
    this._next = 0;
  }

  setEvent(ev) {
    if (ev === this.ev) return;
    this.ev = ev;
    if (ev) this.core.material.color.setHex(VERDICT_COLOR[ev.verdict] || VERDICT_COLOR.dry);
    this._lastT = null;
  }

  phaseAt(t) {
    const ph = this.ev ? this.ev.phases : [];
    if (!ph.length || t < ph[0].t0) return { name: "idle", u: 0, k: -1 };
    for (let k = 0; k < ph.length; k++) {
      const p = ph[k];
      if (t < p.t0 + p.dur) return { name: p.name, u: (t - p.t0) / p.dur, k };
    }
    return { name: "done", u: 1, k: ph.length };
  }

  // Depth of the bit below the ground, metres, at time t (for the data panel).
  depthAt(t) {
    const { name, u } = this.phaseAt(t);
    if (name === "drill") return u < 0.15 ? 0 : DRILL_DEPTH_M * ease((u - 0.15) / 0.85);
    if (name === "sample_lift") return DRILL_DEPTH_M * (1 - ease(u));
    return 0;
  }

  setPose(k) {
    const e = ease(clamp01(k));
    const [ax, ay] = ARM.stowWrist, [bx, by] = ARM.workWrist;
    const wx = ax + (bx - ax) * e;
    const wy = ay + (by - ay) * e + 0.35 * Math.sin(Math.PI * e);
    const [t1, t2] = solveArm(wx, wy);
    this.shoulder.rotation.z = t1;
    this.elbow.rotation.z = t2;
    this.wrist.rotation.z = -(t1 + t2);
  }

  update(t) {
    const dt = this._lastT === null ? 0 : Math.max(0, Math.min(0.1, t - this._lastT));
    this._lastT = t;
    const { name, u } = this.phaseAt(t);
    const groundGap = ARM.workWrist[1] - ARM.mastLen;
    let depth = 0, spin = 0, emit = 0, coreOn = false, pile = 0, pulse = 0, nir = false;
    if (name === "idle" || name === "park" || name === "done") { this.setPose(0); pile = name === "done" ? 1 : 0; }
    else if (name === "nir_scan") { this.setPose(0); nir = true; pulse = 0.5 + 0.5 * Math.sin(u * Math.PI * 8); }
    else if (name === "arm_deploy") this.setPose(u);
    else if (name === "drill") {
      this.setPose(1);
      const touch = 0.15;
      depth = u < touch ? (groundGap - 0.05) * (u / touch) : (groundGap - 0.05) + DRILL_DEPTH_M * ease((u - touch) / (1 - touch));
      spin = 40;
      emit = u > touch ? DUST.rate : 0;
      pile = clamp01((u - touch) / (1 - touch));
    } else if (name === "sample_lift") {
      this.setPose(1);
      depth = ((groundGap - 0.05) + DRILL_DEPTH_M) * (1 - ease(u));
      spin = 6 * (1 - u);
      coreOn = u > 0.5;
      pile = 1;
    } else if (name === "analyze") {
      this.setPose(1); coreOn = true; pile = 1;
      pulse = 0.5 + 0.5 * Math.sin(u * Math.PI * 10);
    } else if (name === "arm_stow") { this.setPose(1 - u); coreOn = u < 0.6; pile = 1; }
    this.bit.position.y = this.bitRest - depth;
    this.bit.rotation.y += spin * dt;
    this.core.visible = coreOn;
    this.core.material.emissive.setHex(name === "analyze" ? this.core.material.color.getHex() : 0);
    this.core.material.emissiveIntensity = 0.5 * pulse;
    const lit = name === "analyze" ? pulse : 0;
    this.specLight.material.emissive.setRGB(0.35 * lit, 0.75 * lit, 1.0 * lit);
    this.nirBeam.visible = nir;
    this.nirBeam.material.opacity = 0.1 + 0.15 * pulse;
    this.pile.scale.set(0.3 + 0.7 * pile, 0.05 + pile, 0.3 + 0.7 * pile);
    this.pile.position.y = 0.03 * pile;
    this.hole.visible = this.pile.visible = pile > 0;
    this._stepDust(dt, emit);
  }

  _stepDust(dt, rate) {
    const u = this.upAxis, h1 = 0, h2 = u === 1 ? 2 : 1;
    this.rover.updateMatrixWorld(true);
    const src = this.mount.localToWorld(new THREE.Vector3(ARM.workWrist[0], 0.02, ARM.side));
    const s = [src.x, src.y, src.z];
    const ground = s[u] - 0.02;
    let toEmit = rate * dt;
    while (toEmit > 0 && (toEmit >= 1 || Math.random() < toEmit)) {
      const i = this._next; this._next = (this._next + 1) % DUST.max;
      const az = Math.random() * Math.PI * 2;
      const el = (20 + Math.random() * 55) * Math.PI / 180;
      const v = DUST.vMin + Math.random() * (DUST.vMax - DUST.vMin);
      const vel = [0, 0, 0];
      vel[h1] = v * Math.cos(el) * Math.cos(az);
      vel[h2] = v * Math.cos(el) * Math.sin(az);
      vel[u] = v * Math.sin(el);
      this.dPos.set(s, i * 3);
      this.dVel.set(vel, i * 3);
      this.dAge[i] = 0;
      toEmit -= 1;
    }
    for (let i = 0; i < DUST.max; i++) {
      const k = i * 3;
      if (this.dAge[i] < 0) { this.dPos[k + u] = -1e6; continue; }
      if (this.dPos[k + u] > ground) {
        this.dVel[k + u] -= MOON_G * dt;
        for (let a = 0; a < 3; a++) this.dPos[k + a] += this.dVel[k + a] * dt;
        if (this.dPos[k + u] < ground) this.dPos[k + u] = ground;
      }
      this.dAge[i] += dt;
      if (this.dAge[i] > 6) this.dAge[i] = -1;
    }
    this.dust.geometry.attributes.position.needsUpdate = true;
  }
}
