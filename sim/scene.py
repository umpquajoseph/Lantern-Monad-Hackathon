"""Step 6 scene: the rover's first-person lamp view and the judges' chase view.

One scene draws both views, so the image the vision step reads is the one
the judges see (docs/handoffs.md, Scene section).

How it draws
------------
VTK draws the terrain mesh on the GPU. A fine mesh (one grid cell, 0.25 m)
covers a square around each rover; a coarse mesh covers the rest of the map
and is stitched to the fine squares so no cracks show.

First person: VTK renders only the depth buffer from the camera. Every pixel
is turned back into a 3D point, and its brightness is computed here in
numpy: lamp spotlight, inverse-square falloff, Lambert shading, and a shadow
test. There is no sunlight and no fill light, so the image is the sensor.

Shadows: the lamp's shadow is computed from a polar horizon map. Rays leave
the lamp in many directions across the ground; along each ray the steepest
terrain elevation seen so far is kept (a running maximum). A point is lit
when it rises above everything between it and the lamp. For a height grid
this is the exact answer to "is the straight line from the lamp to this
point blocked", without the bias and acne of a GPU shadow map at the
grazing angles where the rim shadow lives. It is the spec's "second cast
from the lamp's position, compared by depth" (spec step 6), done along
the ground.

Third person: VTK renders the scene from a chase camera. The terrain mesh
carries per-vertex colours from the same lamp model, so the lamp pool and
rim shadows match first person, plus a dim fill light that exists only so
viewers can see the ground; the image says so on screen.

Frames (docs/handoffs.md): world metres, x east, y north, heading CCW from
+x. Rover frame: x forward, y left, z up, origin at the camera.
"""

from __future__ import annotations

import math
import time

import cv2
import numpy as np
import vtk
from vtk.util import numpy_support as vnp

# ---------------------------------------------------------------------------
# Camera and lamp: grounded in ShadowNav.
# ---------------------------------------------------------------------------
# Atha et al. 2024, sec. V and Table 1: best stereo configuration is the
# camera 2.5 m off the ground with the light 0.2 m below it.
CAMERA_HEIGHT_M = 2.5
LAMP_BELOW_CAMERA_M = 0.2
# Atha et al. 2024, sec. IV: the field rig camera has a 60 deg horizontal FOV.
HFOV_DEG = 60.0
# Atha et al. 2024, Table 1 caption: with the light below the camera, stereo
# works out to 40 m. Used only to size the fine mesh and the lamp's reach.
LAMP_USEFUL_RANGE_M = 40.0

# ---------------------------------------------------------------------------
# Assumed: not given in the papers. Named so they can be changed.
# ---------------------------------------------------------------------------
FP_SIZE = (640, 480)          # assumed: first-person image (w, h) px. The rig is 5120x3840; too big to render live.
TP_SIZE = (960, 540)          # assumed: chase-view image (w, h) px.
CAMERA_PITCH_DEG = 15.0       # assumed: camera axis this far below level. Ground in view from ~3 m out to the horizon.
LAMP_PITCH_DEG = 15.0         # assumed: lamp aimed along the camera axis.
LAMP_INNER_DEG = 30.0         # assumed: full lamp brightness within this angle of the lamp axis.
LAMP_OUTER_DEG = 45.0         # assumed: no light beyond this angle (a flood light, like the field rig's LED flood).
EXPOSURE_REF_RANGE_M = 5.0    # assumed: flat ground this far ahead renders at EXPOSURE_REF_LEVEL.
EXPOSURE_REF_LEVEL = 0.55     # assumed: 0..1 display level of that reference ground (nearest ground in view stays below white).
DISPLAY_GAMMA = 2.2           # assumed: sensor-to-display curve.
LAMP_FADE_START_M = 60.0      # assumed: the lit region ends smoothly between these ranges.
LAMP_FADE_END_M = 80.0        # assumed: by 60 m, 1/r^2 has already made the ground near-black.
ALBEDO_VARIATION = 0.06       # assumed: +/- regolith brightness texture, so the ground is not flat grey.
SHADOW_SOFT_TAN = 0.0015      # assumed: shadow edge antialiasing width, in tan(elevation) units (~0.09 deg).
SHADOW_SELF_MARGIN_M = 0.15   # assumed: terrain this close in front of a point is not tested as its blocker.
HORIZON_AZIMUTHS = 1536       # assumed: rays in the lamp's horizon map, over 180 deg (2 mrad, 8 cm apart at 40 m).
HORIZON_DR_M = 0.1            # assumed: sample spacing along each ray.

FINE_HALF_CELLS = 256         # assumed: fine mesh square is 2*256 cells = 128 m across (lamp reach 40 m + margin).
FINE_RECENTER_M = 24.0        # assumed: rebuild a rover's fine square when it drives this far from its centre.
COARSE_STEP = 8               # assumed: coarse mesh vertex every 8 cells (2 m).
NEAR_CLIP_M = 0.2             # assumed: GPU near and far planes.
FAR_CLIP_M = 3000.0

# Third person only (viewer aids, not sensor light).
CHASE_BACK_M = 14.0           # assumed: chase camera this far behind the rover,
CHASE_UP_M = 8.0              # assumed: this far above it,
CHASE_LOOK_AHEAD_M = 9.0      # assumed: looking at a point this far ahead of it.
CHASE_VIEW_ANGLE_DEG = 50.0   # assumed: vertical view angle.
FILL_LEVEL = 0.22             # assumed: viewer fill light strength (0..1 display, before gamma).
FILL_AZ_DEG, FILL_EL_DEG = 200.0, 35.0  # assumed: fill light direction it comes from.
FILL_RGB = np.array([0.62, 0.68, 0.82])  # assumed: cool fill, so it reads as "not the lamp".
LAMP_RGB = np.array([1.00, 0.93, 0.80])  # assumed: warm lamp light in third person.
AGENCY_RGB = {"A": (0.25, 0.75, 1.00), "B": (1.00, 0.60, 0.20)}  # assumed: rover colours.
STATUS_RGB = {"reported": (1.0, 0.85, 0.2), "confirmed": (0.3, 0.95, 0.4), "disputed": (1.0, 0.3, 0.3)}
DRAPE_M = 0.10                # assumed: tracks float this far above the ground.


def _smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


