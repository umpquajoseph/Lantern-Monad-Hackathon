// Lunar regolith shading for the 3D view.
//
// Reflectance is Hapke, as in the terrain worker's sim/terrain_display.py
// hapke_radf: Xu et al. 2020, Remote Sensing 12(22) 3676, Eqs. (5)-(12), with
// K = 1, no CBOE and no roughness term, and the "old highland" parameters
// from display.json. Cauligi et al. 2023 (ShadowNav) sec. 4A uses the same
// model, their ref. [26].
//
// The material is three.js's Lambert material with its direct-light term
// replaced, so three's own light loop still supplies the lamp cone, the
// inverse-square falloff and the shadow maps. Every light is shaded as
//   radiance = light colour * radf(mu0, mu, g) * albedo factor
// which matches sim/terrain_look.py: E = A * radf * cone * lit / d^2.
import * as THREE from "three";

// --- Hapke radiance factor, the same formula in JS for exposure maths. ---
function H(x, w) {
  const r0 = (1 - Math.sqrt(1 - w)) / (1 + Math.sqrt(1 - w));                     // Eq. (12)
  x = Math.max(x, 1e-6);
  return 1 / (1 - w * x * (r0 + ((1 - 2 * r0 * x) / 2) * Math.log((1 + x) / x)));  // Eq. (11)
}

export function hapkeRadf(mu0, mu, g, hp) {
  const { hapke_w: w, hapke_b: b, hapke_bs0: bs0, hapke_hs: hs } = hp;
  const c = 3.29 * Math.exp(-17.4 * b * b) - 0.98;                                 // Eq. (8)
  const cg = Math.cos(g);
  const pg = ((1 + c) / 2) * (1 - b * b) / Math.pow(1 - 2 * b * cg + b * b, 1.5)
    + ((1 - c) / 2) * (1 - b * b) / Math.pow(1 + 2 * b * cg + b * b, 1.5);          // Eq. (7)
  const bsg = 1 / (1 + Math.tan(g / 2) / hs);                                       // Eq. (9)
  mu0 = Math.min(Math.max(mu0, 0), 1);
  mu = Math.min(Math.max(mu, 1e-4), 1);
  const M = H(mu0, w) * H(mu, w) - 1;                                               // Eq. (10)
  return (w / 4) * (mu0 / (mu0 + mu)) * (pg * (1 + bs0 * bsg) + M);                 // Eq. (5)
}

const HAPKE_GLSL = /* glsl */`
uniform vec4 uHapke;  // w, b, Bs0, hs
float hapkeH(float x, float w) {
  float s = sqrt(1.0 - w);
  float r0 = (1.0 - s) / (1.0 + s);
  x = max(x, 1e-6);
  return 1.0 / (1.0 - w * x * (r0 + (1.0 - 2.0 * r0 * x) * 0.5 * log((1.0 + x) / x)));
}
float hapkeRadf(float mu0, float mu, float g) {
  float w = uHapke.x, b = uHapke.y, bs0 = uHapke.z, hs = uHapke.w;
  float c = 3.29 * exp(-17.4 * b * b) - 0.98;
  float cg = cos(g);
  float pg = (1.0 + c) * 0.5 * (1.0 - b * b) / pow(1.0 - 2.0 * b * cg + b * b, 1.5)
           + (1.0 - c) * 0.5 * (1.0 - b * b) / pow(1.0 + 2.0 * b * cg + b * b, 1.5);
  float bsg = 1.0 / (1.0 + tan(g * 0.5) / hs);
  mu0 = clamp(mu0, 0.0, 1.0);
  mu = clamp(mu, 1e-4, 1.0);
  float M = hapkeH(mu0, w) * hapkeH(mu, w) - 1.0;
  return w * 0.25 * mu0 / (mu0 + mu) * (pg * (1.0 + bs0 * bsg) + M);
}
varying vec3 vViewPosition;
struct LambertMaterial { vec3 diffuseColor; float specularStrength; };
// Set per light by the light loop (see LIGHTS_BEGIN): directional lights are
// the viewer fill, shaded Lambert as sim/scene.py shades its fill; spot
// lights are the rover lamps, shaded Hapke.
bool gFillLight = false;
void RE_Direct_Lambert(const in IncidentLight directLight, const in vec3 geometryPosition, const in vec3 geometryNormal,
    const in vec3 geometryViewDir, const in vec3 geometryClearcoatNormal, const in LambertMaterial material,
    inout ReflectedLight reflectedLight) {
  float mu0 = dot(geometryNormal, directLight.direction);
  if (mu0 <= 0.0) return;
  if (gFillLight) { reflectedLight.directDiffuse += directLight.color * mu0 * BRDF_Lambert(material.diffuseColor); return; }
  float mu = dot(geometryNormal, geometryViewDir);
  float g = acos(clamp(dot(directLight.direction, geometryViewDir), -1.0, 1.0));
  reflectedLight.directDiffuse += directLight.color * hapkeRadf(mu0, mu, g) * material.diffuseColor;
}
void RE_IndirectDiffuse_Lambert(const in vec3 irradiance, const in vec3 geometryPosition, const in vec3 geometryNormal,
    const in vec3 geometryViewDir, const in vec3 geometryClearcoatNormal, const in LambertMaterial material,
    inout ReflectedLight reflectedLight) {
  reflectedLight.indirectDiffuse += irradiance * BRDF_Lambert(material.diffuseColor);
}
#define RE_Direct RE_Direct_Lambert
#define RE_IndirectDiffuse RE_IndirectDiffuse_Lambert
`;

