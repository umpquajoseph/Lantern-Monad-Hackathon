"""Height grids for the terrain windows the campaign runs use, for the site.

A run record names its terrain window (`terrain`: site, x0, y0, cell,
bounds; docs/handoffs.md). The site's 3D world and map ship the 1 km drive
area and the LOLA backdrops (web/build_assets.py). A window outside the 5 m
near backdrop would only get the 20 m one, so this cuts the window from the
same LOLA 5 m GeoTIFF the engine reads (sim/campaign.py, _shoemaker_window)
and writes it beside the other assets:

    web/assets/terrain/windows/<key>.bin   uint16, gzip, rows run north
    web/assets/terrain/windows.json        [{key, site, file, x0, y0, cell, rows, cols, hmin, hscale}]

Display only: the simulation never reads these files.

    python web/build_windows.py <record.json> [...]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web"))
from sim.terrain import LOLA_PX, LOLA_SHOEMAKER, LOLA_X_LEFT, LOLA_Y_TOP  # noqa: E402
from build_assets import write_u16  # noqa: E402

OUT = ROOT / "web" / "assets" / "terrain"
INDEX = OUT / "windows.json"


def window_key(t: dict) -> str:
    return f"{t.get('site', 'site')}_{t['x0']:.1f}_{t['y0']:.1f}_{t['cell']:g}".replace(".", "p")


def load_index() -> list[dict]:
    return json.loads(INDEX.read_text(encoding="utf8")) if INDEX.exists() else []


def build(t: dict, say=print) -> dict | None:
    """Make the window for one record's `terrain` block, if it is a LOLA Shoemaker 5 m window."""
    if not t or "bounds" not in t or not str(t.get("site", "")).lower().startswith("shoemaker"):
        return None
    if abs(float(t["cell"]) - LOLA_PX) > 1e-6:
        say(f"windows: {t.get('file')} has cell {t['cell']} m, not the LOLA {LOLA_PX} m; skipped")
        return None
    key = window_key(t)
    idx = load_index()
    if any(w["key"] == key for w in idx) and (OUT / "windows" / f"{key}.bin").exists():
        return next(w for w in idx if w["key"] == key)
    x0, y0, cell = float(t["x0"]), float(t["y0"]), float(t["cell"])
    xmin, ymin, xmax, ymax = t["bounds"]
    cols, rows = int(round((xmax - xmin) / cell)), int(round((ymax - ymin) / cell))
    # The engine's own transform (sim/campaign.py standin_world), inverted.
    col0 = int(round((x0 - LOLA_X_LEFT) / cell - 0.5))
    row0 = int(round((LOLA_Y_TOP - y0) / cell + 0.5 - rows))
    tif = Path(str(LOLA_SHOEMAKER).format("surf"))
    if not tif.exists():
        say(f"windows: no LOLA tile at {tif}; the 3D world falls back to the 20 m backdrop")
        return None
    import tifffile
    z = tifffile.imread(str(tif))[row0:row0 + rows, col0:col0 + cols]
    z = np.ascontiguousarray(z[::-1]).astype(np.float32)   # rows run north
    if z.shape != (rows, cols):
        say(f"windows: {key} falls off the LOLA tile ({z.shape}); skipped")
        return None
    hmin = float(z.min()) - 1.0
    hscale = (float(z.max()) + 1.0 - hmin) / 65535.0
    (OUT / "windows").mkdir(parents=True, exist_ok=True)
    write_u16(OUT / "windows" / f"{key}.bin", z, hmin, hscale)
    w = {"key": key, "site": t.get("site"), "file": f"windows/{key}.bin", "x0": x0, "y0": y0, "cell": cell,
         "rows": rows, "cols": cols, "hmin": hmin, "hscale": hscale,
         "note": "LOLA Shoemaker 5 m, cut as sim/campaign.py cuts its window (real)"}
    idx = [v for v in idx if v["key"] != key] + [w]
    INDEX.write_text(json.dumps(idx, indent=1), encoding="utf8")
    say(f"windows: built {key} ({rows} x {cols} at {cell:g} m)")
    return w


if __name__ == "__main__":
    for p in sys.argv[1:]:
        build(json.loads(Path(p).read_text(encoding="utf8")).get("terrain"))