# ---------------------------------------------------------------------------
# Height grid sampling (local frame: world minus self.origin).
# ---------------------------------------------------------------------------
class _Grid:
    def __init__(self, height, x0, y0, cell):
        self.h = np.ascontiguousarray(height, dtype=np.float32)
        self.H, self.W = self.h.shape
        self.x0, self.y0, self.cell = float(x0), float(y0), float(cell)
        self._hf = self.h.ravel()
        # Height and its central-difference slopes per cell, gathered together
        # so one lookup gives a point's height and smooth normal.
        gy, gx = np.gradient(self.h, self.cell)
        self._hg = np.stack([self.h, gx, gy], axis=-1).reshape(-1, 3).astype(np.float32)

    def _corners(self, x, y):
        fj = np.clip((np.asarray(x, dtype=np.float64) - self.x0) / self.cell, 0.0, self.W - 1.000001)
        fi = np.clip((np.asarray(y, dtype=np.float64) - self.y0) / self.cell, 0.0, self.H - 1.000001)
        j = fj.astype(np.int64)
        i = fi.astype(np.int64)
        return i * self.W + j, (fj - j).astype(np.float32), (fi - i).astype(np.float32)

    def height(self, x, y):
        """Bilinear height at (x, y); clamps at the map edge."""
        k, tj, ti = self._corners(x, y)
        h, W = self._hf, self.W
        h00, h01, h10, h11 = h[k], h[k + 1], h[k + W], h[k + W + 1]
        a = h00 + (h01 - h00) * tj
        b = h10 + (h11 - h10) * tj
        return a + (b - a) * ti

    def height_normal(self, x, y):
        """(height, unit normal) at points, both bilinear in the grid's slopes."""
        k, tj, ti = self._corners(x, y)
        g, W = self._hg, self.W
        tj, ti = tj[..., None], ti[..., None]
        a = g[k] + (g[k + 1] - g[k]) * tj
        b = g[k + W] + (g[k + W + 1] - g[k + W]) * tj
        s = a + (b - a) * ti
        n = np.stack([-s[..., 1], -s[..., 2], np.ones_like(s[..., 0])], axis=-1)
        return s[..., 0], n / np.linalg.norm(n, axis=-1, keepdims=True)

    def normal(self, x, y, e=None):
        """Unit up-normal from central differences of the bilinear height."""
        if e is None:
            return self.height_normal(x, y)[1]
        hx = (self.height(x + e, y) - self.height(x - e, y)) / (2 * e)
        hy = (self.height(x, y + e) - self.height(x, y - e)) / (2 * e)
        n = np.stack([-hx, -hy, np.ones_like(hx)], axis=-1)
        return n / np.linalg.norm(n, axis=-1, keepdims=True)


# ---------------------------------------------------------------------------
# The lamp and its shadow.
# ---------------------------------------------------------------------------
class _Lamp:
    """One rover's lamp: position, aim, and a polar horizon map for shadows."""

    def __init__(self, grid, pos, heading):
        self.pos = np.asarray(pos, dtype=np.float64)
        self.heading = heading
        p = math.radians(LAMP_PITCH_DEG)
        self.axis = np.array([math.cos(heading) * math.cos(p), math.sin(heading) * math.cos(p), -math.sin(p)])
        # Horizon map over the half-plane in front of the lamp. Everything
        # behind it is outside the cone (pitch + outer angle < 90 deg).
        self.daz = math.pi / (HORIZON_AZIMUTHS - 1)
        az = heading - math.pi / 2 + self.daz * np.arange(HORIZON_AZIMUTHS)
        n_r = int(LAMP_FADE_END_M / HORIZON_DR_M) + 2
        r = HORIZON_DR_M * np.arange(1, n_r + 1)
        X = self.pos[0] + np.cos(az)[:, None] * r[None, :]
        Y = self.pos[1] + np.sin(az)[:, None] * r[None, :]
        tan_el = (grid.height(X, Y) - self.pos[2]) / r[None, :]
        self.horizon = np.maximum.accumulate(tan_el, axis=1).astype(np.float32)

    def visibility(self, P):
        """0..1: how lit point(s) P (..., 3) are, from the horizon map."""
        d = P - self.pos.astype(P.dtype)
        rho = np.hypot(d[..., 0], d[..., 1])
        a = np.arctan2(d[..., 1], d[..., 0]) - self.heading
        a = (a + math.pi) % (2 * math.pi) - math.pi
        fa = (a + math.pi / 2) / self.daz
        fr = (rho - SHADOW_SELF_MARGIN_M) / HORIZON_DR_M - 1.0  # index k holds r = dr * (k + 1)
        A, R = self.horizon.shape
        inside = (fa >= 0) & (fa <= A - 1) & (fr >= 0)
        fa = np.clip(fa, 0, A - 1.000001)
        fr_c = np.clip(fr, 0, R - 1.000001)
        ia, ir = fa.astype(np.int64), fr_c.astype(np.int64)
        ta, tr = fa - ia, fr_c - ir
        Hm = self.horizon
        h0 = Hm[ia, ir] + (Hm[ia, ir + 1] - Hm[ia, ir]) * tr
        h1 = Hm[ia + 1, ir] + (Hm[ia + 1, ir + 1] - Hm[ia + 1, ir]) * tr
        blocker = h0 + (h1 - h0) * ta
        tan_p = d[..., 2] / np.maximum(rho, 1e-6)
        vis = np.clip((tan_p - blocker) / SHADOW_SOFT_TAN + 0.5, 0.0, 1.0)
        return np.where(inside, vis, np.where(fr < 0, 1.0, 0.0))

    def irradiance(self, P, N):
        """Lamp irradiance on points P with normals N, in lamp units / m^2."""
        l = self.pos.astype(P.dtype) - P
        r2 = np.einsum("...i,...i->...", l, l)
        r = np.sqrt(r2)
        lhat = l / r[..., None]
        ndotl = np.clip(np.einsum("...i,...i->...", N, lhat), 0.0, None)
        cos_off = -np.einsum("...i,i->...", lhat, self.axis.astype(P.dtype))
        spot = _smoothstep(math.cos(math.radians(LAMP_OUTER_DEG)), math.cos(math.radians(LAMP_INNER_DEG)), cos_off)
        fade = 1.0 - _smoothstep(LAMP_FADE_START_M, LAMP_FADE_END_M, r)
        out = spot * fade * ndotl / np.maximum(r2, 1e-6)
        lit = out > 0
        if np.any(lit):
            vis = np.zeros_like(out)
            vis[lit] = self.visibility(P[lit])
            out = out * vis
        return out


