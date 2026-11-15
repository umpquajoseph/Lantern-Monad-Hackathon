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


def main():
    height, craters, x0, y0, cell = load_terrain(DATA / "shoemaker.npz")
    H, W = height.shape
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

    disp = DATA / "display"
    dj = disp / "display.json"
    if dj.exists():
        d = json.loads(dj.read_text())
        meta["display"] = {"real": d.get("real", []), "made_up": d.get("made_up", []),
                           "detail_period_m": d["params"]["tile_period_m"]}
        bd = np.load(disp / "backdrop.npz")
        bh = bd["height"]
        bmin = float(bh.min()) - 1.0
        bscale = (float(bh.max()) + 1.0 - bmin) / 65535.0
        write_u16(OUT / "backdrop.bin", bh, bmin, bscale)
        meta["backdrop"] = {"file": "backdrop.bin", "x0": float(bd["x0"]), "y0": float(bd["y0"]),
                            "cell": float(bd["cell"]), "rows": bh.shape[0], "cols": bh.shape[1],
                            "hmin": bmin, "hscale": bscale,
                            "note": "LOLA Shoemaker 5 m averaged to 20 m, whole 20 km tile"}
        alb = cv2.imread(str(disp / "albedo.png"), cv2.IMREAD_UNCHANGED)
        alb = cv2.resize(alb, (2048, 2048), interpolation=cv2.INTER_AREA)[::-1]
        cv2.imwrite(str(OUT / "albedo.jpg"), alb, [cv2.IMWRITE_JPEG_QUALITY, 88])
        meta["albedo"] = {"file": "albedo.jpg", "factor": "value / 128", "north_up": True}
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
