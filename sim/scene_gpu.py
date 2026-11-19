"""GPU shading for sim/scene.py Scene.first_person (display "off", one agency: campaign camera runs).

The same formulas and number types as the numpy path in scene.py (first_person
881-1023, _Grid, _Horizon, _Lamp.irradiance, _albedo_at, _tone_lin), in two CUDA
kernels: horizon maps (one thread per azimuth ray, running max) and the
per-pixel shade. Sensor noise is drawn on the CPU from the same pose seed, so a
frame matches the numpy frame: on 50 check-2 poses Vision.rims was identical on
every frame, pixels within 1 DN, depth exact (bench/render/gpu_fused_test.json).
Needs CuPy and an NVIDIA GPU; Scene falls back to numpy without them.
"""
import math

import numpy as np

import sim.scene as S

MAX_TILT_MARGIN_DEG = 30.0   # numerics: shadow-map span kept for a body tilted up to this; steeper frames clip at it

SRC = r"""
#define M_PI 3.14159265358979323846
extern "C" {
__device__ float hbil(const float* h, int W, int H, double x0, double y0, double cell, double x, double y) {
    double fj = (x - x0) / cell, fi = (y - y0) / cell;
    fj = fmin(fmax(fj, 0.0), W - 1.000001); fi = fmin(fmax(fi, 0.0), H - 1.000001);
    long long j = (long long)fj, i = (long long)fi; long long k = i * W + j;
    float tj = (float)(fj - j), ti = (float)(fi - i);
    float h00 = h[k], h01 = h[k + 1], h10 = h[k + W], h11 = h[k + W + 1];
    float a = h00 + (h01 - h00) * tj, b = h10 + (h11 - h10) * tj;
    return a + (b - a) * ti;
}
__device__ void sbil(const float* g, int W, int H, double x0, double y0, double cell, double x, double y, float* o) {
    double fj = (x - x0) / cell, fi = (y - y0) / cell;
    fj = fmin(fmax(fj, 0.0), W - 1.000001); fi = fmin(fmax(fi, 0.0), H - 1.000001);
    long long j = (long long)fj, i = (long long)fi; long long k = i * W + j;
    float tj = (float)(fj - j), ti = (float)(fi - i);
    for (int c = 0; c < 3; c++) {
        float g00 = g[3*k+c], g01 = g[3*(k+1)+c], g10 = g[3*(k+W)+c], g11 = g[3*(k+W+1)+c];
        float a = g00 + (g01 - g00) * tj, b = g10 + (g11 - g10) * tj;
        o[c] = a + (b - a) * ti;
    }
}
// one thread per azimuth row: heights along the ray, tan elevation from two origin heights, running max
__global__ void horizons(const float* h, int W, int H, double x0, double y0, double cell,
                         double px, double py, double heading, double daz, int n_hz, const double* r, int R,
                         double zc, double zl, int nc, int nl, float* Hc, float* Hl) {
    int row = blockDim.x * blockIdx.x + threadIdx.x;
    if (row >= 2 * n_hz + 1) return;
    double az = heading + daz * (double)(row - n_hz);
    double ca = cos(az), sa = sin(az);
    int rc = row - (n_hz - nc), rl = row - (n_hz - nl);
    bool doc = rc >= 0 && rc <= 2 * nc, dol = rl >= 0 && rl <= 2 * nl;
    double mc = -1e300, ml = -1e300;
    for (int k = 0; k < R; k++) {
        double X = px + ca * r[k], Y = py + sa * r[k];
        float hh = hbil(h, W, H, x0, y0, cell, X, Y);
        if (doc) { double t = ((double)hh - zc) / r[k]; mc = fmax(mc, t); Hc[(long long)rc * R + k] = (float)mc; }
        if (dol) { double t = ((double)hh - zl) / r[k]; ml = fmax(ml, t); Hl[(long long)rl * R + k] = (float)ml; }
    }
}
__device__ float pymodf(float a, float b) { float m = fmodf(a, b); if (m != 0.0f) { if ((b < 0) != (m < 0)) m += b; } else m = copysignf(0.0f, b); return m; }
__device__ double vis(const float* Hm, int A, int R, double half, double daz, int n1, double drn, double nearm, double drf,
                      const float* pos, double heading, float P0, float P1, float P2, double margin, double soft) {
    float d0 = P0 - pos[0], d1 = P1 - pos[1], d2 = P2 - pos[2];
    float rho = hypotf(d0, d1);
    float a = atan2f(d1, d0) - (float)heading;
    a = pymodf(a + (float)M_PI, (float)(2 * M_PI)) - (float)M_PI;
    float faf = (a + (float)half) / (float)daz;
    double rr = (double)rho - margin;
    double fr = rr <= nearm ? rr / drn - 1.0 : (n1 - 1) + (rr - nearm) / drf;
    double fa = (double)faf;
    bool inside = (fa >= 0) && (fa <= A - 1) && (fr >= 0);
    fa = fmin(fmax(fa, 0.0), A - 1.000001); double frc = fmin(fmax(fr, 0.0), R - 1.000001);
    long long ia = (long long)fa, ir = (long long)frc; double ta = fa - ia, tr = frc - ir;
    double h0 = Hm[ia*R+ir] + (Hm[ia*R+ir+1] - Hm[ia*R+ir]) * tr;
    double h1 = Hm[(ia+1)*R+ir] + (Hm[(ia+1)*R+ir+1] - Hm[(ia+1)*R+ir]) * tr;
    double blocker = h0 + (h1 - h0) * ta;
    float tan_p = d2 / fmaxf(rho, 1e-6f);
    double v = fmin(fmax(((double)tan_p - blocker) / soft + 0.5, 0.0), 1.0);
    return inside ? v : (fr < 0 ? 1.0 : 0.0);
}
__device__ bool pix(const float* zb, int w, int h, int r, int c, const double* U, const double* V, const double* L,
                    const double* fr, double n, double f, float* P, double* zeye) {
    int q = r * w + c; double d = (double)zb[q];
    double z_ndc = 2.0 * d - 1.0; double ze = 2.0 * f * n / ((f + n) - z_ndc * (f - n));
    // fr: C(3) fwd(3) right(3) up(3)
    for (int k = 0; k < 3; k++) P[k] = (float)(fr[k] + (fr[3+k] + U[q] * fr[6+k] + V[q] * fr[9+k]) * ze);
    if (zeye) *zeye = ze;
    return d >= 1.0;   // sky
}
__global__ void shade(const float* zb, int w, int h, const double* U, const double* V, const double* L, const double* fr,
                      double n, double f, const float* hg, const float* hf, int W, int H, double x0, double y0, double cell,
                      const float* alb, int na, const float* lamp, const double* lamp64, const float* axis, const float* camp,
                      double heading, const float* Hc, int Ac, double halfc, const float* Hl, int Al, double halfl, int R,
                      double daz, int n1, double drn, double nearm, double drf, double margin, double soft,
                      float c_out, float c_in, float fs, float fe, float reach_m, float inv_eref, float lvl, float knee,
                      float nmin, float* y_out, float* depth, int* noff) {
    int q = blockDim.x * blockIdx.x + threadIdx.x;
    if (q >= w * h) return;
    int r = q / w, c = q % w;
    float P[3]; double ze;
    bool sky = pix(zb, w, h, r, c, U, V, L, fr, n, f, P, &ze);
    depth[q] = sky ? nanf("") : (float)(ze * L[q]);
    y_out[q] = 0.0f;
    if (sky) return;
    if (!(fabs((double)P[0] - lamp64[0]) < reach_m && fabs((double)P[1] - lamp64[1]) < reach_m)) return;
    float s[3]; sbil(hg, W, H, x0, y0, cell, (double)P[0], (double)P[1], s);
    // V = f32(C - P), normalised
    float Vv[3]; for (int k = 0; k < 3; k++) Vv[k] = (float)(fr[k] - (double)P[k]);
    float vn = sqrtf(Vv[0]*Vv[0] + Vv[1]*Vv[1] + Vv[2]*Vv[2]); for (int k = 0; k < 3; k++) Vv[k] /= vn;
    // albedo stand-in tile
    float fj = (P[0] - (float)x0) / (float)cell, fi = (P[1] - (float)y0) / (float)cell;
    float j0f = floorf(fj), i0f = floorf(fi); float tj = fj - j0f, ti = fi - i0f;
    long long j0 = ((long long)j0f % na + na) % na, i0 = ((long long)i0f % na + na) % na;
    long long j1 = (j0 + 1) % na, i1 = (i0 + 1) % na;
    float aa = alb[i0*na+j0] + (alb[i0*na+j1] - alb[i0*na+j0]) * tj;
    float bb = alb[i1*na+j0] + (alb[i1*na+j1] - alb[i1*na+j0]) * tj;
    float al = aa + (bb - aa) * ti;
    // normal from slopes, bent toward the viewer
    float N[3] = {-s[1], -s[2], 1.0f}; float nn = sqrtf(N[0]*N[0] + N[1]*N[1] + N[2]*N[2]); for (int k = 0; k < 3; k++) N[k] /= nn;
    float nv = N[0]*Vv[0] + N[1]*Vv[1] + N[2]*Vv[2];
    if (nv < nmin) { float Nb[3]; for (int k = 0; k < 3; k++) Nb[k] = N[k] + (nmin - nv) * Vv[k];
        float nb = sqrtf(Nb[0]*Nb[0] + Nb[1]*Nb[1] + Nb[2]*Nb[2]); for (int k = 0; k < 3; k++) N[k] = Nb[k] / nb; }
    float Ps[3] = {P[0], P[1], P[2]};
    bool off = fabsf(P[2] - hbil(hf, W, H, x0, y0, cell, (double)P[0], (double)P[1])) > 0.05f;
    if (!off) Ps[2] = s[0];
    else {   // np.gradient of the image's points at this pixel (one-sided at the edge), NaN at sky
        atomicAdd(noff, 1);
        float A0[3], A1[3], B0[3], B1[3]; float gx[3], gy[3]; bool bad = false;
        int cl = c > 0 ? c - 1 : 0, cr = c < w - 1 ? c + 1 : w - 1, ru = r > 0 ? r - 1 : 0, rd = r < h - 1 ? r + 1 : h - 1;
        float dx = (c > 0 && c < w - 1) ? 2.0f : 1.0f, dy = (r > 0 && r < h - 1) ? 2.0f : 1.0f;
        if (c == 0) { cl = 0; cr = 1; } if (c == w - 1) { cl = w - 2; cr = w - 1; }
        if (r == 0) { ru = 0; rd = 1; } if (r == h - 1) { ru = h - 2; rd = h - 1; }
        bad |= pix(zb, w, h, r, cl, U, V, L, fr, n, f, A0, 0); bad |= pix(zb, w, h, r, cr, U, V, L, fr, n, f, A1, 0);
        bad |= pix(zb, w, h, ru, c, U, V, L, fr, n, f, B0, 0); bad |= pix(zb, w, h, rd, c, U, V, L, fr, n, f, B1, 0);
        for (int k = 0; k < 3; k++) { gx[k] = (A1[k] - A0[k]) / dx; gy[k] = (B1[k] - B0[k]) / dy; }
        float No[3] = {gx[1]*gy[2] - gx[2]*gy[1], gx[2]*gy[0] - gx[0]*gy[2], gx[0]*gy[1] - gx[1]*gy[0]};
        float nl = sqrtf(No[0]*No[0] + No[1]*No[1] + No[2]*No[2]) + 1e-12f;
        float sg = 0; for (int k = 0; k < 3; k++) { No[k] /= nl; sg += No[k] * (float)fr[3+k]; }
        sg = sg > 0 ? -1.0f : (sg < 0 ? 1.0f : 0.0f);
        for (int k = 0; k < 3; k++) N[k] = bad ? 0.0f : No[k] * sg;
    }
    // lamp irradiance (scene.py _Lamp.irradiance, Lambert, camera-horizon vote)
    float l[3]; for (int k = 0; k < 3; k++) l[k] = lamp[k] - Ps[k];
    float along = -(l[0]*axis[0] + l[1]*axis[1] + l[2]*axis[2]);
    float r2 = l[0]*l[0] + l[1]*l[1] + l[2]*l[2];
    float e = 0.0f;
    if (along > 0 && along * along > c_out * c_out * r2 && r2 < fe * fe) {
        float rr = sqrtf(r2);
        float ndotl = fmaxf((N[0]*l[0] + N[1]*l[1] + N[2]*l[2]) / rr, 0.0f);
        float t = fminf(fmaxf((along / rr - c_out) / (c_in - c_out), 0.0f), 1.0f); float spot = t * t * (3.0f - 2.0f * t);
        float u = fminf(fmaxf((rr - fs) / (fe - fs), 0.0f), 1.0f); float fade = 1.0f - u * u * (3.0f - 2.0f * u);
        e = spot * fade * ndotl / fmaxf(r2, 1e-6f);
        if (e > 0) {
            double vl = vis(Hl, Al, R, halfl, daz, n1, drn, nearm, drf, lamp, heading, Ps[0], Ps[1], Ps[2], margin, soft);
            double vc = vis(Hc, Ac, R, halfc, daz, n1, drn, nearm, drf, camp, heading, Ps[0], Ps[1], Ps[2], margin, soft);
            e = (float)((double)e * (1.0 - (1.0 - vl) * vc));
        }
    }
    float yl = al * e * inv_eref * lvl;
    y_out[q] = yl <= knee ? yl : knee + (1.0f - knee) * (1.0f - expf(-(yl - knee) / (1.0f - knee)));
}

// lidar: one thread per beam; march ts until the beam is at or under the ground, then 6 bisections
// (sim/lidar.py Lidar.scan, same arithmetic; compiled without FMA contraction so ranges match numpy)
__global__ void lidar_march(const float* h, int W, int H, double x0, double y0, double cell,
                            const double* O, const double* dx, const double* dy, const double* dz, int n,
                            const double* ts, int nts, double min_range, double* out) {
    int q = blockDim.x * blockIdx.x + threadIdx.x;
    if (q >= n) return;
    int first = -1;
    for (int s = 0; s < nts; s++) {
        double px = O[0] + dx[q] * ts[s], py = O[1] + dy[q] * ts[s], pz = O[2] + dz[q] * ts[s];
        if (pz <= (double)hbil(h, W, H, x0, y0, cell, px, py)) { first = s; break; }
    }
    if (first < 0) { out[q] = nan(""); return; }
    double hi = ts[first], lo = first > 0 ? ts[first - 1] : min_range;
    for (int k = 0; k < 6; k++) {
        double mid = 0.5 * (lo + hi);
        bool under = O[2] + dz[q] * mid <= (double)hbil(h, W, H, x0, y0, cell, O[0] + dx[q] * mid, O[1] + dy[q] * mid);
        if (under) hi = mid; else lo = mid;
    }
    out[q] = hi;
}
}
"""
_mod = None


