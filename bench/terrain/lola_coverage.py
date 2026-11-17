"""How finely LOLA actually samples each PSR site's 5 m mosaic.

    python bench/terrain/lola_coverage.py

Barker et al. 2023 (PSJ, sec. 2) define a LOLA product's effective resolution
as the diameter of the largest circle that fits between LOLA spots. Here, per
PSR-floor pixel, twice the distance to the nearest pixel holding a return
(ldec > 0) is the diameter of the empty circle centred there. Reported as
percentiles over the PSR floor. Only the sites' saved ldec layer is read.
"""
import json
from pathlib import Path

import numpy as np
from scipy.ndimage import distance_transform_edt

DATA = Path("C:/Monad-Hackathon-Project/data/terrain/psr_sites")
OUT = Path(__file__).resolve().parent / "out"


def main():
    res = {}
    for s in ("haynes", "s89_014e", "s89_134e", "nobile"):
        z = np.load(DATA / f"{s}.npz", allow_pickle=True)
        c, P, cell = z["ldec"], z["psr"].astype(bool), float(z["cell"])
        gap = 2 * cell * distance_transform_edt(~(c > 0))
        g = gap[P]
        res[s] = dict(cell_m=cell, share_px_with_return=float(np.mean(c > 0)),
                      share_floor_px_with_return=float(np.mean(c[P] > 0)),
                      floor_gap_diam_m={f"p{q}": float(np.percentile(g, q)) for q in (50, 75, 90, 99)},
                      floor_share_gap_over_10m=float(np.mean(g > 10)), floor_share_gap_over_20m=float(np.mean(g > 20)))
        print(s, json.dumps(res[s]))
    OUT.mkdir(exist_ok=True)
    (OUT / "lola_coverage.json").write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
