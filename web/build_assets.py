"""Turn the step 2 terrain into static files the web viewer can load.

The sim grid is 4000 x 4000 float32 at 0.25 m (64 MB), too big to ship raw.
This writes, under web/assets/terrain/:

    meta.json          grid geometry, quantisation, file list, real vs made up
    coarse.bin         whole map at 1 m, uint16, gzip
    tiles/r{i}_c{j}.bin  0.25 m tiles of 512 cells (513 samples, shared edges),
                       uint16, gzip; the viewer loads the ones near the rovers
    backdrop.bin       the 20 km LOLA backdrop at 20 m, uint16, gzip (if built)
    map.png            hillshade of the drive area for the 2D map, north up
    nogo.png           the planner's no-go ground (sim/rover.py SlopeMap), red on clear, north up
    craters.json       the crater list [[x, y, diameter], ...], world metres
    albedo.png         display albedo, 2048 px, north up (if built)
    detail_normal.png, detail_albedo.png   tileable close-up detail (if built)

Heights: metres = hmin + value * hscale. Row 0 of every grid is the south
edge (rows run north), as in docs/handoffs.md.

    python web/build_assets.py                   # Shoemaker drive area -> web/assets/terrain/
    python web/build_assets.py --site s89_134e   # a PSR site -> web/assets/terrain/s89_134e/
    python web/build_assets.py --run-ground live-nav/w00001-bcce1993
                                                 # a camera run's own ground -> web/assets/terrain/runs/<run_id>/

For a PSR site (terrain's sim/psr_sites.py) the grid is data/terrain/<site>.npz,
the display layer data/terrain/display_<site>/, and the backdrops come from
the site's own 5 m LOLA window (data/terrain/psr_sites/<site>.npz): native as
backdrop_near, averaged to 20 m as backdrop. Its display layer's own
backdrops, if any, are not used (terrain: cut from the wrong tile).

A camera run's record names the ground its cameras and lidar saw
(`ground.file`, docs/handoffs.md "Camera navigation"): a 1 m grid built by
sim/site_ground.py for that run. --run-ground builds the viewer's ground from
that grid, so the 3D view and the drawn first-person box show what the
cameras saw. Backdrops and the Hapke and detail parameters still come from
the run's site; rim lips, rocks and the site albedo are left out, because
they belong to the site grid, not to this one.
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sim.terrain import TERRAIN_DIR, load_terrain  # noqa: E402

DATA = TERRAIN_DIR  # LANTERN_DATA/terrain, or the default in sim/terrain.py


def run_display(ground_path):
    """sim/terrain_display.py build_for_ground: the run ground's camera texture dir."""
    sim_dir = str(ROOT / "sim")
    if sim_dir not in sys.path:
        sys.path.insert(0, sim_dir)  # terrain_display does `import terrain`
    import terrain_display as TD
    return TD.build_for_ground(ground_path)
OUT = ROOT / "web" / "assets" / "terrain"
TILE = 512          # cells per fine tile (128 m)
COARSE = 4          # coarse grid keeps every 4th sample (1 m)


def imwrite(path, img):
    """cv2.imwrite that fails loudly: cv2 returns False instead of raising."""
    if not cv2.imwrite(str(path), img):
        raise OSError(f"could not write {path}")


def write_u16(path, z, hmin, hscale):
    q = np.clip(np.round((z - hmin) / hscale), 0, 65535).astype("<u2")
    path.write_bytes(gzip.compress(np.ascontiguousarray(q).tobytes(), 9))


def hillshade(z, cell, az_deg=315.0, el_deg=25.0):
    gy, gx = np.gradient(z.astype(np.float64), cell)
    nx, ny, nz = -gx, -gy, np.ones_like(z)
    n = np.sqrt(nx * nx + ny * ny + 1)
    az, el = np.radians(az_deg), np.radians(el_deg)
    lx, ly, lz = np.cos(el) * np.sin(az), np.cos(el) * np.cos(az), np.sin(el)
    return np.clip((nx * lx + ny * ly + nz * lz) / n, 0, 1)


