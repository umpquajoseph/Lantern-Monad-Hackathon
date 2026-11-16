"""Reference renders of the display terrain (sim/terrain_display.py).

Not the demo renderer (that is sim/scene.py). This exists so the terrain
work can be looked at: VTK draws depth only, every pixel is shaded here in
numpy with the Hapke model, the lamp's cast shadows, the albedo map and the
fine display height.

    python sim/terrain_look.py            # writes data/terrain/display/look_*.png
    python sim/terrain_look.py --strength 0 --tag bare

Camera and lamp, first person: camera on the 1.6 m mast (brief p.6), lamp
0.2 m below it ([A24] sec. IV), 60 deg horizontal field of view. Pitch, lamp
cone, exposure, chase camera and fill light copy the ASSUMED values in
sim/scene.py so the two renderers can be compared.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import vtk
from vtk.util import numpy_support as vnp
from scipy.ndimage import map_coordinates, rotate

import terrain as T
import terrain_display as TD

try:
    from sim.settings import Settings as _Settings
except ImportError:  # run as a script from sim/
    from settings import Settings as _Settings
CAMERA_HEIGHT_M = _Settings().camera_height_m          # brief p.6 1.6 m mast
LAMP_BELOW_CAMERA_M = _Settings().lamp_below_camera_m  # [A24] sec. IV
HFOV_DEG = 60.0              # [A24] sec. IV
FP_SIZE = (640, 480)         # sim/scene.py, ASSUMED there
CAMERA_PITCH_DEG = 15.0      # sim/scene.py, ASSUMED there
LAMP_INNER_DEG, LAMP_OUTER_DEG = 15.0, 32.0      # sim/scene.py (2c8af7e, was 30/45), ASSUMED there
EXPOSURE_REF_RANGE_M, EXPOSURE_REF_LEVEL = 5.0, 0.55  # sim/scene.py, ASSUMED there
GAMMA = 2.2
TP_SIZE = (960, 540)                             # sim/scene.py, ASSUMED there
CHASE_BACK_M, CHASE_UP_M, CHASE_LOOK_AHEAD_M, CHASE_VFOV_DEG = 14.0, 8.0, 9.0, 50.0
FILL_LEVEL, FILL_AZ_DEG, FILL_EL_DEG = 0.22, 200.0, 35.0
FILL_RGB = np.array([0.62, 0.68, 0.82])
LAMP_RGB = np.array([1.00, 0.93, 0.80])

FINE_RES_M = 0.02            # ASSUMED: display patch spacing near the camera
FINE_RANGE_M = 40.0          # ASSUMED: display patch reach; beyond it the 0.25 m grid
SUN_EL_DEG = 8.0             # ASSUMED: overview sun, for legibility (real polar sun is lower)
SUN_AZ_DEG = 135.0


# --------------------------------------------------------------------------
# VTK: meshes and depth.
# --------------------------------------------------------------------------

def _mesh(x, y, z, cell_mask=None):
    ny, nx = z.shape
    X, Y = np.meshgrid(x, y)
    pts = np.column_stack([X.ravel(), Y.ravel(), z.ravel()]).astype(np.float64)
    i, j = np.mgrid[0:ny - 1, 0:nx - 1]
    a = (i * nx + j).ravel()
    keep = np.ones(a.shape, bool) if cell_mask is None else cell_mask.ravel()
    a = a[keep]
    tri = np.concatenate([np.stack([a, a + 1, a + nx + 1], 1), np.stack([a, a + nx + 1, a + nx], 1)])
    poly = vtk.vtkPolyData()
    vp = vtk.vtkPoints()
    vp.SetData(vnp.numpy_to_vtk(pts, deep=True))
    poly.SetPoints(vp)
    cells = vtk.vtkCellArray()
    off = np.arange(0, 3 * len(tri) + 1, 3, dtype=np.int64)
    cells.SetData(vnp.numpy_to_vtkIdTypeArray(off, deep=True),
                  vnp.numpy_to_vtkIdTypeArray(tri.ravel().astype(np.int64), deep=True))
    poly.SetPolys(cells)
    return poly


def _depth(polys, cam, focal, up, W, H, vfov_deg, near, far):
    ren = vtk.vtkRenderer()
    for p in polys:
        m = vtk.vtkPolyDataMapper(); m.SetInputData(p)
        a = vtk.vtkActor(); a.SetMapper(m); ren.AddActor(a)
    win = vtk.vtkRenderWindow(); win.SetOffScreenRendering(1); win.SetSize(W, H); win.AddRenderer(ren)
    c = ren.GetActiveCamera()
    c.SetPosition(*cam); c.SetFocalPoint(*focal); c.SetViewUp(*up); c.SetViewAngle(vfov_deg)
    c.SetClippingRange(near, far)
    win.Render()
    f = vtk.vtkWindowToImageFilter(); f.SetInput(win); f.SetInputBufferTypeToZBuffer(); f.ReadFrontBufferOff(); f.Update()
    zb = vnp.vtk_to_numpy(f.GetOutput().GetPointData().GetScalars()).reshape(H, W)[::-1]
    win.Finalize()
    zn = 2 * zb - 1
    z_eye = 2 * near * far / (far + near - zn * (far - near))
    z_eye[zb >= 1.0] = np.nan
    return z_eye


def _rays(cam, focal, up, W, H, vfov_deg):
    fwd = np.asarray(focal, float) - cam; fwd /= np.linalg.norm(fwd)
    right = np.cross(fwd, up); right /= np.linalg.norm(right)
    upc = np.cross(right, fwd)
    f = (H / 2) / math.tan(math.radians(vfov_deg) / 2)
    u = (np.arange(W) + 0.5 - W / 2) / f
    v = -(np.arange(H) + 0.5 - H / 2) / f
    U, V = np.meshgrid(u, v)
    return fwd[None, None] + U[..., None] * right + V[..., None] * upc  # z_eye = 1 along fwd


# --------------------------------------------------------------------------
# Shading.
# --------------------------------------------------------------------------

def _sample(arr, x0, y0, res, X, Y, order=1):
    return map_coordinates(arr, [(Y - y0) / res, (X - x0) / res], order=order, mode="nearest")


def _normals(h, res):
    gy, gx = np.gradient(h.astype(np.float32), res)
    return gx, gy


def _horizon(h, x0, y0, res, L, az_lo, az_hi, rmax, n_az=3000):
    """Running max of tan(elevation) seen from lamp L along rays in [az_lo, az_hi]."""
    az = np.linspace(az_lo, az_hi, n_az)
    r = np.arange(0.3, rmax, res)
    X = L[0] + r[None, :] * np.cos(az)[:, None]
    Y = L[1] + r[None, :] * np.sin(az)[:, None]
    hz = _sample(h, x0, y0, res, X, Y)
    tan = (hz - L[2]) / r[None, :]
    return az, r, np.maximum.accumulate(tan, axis=1)


def _lit(P, L, hor, margin, soft=0.002):
    az, r, cm = hor
    d = P[..., :2] - L[:2]
    rp = np.hypot(d[..., 0], d[..., 1])
    ap = np.arctan2(d[..., 1], d[..., 0])
    ia = np.clip(np.round((ap - az[0]) / (az[1] - az[0])).astype(int), 0, len(az) - 1)
    ir = np.clip(((rp - margin - r[0]) / (r[1] - r[0])).astype(int), 0, len(r) - 1)
    tp = (P[..., 2] - L[2]) / np.maximum(rp, 1e-3)
    lit = np.clip((tp - cm[ia, ir]) / soft + 0.5, 0, 1)
    lit[rp - margin < r[0]] = 1
    return lit


def _hapke_light(P, N, S, C, p):
    """Radiance factor for light at S, viewer at C, unit normals N."""
    l = S - P; dl = np.linalg.norm(l, axis=-1, keepdims=True); l /= dl
    v = C - P; v /= np.linalg.norm(v, axis=-1, keepdims=True)
    mu0 = (N * l).sum(-1); mu = (N * v).sum(-1)
    g = np.arccos(np.clip((l * v).sum(-1), -1, 1))
    return TD.hapke_radf(mu0, mu, g, p), dl[..., 0], l


def _cone(l_from_lamp, axis):
    ang = np.degrees(np.arccos(np.clip((l_from_lamp * axis).sum(-1), -1, 1)))
    t = np.clip((LAMP_OUTER_DEG - ang) / (LAMP_OUTER_DEG - LAMP_INNER_DEG), 0, 1)
    return t * t * (3 - 2 * t)


class Look:
    def __init__(self, strength=None, terrain_path=None):
        self.D = TD.Display(terrain_path, strength=strength)
        self.g = self.D.height
        self.c = self.D.cell

    def ground(self, x, y):
        return float(_sample(self.g, self.D.x0, self.D.y0, self.c, np.array([x]), np.array([y]), order=1)[0])

    def _fine(self, xs, ys, res):
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        nx, ny = int((xmax - xmin) / res) + 1, int((ymax - ymin) / res) + 1
        h, a = self.D.patch(xmin, ymin, nx, ny, res)
        return h, a, xmin, ymin

    def _coarse(self, cx, cy, half, step=2):
        c, D = self.c, self.D
        j0 = max(int((cx - half - D.x0) / c), 0); j1 = min(int((cx + half - D.x0) / c), self.g.shape[1] - 1)
        i0 = max(int((cy - half - D.y0) / c), 0); i1 = min(int((cy + half - D.y0) / c), self.g.shape[0] - 1)
        x = D.x0 + c * np.arange(j0, j1, step); y = D.y0 + c * np.arange(i0, i1, step)
        return x, y, self.g[i0:i1:step, j0:j1:step] - 0.05

    def render(self, cam, focal, up, W, H, vfov, lamp, lamp_axis, fill=False, fine_box=None, exposure_ref=None):
        """Shade one view. fine_box: (xs, ys) extents for the fine patch."""
        p = self.D.p
        h, a, fx0, fy0 = self._fine(*fine_box, FINE_RES_M)
        k = 1
        xs = fx0 + FINE_RES_M * np.arange(h.shape[1]); ys = fy0 + FINE_RES_M * np.arange(h.shape[0])
        polys = [_mesh(xs[::k], ys[::k], h[::k, ::k])]
        cx, cy = 0.5 * (xs[0] + xs[-1]), 0.5 * (ys[0] + ys[-1])
        gx_, gy_, gz_ = self._coarse(cx, cy, 160.0)
        polys.append(_mesh(gx_, gy_, gz_))
        depth = _depth(polys, cam, focal, up, W, H, vfov, 0.2, 3000.0)
        P = cam + depth[..., None] * _rays(cam, focal, up, W, H, vfov)
        sky = np.isnan(depth)
        P[sky] = cam
        inf = ((P[..., 0] >= xs[0]) & (P[..., 0] <= xs[-1]) & (P[..., 1] >= ys[0]) & (P[..., 1] <= ys[-1]))
        # Height, normal and albedo from the fine patch, else the sim grid.
        hgx, hgy = _normals(h, FINE_RES_M)
        ggx, ggy = _normals(self.g, self.c)
        X, Y = P[..., 0], P[..., 1]
        Z = np.where(inf, _sample(h, fx0, fy0, FINE_RES_M, X, Y), _sample(self.g, self.D.x0, self.D.y0, self.c, X, Y))
        GX = np.where(inf, _sample(hgx, fx0, fy0, FINE_RES_M, X, Y), _sample(ggx, self.D.x0, self.D.y0, self.c, X, Y))
        GY = np.where(inf, _sample(hgy, fx0, fy0, FINE_RES_M, X, Y), _sample(ggy, self.D.x0, self.D.y0, self.c, X, Y))
        A = np.where(inf, _sample(a, fx0, fy0, FINE_RES_M, X, Y),
                     1 + self.D.s * (_sample(self.D.alb, self.D.x0, self.D.y0, self.c, X, Y) - 1))
        P = np.stack([X, Y, Z], -1)
        N = np.stack([-GX, -GY, np.ones_like(GX)], -1); N /= np.linalg.norm(N, axis=-1, keepdims=True)
        # Lamp.
        radf, dist, l = _hapke_light(P, N, lamp, cam, p)
        cone = _cone(-l, lamp_axis)
        d2 = P[..., :2] - lamp[:2]
        azs = np.arctan2(d2[..., 1], d2[..., 0])[~sky & inf]
        hor = _horizon(h, fx0, fy0, FINE_RES_M, lamp, azs.min() - 0.01, azs.max() + 0.01, FINE_RANGE_M * 1.5)
        lit = np.where(inf, _lit(P, lamp, hor, margin=2.5 * FINE_RES_M), 1.0)
        E = A * radf * cone * lit / np.maximum(dist, 0.5) ** 2
        if exposure_ref is None:
            exposure_ref = self._exposure_ref(cam, lamp, lamp_axis, p)
        lin = E * (EXPOSURE_REF_LEVEL ** GAMMA / exposure_ref)
        lin = np.where(sky, 0, lin)
        if not fill:
            img = np.clip(lin, 0, 1) ** (1 / GAMMA)
            return (np.repeat(img[..., None], 3, -1) * 255).astype(np.uint8), lit, P
        far = cam + np.array([math.cos(math.radians(FILL_EL_DEG)) * math.cos(math.radians(FILL_AZ_DEG)),
                              math.cos(math.radians(FILL_EL_DEG)) * math.sin(math.radians(FILL_AZ_DEG)),
                              math.sin(math.radians(FILL_EL_DEG))]) * 1e6
        fr, _, _ = _hapke_light(P, N, far, cam, p)
        flat = TD.hapke_radf(math.sin(math.radians(FILL_EL_DEG)), 0.7, math.radians(60), p)
        fl = FILL_LEVEL ** GAMMA * A * fr / flat
        rgb = lin[..., None] * LAMP_RGB + np.where(sky, 0, fl)[..., None] * FILL_RGB
        img = np.clip(rgb, 0, 1) ** (1 / GAMMA)
        return (img * 255).astype(np.uint8), lit, P

    def _exposure_ref(self, cam, lamp, axis, p):
        f = np.array([axis[0], axis[1], 0.0]); f /= np.linalg.norm(f)
        P = np.array([cam[0], cam[1], cam[2] - CAMERA_HEIGHT_M]) + EXPOSURE_REF_RANGE_M * f
        N = np.array([0, 0, 1.0])
        radf, dist, l = _hapke_light(P[None], N[None], lamp, cam, p)
        return float(radf[0] * _cone(-l, axis)[0] / dist[0] ** 2)

    # ---- views ----
    def first_person(self, x, y, heading):
        gz = self.ground(x, y)
        cam = np.array([x, y, gz + CAMERA_HEIGHT_M])
        lamp = cam - np.array([0, 0, LAMP_BELOW_CAMERA_M])
        pitch = math.radians(-CAMERA_PITCH_DEG)
        axis = np.array([math.cos(pitch) * math.cos(heading), math.cos(pitch) * math.sin(heading), math.sin(pitch)])
        W, H = FP_SIZE
        vfov = math.degrees(2 * math.atan(math.tan(math.radians(HFOV_DEG / 2)) * H / W))
        half = math.radians(HFOV_DEG / 2 + 12)
        R = FINE_RANGE_M
        xs = [x - 1, x + 1] + [x + R * math.cos(heading + s * half) for s in (-1, 0, 1)]
        ys = [y - 1, y + 1] + [y + R * math.sin(heading + s * half) for s in (-1, 0, 1)]
        return self.render(cam, cam + axis, (0, 0, 1), W, H, vfov, lamp, axis, fine_box=(xs, ys))

    def chase(self, x, y, heading):
        gz = self.ground(x, y)
        f = np.array([math.cos(heading), math.sin(heading), 0.0])
        rover_cam = np.array([x, y, gz + CAMERA_HEIGHT_M])
        lamp = rover_cam - np.array([0, 0, LAMP_BELOW_CAMERA_M])
        pitch = math.radians(-CAMERA_PITCH_DEG)
        axis = np.array([math.cos(pitch) * f[0], math.cos(pitch) * f[1], math.sin(pitch)])
        cam = np.array([x, y, gz]) - CHASE_BACK_M * f + np.array([0, 0, CHASE_UP_M])
        look = np.array([x, y, gz]) + CHASE_LOOK_AHEAD_M * f
        W, H = TP_SIZE
        R = FINE_RANGE_M
        side = np.array([-f[1], f[0], 0])
        corners = [cam - 4 * f + s * 30 * side for s in (-1, 1)] + [rover_cam + R * f + s * 35 * side for s in (-1, 1)]
        xs = [c[0] for c in corners]; ys = [c[1] for c in corners]
        ref = self._exposure_ref(rover_cam, lamp, axis, self.D.p)
        img, lit, P = self.render(cam, look, (0, 0, 1), W, H, CHASE_VFOV_DEG, lamp, axis, fill=True,
                                  fine_box=(xs, ys), exposure_ref=ref)
        return img

    def overview(self, W=1280, H=720, cam_off=(-900, -1100, 420), look_off=(0, 150, -120),
                 backdrop=True, ss=2):
        """Whole drive area plus LOLA backdrop under a low sun, with sun shadows.
        Layers, finest wins: drive area (sim grid), near backdrop (LOLA 5 m),
        far backdrop (LOLA 20 m). Rendered at ss x size and averaged down."""
        D, p = self.D, self.D.p
        k = 4  # 1 m mesh
        x = D.x0 + self.c * np.arange(0, self.g.shape[1], k)
        y = D.y0 + self.c * np.arange(0, self.g.shape[0], k)
        ctr = np.array([x.mean(), y.mean(), float(self.g[::k, ::k].mean())])
        Rm = D.doc["moon_radius_m"]
        sun = np.array([math.cos(math.radians(SUN_EL_DEG)) * math.cos(math.radians(SUN_AZ_DEG)),
                        math.cos(math.radians(SUN_EL_DEG)) * math.sin(math.radians(SUN_AZ_DEG)),
                        math.sin(math.radians(SUN_EL_DEG))])
        # layers: (x0, y0, cell, z, albedo, bounds) finest first
        k2 = 2
        g2 = self.g[::k2, ::k2]
        da = D.doc["drive_area"]
        layers = [(D.x0, D.y0, self.c * k2, g2, 1 + D.s * (D.alb[::k2, ::k2] - 1),
                   (da["x_min"], da["x_max"], da["y_min"], da["y_max"]))]
        polys = [_mesh(x, y, self.g[::k, ::k])]
        if backdrop:
            for b in (D.backdrop_near, D.backdrop):
                if b is None:
                    continue
                bc = float(b["cell"])
                bx = float(b["x0"]) + bc * np.arange(b["height"].shape[1])
                by = float(b["y0"]) + bc * np.arange(b["height"].shape[0])
                BX, BY = np.meshgrid(bx, by)
                bz = b["height"] - ((BX - ctr[0]) ** 2 + (BY - ctr[1]) ** 2) / (2 * Rm) - 0.5
                hole = np.zeros((len(by) - 1, len(bx) - 1), bool)
                for (_, _, _, _, _, (x_lo, x_hi, y_lo, y_hi)) in layers:
                    hole |= ((BX[:-1, :-1] >= x_lo) & (BX[1:, 1:] <= x_hi)
                             & (BY[:-1, :-1] >= y_lo) & (BY[1:, 1:] <= y_hi))
                polys.append(_mesh(bx, by, bz, ~hole))
                layers.append((bx[0], by[0], bc, bz, np.ones_like(bz),
                               (bx[0] - bc / 2, bx[-1] + bc / 2, by[0] - bc / 2, by[-1] + bc / 2)))
        cam = ctr + np.array(cam_off); focal = ctr + np.array(look_off)
        Ws, Hs = W * ss, H * ss
        depth = _depth(polys, cam, focal, (0, 0, 1), Ws, Hs, 40.0, 10.0, 60000.0)
        P = cam + depth[..., None] * _rays(cam, focal, (0, 0, 1), Ws, Hs, 40.0)
        sky = np.isnan(depth)
        P[sky] = ctr
        X, Y = P[..., 0], P[..., 1]
        vals = {n: np.zeros_like(X) for n in ("z", "gx", "gy", "a", "lit")}
        done = np.zeros(X.shape, bool)
        ft = D.far_tile
        if ft is not None and D.s > 0:
            fc = D.p.far_tile_cell_m
            fper = ft.shape[0] * fc
            fgx, fgy = _normals(np.tile(ft, (3, 3)), fc)
            n0 = ft.shape[0]
            fgx, fgy = fgx[n0:2 * n0, n0:2 * n0], fgy[n0:2 * n0, n0:2 * n0]
            fsh = _sun_shadow(D.s * np.tile(ft, (3, 3)), fc, sun)[n0:2 * n0, n0:2 * n0]
        for li, (lx, ly, lc, lz, la, (x_lo, x_hi, y_lo, y_hi)) in enumerate(layers):
            m = ~done & (X >= x_lo) & (X <= x_hi) & (Y >= y_lo) & (Y <= y_hi)
            if not m.any():
                continue
            gx_, gy_ = _normals(lz, lc)
            sh = _sun_shadow(lz, lc, sun)
            for n, arr in (("z", lz), ("gx", gx_), ("gy", gy_), ("a", la), ("lit", sh)):
                vals[n][m] = _sample(arr, lx, ly, lc, X[m], Y[m])
            if li > 0 and ft is not None and D.s > 0:
                Xm, Ym = X[m] % fper, Y[m] % fper
                vals["gx"][m] += D.s * _sample(fgx, 0, 0, fc, Xm, Ym, order=1)
                vals["gy"][m] += D.s * _sample(fgy, 0, 0, fc, Xm, Ym, order=1)
                vals["lit"][m] *= _sample(fsh, 0, 0, fc, Xm, Ym, order=1)
            done |= m
        P = np.stack([X, Y, vals["z"]], -1)
        N = np.stack([-vals["gx"], -vals["gy"], np.ones_like(X)], -1); N /= np.linalg.norm(N, axis=-1, keepdims=True)
        radf, _, _ = _hapke_light(P, N, cam + sun * 1e7, cam, p)
        ref = TD.hapke_radf(1.0, 0.8, math.radians(37), p)  # flat ground in full sun, mid view
        lin = vals["a"] * radf * vals["lit"] / ref * 0.35
        lin = np.where(sky, 0, lin)
        lin = lin.reshape(H, ss, W, ss).mean((1, 3))
        img = np.clip(lin, 0, 1) ** (1 / GAMMA)
        return (np.repeat(img[..., None], 3, -1) * 255).astype(np.uint8)


def _sun_shadow(z, res, sun):
    """1 where lit by a sun at direction `sun` (unit, toward the sun), else 0.
    Rotates the grid so light travels along +columns, then a running max."""
    az = math.degrees(math.atan2(sun[1], sun[0]))
    tan_el = sun[2] / math.hypot(sun[0], sun[1])
    # rows run north (y up), columns east. Rotate so the sun direction points to -x (column 0).
    ang = 180.0 - az
    big = -1e6
    zr = rotate(z, -ang, reshape=True, order=1, cval=np.nan)
    valid = ~np.isnan(zr)
    zr = np.where(valid, zr, big)
    col = np.arange(zr.shape[1])[None, :] * res
    q = zr + col * tan_el
    lit_r = (q >= np.maximum.accumulate(q, axis=1) - 1e-3).astype(np.float32)
    back = rotate(lit_r, ang, reshape=True, order=1, cval=1.0)
    oy = (back.shape[0] - z.shape[0]) // 2; ox = (back.shape[1] - z.shape[1]) // 2
    return np.clip(back[oy:oy + z.shape[0], ox:ox + z.shape[1]], 0, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strength", type=float)
    ap.add_argument("--tag", default="")
    ap.add_argument("--x", type=float); ap.add_argument("--y", type=float)
    ap.add_argument("--heading", type=float, default=0.6)
    ap.add_argument("--views", default="fp,chase,overview")
    a = ap.parse_args()
    from PIL import Image
    L = Look(a.strength)
    x = a.x if a.x is not None else L.D.x0 + 500
    y = a.y if a.y is not None else L.D.y0 + 500
    out = TD.OUT
    tag = f"_{a.tag}" if a.tag else ""
    for v in a.views.split(","):
        if v == "fp":
            img, lit, _ = L.first_person(x, y, a.heading)
        elif v == "chase":
            img = L.chase(x, y, a.heading)
        else:
            img = L.overview()
        path = out / f"look_{v}{tag}.png"
        Image.fromarray(img).save(path)
        print("wrote", path)


if __name__ == "__main__":
    main()


# --------------------------------------------------------------------------
# Reference stills at poses from a demo run log, for judging other renderers.
# --------------------------------------------------------------------------

def refs(run_dir, picks, strength=None, out_dir=None):
    """Render first person and chase at (step, agency) picks of a run log.
    Writes <out>/<run>_s<step><agency>_{fp,chase}.png and returns the paths."""
    import json
    from PIL import Image
    run_dir = Path(run_dir)
    log = json.loads((run_dir / "log.json").read_text())
    out = Path(out_dir) if out_dir else TD.OUT / "refs"
    out.mkdir(parents=True, exist_ok=True)
    L = Look(strength)
    paths = []
    for step, ag in picks:
        x, y, hd = log["steps"][step]["rovers"][ag]["true_pose"]
        stem = f"{log['run_id']}_s{step:04d}{ag}"
        img, _, _ = L.first_person(x, y, hd)
        Image.fromarray(img).save(out / f"{stem}_fp.png")
        Image.fromarray(L.chase(x, y, hd)).save(out / f"{stem}_chase.png")
        paths.append((step, ag, out / f"{stem}_fp.png", out / f"{stem}_chase.png"))
    return paths