def _reference_irradiance():
    """Lamp irradiance on flat ground EXPOSURE_REF_RANGE_M straight ahead."""
    hl = CAMERA_HEIGHT_M - LAMP_BELOW_CAMERA_M
    l = np.array([-EXPOSURE_REF_RANGE_M, 0.0, hl])
    r2 = float(l @ l)
    lhat = l / math.sqrt(r2)
    p = math.radians(LAMP_PITCH_DEG)
    cos_off = float(-lhat @ np.array([math.cos(p), 0.0, -math.sin(p)]))
    spot = float(_smoothstep(math.cos(math.radians(LAMP_OUTER_DEG)), math.cos(math.radians(LAMP_INNER_DEG)), cos_off))
    return spot * lhat[2] / r2


# ---------------------------------------------------------------------------
# VTK helpers.
# ---------------------------------------------------------------------------
def _polydata(points, tris):
    pd = vtk.vtkPolyData()
    pts = vtk.vtkPoints()
    pts.SetData(vnp.numpy_to_vtk(np.ascontiguousarray(points, dtype=np.float32), deep=True))
    pd.SetPoints(pts)
    _set_tris(pd, tris)
    return pd


def _set_tris(pd, tris):
    tris = np.ascontiguousarray(tris, dtype=np.int64).ravel()
    offsets = np.arange(0, tris.size + 1, 3, dtype=np.int64)
    ca = vtk.vtkCellArray()
    ca.SetData(vnp.numpy_to_vtkIdTypeArray(offsets, deep=True), vnp.numpy_to_vtkIdTypeArray(tris, deep=True))
    pd.SetPolys(ca)
    pd.Modified()


def _set_colors(pd, rgb01):
    arr = vnp.numpy_to_vtk(np.ascontiguousarray(np.clip(rgb01 * 255.0 + 0.5, 0, 255).astype(np.uint8)), deep=True)
    arr.SetName("rgb")
    pd.GetPointData().SetScalars(arr)
    pd.Modified()


def _color_actor(pd, lighting=False):
    m = vtk.vtkPolyDataMapper()
    m.SetInputData(pd)
    m.SetColorModeToDirectScalars()
    m.SetScalarModeToUsePointData()
    a = vtk.vtkActor()
    a.SetMapper(m)
    if not lighting:
        a.GetProperty().LightingOff()
    return a


def _grid_tris(nr, nc, cell_mask=None):
    """Two triangles per cell of an (nr x nc) vertex grid, CCW seen from +z."""
    i, j = np.meshgrid(np.arange(nr - 1), np.arange(nc - 1), indexing="ij")
    if cell_mask is not None:
        i, j = i[cell_mask], j[cell_mask]
    v = (i * nc + j).ravel()
    t1 = np.stack([v, v + 1, v + nc + 1], axis=1)
    t2 = np.stack([v, v + nc + 1, v + nc], axis=1)
    return np.concatenate([t1, t2])


def _make_window(size, samples):
    rw = vtk.vtkRenderWindow()
    rw.SetOffScreenRendering(1)
    rw.SetSize(*size)
    rw.SetMultiSamples(samples)
    ren = vtk.vtkRenderer()
    rw.AddRenderer(ren)
    return rw, ren


def _read_rgb(rw):
    w, h = rw.GetSize()
    f = vtk.vtkWindowToImageFilter()
    f.SetInput(rw)
    f.SetInputBufferTypeToRGB()
    f.ReadFrontBufferOff()
    f.Update()
    img = vnp.vtk_to_numpy(f.GetOutput().GetPointData().GetScalars()).reshape(h, w, 3)
    return np.ascontiguousarray(img[::-1])


def _rover_polydata():
    """A simple rover in its own frame: x forward, y left, z up, origin on the ground under the mast."""
    app = vtk.vtkAppendPolyData()

    def box(cx, cy, cz, dx, dy, dz):
        s = vtk.vtkCubeSource()
        s.SetCenter(cx, cy, cz)
        s.SetXLength(dx)
        s.SetYLength(dy)
        s.SetZLength(dz)
        s.Update()
        app.AddInputData(s.GetOutput())

    box(-0.2, 0, 0.75, 1.7, 1.2, 0.5)        # body
    box(-0.2, 0, 1.05, 1.2, 1.0, 0.08)       # deck
    box(0, 0, 1.75, 0.10, 0.10, 1.5)         # mast
    box(0.02, 0, CAMERA_HEIGHT_M, 0.18, 0.40, 0.14)  # camera head
    for x in (0.45, -0.85):
        for y in (0.72, -0.72):
            c = vtk.vtkCylinderSource()
            c.SetRadius(0.3)
            c.SetHeight(0.22)
            c.SetResolution(20)
            c.SetCenter(x, y, 0.3)  # cylinder axis is y: wheels already sideways
            c.Update()
            app.AddInputData(c.GetOutput())
    app.Update()
    return app.GetOutput()