def available():
    try:
        import cupy as cp
        return cp.cuda.runtime.getDeviceCount() > 0
    except Exception:
        return False


class GpuShade:
    def __init__(self, sc):
        import cupy as cp
        global _mod
        if _mod is None:
            _mod = cp.RawModule(code=SRC, options=("--std=c++14", "--fmad=false"))
        self.cp = cp
        self.k_hz, self.k_sh = _mod.get_function("horizons"), _mod.get_function("shade")
        self.sc = sc
        g = sc.grid
        self.g = g
        self.hg = cp.asarray(np.ascontiguousarray(g._hg))
        self.hf = cp.asarray(np.ascontiguousarray(g._hf))
        self.alb = cp.asarray(sc._albedo)
        U, V, L = sc._pixel_rays()
        self.U, self.V, self.L = (cp.asarray(np.ascontiguousarray(v), dtype=cp.float64) for v in (U, V, L))
        self.daz = math.pi / (S.HORIZON_AZIMUTHS - 1)
        self.n1 = int(round(S.HORIZON_NEAR_M / S.HORIZON_DR_NEAR_M))
        r1 = S.HORIZON_DR_NEAR_M * np.arange(1, self.n1 + 1)
        n2 = int(math.ceil((S.LAMP_FADE_END_M - S.HORIZON_NEAR_M) / S.HORIZON_DR_M)) + 2
        self.r = cp.asarray(np.concatenate([r1, S.HORIZON_NEAR_M + S.HORIZON_DR_M * np.arange(1, n2 + 1)]))
        self.R = int(self.r.size)
        # Buffers wide enough for a body tilted MAX_TILT_MARGIN_DEG; each frame
        # fills the span its tilt needs (Scene._tilt_margin_deg).
        nc = S._horizon_n_half(S.CAMERA_HORIZON_HALF_DEG + MAX_TILT_MARGIN_DEG)
        nl = S._horizon_n_half(S.LAMP_HORIZON_HALF_DEG + MAX_TILT_MARGIN_DEG)
        self.Hc = cp.empty((2 * nc + 1, self.R), cp.float32)
        self.Hl = cp.empty((2 * nl + 1, self.R), cp.float32)
        w, h = S.FP_SIZE
        self.y = cp.empty(w * h, cp.float32)
        self.depth = cp.empty(w * h, cp.float32)
        self.noff = cp.zeros(1, cp.int32)

    def shade(self, agency, d_np, n, f, C, fwd, right, up, noise):
        """(img, depth) from the frame's z-buffer d_np (h, w, rows bottom first, as GL reads them), as first_person returns."""
        cp, sc, g = self.cp, self.sc, self.g
        w, h = S.FP_SIZE
        hd = float(sc.poses[agency][2])
        lp = sc._lamp_pos(agency)
        axis = sc._lamp_axis(agency)   # aimed with the body (sim/scene.py body_frame)
        m = min(sc._tilt_margin_deg(agency), MAX_TILT_MARGIN_DEG)
        self.nc = S._horizon_n_half(S.CAMERA_HORIZON_HALF_DEG + m)
        self.nl = S._horizon_n_half(S.LAMP_HORIZON_HALF_DEG + m)
        self.n_hz = max(self.nc, self.nl)
        F = cp.asarray(np.concatenate([C, fwd, right, up]).astype(np.float64))
        lamp32, camp32 = cp.asarray(lp.astype(np.float32)), cp.asarray(C.astype(np.float32))
        lamp64, axis32 = cp.asarray(lp.astype(np.float64)), cp.asarray(axis.astype(np.float32))
        # d_np: the z-buffer as GL returns it (rows bottom first); flipped here.
        zbg = cp.ascontiguousarray(cp.asarray(np.ascontiguousarray(d_np, dtype=np.float32))[::-1])
        rows = 2 * self.n_hz + 1
        self.k_hz(((rows + 127) // 128,), (128,), (
            self.hf, np.int32(g.W), np.int32(g.H), np.float64(g.x0), np.float64(g.y0), np.float64(g.cell),
            np.float64(C[0]), np.float64(C[1]), np.float64(hd), np.float64(self.daz), np.int32(self.n_hz), self.r,
            np.int32(self.R), np.float64(C[2]), np.float64(lp[2]), np.int32(self.nc), np.int32(self.nl), self.Hc, self.Hl))
        self.noff.fill(0)
        npx = w * h
        self.k_sh(((npx + 255) // 256,), (256,), (
            zbg, np.int32(w), np.int32(h), self.U, self.V, self.L, F, np.float64(n), np.float64(f),
            self.hg, self.hf, np.int32(g.W), np.int32(g.H), np.float64(g.x0), np.float64(g.y0), np.float64(g.cell),
            self.alb, np.int32(self.alb.shape[0]), lamp32, lamp64, axis32, camp32, np.float64(hd),
            self.Hc, np.int32(2 * self.nc + 1), np.float64(self.nc * self.daz),
            self.Hl, np.int32(2 * self.nl + 1), np.float64(self.nl * self.daz),
            np.int32(self.R), np.float64(self.daz), np.int32(self.n1), np.float64(S.HORIZON_DR_NEAR_M),
            np.float64(S.HORIZON_NEAR_M), np.float64(S.HORIZON_DR_M), np.float64(S.SHADOW_SELF_MARGIN_CAM_M),
            np.float64(S.SHADOW_SOFT_TAN_CAM),
            np.float32(math.cos(math.radians(S.LAMP_OUTER_DEG))), np.float32(math.cos(math.radians(S.LAMP_INNER_DEG))),
            np.float32(S.LAMP_FADE_START_M), np.float32(S.LAMP_FADE_END_M), np.float32(S.LAMP_FADE_END_M),
            np.float32(1.0 / sc.e_ref), np.float32(S.EXPOSURE_REF_LEVEL ** S.DISPLAY_GAMMA), np.float32(S.TONE_KNEE),
            np.float32(S.NORMAL_MIN_VIEW_COS), self.y, self.depth, self.noff))
        y = self.y.reshape(h, w)
        if S.SENSOR_FULL_WELL_E > 0:
            el = y * cp.float32(S.SENSOR_FULL_WELL_E)
            sd = cp.sqrt(el + cp.float32(S.SENSOR_READ_E ** 2))
            y = cp.clip((el + sd * cp.asarray(noise)) / cp.float32(S.SENSOR_FULL_WELL_E), 0.0, 1.0)
        grey = (y ** cp.float32(1.0 / S.DISPLAY_GAMMA) * cp.float32(255.0) + cp.float32(0.5)).astype(cp.uint8)
        # Three equal channels, made on the GPU (a CPU np.repeat cost 4.7 ms a frame).
        img = cp.asnumpy(cp.ascontiguousarray(cp.broadcast_to(grey[..., None], (h, w, 3))))
        return img, cp.asnumpy(self.depth).reshape(h, w)

    def lidar_ranges(self, O, dx, dy, dz, ts, min_range):
        """sim/lidar.py's march and bisection for every beam at once: ranges (n,) float64, NaN = no return."""
        cp, g = self.cp, self.g
        n = len(dx)
        out = cp.empty(n, cp.float64)
        if not hasattr(self, "k_lid"):
            self.k_lid = _mod.get_function("lidar_march")
        self.k_lid(((n + 127) // 128,), (128,), (
            self.hf, np.int32(g.W), np.int32(g.H), np.float64(g.x0), np.float64(g.y0), np.float64(g.cell),
            cp.asarray(np.asarray(O, np.float64)), cp.asarray(dx), cp.asarray(dy), cp.asarray(dz), np.int32(n),
            cp.asarray(ts), np.int32(len(ts)), np.float64(min_range), out))
        return cp.asnumpy(out)