// three's light loop, with each light marked fill (directional) or lamp.
const LIGHTS_BEGIN = (() => {
  const s = THREE.ShaderChunk.lights_fragment_begin;
  const out = s
    .replace("getDirectionalLightInfo( directionalLight, directLight );", "getDirectionalLightInfo( directionalLight, directLight ); gFillLight = true;")
    .replace("getSpotLightInfo( spotLight, geometryPosition, directLight );", "getSpotLightInfo( spotLight, geometryPosition, directLight ); gFillLight = false;")
    .replace("getPointLightInfo( pointLight, geometryPosition, directLight );", "getPointLightInfo( pointLight, geometryPosition, directLight ); gFillLight = false;");
  if (out === s || !/gFillLight = true/.test(out)) console.error("regolith: light loop not marked; fill shades as Hapke");
  return out;
})();

// opts:
//   hapke       meta.hapke
//   albedo      texture, factor = value * 255 / 128 (display.json), on uv
//   detailAlb   tileable texture, same factor, on uv1 (needs normalMap on channel 1)
//   normalMap   tileable tangent-space normal map on uv1
//   strength    display strength s: factor -> 1 + s (factor - 1)
//   discard     GLSL that may `discard;`, given `vec2 vXY` (local metres)
//   uniforms    extra uniforms for `discard`
//   color       base factor (rocks: rock_albedo), default 1
//   flat        flat shading (rocks)
//   instanced   per-instance colour carries a factor (rocks)
export function regolith(opts) {
  const mat = new THREE.MeshLambertMaterial({
    color: new THREE.Color().setScalar(opts.color ?? 1),
    normalMap: opts.normalMap || null,
    flatShading: !!opts.flat,
  });
  if (opts.normalMap) {
    opts.normalMap.channel = 1;
    mat.normalScale = new THREE.Vector2(1, 1);
  }
  const s = opts.strength ?? 1;
  mat.onBeforeCompile = (sh) => {
    const hp = opts.hapke;
    sh.uniforms.uHapke = { value: new THREE.Vector4(hp.hapke_w, hp.hapke_b, hp.hapke_bs0, hp.hapke_hs) };
    sh.uniforms.uAlb = { value: opts.albedo || null };
    sh.uniforms.uDetailAlb = { value: opts.detailAlb || null };
    sh.uniforms.uS = { value: s };
    Object.assign(sh.uniforms, opts.uniforms || {});
    const defs = [];
    if (opts.albedo) defs.push("#define USE_ALB_FACTOR");
    if (opts.detailAlb && opts.normalMap) defs.push("#define USE_DETAIL_ALB");
    sh.vertexShader = sh.vertexShader
      .replace("#include <common>", `#include <common>\nvarying vec2 vXY;\n${opts.albedo ? "varying vec2 vAlbUv;" : ""}`)
      .replace("#include <begin_vertex>", `#include <begin_vertex>\nvXY = (modelMatrix * vec4(position, 1.0)).xy;\n${opts.albedo ? "vAlbUv = uv;" : ""}`);
    sh.fragmentShader = sh.fragmentShader
      .replace("#include <common>", `${defs.join("\n")}\n#include <common>\nvarying vec2 vXY;\nuniform float uS;\n` +
        `#ifdef USE_ALB_FACTOR\nuniform sampler2D uAlb;\nvarying vec2 vAlbUv;\n#endif\n` +
        `#ifdef USE_DETAIL_ALB\nuniform sampler2D uDetailAlb;\n#endif\n` +
        Object.keys(opts.uniforms || {}).map((k) => `uniform ${glslType(opts.uniforms[k].value)} ${k};`).join("\n"))
      .replace("#include <lights_lambert_pars_fragment>", HAPKE_GLSL)
      .replace("#include <lights_fragment_begin>", LIGHTS_BEGIN)
      .replace("#include <map_fragment>", `
        #ifdef USE_ALB_FACTOR
          diffuseColor.rgb *= 1.0 + uS * (texture2D(uAlb, vAlbUv).r * (255.0 / 128.0) - 1.0);
        #endif
        #ifdef USE_DETAIL_ALB
          diffuseColor.rgb *= 1.0 + uS * (texture2D(uDetailAlb, vNormalMapUv).r * (255.0 / 128.0) - 1.0);
        #endif`)
      .replace("void main() {", `void main() {\n${opts.discard || ""}`);
  };
  // Distinct programs per variant.
  mat.customProgramCacheKey = () => JSON.stringify([!!opts.albedo, !!opts.detailAlb, !!opts.normalMap, opts.discard || ""]);
  return mat;
}