def add_lips(height, craters, lips, x0, y0, cell, s):
    """Rim lips from the terrain worker's display layer (lips.npy, one row per
    crater: lip height, lip width), as in sim/terrain_display.py Display.patch:
    h += s * lh * exp(-((r - R) / lw)^2). Display only; the sim grid is untouched."""
    h = height.copy()
    H, W = h.shape
    for (cx, cy, D), (lh, lw) in zip(craters, lips):
        R = D / 2
        reach = R + 3 * lw
        j0, j1 = max(int((cx - reach - x0) / cell), 0), min(int((cx + reach - x0) / cell) + 2, W)
        i0, i1 = max(int((cy - reach - y0) / cell), 0), min(int((cy + reach - y0) / cell) + 2, H)
        if j0 >= j1 or i0 >= i1:
            continue
        X = x0 + cell * np.arange(j0, j1)
        Y = y0 + cell * np.arange(i0, i1)
        r = np.hypot(X[None, :] - cx, Y[:, None] - cy)
        h[i0:i1, j0:j1] += (s * lh * np.exp(-((r - R) / lw) ** 2)).astype(np.float32)
    return h


def nogo_png(height, x0, y0, cell, path):
    """The planner's own no-go ground: SlopeMap(height).fine_deg > SLOPE_LIMIT_DEG
    (sim/rover.py), i.e. steeper than the limit across 2 m, widened by the
    rover's half width. Written as red on clear, north up, one pixel per sim
    cell. Returns the meta entry, or None when sim/rover.py has no SlopeMap."""
    try:
        from sim.rover import SlopeMap, SLOPE_LIMIT_DEG
    except ImportError as e:
        print(f"no-go layer skipped: {e}")
        return None
    bad = SlopeMap(height, x0, y0, cell).fine_deg > SLOPE_LIMIT_DEG
    rgba = np.zeros(bad.shape + (4,), np.uint8)
    rgba[bad] = (79, 87, 229, 255)          # BGRA for cv2: #e5574f, the page's "disputed" red
    imwrite(path, rgba[::-1])
    return {"file": path.name, "limit_deg": SLOPE_LIMIT_DEG, "fraction": round(float(bad.mean()), 4),
            "source": "sim/rover.py SlopeMap(height, x0, y0, cell).fine_deg > SLOPE_LIMIT_DEG, on the sim grid without display lips"}