# ---------------------------------------------------------------------------
# The scene.
# ---------------------------------------------------------------------------
class Scene:
    """Both views of both rovers. See docs/handoffs.md, Scene section.

    depth from first_person() is the straight-line distance in metres from
    the camera centre to the surface along each pixel's ray, NaN for sky.
    Use camera_model() or depth_to_rover() to turn it into rover-frame points.
    """

    def __init__(self, height, craters, x0, y0, cell):
        height = np.asarray(height, dtype=np.float32)
        H, W = height.shape
        # Render in a local frame near the map centre: world coordinates such
        # as LOLA stereographic metres (~1e5) lose millimetres in float32.
        self.origin = np.array([x0 + 0.5 * W * cell, y0 + 0.5 * H * cell, float(np.median(height))])
        self.grid = _Grid(height - self.origin[2], x0 - self.origin[0], y0 - self.origin[1], cell)
        self.craters = np.asarray(craters, dtype=np.float64)
        self.e_ref = _reference_irradiance()
        self._albedo = self._make_albedo()

        self.poses = {}     # agency -> (x, y, heading), world
        self.lamps = {}     # agency -> _Lamp (local frame), built lazily
        self.blocks = {}    # agency -> fine mesh block
        self._lamp_colors_dirty = True

        # Coarse mesh over the whole map.
        g = self.grid
        K = COARSE_STEP
        ci = np.arange(0, H, K)
        cj = np.arange(0, W, K)
        if ci[-1] != H - 1:
            ci = np.append(ci, H - 1)
        if cj[-1] != W - 1:
            cj = np.append(cj, W - 1)
        self._ci, self._cj = ci, cj
        CI, CJ = np.meshgrid(ci, cj, indexing="ij")
        X = g.x0 + CJ * g.cell
        Y = g.y0 + CI * g.cell
        Z = g.h[CI, CJ]
        self._coarse_xyz = np.stack([X, Y, Z], axis=-1).reshape(-1, 3).astype(np.float64)
        self._coarse_pd = _polydata(self._coarse_xyz, _grid_tris(len(ci), len(cj)))
        n = g.normal(self._coarse_xyz[:, 0], self._coarse_xyz[:, 1], e=K * g.cell / 2)
        self._coarse_albedo = self._albedo_at(self._coarse_xyz)
        self._coarse_fill = self._fill_rgb(self._coarse_xyz, n)
        self._coarse_n = n
        _set_colors(self._coarse_pd, self._gamma(self._coarse_fill))
        self._coarse_actor = _color_actor(self._coarse_pd)

        # Render windows: first person (depth only, no MSAA so the depth is
        # exact) and third person (antialiased colour).
        self._fp_rw, self._fp_ren = _make_window(FP_SIZE, 0)
        self._tp_rw, self._tp_ren = _make_window(TP_SIZE, 8)
        self._tp_ren.SetBackground(0.015, 0.017, 0.03)
        self._fp_ren.SetBackground(0, 0, 0)
        for ren in (self._fp_ren, self._tp_ren):
            ren.AddActor(self._coarse_actor)
        # Third-person fill light for rover bodies (viewer aid only).
        self._tp_ren.AutomaticLightCreationOff()
        light = vtk.vtkLight()
        light.SetLightTypeToSceneLight()
        az, el = math.radians(FILL_AZ_DEG), math.radians(FILL_EL_DEG)
        light.SetPosition(math.cos(az) * math.cos(el), math.sin(az) * math.cos(el), math.sin(el))
        light.SetFocalPoint(0, 0, 0)
        light.SetPositional(False)
        light.SetIntensity(0.9)
        self._tp_ren.AddLight(light)
        head = vtk.vtkLight()
        head.SetLightTypeToHeadlight()
        head.SetIntensity(0.35)
        self._tp_ren.AddLight(head)

        self._rover_pd = _rover_polydata()
        self._rover_actors = {}  # agency -> (fp actor, tp actor)
        self.timings = {}

    # -- public API (docs/handoffs.md) ---------------------------------------
    def set_rover(self, agency, true_pose):
        x, y, hd = (float(v) for v in true_pose)
        self.poses[agency] = (x, y, hd)
        self.lamps.pop(agency, None)
        self._lamp_colors_dirty = True
        lx, ly = x - self.origin[0], y - self.origin[1]
        blk = self.blocks.get(agency)
        if blk is None or math.hypot(lx - blk["cx"], ly - blk["cy"]) > FINE_RECENTER_M:
            self._build_block(agency, lx, ly)
        self._place_rover_actor(agency)

    def first_person(self, agency):
        """(img (h, w, 3) uint8, depth (h, w) float32 metres, NaN for sky)."""
        t0 = time.perf_counter()
        C, fwd, right, up = self._camera_frame(agency)
        cam = self._fp_ren.GetActiveCamera()
        cam.SetPosition(*C)
        cam.SetFocalPoint(*(C + fwd * 10.0))
        cam.SetViewUp(*up)
        w, h = FP_SIZE
        vfov = 2.0 * math.degrees(math.atan(math.tan(math.radians(HFOV_DEG / 2)) * h / w))
        cam.SetViewAngle(vfov)
        cam.SetClippingRange(NEAR_CLIP_M, FAR_CLIP_M)
        for ag, (fa, _) in self._rover_actors.items():
            fa.SetVisibility(ag != agency)  # the camera never sees its own rover
        self._fp_rw.Render()
        zb = vtk.vtkFloatArray()
        self._fp_rw.GetZbufferData(0, 0, w - 1, h - 1, zb)
        d = vnp.vtk_to_numpy(zb).reshape(h, w)[::-1].astype(np.float64)
        n, f = cam.GetClippingRange()
        t1 = time.perf_counter()

        sky = d >= 1.0
        z_ndc = 2.0 * d - 1.0
        z_eye = 2.0 * f * n / ((f + n) - z_ndc * (f - n))
        fx, fy, cx, cy = self._intrinsics()
        u = (np.arange(w) + 0.5 - cx) / fx
        v = (cy - (np.arange(h) + 0.5)) / fy
        U, V = np.meshgrid(u, v)
        rng = z_eye * np.sqrt(1.0 + U * U + V * V)
        rng[sky] = np.nan
        depth = rng.astype(np.float32)

        # Shade: back-project, lamp model, shadow test.
        ok = ~sky
        dirs = fwd[None, :] + U[ok][:, None] * right[None, :] + V[ok][:, None] * up[None, :]
        P = (C[None, :] + dirs * z_eye[ok][:, None]).astype(np.float32)
        hP, N = self.grid.height_normal(P[:, 0], P[:, 1])
        off_ground = np.abs(P[:, 2] - hP) > 0.05
        if np.any(off_ground):  # another rover: normal from the depth image instead
            Pimg = np.full((h, w, 3), np.nan)
            Pimg[ok] = P
            nimg = np.cross(np.gradient(Pimg, axis=1), np.gradient(Pimg, axis=0))
            nimg /= np.linalg.norm(nimg, axis=-1, keepdims=True) + 1e-12
            nimg *= -np.sign(np.einsum("hwi,i->hw", nimg, fwd))[..., None]
            No = nimg[ok][off_ground]
            N[off_ground] = np.where(np.isfinite(No), No, 0.0)
        E = np.zeros(len(P), dtype=np.float32)
        for ag in self.poses:
            lamp = self._lamp(ag)
            near = np.abs(P[:, 0] - lamp.pos[0]) + np.abs(P[:, 1] - lamp.pos[1]) < 1.42 * LAMP_FADE_END_M
            if np.any(near):
                E[near] += lamp.irradiance(P[near], N[near])
        lin = self._albedo_at(P) * E / self.e_ref * EXPOSURE_REF_LEVEL ** DISPLAY_GAMMA
        grey = np.zeros((h, w))
        grey[ok] = np.clip(lin, 0.0, 1.0) ** (1.0 / DISPLAY_GAMMA)
        img = np.repeat((grey * 255.0 + 0.5).astype(np.uint8)[..., None], 3, axis=2)
        t2 = time.perf_counter()
        self.timings["fp_render_ms"] = 1e3 * (t1 - t0)
        self.timings["fp_shade_ms"] = 1e3 * (t2 - t1)
        return img, depth

    def third_person(self, agency, overlays=None):
        """(h, w, 3) uint8 chase view of `agency`, with optional overlays."""
        t0 = time.perf_counter()
        overlays = overlays or {}
        self._update_lamp_colors()
        x, y, hd = self.poses[agency]
        lx, ly = x - self.origin[0], y - self.origin[1]
        gz = float(self.grid.height(lx, ly))
        fwd = np.array([math.cos(hd), math.sin(hd), 0.0])
        eye = np.array([lx, ly, gz]) - fwd * CHASE_BACK_M
        eye[2] = max(gz + CHASE_UP_M, float(self.grid.height(eye[0], eye[1])) + 3.0)
        target = np.array([lx, ly, gz]) + fwd * CHASE_LOOK_AHEAD_M
        target[2] = float(self.grid.height(target[0], target[1])) + 0.5
        cam = self._tp_ren.GetActiveCamera()
        cam.SetPosition(*eye)
        cam.SetFocalPoint(*target)
        cam.SetViewUp(0, 0, 1)
        cam.SetViewAngle(CHASE_VIEW_ANGLE_DEG)
        cam.SetClippingRange(0.5, FAR_CLIP_M)
        for _, ta in self._rover_actors.values():
            ta.SetVisibility(True)

        extra = self._overlay_actors(agency, overlays)
        for a in extra:
            self._tp_ren.AddActor(a)
        self._tp_rw.Render()
        img = _read_rgb(self._tp_rw)
        for a in extra:
            self._tp_ren.RemoveActor(a)
        img = self._label_third_person(img, agency, overlays)
        self.timings["tp_ms"] = 1e3 * (time.perf_counter() - t0)
        return img

    # -- helpers for the vision step -----------------------------------------
    def camera_model(self):
        """Pinhole model of the first-person camera.

        Pixel (u, v), u right and v down from the top-left corner, has its
        centre at (u + 0.5, v + 0.5). Its ray in the camera frame (right,
        up, forward) is ((u + 0.5 - cx) / fx, (cy - v - 0.5) / fy, 1).
        The camera is pitched down by pitch_deg about its right axis and is
        levelled (no roll, no terrain tilt): an assumption of this scene.
        """
        fx, fy, cx, cy = self._intrinsics()
        return dict(width=FP_SIZE[0], height=FP_SIZE[1], fx=fx, fy=fy, cx=cx, cy=cy,
                    pitch_deg=CAMERA_PITCH_DEG, camera_height_m=CAMERA_HEIGHT_M,
                    lamp_below_camera_m=LAMP_BELOW_CAMERA_M, hfov_deg=HFOV_DEG)

    def depth_to_rover(self, depth):
        """(h, w, 3) rover-frame points [x_forward, y_left, z_up] in metres from the camera."""
        h, w = depth.shape
        fx, fy, cx, cy = self._intrinsics()
        u = (np.arange(w) + 0.5 - cx) / fx
        v = (cy - (np.arange(h) + 0.5)) / fy
        U, V = np.meshgrid(u, v)
        z_eye = depth / np.sqrt(1.0 + U * U + V * V)
        p = math.radians(CAMERA_PITCH_DEG)
        # Camera axes in the rover frame.
        fwd = np.array([math.cos(p), 0.0, -math.sin(p)])
        right = np.array([0.0, -1.0, 0.0])
        up = np.array([math.sin(p), 0.0, math.cos(p)])
        return z_eye[..., None] * (fwd + U[..., None] * right + V[..., None] * up)

    # -- internals ------------------------------------------------------------
    def _intrinsics(self):
        w, h = FP_SIZE
        fx = (w / 2.0) / math.tan(math.radians(HFOV_DEG / 2))
        return fx, fx, w / 2.0, h / 2.0

    def _camera_frame(self, agency):
        x, y, hd = self.poses[agency]
        lx, ly = x - self.origin[0], y - self.origin[1]
        C = np.array([lx, ly, float(self.grid.height(lx, ly)) + CAMERA_HEIGHT_M])
        p = math.radians(CAMERA_PITCH_DEG)
        fwd = np.array([math.cos(hd) * math.cos(p), math.sin(hd) * math.cos(p), -math.sin(p)])
        right = np.array([math.sin(hd), -math.cos(hd), 0.0])
        up = np.cross(right, fwd)
        return C, fwd, right, up

    def _lamp(self, agency):
        lamp = self.lamps.get(agency)
        if lamp is None:
            C, *_ = self._camera_frame(agency)
            lamp = _Lamp(self.grid, C - np.array([0.0, 0.0, LAMP_BELOW_CAMERA_M]), self.poses[agency][2])
            self.lamps[agency] = lamp
        return lamp

    def _make_albedo(self):
        rng = np.random.default_rng(7)
        n = 1024
        f = np.fft.fftfreq(n)
        f2 = f[:, None] ** 2 + f[None, :] ** 2

        def blur(sigma_px):  # periodic Gaussian blur, so the tile repeats seamlessly
            w = rng.standard_normal((n, n))
            return np.fft.ifft2(np.fft.fft2(w) * np.exp(-2 * (math.pi * sigma_px) ** 2 * f2)).real

        a, b = blur(1.2), blur(14.0)
        t = a / a.std() + 0.7 * b / b.std()
        return (1.0 + ALBEDO_VARIATION * t / t.std()).astype(np.float32)

    def _albedo_at(self, P):
        """Bilinear sample of the repeating albedo tile (one texel per grid cell)."""
        g = self.grid
        A = self._albedo
        n = A.shape[0]
        fj = (P[..., 0] - g.x0) / g.cell
        fi = (P[..., 1] - g.y0) / g.cell
        j0, i0 = np.floor(fj), np.floor(fi)
        tj, ti = (fj - j0).astype(np.float32), (fi - i0).astype(np.float32)
        j0 = j0.astype(np.int64) % n
        i0 = i0.astype(np.int64) % n
        j1, i1 = (j0 + 1) % n, (i0 + 1) % n
        a = A[i0, j0] + (A[i0, j1] - A[i0, j0]) * tj
        b = A[i1, j0] + (A[i1, j1] - A[i1, j0]) * tj
        return a + (b - a) * ti

    def _gamma(self, lin):
        return np.clip(lin, 0.0, 1.0) ** (1.0 / DISPLAY_GAMMA)

    def _fill_rgb(self, P, N):
        az, el = math.radians(FILL_AZ_DEG), math.radians(FILL_EL_DEG)
        s = np.array([math.cos(az) * math.cos(el), math.sin(az) * math.cos(el), math.sin(el)])
        k = FILL_LEVEL * (0.35 + 0.65 * np.clip(N @ s, 0.0, None))
        return (self._albedo_at(P) * k)[:, None] * FILL_RGB[None, :]

    # Fine mesh blocks -----------------------------------------------------
    def _build_block(self, agency, lx, ly):
        g = self.grid
        K = COARSE_STEP
        jc = int(round((lx - g.x0) / g.cell / K)) * K
        ic = int(round((ly - g.y0) / g.cell / K)) * K
        i0 = max(0, ic - FINE_HALF_CELLS)
        j0 = max(0, jc - FINE_HALF_CELLS)
        i1 = min(g.H - 1, ic + FINE_HALF_CELLS)
        j1 = min(g.W - 1, jc + FINE_HALF_CELLS)
        I, J = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1), indexing="ij")
        xyz = np.stack([g.x0 + J * g.cell, g.y0 + I * g.cell, g.h[I, J]], axis=-1).reshape(-1, 3).astype(np.float64)
        old = self.blocks.get(agency)
        blk = dict(i0=i0, i1=i1, j0=j0, j1=j1, cx=lx, cy=ly, xyz=xyz,
                   n=g.normal(xyz[:, 0], xyz[:, 1]))
        blk["albedo"] = self._albedo_at(xyz)
        blk["fill"] = self._fill_rgb(xyz, blk["n"])
        blk["pd"] = _polydata(xyz, np.zeros((0, 3), dtype=np.int64))
        blk["actor"] = _color_actor(blk["pd"])
        if old is not None:
            for ren in (self._fp_ren, self._tp_ren):
                ren.RemoveActor(old["actor"])
        self.blocks[agency] = blk
        for ren in (self._fp_ren, self._tp_ren):
            ren.AddActor(blk["actor"])
        self._rebuild_topology()
        self._lamp_colors_dirty = True

    def _rebuild_topology(self):
        """Fine blocks own their cells in agency order; coarse covers the rest; stitch the seams."""
        g = self.grid
        K = COARSE_STEP
        order = sorted(self.blocks)

        def in_block(b, i, j):
            return (i >= b["i0"]) & (i < b["i1"]) & (j >= b["j0"]) & (j < b["j1"])

        def in_any(i, j):
            m = np.zeros(np.shape(i), dtype=bool)
            for a in order:
                m |= in_block(self.blocks[a], i, j)
            return m

        for k, a in enumerate(order):
            b = self.blocks[a]
            nr, nc = b["i1"] - b["i0"] + 1, b["j1"] - b["j0"] + 1
            I, J = np.meshgrid(np.arange(b["i0"], b["i1"]), np.arange(b["j0"], b["j1"]), indexing="ij")
            owned = np.ones(I.shape, dtype=bool)
            for a2 in order[:k]:
                owned &= ~in_block(self.blocks[a2], I, J)
            tris = [_grid_tris(nr, nc, owned)]
            # Stitch fans where an owned edge cell borders coarse ground.
            def vid(i, j):
                return (i - b["i0"]) * nc + (j - b["j0"])
            for side in ("s", "n", "w", "e"):
                if side in ("s", "n"):
                    i_in = b["i0"] if side == "s" else b["i1"] - 1
                    i_out = i_in - 1 if side == "s" else i_in + 1
                    i_v = b["i0"] if side == "s" else b["i1"]
                    if i_out < 0 or i_out >= g.H - 1:
                        continue
                    for js in range(b["j0"], b["j1"], K):
                        je = min(js + K, b["j1"])
                        if owned[i_in - b["i0"], js - b["j0"]] and not in_any(i_out, js):
                            vs = [vid(i_v, j) for j in range(js, je + 1)]
                            tris.append(np.array([[vs[0], vs[t], vs[t + 1]] for t in range(1, len(vs) - 1)], dtype=np.int64).reshape(-1, 3))
                else:
                    j_in = b["j0"] if side == "w" else b["j1"] - 1
                    j_out = j_in - 1 if side == "w" else j_in + 1
                    j_v = b["j0"] if side == "w" else b["j1"]
                    if j_out < 0 or j_out >= g.W - 1:
                        continue
                    for is_ in range(b["i0"], b["i1"], K):
                        ie = min(is_ + K, b["i1"])
                        if owned[is_ - b["i0"], j_in - b["j0"]] and not in_any(is_, j_out):
                            vs = [vid(i, j_v) for i in range(is_, ie + 1)]
                            tris.append(np.array([[vs[0], vs[t], vs[t + 1]] for t in range(1, len(vs) - 1)], dtype=np.int64).reshape(-1, 3))
            _set_tris(b["pd"], np.concatenate(tris))

        ci, cj = self._ci, self._cj
        CI, CJ = np.meshgrid(ci[:-1], cj[:-1], indexing="ij")
        keep = ~in_any(CI, CJ)
        _set_tris(self._coarse_pd, _grid_tris(len(ci), len(cj), keep))

    def _update_lamp_colors(self):
        if not self._lamp_colors_dirty:
            return
        lamps = [self._lamp(a) for a in self.poses]
        for b in self.blocks.values():
            _set_colors(b["pd"], self._gamma(b["fill"] + self._lamp_rgb(b["xyz"], b["n"], b["albedo"], lamps)))
        _set_colors(self._coarse_pd, self._gamma(self._coarse_fill + self._lamp_rgb(self._coarse_xyz, self._coarse_n, self._coarse_albedo, lamps)))
        self._lamp_colors_dirty = False

    def _lamp_rgb(self, xyz, n, albedo, lamps):
        E = np.zeros(len(xyz), dtype=np.float32)
        for lamp in lamps:
            sel = np.hypot(xyz[:, 0] - lamp.pos[0], xyz[:, 1] - lamp.pos[1]) < LAMP_FADE_END_M
            if np.any(sel):
                E[sel] += lamp.irradiance(xyz[sel], n[sel])
        lin = albedo * E / self.e_ref * EXPOSURE_REF_LEVEL ** DISPLAY_GAMMA
        return lin[:, None] * LAMP_RGB[None, :]

    def _place_rover_actor(self, agency):
        if agency not in self._rover_actors:
            pair = []
            for ren in (self._fp_ren, self._tp_ren):
                m = vtk.vtkPolyDataMapper()
                m.SetInputData(self._rover_pd)
                m.ScalarVisibilityOff()
                a = vtk.vtkActor()
                a.SetMapper(m)
                a.GetProperty().SetColor(*AGENCY_RGB.get(agency, (0.8, 0.8, 0.8)))
                a.GetProperty().SetAmbient(0.25)
                ren.AddActor(a)
                pair.append(a)
            self._rover_actors[agency] = tuple(pair)
        x, y, hd = self.poses[agency]
        lx, ly = x - self.origin[0], y - self.origin[1]
        gz = float(self.grid.height(lx, ly))
        for a in self._rover_actors[agency]:
            a.SetPosition(lx, ly, gz)
            a.SetOrientation(0, 0, math.degrees(hd))

    # Third-person overlays ------------------------------------------------
    def _drape(self, xy, lift=DRAPE_M, step=0.5):
        """World (K, 2) polyline -> local (M, 3) points following the ground."""
        xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2) - self.origin[:2]
        if len(xy) >= 2:
            out = [xy[:1]]
            for a, b in zip(xy[:-1], xy[1:]):
                n = max(1, int(math.ceil(np.hypot(*(b - a)) / step)))
                t = (np.arange(1, n + 1) / n)[:, None]
                out.append(a + (b - a) * t)
            xy = np.concatenate(out)
        z = self.grid.height(xy[:, 0], xy[:, 1]) + lift
        return np.column_stack([xy, z])

    def _line_actor(self, pts, rgb, width=3.0, opacity=1.0):
        pd = vtk.vtkPolyData()
        vp = vtk.vtkPoints()
        vp.SetData(vnp.numpy_to_vtk(np.ascontiguousarray(pts, dtype=np.float32), deep=True))
        pd.SetPoints(vp)
        line = vtk.vtkPolyLine()
        line.GetPointIds().SetNumberOfIds(len(pts))
        for k in range(len(pts)):
            line.GetPointIds().SetId(k, k)
        cells = vtk.vtkCellArray()
        cells.InsertNextCell(line)
        pd.SetLines(cells)
        m = vtk.vtkPolyDataMapper()
        m.SetInputData(pd)
        a = vtk.vtkActor()
        a.SetMapper(m)
        p = a.GetProperty()
        p.SetColor(*rgb)
        p.SetLineWidth(width)
        p.SetOpacity(opacity)
        p.LightingOff()
        return a

    def _sphere_actor(self, c, r, rgb):
        s = vtk.vtkSphereSource()
        s.SetCenter(*c)
        s.SetRadius(r)
        s.SetThetaResolution(20)
        s.SetPhiResolution(12)
        m = vtk.vtkPolyDataMapper()
        m.SetInputConnection(s.GetOutputPort())
        a = vtk.vtkActor()
        a.SetMapper(m)
        a.GetProperty().SetColor(*rgb)
        a.GetProperty().SetAmbient(0.5)
        return a

    def _ring(self, x, y, r, n=96):
        t = np.linspace(0, 2 * math.pi, n + 1)
        return self._drape(np.column_stack([x + r * np.cos(t), y + r * np.sin(t)]), lift=DRAPE_M + 0.05, step=10.0)

    def _overlay_actors(self, agency, ov):
        acts = []
        col = AGENCY_RGB.get(agency, (0.8, 0.8, 0.8))
        if ov.get("odom_track") is not None and len(ov["odom_track"]) >= 2:
            acts.append(self._line_actor(self._drape(ov["odom_track"]), (1.0, 0.55, 0.25), 3.0, 0.9))
        if ov.get("true_track") is not None and len(ov["true_track"]) >= 2:
            acts.append(self._line_actor(self._drape(ov["true_track"]), (0.95, 0.95, 0.95), 3.0))
        if ov.get("estimate") is not None:
            ex, ey = ov["estimate"]
            c = self._drape([[ex, ey]], lift=0.35)[0]
            acts.append(self._sphere_actor(c, 0.25, col))
            if ov.get("sigma") is not None and ov["sigma"] > 0:
                acts.append(self._line_actor(self._ring(ex, ey, float(ov["sigma"])), col, 4.0))
        for f in ov.get("finds") or []:
            rgb = STATUS_RGB.get(str(f.get("status", "reported")).lower(), (0.9, 0.9, 0.9))
            base = self._drape([[f["x"], f["y"]]], lift=0.0)[0]
            acts.append(self._line_actor(np.array([base, base + [0, 0, 3.0]]), rgb, 4.0))
            acts.append(self._sphere_actor(base + [0, 0, 3.0], 0.3, rgb))
            if f.get("sigma"):
                acts.append(self._line_actor(self._ring(f["x"], f["y"], float(f["sigma"])), rgb, 2.0, 0.8))
        return acts

    def _label_third_person(self, img, agency, ov):
        img = np.ascontiguousarray(img)
        font = cv2.FONT_HERSHEY_SIMPLEX
        h, w = img.shape[:2]

        def text(s, org, scale=0.6, rgb=(235, 235, 235), thick=1):
            cv2.putText(img, s, org, font, scale, (0, 0, 0), thick + 2, cv2.LINE_AA)
            cv2.putText(img, s, org, font, scale, rgb, thick, cv2.LINE_AA)

        col = tuple(int(255 * c) for c in AGENCY_RGB.get(agency, (0.8, 0.8, 0.8)))
        text(f"Rover {agency}  chase view", (14, 30), 0.75, col, 2)
        text("Blue-grey fill light is for viewers only. The rover sees by its own lamp.", (14, h - 14), 0.5, (190, 200, 225))
        y = 56
        for key, label, rgb in (("true_track", "true track", (242, 242, 242)),
                                ("odom_track", "odometry", (255, 140, 64)),
                                ("sigma", "estimate and 1-sigma", col)):
            if ov.get(key) is not None:
                cv2.line(img, (16, y - 5), (40, y - 5), rgb, 3, cv2.LINE_AA)
                text(label, (48, y), 0.5)
                y += 22
        return img


