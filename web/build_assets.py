"""Turn the step 2 terrain into static files the web viewer can load.

The sim grid is 4000 x 4000 float32 at 0.25 m (64 MB), too big to ship raw.
This writes, under web/assets/terrain/:

    meta.json          grid geometry, quantisation, file list, real vs made up
    coarse.bin         whole map at 1 m, uint16, gzip
    tiles/r{i}_c{j}.bin  0.25 m tiles of 512 cells (513 samples, shared edges),
                       uint16, gzip; the viewer loads the ones near the rovers
    backdrop.bin       the 20 km LOLA backdrop at 20 m, uint16, gzip (if built)
    map.png            hillshade of the drive area for the 2D map, north up
    craters.json       the crater list [[x, y, diameter], ...], world metres
    albedo.jpg         display albedo, 2048 px, north up (if built)
    detail_normal.png, detail_albedo.png   tileable close-up detail (if built)

Heights: metres = hmin + value * hscale. Row 0 of every grid is the south
edge (rows run north), as in docs/handoffs.md.

    python web/build_assets.py
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
from sim.terrain import load_terrain  # noqa: E402

DATA = Path(r"C:\Monad-Hackathon-Project\data\terrain")
OUT = ROOT / "web" / "assets" / "terrain"
TILE = 512          # cells per fine tile (128 m)
COARSE = 4          # coarse grid keeps every 4th sample (1 m)


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


def far_normal(z, cell):
    """Tileable tangent-space normal map (x east, y north, z up) of a periodic height tile."""
    gx = (np.roll(z, -1, 1) - np.roll(z, 1, 1)) / (2 * cell)
    gy = (np.roll(z, -1, 0) - np.roll(z, 1, 0)) / (2 * cell)
    n = np.stack([-gx, -gy, np.ones_like(z)], -1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    rgb = np.clip(n * 127.5 + 127.5, 0, 255).astype(np.uint8)
    return rgb[::-1]  # north up


def main():
    height, craters, x0, y0, cell = load_terrain(DATA / "shoemaker.npz")
    H, W = height.shape
    disp = DATA / "display"
    dj = disp / "display.json"
    d = json.loads(dj.read_text()) if dj.exists() else None
    if d is not None and (disp / "lips.npy").exists():
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
    cv2.imwrite(str(OUT / "map.png"), img)

    (OUT / "craters.json").write_text(json.dumps(np.round(craters, 2).tolist(), separators=(",", ":")))

    meta = {
        "source": "shoemaker.npz (step 2: LOLA Shoemaker 5 m window, upsampled, with stamped small craters)",
        "x0": x0, "y0": y0, "cell": cell, "rows": H, "cols": W,
        "hmin": hmin, "hscale": hscale,
        "coarse": {"file": "coarse.bin", "step": COARSE, "rows": coarse.shape[0], "cols": coarse.shape[1]},
        "tile": TILE, "tiles": tiles,
        "map": {"file": "map.png", "note": "hillshade, north up, light from the north-west (display only)"},
        "craters": "craters.json",
    }

    if d is not None:
        P = d["params"]
        meta["display"] = {"real": d.get("real", []), "made_up": d.get("made_up", []),
                           "detail_period_m": P["tile_period_m"], "strength": P["strength"],
                           "lips": "baked into the heights above (display only)",
                           "moon_radius_m": d.get("moon_radius_m", 1737400.0)}
        # Hapke [X20] parameters, from display.json (terrain worker's sources).
        meta["hapke"] = {k: P[k] for k in ("hapke_w", "hapke_b", "hapke_bs0", "hapke_hs")}
        meta["hapke"]["source"] = d.get("sources", {}).get("hapke", "")
        for key, name in (("backdrop", "backdrop.npz"), ("backdrop_near", "backdrop_near.npz")):
            if not (disp / name).exists():
                continue
            bd = np.load(disp / name)
            bh = bd["height"]
            bmin = float(bh.min()) - 1.0
            bscale = (float(bh.max()) + 1.0 - bmin) / 65535.0
            write_u16(OUT / f"{key}.bin", bh, bmin, bscale)
            meta[key] = {"file": f"{key}.bin", "x0": float(bd["x0"]), "y0": float(bd["y0"]),
                         "cell": float(bd["cell"]), "rows": bh.shape[0], "cols": bh.shape[1],
                         "hmin": bmin, "hscale": bscale}
        meta["backdrop"]["note"] = "LOLA Shoemaker 5 m averaged to 20 m, whole 20 km tile (real)"
        if "backdrop_near" in meta:
            meta["backdrop_near"]["note"] = "LOLA Shoemaker native 5 m, 5 km square (real)"
        if (disp / "far_detail_height.npy").exists():
            fz = np.load(disp / "far_detail_height.npy") * P["strength"]
            cv2.imwrite(str(OUT / "far_normal.png"), far_normal(fz, P["far_tile_cell_m"])[..., ::-1])
            meta["far_detail"] = {"normal": "far_normal.png", "period_m": fz.shape[0] * P["far_tile_cell_m"],
                                  "note": "made-up sub-5 m relief for the backdrops"}
        if (disp / "rocks.npy").exists():
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
        alb = cv2.imread(str(disp / "albedo.png"), cv2.IMREAD_UNCHANGED)[::-1]
        cv2.imwrite(str(OUT / "albedo.png"), alb)
        meta["albedo"] = {"file": "albedo.png", "factor": "value / 128", "north_up": True}
        for name in ("detail_normal.png", "detail_albedo.png"):
            im = cv2.imread(str(disp / name), cv2.IMREAD_UNCHANGED)
            im = cv2.resize(im, (1024, 1024), interpolation=cv2.INTER_AREA)[::-1]
            cv2.imwrite(str(OUT / name), im)
        meta["detail"] = {"normal": "detail_normal.png", "albedo": "detail_albedo.png",
                          "period_m": d["params"]["tile_period_m"], "north_up": True}

    (OUT / "meta.json").write_text(json.dumps(meta, indent=1))
    total = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
    print(f"{len(tiles)} tiles, {total / 1e6:.1f} MB -> {OUT}")


if __name__ == "__main__":
    main()