def far_normal(z, cell):
    """Tileable tangent-space normal map (x east, y north, z up) of a periodic height tile."""
    gx = (np.roll(z, -1, 1) - np.roll(z, 1, 1)) / (2 * cell)
    gy = (np.roll(z, -1, 0) - np.roll(z, 1, 0)) / (2 * cell)
    n = np.stack([-gx, -gy, np.ones_like(z)], -1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    rgb = np.clip(n * 127.5 + 127.5, 0, 255).astype(np.uint8)
    return rgb[::-1]  # north up


def site_backdrops(site):
    """(backdrop, backdrop_near) dicts of height, x0, y0, cell from the site's 5 m LOLA window."""
    z = np.load(DATA / "psr_sites" / f"{site}.npz")
    h, x0, y0, c = z["height"].astype(np.float32), float(z["x0"]), float(z["y0"]), float(z["cell"])
    k = 4
    n0, n1 = (h.shape[0] // k) * k, (h.shape[1] // k) * k
    far = h[:n0, :n1].reshape(n0 // k, k, n1 // k, k).mean((1, 3))
    # A block of k cells centred at x0 + c * (k - 1) / 2.
    return ({"height": far, "x0": x0 + c * (k - 1) / 2, "y0": y0 + c * (k - 1) / 2, "cell": c * k},
            {"height": h, "x0": x0, "y0": y0, "cell": c})


def main(site=None, run_ground=None):
    global OUT
    ground_rec = None
    if run_ground:
        rec = json.loads((DATA.parent / "campaigns" / run_ground / "record.json").read_text())
        ground_rec = rec["ground"]
        site = rec["terrain"]["site"]
        OUT = OUT / "runs" / rec["run_id"]
    elif site:
        OUT = OUT / site
    OUT.mkdir(parents=True, exist_ok=True)
    src = DATA / ground_rec["file"] if ground_rec else DATA / f"{site or 'shoemaker'}.npz"
    height, craters, x0, y0, cell = load_terrain(src)
    H, W = height.shape
    # The planner works on the sim grid as is, so the no-go layer does too.
    nogo = nogo_png(height, x0, y0, cell, OUT / "nogo.png")
    disp = DATA / (f"display_{site}" if site else "display")
    if ground_rec:
        # The camera texture built for this run ground (sim/terrain_display.py
        # build_run, from the ground's own roughness), the same one the
        # Scouts' cameras render with; built here if the run predates it.
        disp = run_display(src)
    dj = disp / "display.json"
    d = json.loads(dj.read_text()) if dj.exists() else None
    if d is not None and (disp / "lips.npy").exists() and not ground_rec:
        height = add_lips(height, craters, np.load(disp / "lips.npy"), x0, y0, cell, d["params"]["strength"])
    hmin = float(height.min()) - 1.0
    hscale = (float(height.max()) + 1.0 - hmin) / 65535.0
    (OUT / "tiles").mkdir(parents=True, exist_ok=True)

    coarse = height[::COARSE, ::COARSE]
    write_u16(OUT / "coarse.bin", coarse, hmin, hscale)

    tiles = []
    for i in range(0, H - 1, TILE):
        for j in range(0, W - 1, TILE):
            t = height[i:i + TILE + 1, j:j + TILE + 1]
            name = f"tiles/r{i // TILE}_c{j // TILE}.bin"
            write_u16(OUT / name, t, hmin, hscale)
            tiles.append({"file": name, "i0": i, "j0": j, "rows": t.shape[0], "cols": t.shape[1]})

    # 2D map: hillshade at 1 m, north up (flip rows for the image).
    hs = hillshade(coarse, cell * COARSE)
    img = (40 + 170 * hs ** 0.8).astype(np.uint8)[::-1]
    imwrite(OUT / "map.png", img)

    (OUT / "craters.json").write_text(json.dumps(np.round(craters, 2).tolist(), separators=(",", ":")))

    meta = {
        "source": (f"{ground_rec['file']} (the run's own ground, sim/site_ground.py build_ground: {ground_rec.get('note', '')})" if ground_rec
                   else f"{site}.npz (sim/terrain.py build --site {site}: LOLA 5 m, upsampled, with stamped small craters)" if site
                   else "shoemaker.npz (step 2: LOLA Shoemaker 5 m window, upsampled, with stamped small craters)"),
        "site": site or "shoemaker",
        **({"run_ground": ground_rec["file"]} if ground_rec else {}),
        "x0": x0, "y0": y0, "cell": cell, "rows": H, "cols": W,
        "hmin": hmin, "hscale": hscale,
        "coarse": {"file": "coarse.bin", "step": COARSE, "rows": coarse.shape[0], "cols": coarse.shape[1]},
        "tile": TILE, "tiles": tiles,
        "map": {"file": "map.png", "note": "hillshade, north up, light from the north-west (display only)"},
        **({"nogo": nogo} if nogo else {}),
        "craters": "craters.json",
    }

    if d is not None:
        P = d["params"]
        meta["display"] = {"real": d.get("real", []), "made_up": d.get("made_up", []),
                           "detail_period_m": P["tile_period_m"], "strength": P["strength"],
                           "lips": "none: the run's own ground" if ground_rec else "baked into the heights above (display only)",
                           "moon_radius_m": d.get("moon_radius_m", 1737400.0)}
        # Hapke [X20] parameters, from display.json (terrain worker's sources).
        meta["hapke"] = {k: P[k] for k in ("hapke_w", "hapke_b", "hapke_bs0", "hapke_hs")}
        meta["hapke"]["source"] = d.get("sources", {}).get("hapke", "")
        bds = dict(zip(("backdrop", "backdrop_near"), site_backdrops(site))) if site else {}
        for key, name in (("backdrop", "backdrop.npz"), ("backdrop_near", "backdrop_near.npz")):
            if site:
                bd = bds[key]
            elif (disp / name).exists():
                bd = np.load(disp / name)
            else:
                continue
            bh = bd["height"]
            bmin = float(bh.min()) - 1.0
            bscale = (float(bh.max()) + 1.0 - bmin) / 65535.0
            write_u16(OUT / f"{key}.bin", bh, bmin, bscale)
            meta[key] = {"file": f"{key}.bin", "x0": float(bd["x0"]), "y0": float(bd["y0"]),
                         "cell": float(bd["cell"]), "rows": bh.shape[0], "cols": bh.shape[1],
                         "hmin": bmin, "hscale": bscale}
        if site:
            meta["backdrop"]["note"] = f"LOLA 5 m mosaic (87S) averaged to 20 m, the site's 8 km window (real)"
            meta["backdrop_near"]["note"] = f"LOLA 5 m mosaic (87S), the site's 8 km window, native (real)"
        else:
            meta["backdrop"]["note"] = "LOLA Shoemaker 5 m averaged to 20 m, whole 20 km tile (real)"
            if "backdrop_near" in meta:
                meta["backdrop_near"]["note"] = "LOLA Shoemaker native 5 m, 5 km square (real)"
        if (disp / "far_detail_height.npy").exists():
            fz = np.load(disp / "far_detail_height.npy") * P["strength"]
            imwrite(OUT / "far_normal.png", far_normal(fz, P["far_tile_cell_m"])[..., ::-1])
            meta["far_detail"] = {"normal": "far_normal.png", "period_m": fz.shape[0] * P["far_tile_cell_m"],
                                  "note": "made-up sub-5 m relief for the backdrops"}
        if (disp / "rocks.npy").exists() and not ground_rec:
            rk = np.load(disp / "rocks.npy").astype("<f4")
            rk = rk[: int(round(P["strength"] * len(rk)))]
            # Relative to the grid origin so float32 keeps millimetres.
            rel = rk.copy()
            rel[:, 0] -= x0
            rel[:, 1] -= y0
            (OUT / "rocks.bin").write_bytes(gzip.compress(np.ascontiguousarray(rel).tobytes(), 9))
            meta["rocks"] = {"file": "rocks.bin", "count": len(rk), "cols": 7,
                             "layout": "[x - x0, y - y0, long_m, short_m, height_m, yaw_rad, seed] float32",
                             "rock_albedo": P["rock_albedo"], "sink_frac": P["rock_sink_frac"]}
        # Rim lips, for the viewer's fine patch near each rover: lips are a few
        # cm wide, finer than the 0.25 m tiles they are also baked into.
        lp = np.load(disp / "lips.npy") if not ground_rec else None
        lt = None if lp is None else np.column_stack([craters[:, 0] - x0, craters[:, 1] - y0, craters[:, 2],
                              P["strength"] * lp[:, 0], lp[:, 1]]).astype(np.float32)
        if lt is not None:
            (OUT / "lips.bin").write_bytes(gzip.compress(np.ascontiguousarray(lt).tobytes(), 9))
            meta["lips"] = {"file": "lips.bin", "count": len(lt), "cols": 5,
                            "layout": "[x - x0, y - y0, diameter_m, lip_height_m * strength, lip_width_m] float32",
                            "formula": "h += lip_height * exp(-((r - D/2) / lip_width)^2), already in the tiles"}
        if not ground_rec:
            alb = cv2.imread(str(disp / "albedo.png"), cv2.IMREAD_UNCHANGED)[::-1]
            imwrite(OUT / "albedo.png", alb)
            meta["albedo"] = {"file": "albedo.png", "factor": "value / 128", "north_up": True}
        for name in ("detail_normal.png", "detail_albedo.png"):
            im = cv2.imread(str(disp / name), cv2.IMREAD_UNCHANGED)
            im = cv2.resize(im, (1024, 1024), interpolation=cv2.INTER_AREA)[::-1]
            imwrite(OUT / name, im)
        meta["detail"] = {"normal": "detail_normal.png", "albedo": "detail_albedo.png",
                          "period_m": d["params"]["tile_period_m"], "north_up": True}

    (OUT / "meta.json").write_text(json.dumps(meta, indent=1))
    total = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
    print(f"{len(tiles)} tiles, {total / 1e6:.1f} MB -> {OUT}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", help="a PSR site with data/terrain/<site>.npz (default: the Shoemaker drive area)")
    ap.add_argument("--run-ground", help="<batch>/<run_id> of a camera run: build from the ground its record names")
    a = ap.parse_args()
    main(a.site, a.run_ground)