# ---------------------------------------------------------------------------
# Terrain loading and a stand-in grid.
# ---------------------------------------------------------------------------
def load_any_terrain(path=None):
    """(height, craters, x0, y0, cell): step 2's file if given, else the stand-in."""
    if path is None:
        return standin_terrain()
    try:
        from sim.terrain import load_terrain  # step 2, once merged
        return load_terrain(path)
    except ImportError:
        z = np.load(path)
        return z["height"], z["craters"], float(z["x0"]), float(z["y0"]), float(z["cell"])


def standin_terrain(seed=3, n=4000, cell=0.25):
    """A stand-in in step 2's format until data/terrain/*.npz exists.

    Gentle slope, centimetre roughness, bowl craters 2 to 12 m wide and up to
    1 m deep (scene brief), several placed along the scripted drive. All of
    it is assumed, not from a source.
    """
    rng = np.random.default_rng(seed)
    x = np.arange(n, dtype=np.float32) * cell
    h = (0.02 * x[None, :] + 0.01 * x[:, None]).astype(np.float32)  # assumed 2% east, 1% north slope
    rough = rng.standard_normal((n, n)).astype(np.float32)
    h += 0.02 * cv2.GaussianBlur(rough, (0, 0), 2.0) / 0.14  # assumed ~2 cm roughness at ~0.5 m scales
    h += 0.15 * cv2.GaussianBlur(rng.standard_normal((n, n)).astype(np.float32), (0, 0), 40.0) / 0.007  # gentle swells
    craters = []
    # Craters along the scripted drive (y = 500 m, x from 100 m east).
    for k, (dx, dy, d) in enumerate([(12, 1.5, 6.0), (24, -3.0, 9.0), (37, 2.0, 4.0), (50, -1.0, 11.0),
                                     (64, 3.5, 7.0), (76, -2.5, 5.0), (90, 0.5, 8.0)]):
        craters.append((100 + dx, 500 + dy, d))
    for _ in range(600):
        craters.append((rng.uniform(20, n * cell - 20), rng.uniform(20, n * cell - 20), rng.uniform(2.0, 12.0)))
    out = []
    for cx, cy, d in craters:
        R = d / 2
        depth = min(1.0, 0.12 * d)  # assumed depth/diameter 0.12, capped at 1 m
        r_box = int(math.ceil(1.8 * R / cell))
        ci, cj = int(cy / cell), int(cx / cell)
        i0, i1 = max(0, ci - r_box), min(n, ci + r_box + 1)
        j0, j1 = max(0, cj - r_box), min(n, cj + r_box + 1)
        Y, X = np.meshgrid(np.arange(i0, i1) * cell, np.arange(j0, j1) * cell, indexing="ij")
        r = np.hypot(X - cx, Y - cy) / R
        bowl = np.where(r < 1, -depth * (1 - r * r), 0.0)
        rim = 0.12 * depth * np.exp(-((r - 1.0) / 0.25) ** 2)  # assumed raised rim
        h[i0:i1, j0:j1] += (bowl + rim).astype(np.float32)
        out.append((cx, cy, d))
    return h, np.array(out), 0.0, 0.0, cell