function glslType(v) {
  if (v && v.isTexture) return "sampler2D";
  if (v && v.isVector2) return "vec2";
  if (v && v.isVector3) return "vec3";
  if (v && v.isVector4) return "vec4";
  return "float";
}

// sim/scene.py's tone curve (step-6-scene 2c8af7e, scene.py:83-87 and
// _tone_lin, assumed there): in display-linear light y, straight below
// TONE_KNEE = 0.6, then a soft shoulder y = k + w (1 - exp(-(y - k) / w)),
// w = 1 - k, so hot ground rolls off toward white instead of clipping. The
// renderer's input is y itself; its sRGB output stands in for scene.py's
// DISPLAY_GAMMA = 2.2.
export const TONE_KNEE = 0.6;
export const DISPLAY_GAMMA = 2.2;
export const TONE_GLSL = /* glsl */`vec3 CustomToneMapping( vec3 color ) {
  vec3 y = toneMappingExposure * max(color, 0.0);
  const float k = ${TONE_KNEE.toFixed(4)}, w = ${(1 - TONE_KNEE).toFixed(4)};
  return mix(y, k + w * (1.0 - exp(-(y - k) / w)), step(k, y));
}`;
// Display-linear input that shows at display value v through that curve.
export function radianceFor(v) {
  const y = Math.pow(v, DISPLAY_GAMMA), k = TONE_KNEE, w = 1 - TONE_KNEE;
  return y <= k ? y : k - w * Math.log(1 - (y - k) / w);
}

// The lamp intensity that puts flat ground refRange ahead of the rover at
// refLevel on screen, the rule sim/scene.py uses for the rover camera
// (ASSUMED there).
export function lampIntensity(hp, { camH, lampBelow, pitchDeg, refRange, refLevel }) {
  const lampZ = camH - lampBelow;
  const P = [refRange, 0, 0], L = [0, 0, lampZ], C = [0, 0, camH];
  const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  const len = (a) => Math.hypot(...a);
  const unit = (a) => { const n = len(a); return [a[0] / n, a[1] / n, a[2] / n]; };
  const l = unit(sub(L, P)), v = unit(sub(C, P));
  const d = len(sub(L, P));
  const g = Math.acos(l[0] * v[0] + l[1] * v[1] + l[2] * v[2]);
  const radf = hapkeRadf(l[2], v[2], g, hp);
  // three.js spot light: colour * intensity / d^2 at that point (cone = 1 there:
  // the point is atan(2.3/5) = 25 deg below level, 10 deg off the lamp axis).
  return (radianceFor(refLevel) * d * d) / radf;
}

// Fill-light intensity as sim/terrain_look.py: flat ground under the fill
// alone shows at `level`, using that file's reference geometry (sun at elDeg,
// mu = 0.7, phase 60 deg).
// sim/scene.py _fill_rgb (step-6-scene, scene.py:1131), in display-linear
// light before the tone curve: albedo * level * (wrap + (1 - wrap) * max(N.s, 0)).
// Directional part and wrap part (ambient light) are both Lambert.
export const FILL_WRAP = 0.35;   // scene.py:1131, assumed there
// The fill is shaded Lambert (albedo * N.L / pi), as scene.py's, so the
// directional light's intensity is level * (1 - wrap) * pi.
export function fillIntensity(level) { return level * (1 - FILL_WRAP) * Math.PI; }
export function fillAmbient(level) { return level * FILL_WRAP * Math.PI; }
