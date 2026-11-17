"""Scratch CuPy port of sim/scene.py Scene.first_person for display "off" (the campaign runs' mode).

Same formulas and dtypes as the numpy path, line for line (scene.py 881-1023, _Grid 210-265,
_Horizon 435-505, _Lamp.irradiance 522-563, _albedo_at 1195-1215, _tone_lin 587-591).
Run code is not touched: this module reads a Scene's state and renders with its VTK window.
"""
import math, zlib
import numpy as np
import cupy as cp
import vtk
from vtk.util import numpy_support as vnp
import sim.scene as S

f32, f64 = cp.float32, cp.float64


class GpuShade:
    def __init__(self, sc):
        assert sc.display == "off" and sc._tile is None and sc._brdf is None, "display 'off' only"
        self.sc = sc
        g = sc.grid
        self.g = g
        self.hg = cp.asarray(g._hg)                    # (H*W, 3) float32 [h, dh/dx, dh/dy]
        self.hf = cp.asarray(g._hf)                    # (H*W,) float32
        self.alb = cp.asarray(sc._albedo)              # 1024^2 float32 stand-in tile
        U, V, L = sc._pixel_rays()
        self.U, self.V, self.L = cp.asarray(U), cp.asarray(V), cp.asarray(L)   # float64
        daz = math.pi / (S.HORIZON_AZIMUTHS - 1)
        self.daz = daz
        n1 = int(round(S.HORIZON_NEAR_M / S.HORIZON_DR_NEAR_M))
        r1 = S.HORIZON_DR_NEAR_M * np.arange(1, n1 + 1)
        n2 = int(math.ceil((S.LAMP_FADE_END_M - S.HORIZON_NEAR_M) / S.HORIZON_DR_M)) + 2
        self.r = cp.asarray(np.concatenate([r1, S.HORIZON_NEAR_M + S.HORIZON_DR_M * np.arange(1, n2 + 1)]))
        self.n1 = n1
        self.n_hz = S._horizon_n_half(max(S.LAMP_HORIZON_HALF_DEG, S.CAMERA_HORIZON_HALF_DEG))

    # ---- _Grid (base grid only: display "off" has no fine or near blocks)
    def _corners(self, x, y):
        g = self.g
        fj = cp.clip((x.astype(f64) - g.x0) / g.cell, 0.0, g.W - 1.000001)
        fi = cp.clip((y.astype(f64) - g.y0) / g.cell, 0.0, g.H - 1.000001)
        j = fj.astype(cp.int64); i = fi.astype(cp.int64)
        return i * g.W + j, (fj - j).astype(f32), (fi - i).astype(f32)

    def height(self, x, y):
        k, tj, ti = self._corners(x, y)
        h, W = self.hf, self.g.W
        h00, h01, h10, h11 = h[k], h[k + 1], h[k + W], h[k + W + 1]
        a = h00 + (h01 - h00) * tj
        b = h10 + (h11 - h10) * tj
        return a + (b - a) * ti

    def height_slopes(self, x, y):
        k, tj, ti = self._corners(x, y)
        g, W = self.hg, self.g.W
        tj, ti = tj[..., None], ti[..., None]
        a = g[k] + (g[k + 1] - g[k]) * tj
        b = g[k + W] + (g[k + W + 1] - g[k + W]) * tj
        return a + (b - a) * ti

    @staticmethod
    def slope_normal(sx, sy):
        n = cp.stack([-sx, -sy, cp.ones_like(sx)], axis=-1)
        return n / cp.linalg.norm(n, axis=-1, keepdims=True)

    def albedo_at(self, P):
        g, A = self.g, self.alb
        n = A.shape[0]
        fj = (P[..., 0] - f32(g.x0)) / f32(g.cell)
        fi = (P[..., 1] - f32(g.y0)) / f32(g.cell)
        j0, i0 = cp.floor(fj), cp.floor(fi)
        tj, ti = (fj - j0).astype(f32), (fi - i0).astype(f32)
        j0 = j0.astype(cp.int64) % n; i0 = i0.astype(cp.int64) % n
        j1, i1 = (j0 + 1) % n, (i0 + 1) % n
        a = A[i0, j0] + (A[i0, j1] - A[i0, j0]) * tj
        b = A[i1, j0] + (A[i1, j1] - A[i1, j0]) * tj
        return a + (b - a) * ti

    # ---- horizon maps
    def horizon_heights(self, px, py, heading):
        az = heading + self.daz * cp.arange(-self.n_hz, self.n_hz + 1, dtype=f64)
        X = px + cp.cos(az)[:, None] * self.r[None, :]
        Y = py + cp.sin(az)[:, None] * self.r[None, :]
        return self.height(X, Y)          # float32, like _par(surface.height, X, Y)

    def horizon(self, hgt, pos_z, half_deg):
        n_half = S._horizon_n_half(half_deg)
        h = hgt[self.n_hz - n_half: self.n_hz + n_half + 1]
        tan_el = (h.astype(f64) - pos_z) / self.r[None, :]     # numpy: float32 - np.float64 scalar -> float64
        s = 1                                                 # running max along rays (cupy has no maximum.accumulate):
        while s < tan_el.shape[1]:                            # doubling scan, exact for max
            tan_el[:, s:] = cp.maximum(tan_el[:, s:], tan_el[:, :-s].copy())
            s *= 2
        return dict(H=tan_el.astype(f32), half=n_half * self.daz)

    def visibility(self, hz, pos, heading, P, margin, soft):
        d = P - cp.asarray(pos, dtype=P.dtype)
        rho = cp.hypot(d[..., 0], d[..., 1])
        a = cp.arctan2(d[..., 1], d[..., 0]) - f32(heading)
        a = (a + f32(math.pi)) % f32(2 * math.pi) - f32(math.pi)
        fa = (a + f32(hz["half"])) / f32(self.daz)
        rr = rho.astype(f64) - margin
        near = rr / S.HORIZON_DR_NEAR_M - 1.0
        far = (self.n1 - 1) + (rr - S.HORIZON_NEAR_M) / S.HORIZON_DR_M
        fr = cp.where(rr <= S.HORIZON_NEAR_M, near, far)
        fa = fa.astype(f64)
        Hm = hz["H"]; A, R = Hm.shape
        inside = (fa >= 0) & (fa <= A - 1) & (fr >= 0)
        fa = cp.clip(fa, 0, A - 1.000001); fr_c = cp.clip(fr, 0, R - 1.000001)
        ia, ir = fa.astype(cp.int64), fr_c.astype(cp.int64)
        ta, tr = fa - ia, fr_c - ir
        h0 = Hm[ia, ir] + (Hm[ia, ir + 1] - Hm[ia, ir]) * tr
        h1 = Hm[ia + 1, ir] + (Hm[ia + 1, ir + 1] - Hm[ia + 1, ir]) * tr
        blocker = h0 + (h1 - h0) * ta
        tan_p = d[..., 2] / cp.maximum(rho, f32(1e-6))
        vis = cp.clip((tan_p - blocker) / soft + 0.5, 0.0, 1.0)
        return cp.where(inside, vis, cp.where(fr < 0, 1.0, 0.0))

    def irradiance(self, lamp_pos, heading, lamp_hz, cam_pos, cam_hz, P, N):
        out = cp.zeros(len(P), dtype=f32)
        p = math.radians(S.LAMP_PITCH_DEG)
        axis = np.array([math.cos(heading) * math.cos(p), math.sin(heading) * math.cos(p), -math.sin(p)])
        l = cp.asarray(lamp_pos, dtype=P.dtype) - P
        along = -(l @ cp.asarray(axis, dtype=P.dtype))
        r2 = cp.einsum("ij,ij->i", l, l)
        c_out = math.cos(math.radians(S.LAMP_OUTER_DEG))
        keep = (along > 0) & (along * along > f32(c_out * c_out) * r2) & (r2 < f32(S.LAMP_FADE_END_M ** 2))
        l, r2, along, Pk, Nk = l[keep], r2[keep], along[keep], P[keep], N[keep]
        r = cp.sqrt(r2)
        ndotl = cp.clip(cp.einsum("ij,ij->i", Nk, l) / r, f32(0.0), None)
        spot = smooth(c_out, math.cos(math.radians(S.LAMP_INNER_DEG)), along / r)
        fade = f32(1.0) - smooth(S.LAMP_FADE_START_M, S.LAMP_FADE_END_M, r)
        e = spot * fade * ndotl / cp.maximum(r2, f32(1e-6))
        lit = e > 0
        Pl = Pk[lit]
        vl = self.visibility(lamp_hz, lamp_pos, heading, Pl, S.SHADOW_SELF_MARGIN_CAM_M, S.SHADOW_SOFT_TAN_CAM)
        vc = self.visibility(cam_hz, cam_pos, heading, Pl, S.SHADOW_SELF_MARGIN_CAM_M, S.SHADOW_SOFT_TAN_CAM)
        vl = 1.0 - (1.0 - vl) * vc
        e[lit] *= vl.astype(f32)
        out[keep] = e
        return out

    # ---- the frame
    def first_person(self, agency, noise=None, timings=None, prof=None):
        """(img uint8 (h, w, 3) numpy, depth float32 numpy). noise: the (h, w) float32 standard normals
        (None: drawn on the CPU from the pose seed, exactly as scene.py does)."""
        import time
        sc = self.sc
        t0 = time.perf_counter()
        C, fwd, right, up = sc._camera_frame(agency)
        cam = sc._fp_ren.GetActiveCamera()
        cam.SetPosition(*C); cam.SetFocalPoint(*(C + fwd * 10.0)); cam.SetViewUp(*up)
        w, h = S.FP_SIZE
        cam.SetViewAngle(2.0 * math.degrees(math.atan(math.tan(math.radians(S.HFOV_DEG / 2)) * h / w)))
        cam.SetClippingRange(S.NEAR_CLIP_M, S.FAR_CLIP_M)
        for ag, (fa, _) in sc._rover_actors.items():
            fa.SetVisibility(ag != agency)
        sc._fp_rw.Render()
        zb = vtk.vtkFloatArray()
        sc._fp_rw.GetZbufferData(0, 0, w - 1, h - 1, zb)
        d_np = vnp.vtk_to_numpy(zb).reshape(h, w)[::-1]
        n, f = cam.GetClippingRange()
        t1 = time.perf_counter()

        if prof is not None: cp.cuda.Device().synchronize(); prof.append((0, time.perf_counter()))
        d = cp.asarray(np.ascontiguousarray(d_np)).astype(f64)
        sky = d >= 1.0
        z_ndc = 2.0 * d - 1.0
        z_eye = 2.0 * f * n / ((f + n) - z_ndc * (f - n))
        rng = z_eye * self.L
        rng[sky] = cp.nan
        depth = rng.astype(f32)
        Cg, fg, rg, ug = (cp.asarray(v) for v in (C, fwd, right, up))
        P = (Cg[None, None, :] + (fg + self.U[..., None] * rg + self.V[..., None] * ug) * z_eye[..., None]).astype(f32)
        if prof is not None: cp.cuda.Device().synchronize(); prof.append((1, time.perf_counter()))
        lp = sc._lamp_pos(agency)
        reach = (cp.abs(P[..., 0] - lp[0]) < S.LAMP_FADE_END_M) & (cp.abs(P[..., 1] - lp[1]) < S.LAMP_FADE_END_M)
        reach &= ~sky
        Pr = P[reach]
        if prof is not None: cp.cuda.Device().synchronize(); prof.append((2, time.perf_counter()))
        Sl = self.height_slopes(Pr[:, 0], Pr[:, 1])
        hP = Sl[:, 0]
        V = (Cg[None, :] - Pr).astype(f32)
        V /= cp.linalg.norm(V, axis=1, keepdims=True)
        alb = self.albedo_at(Pr)
        N = self.slope_normal(Sl[:, 1], Sl[:, 2]).astype(f32)
        nv = cp.einsum("ij,ij->i", N, V)
        bad = nv < S.NORMAL_MIN_VIEW_COS
        Nb = N[bad] + (f32(S.NORMAL_MIN_VIEW_COS) - nv[bad])[:, None] * V[bad]
        N[bad] = Nb / cp.linalg.norm(Nb, axis=1, keepdims=True)
        if prof is not None: cp.cuda.Device().synchronize(); prof.append((3, time.perf_counter()))
        off = cp.abs(Pr[:, 2] - self.height(Pr[:, 0], Pr[:, 1])) > 0.05
        Ps = Pr.copy()
        Ps[~off, 2] = hP[~off]
        n_off = int(off.sum())
        if n_off:   # normals from the depth image's central differences (np.gradient formulas)
            Pimg = cp.where(sky[..., None], cp.nan, P)
            gx = cp.empty_like(Pimg); gy = cp.empty_like(Pimg)
            gx[:, 1:-1] = (Pimg[:, 2:] - Pimg[:, :-2]) / 2.0; gx[:, 0] = Pimg[:, 1] - Pimg[:, 0]; gx[:, -1] = Pimg[:, -1] - Pimg[:, -2]
            gy[1:-1] = (Pimg[2:] - Pimg[:-2]) / 2.0; gy[0] = Pimg[1] - Pimg[0]; gy[-1] = Pimg[-1] - Pimg[-2]
            No = cp.cross(gx[reach][off], gy[reach][off])
            No /= cp.linalg.norm(No, axis=-1, keepdims=True) + 1e-12
            No *= -cp.sign(No @ cp.asarray(fwd, dtype=f32))[:, None]
            N[off] = cp.where(cp.isfinite(No), No, 0.0)
        if prof is not None: cp.cuda.Device().synchronize(); prof.append((4, time.perf_counter()))
        # lamp and camera horizon maps from shared ray heights (scene.py _hz_heights)
        hd = sc.poses[agency][2]
        hh = self.horizon_heights(C[0], C[1], hd)
        cam_hz = self.horizon(hh, C[2], S.CAMERA_HORIZON_HALF_DEG)
        lamp_hz = self.horizon(hh, lp[2], S.LAMP_HORIZON_HALF_DEG)
        if prof is not None: cp.cuda.Device().synchronize(); prof.append((5, time.perf_counter()))
        E = self.irradiance(lp, hd, lamp_hz, C, cam_hz, Ps, N)
        if prof is not None: cp.cuda.Device().synchronize(); prof.append((6, time.perf_counter()))
        lin_rel = alb * E * f32(1.0 / sc.e_ref)
        y = cp.zeros((h, w), dtype=f32)
        y[reach] = tone(lin_rel)
        if prof is not None: cp.cuda.Device().synchronize(); prof.append((7, time.perf_counter()))
        if noise is None:
            seed = zlib.crc32(repr((agency,) + tuple(round(v, 3) for v in sc.poses[agency])).encode())
            noise = np.random.default_rng(seed).standard_normal((h, w), dtype=np.float32)
        if prof is not None: cp.cuda.Device().synchronize(); prof.append((8, time.perf_counter()))
        nz = cp.asarray(noise)
        el = y * f32(S.SENSOR_FULL_WELL_E)
        sd = cp.sqrt(el + f32(S.SENSOR_READ_E ** 2))
        y = cp.clip((el + sd * nz) / f32(S.SENSOR_FULL_WELL_E), 0.0, 1.0)
        grey = (y ** f32(1.0 / S.DISPLAY_GAMMA) * f32(255.0) + f32(0.5)).astype(cp.uint8)
        if prof is not None: cp.cuda.Device().synchronize(); prof.append((9, time.perf_counter()))
        img = cp.asnumpy(cp.repeat(grey[..., None], 3, axis=2))
        dep = cp.asnumpy(depth)
        if prof is not None: cp.cuda.Device().synchronize(); prof.append((10, time.perf_counter()))
        t2 = time.perf_counter()
        if timings is not None:
            timings.append((t1 - t0, t2 - t1, n_off))
        return img, dep


def smooth(e0, e1, x):
    t = cp.clip((x - f32(e0)) / f32(e1 - e0), f32(0.0), f32(1.0))
    return t * t * (f32(3.0) - f32(2.0) * t)


def tone(lin_rel):
    y = lin_rel.astype(f32) * f32(S.EXPOSURE_REF_LEVEL ** S.DISPLAY_GAMMA)
    k, w = f32(S.TONE_KNEE), f32(1.0 - S.TONE_KNEE)
    return cp.where(y <= k, y, k + w * (f32(1.0) - cp.exp(-(y - k) / w))).astype(f32)