# ---------------------------------------------------------------------------
# Scripted drive: python -m sim.scene [--terrain file.npz] [--out drive.mp4]
# ---------------------------------------------------------------------------
def _scripted_drive(frames):
    """Rover A drives east past the stand-in craters; rover B idles nearby."""
    poses = []
    x, y, hd = 100.0, 500.0, 0.0
    for k in range(frames):
        hd = 0.12 * math.sin(k / 25.0)
        x += 0.5 * math.cos(hd)
        y += 0.5 * math.sin(hd)
        poses.append((x, y, hd))
    return poses


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--terrain", default=None, help="data/terrain/*.npz from step 2 (default: stand-in)")
    ap.add_argument("--out", default="frames/scene_drive.mp4")
    ap.add_argument("--frames", type=int, default=160)
    ap.add_argument("--start", default=None, help="x,y,heading for rover A on a real tile")
    args = ap.parse_args()

    t = time.perf_counter()
    height, craters, x0, y0, cell = load_any_terrain(args.terrain)
    print(f"terrain {height.shape} in {time.perf_counter() - t:.1f}s")
    t = time.perf_counter()
    scene = Scene(height, craters, x0, y0, cell)
    print(f"scene built in {time.perf_counter() - t:.1f}s")
    poses = _scripted_drive(args.frames)
    if args.start:
        sx, sy, sh = (float(v) for v in args.start.split(","))
        dx, dy = sx - poses[0][0], sy - poses[0][1]
        poses = [(px + dx, py + dy, ph + sh) for px, py, ph in poses]
    bx, by, _ = poses[0]
    scene.set_rover("B", (bx + 30.0, by + 25.0, -2.4))

    import os
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    fw, fh = FP_SIZE
    tw, th = TP_SIZE
    vw = cv2.VideoWriter(args.out, cv2.VideoWriter_fourcc(*"mp4v"), 10, (tw + fw, th))
    times = []
    track = []
    for k, pose in enumerate(poses):
        t0 = time.perf_counter()
        scene.set_rover("A", pose)
        track.append(pose[:2])
        img, depth = scene.first_person("A")
        t1 = time.perf_counter()
        tp = scene.third_person("A", {"true_track": np.array(track), "estimate": pose[:2], "sigma": 1.5})
        t2 = time.perf_counter()
        times.append((t1 - t0, t2 - t1))
        frame = np.zeros((th, tw + fw, 3), np.uint8)
        frame[:, :tw] = tp
        frame[(th - fh) // 2:(th - fh) // 2 + fh, tw:] = img
        cv2.putText(frame, "Rover A camera: lamp only", (tw + 10, (th - fh) // 2 + 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1, cv2.LINE_AA)
        vw.write(frame[..., ::-1])
        if k in (0, len(poses) // 2):
            cv2.imwrite(args.out.replace(".mp4", f"_f{k:03d}.png"), frame[..., ::-1])
            np.save(args.out.replace(".mp4", f"_f{k:03d}_depth.npy"), depth)
    vw.release()
    ts = np.array(times[1:]) * 1e3
    print(f"{len(times)} frames -> {args.out}")
    print(f"first person ms: median {np.median(ts[:, 0]):.0f}, max {ts[:, 0].max():.0f}")
    print(f"third person ms: median {np.median(ts[:, 1]):.0f}, max {ts[:, 1].max():.0f}")


if __name__ == "__main__":
    main()
