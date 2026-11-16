"""Candidate PSR terrain windows for the campaign (docs/campaign-spec.md 11.1).

Each site is an 8 km square of real LOLA terrain at its native spacing (5 m
in 87-90 S, 10 m in 84.5-87 S),
centred on a dock on sunlit ground at the edge of a permanently shadowed
region, with PSR floor that a Scout can reach on slopes under 15 deg.

    python -m sim.psr_sites screen [87s|85s] # every PSR in 87-90 S (40 m) or 84.5-87 S (80 m)
    python -m sim.psr_sites build [SITE]    # fetch the native windows, pick the dock
    python -m sim.psr_sites look [SITE]     # top-down check maps

Built files: DATA/terrain/psr_sites/<site>.npz (git-ignored, shared by all
worktrees through sim/terrain.py DATA). Arrays follow docs/handoffs.md
"Frames and units": cell [i, j] centre at x = x0 + j*cell, y = y0 + i*cell,
rows run north; x, y are south polar stereographic metres (R 1737.4 km,
true scale at the pole), the frame of every LOLA product used here.

Licence. Every PGDA dataset below is NASA LOLA team work. PGDA's
"Reproduction Guidelines" link points to the NASA Images and Media Usage
Guidelines (https://www.nasa.gov/nasa-brand-center/images-and-media/, read
16 Nov 2026): NASA content "generally [is] not subject to copyright in the
United States", may be used for educational purposes and in "computer
graphical simulations", and "NASA should be acknowledged as the source".
PGDA asks that the papers below be cited. The IAU Gazetteer is a public
USGS service. Datasets:
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
from scipy import ndimage as ndi

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sim.geotiff_window import open_geotiff
from sim.settings import SiteBuild
from sim.terrain import DATA

SITE_DIR = DATA / "terrain" / "psr_sites"
R_MOON = 1737400.0  # GeoTIFF tags of every product below: sphere 1737400 m

PGDA = "https://pgda.gsfc.nasa.gov/data/"
SOURCES = {
    "ldem": dict(url=PGDA + "LOLA_5mpp/87S/ldem_87s_5mpp.tif", size="3.3 GB (3,465,285,714 B); read by HTTP range, "
                 "only the tiles a window touches", what="surface height, m, 5 m/px, 87-90 S",
                 page="https://pgda.gsfc.nasa.gov/products/81",
                 cite="Barker et al. 2021, PSS 203, 105119, doi:10.1016/j.pss.2020.105119"),
    "ldsm": dict(url=PGDA + "LOLA_5mpp/87S/ldsm_87s_5mpp.tif", size="4.9 GB; read by HTTP range",
                 what="surface slope, deg, at the native 5 m spacing", page="https://pgda.gsfc.nasa.gov/products/81",
                 cite="Barker et al. 2021"),
    "ldec": dict(url=PGDA + "LOLA_5mpp/87S/ldec_87s_5mpp.tif", size="140 MB; read by HTTP range",
                 what="LOLA returns per pixel (0 = interpolated)", page="https://pgda.gsfc.nasa.gov/products/81",
                 cite="Barker et al. 2021"),
    "psr": dict(url=PGDA + "LOLA_20mpp/LPSR_80S_20MPP_ADJ.TIF", size="154 MB, downloaded to DATA/psr/",
                local=str(DATA / "psr" / "LPSR_80S_20MPP_ADJ.TIF"),
                what="permanent shadow map, 1 = PSR, 20 m/px, 80-90 S", page="https://pgda.gsfc.nasa.gov/products/90",
                cite="Barker et al., 'A New View of the Lunar South Pole from the Lunar Orbiter Laser "
                     "Altimeter (LOLA)', Planetary Science Journal, Jan 2023 (volume and page UNVERIFIED)"),
    "sun": dict(url=PGDA + "MoonIllumination/AVGVISIB_85S_060M_201608.TIF", size="34 MB, downloaded to DATA/illum/",
                local=str(DATA / "illum" / "AVGVISIB_85S_060M_201608.TIF"),
                what="average sunlit fraction at the ground over 18.6 yr, int16 x 4e-5 (GDAL SCALE tag), 60 m/px",
                page="https://pgda.gsfc.nasa.gov/products/69",
                cite="Mazarico et al. 2011, Icarus 211, 1066, doi:10.1016/j.icarus.2010.10.030"),
    "earth": dict(url=PGDA + "MoonIllumination/AVGVISIB_85S_060M_201608_EARTH.TIF",
                  size="25 MB, downloaded to DATA/illum/",
                  local=str(DATA / "illum" / "AVGVISIB_85S_060M_201608_EARTH.TIF"),
                  what="average fraction of time Earth is visible, int16 x 4e-5, 60 m/px",
                  page="https://pgda.gsfc.nasa.gov/products/69", cite="Mazarico et al. 2011"),
    "ldem83": dict(url=PGDA + "LOLA_20mpp/LDEM_83S_10MPP_ADJ.TIF", size="4.8 GB; read by HTTP range",
                   what="surface height, m, 10 m/px, 83-90 S (used for 84.5-87 S)",
                   page="https://pgda.gsfc.nasa.gov/products/90", cite="Barker et al. 2023 (as psr)"),
    "ldsm83": dict(url=PGDA + "LOLA_20mpp/LDSM_83S_10MPP_ADJ.TIF", size="7.5 GB; read by HTTP range",
                   what="surface slope, deg, at the native 10 m spacing",
                   page="https://pgda.gsfc.nasa.gov/products/90", cite="Barker et al. 2023 (as psr)"),
    "ldec83": dict(url=PGDA + "LOLA_20mpp/LDEC_83S_10MPP_ADJ.TIF", size="435 MB; read by HTTP range",
                   what="LOLA returns per 10 m pixel", page="https://pgda.gsfc.nasa.gov/products/90",
                   cite="Barker et al. 2023 (as psr)"),
    "sun75": dict(url=PGDA + "MoonIllumination/AVGVISIB_75S_120M_201608.TIF", size="83 MB, downloaded to DATA/illum/",
                  local=str(DATA / "illum" / "AVGVISIB_75S_120M_201608.TIF"),
                  what="as sun, 120 m/px, 75-90 S; used where the 60 m map has no data",
                  page="https://pgda.gsfc.nasa.gov/products/69", cite="Mazarico et al. 2011"),
    "earth75": dict(url=PGDA + "MoonIllumination/AVGVISIB_75S_120M_201608_EARTH.TIF",
                    size="downloaded to DATA/illum/", local=str(DATA / "illum" / "AVGVISIB_75S_120M_201608_EARTH.TIF"),
                    what="as earth, 120 m/px, 75-90 S", page="https://pgda.gsfc.nasa.gov/products/69",
                    cite="Mazarico et al. 2011"),
    "names": dict(url="https://planetarynames.wr.usgs.gov/", what="IAU Gazetteer, LOLA-frame centres",
                  cite="IAU WGPSN Gazetteer of Planetary Nomenclature"),
}
__doc__ += "\n".join(f"  {k:6} {v['url']}\n         {v['what']}; {v.get('size', '')}\n         cite {v['cite']}"
                     for k, v in SOURCES.items())

# IAU Gazetteer (LOLA frame), read 16 Nov 2026: name -> (diameter km, lat, lon E).
GAZETTEER = {
    "Shackleton": (20.92, -89.67, 129.78), "Haynes": (17.0, -88.75, 167.39),
    "Shoemaker": (51.82, -88.14, 45.91), "Haworth": (51.42, -87.45, -5.17),
    "Faustini": (42.48, -87.18, 84.31), "de Gerlache": (32.71, -88.48, -88.34),
    "Slater": (25.12, -88.08, 111.29), "Sverdrup": (32.83, -88.32, -153.39),
    "Henson": (43.0, -88.57, -130.63), "Spudis": (13.0, -89.51, -84.61),
    "Nobile": (79.27, -85.28, 53.27), "Cabeus": (100.58, -85.33, -42.13),
}

# Chosen from `screen` (40 m), then checked at 5 m (`build`). `screen_dock`
# is the 5 m check's dock and the centre of the fetch; `build` refines it.
SITES = {
    # Unnamed PSRs; key = nearest named crater or dock lat/lon. All three are
    # closed lows: floor median 47-111 m below a ring 50-300 m outside it.
    "haynes": dict(label="Unnamed PSR 3 km outside Haynes' rim (88.9 S 151 E), 15 km2",
                   screen_dock=(14438.0, -30632.0)),
    "s89_014e": dict(label="Unnamed PSR at 89.3 S 20 E, 7.7 km2", screen_dock=(4592.0, 19018.0)),
    "s89_134e": dict(label="Unnamed PSR crater at 89.0 S 130 E, 5.9 km2", screen_dock=(22192.0, -21642.0)),
    # From `screen 85s` (80 m), the best of 306 PSRs in 84.5-87 S. Built on the
    # 10 m 83 S product: the 5 m mosaic stops at 87 S. Cabeus: of its 62 PSRs
    # of 1 km2 or more (within 1.1 radii), the 8 largest (13-127 km2, its
    # floor) have no ground lit 30% of the time within 1.5 km (best 0.03-0.24);
    # 5 small ones (1.2-6 km2) pass with 1.0-3.4 km2 reachable.
    "nobile": dict(label="PSR on Nobile's floor (85.4 S 50 E), 138 km2", screen_dock=(111960.0, 83400.0),
                   region="85s"),
}


# ------------------------------------------------------------------ geometry

def latlon(x, y):
    rho = math.hypot(x, y)
    return -(90 - 2 * math.degrees(math.atan(rho / (2 * R_MOON)))), math.degrees(math.atan2(x, y))


def xy(lat, lon):
    rho = 2 * R_MOON * math.tan(math.radians(90 + lat) / 2)
    return rho * math.sin(math.radians(lon)), rho * math.cos(math.radians(lon))


def nearest_named(x, y):
    best = min(((math.hypot(cx - x, cy - y), n, d) for n, (d, la, lo) in GAZETTEER.items()
                for cx, cy in [xy(la, lo)]))
    dist, n, d = best
    return f"{n} (D {d:.0f} km): {dist / 1e3:.1f} km from its centre, {dist / (d * 500):.2f} radii"


def _resample(g, x0, y_top, ncol, nrow, cell):
    """Nearest-neighbour sample of GeoTIFF g at cell centres of a grid whose
    [0, 0] centre is (x0, y_top), rows running south."""
    xs = x0 + cell * np.arange(ncol)
    ys = y_top - cell * np.arange(nrow)
    a, ax, ay = g.window(xs[0] - 2 * g.px, xs[-1] + 2 * g.px, ys[-1] - 2 * g.px, ys[0] + 2 * g.px)
    c = np.clip(np.round((xs - ax) / g.px).astype(int), 0, a.shape[1] - 1)
    r = np.clip(np.round((ay - ys) / g.px).astype(int), 0, a.shape[0] - 1)
    return a[np.ix_(r, c)]


def geodesic(ok, start, cell):
    """Driving distance (m) from `start` (i, j) over cells where ok is True,
    8-neighbour, inf where unreachable."""
    from skimage.graph import MCP_Geometric
    cost = np.where(ok, 1.0, np.inf)
    cost[start] = 1.0
    d, _ = MCP_Geometric(cost).find_costs([start])
    return d * cell


# ------------------------------------------------------------------- regions

def rho_of(lat):
    return 2 * R_MOON * math.tan(math.radians(90 + lat) / 2)


# Two LOLA products cover the two latitude bands. Each is read at its own
# native spacing for building, and through its overviews for screening.
REGIONS = {
    "87s": dict(dem="ldem", slope="ldsm", count="ldec", cell=5.0, screen_px=40.0, rho=None),
    "85s": dict(dem="ldem83", slope="ldsm83", count="ldec83", cell=10.0, screen_px=80.0,
                rho=(rho_of(-87.0), rho_of(-84.5))),
}


def _lit(kind, x0, y_top, ncol, nrow, cell):
    """Average visible fraction (sun or earth) at cell centres: the 60 m 85-90 S
    map where it has data, else the 120 m 75-90 S map."""
    out = None
    for key in (kind, kind + "75"):
        with open_geotiff(SOURCES[key]["local"]) as g:
            a = _resample(g, x0, y_top, ncol, nrow, cell).astype(np.float32)
        a = np.where(np.isnan(a) | (a < 0), np.nan, a * 4e-5).astype(np.float32)
        out = a if out is None else np.where(np.isnan(out), a, out)
    return out


def _psr_reduced(X0, Y0, n, px):
    """PSR share per px cell from the 20 m map, read in strips (memory)."""
    k = int(round(px / 20.0))
    out = np.zeros((n, n), np.float32)
    with open_geotiff(SOURCES["psr"]["local"]) as g:
        step = max(1, 4000 // k)
        for r in range(0, n, step):
            rr = min(step, n - r)
            a, _, _ = g.window(X0, X0 + n * px, Y0 - (r + rr) * px, Y0 - r * px)
            a = np.nan_to_num(a[:rr * k, :n * k])
            out[r:r + rr] = a.reshape(rr, k, n, k).mean((1, 3))
    return out


# -------------------------------------------------------------------- screen

def screen(region: str = "87s", b: SiteBuild = SiteBuild(), top=40):
    """Every PSR >= psr_min_km2 in the region, scored at the region's screen
    resolution by the PSR floor under the slope limit within reach_m driving
    of a sunlit dock. Writes every PSR, passing or not, to screen_<region>.json."""
    R = REGIONS[region]
    dem = SOURCES[R["dem"]]["url"]
    level = int(round(math.log2(R["screen_px"] / R["cell"])))
    with open_geotiff(dem, level=level) as g:
        px = g.px
        if R["rho"] is None:
            z = g.read(0, g.shape[0], 0, g.shape[1])
            X0, Y0 = g.x_left, g.y_top
        else:
            half = R["rho"][1] + b.reach_m + 2000
            z, xc, yc = g.window(-half, half, -half, half)
            X0, Y0 = xc - px / 2, yc + px / 2
    z = z.astype(np.float32)
    n = z.shape[0]
    psr = _psr_reduced(X0, Y0, n, px) >= 0.5
    sun = np.nan_to_num(_lit("sun", X0 + px / 2, Y0 - px / 2, n, n, px))
    gy, gx = np.gradient(z, np.float32(px))
    ok = np.isfinite(z) & (np.degrees(np.arctan(np.hypot(gx, gy))) < b.slope_limit_deg)
    del gx, gy
    lab, nlab = ndi.label(psr)
    area = ndi.sum(np.ones(lab.shape, np.float32), lab, np.arange(1, nlab + 1)) * px * px / 1e6
    com = ndi.center_of_mass(psr, lab, np.arange(1, nlab + 1))
    objs = ndi.find_objects(lab)
    m = int(b.reach_m / px) + 5
    rng = np.random.default_rng(0)
    rows = []
    for kk in np.where(area >= b.psr_min_km2)[0] + 1:
        ci, cj = com[kk - 1]
        px_x, px_y = X0 + (cj + .5) * px, Y0 - (ci + .5) * px
        if R["rho"] is not None and not (R["rho"][0] <= math.hypot(px_x, px_y) <= R["rho"][1]):
            continue
        sl = objs[kk - 1]
        r0, r1 = max(sl[0].start - m, 0), min(sl[0].stop + m, n)
        c0, c1 = max(sl[1].start - m, 0), min(sl[1].stop + m, n)
        row = dict(psr_km2=round(float(area[kk - 1]), 2), centre=[round(px_x), round(px_y)],
                   centre_latlon=[round(v, 3) for v in latlon(px_x, px_y)], near=nearest_named(px_x, px_y),
                   reach_km2=0.0, dock_candidates=0)
        rows.append(row)
        if np.isnan(z[r0:r1, c0:c1]).mean() > 0.01:
            row["note"] = "DEM gaps"
            continue
        L, O, P, S = lab[r0:r1, c0:c1] == kk, ok[r0:r1, c0:c1], psr[r0:r1, c0:c1], sun[r0:r1, c0:c1]
        near = ndi.distance_transform_edt(~L) * px <= b.dock_max_from_psr_m
        cand = np.argwhere(O & ~P & (S >= b.dock_min_sun) & near)
        row["dock_candidates"] = int(len(cand))
        row["best_sun_within_1500m"] = round(float(S[near & ~P].max()), 3) if (near & ~P).any() else 0.0
        if not len(cand):
            continue
        best = (0.0, None)
        for a, c in cand[rng.choice(len(cand), min(30, len(cand)), replace=False)]:
            d = geodesic(O, (a, c), px)
            A = float(((d <= b.reach_m) & L & O).sum() * px * px / 1e6)
            if A > best[0]:
                best = (A, (a, c))
        if best[1] is None:
            continue
        a, c = best[1]
        x, y = X0 + (c0 + c + .5) * px, Y0 - (r0 + a + .5) * px
        row.update(reach_km2=round(best[0], 2), dock=[round(x), round(y)],
                   dock_latlon=[round(v, 3) for v in latlon(x, y)], dock_sun=round(float(S[a, c]), 3))
    rows.sort(key=lambda r: -r["reach_km2"])
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    out = SITE_DIR / f"screen_{region}.json"
    out.write_text(json.dumps(dict(region=region, screen_px=px, settings=b.__dict__, rows=rows), indent=1))
    passing = [r for r in rows if r["reach_km2"] >= b.psr_min_km2]
    for r in passing[:top]:
        print(r)
    print(f"{region}: {len(rows)} PSRs >= {b.psr_min_km2} km2, {len(passing)} pass; wrote {out}")
    return rows


# --------------------------------------------------------------------- build

def build(site: str, b: SiteBuild = SiteBuild(), centre=None, region=None, save=True):
    """Fetch the native window around a site, refine the dock, save the npz.

    With save=False (and centre, region given) it only measures a candidate."""
    t0 = time.time()
    if centre is None:
        sx, sy = SITES[site]["screen_dock"]
        region = SITES[site].get("region", "87s")
    else:
        sx, sy = centre
    R = REGIONS[region]
    cell, half = R["cell"], b.window_m / 2
    big = half + b.dock_max_from_psr_m
    raw = SITE_DIR / "raw" / f"{site}_{sx:.0f}_{sy:.0f}_{big:.0f}.npz"
    if raw.exists():
        with np.load(raw) as f:
            z, s, c, zx, zy, fetched = f["z"], f["s"], f["c"], float(f["zx"]), float(f["zy"]), 0
    else:
        with open_geotiff(SOURCES[R["dem"]]["url"]) as gz, open_geotiff(SOURCES[R["slope"]]["url"]) as gs, \
                open_geotiff(SOURCES[R["count"]]["url"]) as gc:
            z, zx, zy = gz.window(sx - big, sx + big, sy - big, sy + big)
            s, sx_, sy_ = gs.window(sx - big, sx + big, sy - big, sy + big)
            c, cx_, cy_ = gc.window(sx - big, sx + big, sy - big, sy + big)
            assert (zx, zy) == (sx_, sy_) == (cx_, cy_) and z.shape == s.shape == c.shape
            fetched = gz.fh.fetched + gs.fh.fetched + gc.fh.fetched
        raw.parent.mkdir(parents=True, exist_ok=True)
        np.savez(raw, z=z, s=s, c=c, zx=zx, zy=zy)
    nr, nc = z.shape
    with open_geotiff(SOURCES["psr"]["local"]) as g:
        shadow = np.nan_to_num(_resample(g, zx, zy, nc, nr, cell)) > 0.5
    # Floor = PSR patches of at least psr_min_km2. Smaller patches are the
    # shadowed insides of small craters, not ground a survey can work.
    lab, nlab = ndi.label(shadow)
    sizes = np.bincount(lab.ravel()) * cell * cell / 1e6
    psr = (sizes >= b.psr_min_km2)[lab] & shadow
    lit = {k: _lit(k, zx, zy, nc, nr, cell) for k in ("sun", "earth")}
    ok = np.isfinite(s) & (s < b.slope_limit_deg)

    # Dock: sunlit, not PSR, under the slope limit, within dock_max_from_psr_m
    # of PSR and of the screen dock; maximise reachable PSR floor.
    ii, jj = np.mgrid[0:nr, 0:nc]
    near_centre = np.hypot((jj - (nc - 1) / 2) * cell, (ii - (nr - 1) / 2) * cell) <= b.dock_max_from_psr_m
    near_psr = ndi.distance_transform_edt(~psr) * cell <= b.dock_max_from_psr_m
    cand = np.argwhere(ok & ~shadow & (np.nan_to_num(lit["sun"]) >= b.dock_min_sun) & near_psr & near_centre)
    if not len(cand):
        print(f"{site}: no dock candidate at {cell:g} m")
        return dict(site=site, reach_km2=0.0, dock_candidates=0)
    rng = np.random.default_rng(0)
    pick = cand[rng.choice(len(cand), min(b.dock_samples, len(cand)), replace=False)]
    m = int(b.reach_m / cell) + 4
    best = (-1.0, None)
    for a, cc in pick:
        r0, c0 = max(a - m, 0), max(cc - m, 0)
        sub = ok[r0:a + m, c0:cc + m]
        d = geodesic(sub, (a - r0, cc - c0), cell)
        A = float(((d <= b.reach_m) & psr[r0:a + m, c0:cc + m] & sub).sum() * cell * cell / 1e6)
        if A > best[0]:
            best = (A, (int(a), int(cc)))
    (da, dc) = best[1]
    dock_x, dock_y = zx + dc * cell, zy - da * cell

    # Crop window_m centred on the dock; flip rows to run north.
    h = int(round(half / cell))
    r0, r1, c0, c1 = da - h, da + h, dc - h, dc + h
    assert r0 >= 0 and c0 >= 0 and r1 <= nr and c1 <= nc, "dock too near the fetched edge"
    crop = lambda a: np.ascontiguousarray(a[r0:r1, c0:c1][::-1])
    Z, Sl, C, P, SH, SUN, EA, OK = map(crop, (z, s, c, psr, shadow, lit["sun"], lit["earth"], ok))
    x0 = zx + c0 * cell
    y0 = zy - (r1 - 1) * cell              # after the flip, row 0 is the south edge
    di, dj = int(round((dock_y - y0) / cell)), int(round((dock_x - x0) / cell))
    dist = geodesic(OK, (di, dj), cell).astype(np.float32)
    dist[~np.isfinite(dist)] = np.nan
    floor_ok = P & OK
    la, lo = latlon(dock_x, dock_y)
    reach = floor_ok & (dist <= b.reach_m)
    meta = dict(
        site=site, label=SITES[site]["label"] if site in SITES else site, region=region, cell=cell,
        dock=[dock_x, dock_y], dock_latlon=[round(la, 4), round(lo, 3)],
        near=nearest_named(dock_x, dock_y), dock_sun=float(SUN[di, dj]), dock_earth=float(EA[di, dj]),
        dock_slope_deg=float(Sl[di, dj]),
        reach_km2=round(float(reach.sum() * cell * cell / 1e6), 3),
        psr_km2_in_window=round(float(P.sum() * cell * cell / 1e6), 3),
        shadow_km2_in_window=round(float(SH.sum() * cell * cell / 1e6), 3),
        descent_m=round(float(np.nanmin(np.where(floor_ok, dist, np.nan))), 1) if floor_ok.any() else None,
        ldec_share=round(float((C > 0).mean()), 4), nan_share=round(float(np.isnan(Z).mean()), 5),
        slope_over_limit_share=round(float((Sl >= b.slope_limit_deg).mean()), 4),
        slope_median_deg=round(float(np.nanmedian(Sl)), 2),
        relief_m=round(float(np.nanmax(Z) - np.nanmin(Z)), 1),
        height_dock_minus_floor_median_m=(round(float(Z[di, dj] - np.nanmedian(Z[reach])), 1)
                                          if reach.any() else None),
        sources={k: dict(url=v["url"], cite=v["cite"]) for k, v in SOURCES.items()},
        settings=b.__dict__, fetched_mb=round(fetched / 1e6, 1), built_s=round(time.time() - t0, 1),
    )
    if not save:
        return meta
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    out = SITE_DIR / f"{site}.npz"
    np.savez(out, height=Z.astype(np.float32), slope=Sl.astype(np.float32), ldec=C.astype(np.float32),
             psr=P, shadow=SH, sun=SUN, earth=EA, dist_dock=dist, x0=np.float64(x0), y0=np.float64(y0),
             cell=np.float64(cell), dock=np.array([dock_x, dock_y]), meta=np.array(json.dumps(meta)))
    print(json.dumps({k: v for k, v in meta.items() if k not in ("sources", "settings")}, indent=1))
    print(f"wrote {out}")
    return out


def load_site(site: str) -> dict:
    with np.load(SITE_DIR / f"{site}.npz") as f:
        d = {k: f[k] for k in f.files}
    d["meta"] = json.loads(str(d["meta"]))
    for k in ("x0", "y0", "cell"):
        d[k] = float(d[k])
    d["dock"] = tuple(float(v) for v in d["dock"])
    return d


# ---------------------------------------------------------------------- look

def look(site: str, seed: int = 1):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LightSource, ListedColormap
    from scipy.spatial import cKDTree
    from sim.settings import WorldRanges
    from sim.terrain import campaign_world

    d = load_site(site)
    meta = d["meta"]
    b = SiteBuild()
    Z, S, P, SH, dist = d["height"], d["slope"], d["psr"], d["shadow"], d["dist_dock"]
    x0, y0, c = d["x0"], d["y0"], d["cell"]
    n = Z.shape[0]
    ext = [x0 - c / 2, x0 + (n - .5) * c, y0 - c / 2, y0 + (n - .5) * c]
    xs, ys = x0 + c * np.arange(n), y0 + c * np.arange(n)
    reach = ndi.binary_fill_holes(np.nan_to_num(dist, nan=1e9) <= b.reach_m)
    # A low-density world, so single craters are visible on an 8 km map.
    w = campaign_world(site, seed, WorldRanges(crater_n_ge10_per_km2=(100.0, 100.0)))
    tc, m = w.craters, w.map_start
    in_map = cKDTree(m[:, :2]).query(tc[:, :2])[0] < 1e-6 if len(m) else np.zeros(len(tc), bool)
    fl = P & (S < b.slope_limit_deg)
    k = int(np.nanargmin(np.where(fl, dist, np.nan)))
    ei, ej = divmod(k, n)
    ex, ey = x0 + ej * c, y0 + ei * c

    fig, axs = plt.subplots(1, 2, figsize=(20, 10.6), dpi=90)
    hs = LightSource(azdeg=315, altdeg=30).hillshade(np.nan_to_num(Z), dx=c, dy=c)
    for ax, zoom in ((axs[0], False), (axs[1], True)):
        ax.imshow(hs, cmap="gray", origin="lower", extent=ext, vmin=0, vmax=1)
        ax.imshow(np.ma.masked_where(~(SH & ~P), SH), cmap=ListedColormap(["#64d2ff"]), alpha=0.35,
                  origin="lower", extent=ext)
        ax.imshow(np.ma.masked_where(~P, P), cmap=ListedColormap(["#2b6cff"]), alpha=0.32, origin="lower", extent=ext)
        ax.imshow(np.ma.masked_where(S < b.slope_limit_deg, S), cmap=ListedColormap(["#ff3b30"]), alpha=0.33,
                  origin="lower", extent=ext)
        ax.contour(xs, ys, P.astype(float), levels=[0.5], colors="#00e5ff", linewidths=1.3)
        ax.contour(xs, ys, reach.astype(float), levels=[0.5], colors="#ff9f0a", linewidths=1.8)
        ax.plot(*d["dock"], marker="*", ms=24, color="#ffd60a", mec="k", mew=1.2, zorder=5)
        ax.plot(ex, ey, "o", ms=12, mfc="none", mec="#ffd60a", mew=2.2, zorder=5)
        s_ = 7 if zoom else 1.2
        ax.scatter(tc[in_map, 0], tc[in_map, 1], s=s_, c="#30d158", lw=0, label="crater on start map")
        ax.scatter(tc[~in_map, 0], tc[~in_map, 1], s=s_, c="#ff375f", lw=0, label="crater missing from start map")
        for dep in w.ice:
            ax.plot(dep["x"], dep["y"], marker="D", ms=9 if not zoom else 12, mfc="none", mec="#bf5af2", mew=2)
        ax.set_xlabel("x (m, south polar stereographic)")
        ax.set_ylabel("y (m)")
        ax.set_aspect("equal")
    axs[1].set_xlim(ex - 800, ex + 800)
    axs[1].set_ylim(ey - 800, ey + 800)
    axs[0].plot([ex - 800, ex + 800, ex + 800, ex - 800, ex - 800],
                [ey - 800, ey - 800, ey + 800, ey + 800, ey - 800], color="w", lw=1)
    axs[0].set_title(f"{meta['label']} ({site}). Dock (star) {meta['dock_latlon'][0]:.3f}, "
                     f"{meta['dock_latlon'][1]:.2f} E, sun {meta['dock_sun']:.2f}, Earth {meta['dock_earth']:.2f}\n"
                     f"{meta['near']}. Floor reachable in {b.reach_m / 1e3:.0f} km: {meta['reach_km2']:.1f} km2",
                     fontsize=11)
    axs[1].set_title(f"1.6 km around the first floor cell (circle), {meta['descent_m']:.0f} m drive from the dock\n"
                     f"craters 7.5-20 m drawn at {w.draws['crater_n_ge10_per_km2']:.0f}/km2 (D>=10 m), "
                     f"seed {seed}; diamonds = hidden ice", fontsize=11)
    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D
    axs[1].legend(handles=[
        Patch(color="#2b6cff", alpha=.5, label=f"PSR floor, patches >= {b.psr_min_km2:g} km2 (Barker 2023 LPSR)"),
        Patch(color="#64d2ff", alpha=.5, label="smaller permanent shadow (not floor)"),
        Patch(color="#ff3b30", alpha=.5, label=f"slope >= {b.slope_limit_deg:g} deg (LOLA slope, native {d['cell']:g} m)"),
        Line2D([], [], color="#ff9f0a", lw=2, label=f"{b.reach_m / 1e3:.0f} km drive from the dock, slopes < 15 deg"),
        Line2D([], [], ls="", marker="o", ms=5, color="#30d158", label="crater on start map"),
        Line2D([], [], ls="", marker="o", ms=5, color="#ff375f", label="crater missing from start map"),
        Line2D([], [], ls="", marker="D", ms=8, mfc="none", mec="#bf5af2", mew=2, label="hidden ice deposit"),
    ], loc="lower left", fontsize=9, framealpha=.85)
    out = SITE_DIR / f"{site}_map.png"
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("screen")
    sc.add_argument("region", nargs="?", default="87s", choices=list(REGIONS))
    for name in ("build", "look"):
        p = sub.add_parser(name)
        p.add_argument("site", nargs="*", default=list(SITES))
    a = ap.parse_args()
    if a.cmd == "screen":
        screen(a.region)
    else:
        for s in a.site:
            (build if a.cmd == "build" else look)(s)


if __name__ == "__main__":
    main()
