"""Cross-checks between Scouts and crater-map refinement (spec 3.5, draft 4.3-4.4).

Pure functions: no I/O, no global state, the rng is passed in. Part 1
(`sim/campaign.py`) calls them from its loop; formats are in
`docs/handoffs.md`, "Campaign simulator".

The module has two halves, and the split is the point:

- **Scout and dock side.** What a Scout or the dock decides from what it
  believes: estimated positions, believed 95th-percentile bounds, readings,
  sightings. None of these functions takes the hidden truth as an argument.
- **World side.** The sensor models that turn truth into what a Scout sees,
  and the scoring against truth (false confirmations, missed deposits,
  misplaced craters). Only part 1's world model and its summary call these.

Shared error (draft 4.2, 4.4). Both Scouts start every sortie from one dock
whose registration error they share, and every position before a sortie's
first check carries it. Positions that come from the same fix share that
fix's error too. Each position therefore carries an `anchor`: "dock" until
the Scout's first check of the sortie, then "<sortie_id>#<check n>". Two
positions with the same anchor are never treated as independent:

- the crater merge counts sightings with one anchor as one sighting, so two
  sightings that agree only because they share the dock error cannot add a
  crater on their own;
- a confirmed deposit's position bound is not shrunk below the smaller of
  the two bounds when the two positions share an anchor.

A confirmation checks relative position. The shared part of the two errors
cancels in it, so two Scouts can agree on a deposit that both place in the
wrong spot. The search radius below uses both full bounds, which covers the
relative error whatever the correlation; the absolute bound is handled as
above.
"""

from __future__ import annotations

import math

import numpy as np

from sim.settings import Settings

# Radius holding 95 % of a circular 2-D normal, per unit sigma per axis:
# sqrt(-2 ln 0.05).
R95_2D = math.sqrt(-2.0 * math.log(0.05))

FIND_OPEN = "find"


def _xy(p) -> np.ndarray:
    return np.asarray(p, dtype=np.float64)[:2]


# ======================================================================
# Scout and dock side. Never reads truth.
# ======================================================================

def search_area(find: dict, searcher_bound95: float, s: Settings) -> float:
    """Search radius around a find, in metres (draft 4.4, item 14).

    k * hypot(finder bound, searcher bound) + footprint. The hypot is the
    independent-error combination of the two full bounds. The part of the
    error the two Scouts share cancels in their relative position, so this
    radius covers the relative error at least as well as if they were
    independent.
    """
    return s.search_radius_k * math.hypot(find["bound95"], searcher_bound95) + s.footprint_radius_m


def pick_searcher(find: dict, scouts_ready) -> str | None:
    """The first ready Scout that is not the finder; None if there is none.

    Only an open find (status "find") is searched, once. With one Scout this
    is always None: the 2029 pathfinder cannot cross-check (spec 3.1).
    """
    if find.get("status") != FIND_OPEN:
        return None
    for sid in scouts_ready:
        if sid != find["scout"]:
            return sid
    return None


def search_points(find: dict, radius_m: float, s: Settings) -> np.ndarray:
    """Reading points for a search, (K, 2), in the searcher's estimated frame.

    A square grid at `search_line_spacing_m`, centred on the find's estimate
    and clipped to the search circle, in boustrophedon order so consecutive
    points are neighbours. The centre is always included. The searcher
    drives to each point by its own estimate and reads there; its true
    position is the estimate minus its error, which only part 1 knows.
    """
    sp = s.search_line_spacing_m
    c = _xy(find["est"])
    n = int(math.floor(radius_m / sp))
    pts = []
    for row, j in enumerate(range(-n, n + 1)):
        cols = range(-n, n + 1) if row % 2 == 0 else range(n, -n - 1, -1)
        for i in cols:
            if math.hypot(i * sp, j * sp) <= radius_m + 1e-9:
                pts.append((c[0] + i * sp, c[1] + j * sp))
    if not pts:
        pts = [(c[0], c[1])]
    return np.array(pts, dtype=np.float64)


def judge_search_detail(find: dict, readings, s: Settings) -> dict:
    """Confirm a find or not from the searcher's readings, with the reason.

    `readings`: the searcher's (est_x, est_y, bound95, reading_wt[, anchor]).
    A reading matches when it is at or above the find threshold and its
    estimate lies within k * hypot(find bound, its bound) + footprint of the
    find's estimate. A match confirms only if the check is meaningful: the
    combined bound is at most `confirm_bound_max_m` (draft 4.4: "well below
    the deposit radius and the spectrometer footprint").

    Returns result ("confirmed" / "unconfirmed"), reason ("match",
    "no_readings", "no_match", "bound_too_large"), match (index into
    readings or None), dist_m, n_readings, and for a confirmation the
    deposit's est and bound95.
    """
    out = {"result": "unconfirmed", "reason": "no_readings", "match": None,
           "dist_m": None, "n_readings": len(readings), "est": None, "bound95": None}
    if not len(readings):
        return out
    f = _xy(find["est"])
    fb = float(find["bound95"])
    f_anchor = find.get("anchor")

    best = None          # (dist, i) among meaningful matches
    any_match = False
    for i, r in enumerate(readings):
        rx, ry, rb, wt = float(r[0]), float(r[1]), float(r[2]), float(r[3])
        if wt < s.find_threshold_wt:
            continue
        comb = math.hypot(fb, rb)
        dist = math.hypot(rx - f[0], ry - f[1])
        if dist > s.search_radius_k * comb + s.footprint_radius_m:
            continue
        any_match = True
        if comb > s.confirm_bound_max_m:
            continue
        if best is None or dist < best[0]:
            best = (dist, i)

    if best is None:
        out["reason"] = "bound_too_large" if any_match else "no_match"
        return out

    dist, i = best
    r = readings[i]
    rb = float(r[2])
    r_anchor = r[4] if len(r) > 4 else None
    # Fuse the two positions. Independent only when both anchors are known
    # and differ; otherwise they may share an error and the bound must not
    # shrink below the better of the two.
    w1, w2 = 1.0 / fb ** 2, 1.0 / rb ** 2
    est = (w1 * f + w2 * np.array([float(r[0]), float(r[1])])) / (w1 + w2)
    independent = f_anchor is not None and r_anchor is not None and f_anchor != r_anchor
    bound = 1.0 / math.sqrt(w1 + w2) if independent else min(fb, rb)
    out.update(result="confirmed", reason="match", match=i, dist_m=round(dist, 3),
               est=[round(float(est[0]), 3), round(float(est[1]), 3)],
               bound95=round(bound, 3), independent=independent)
    return out


def judge_search(find: dict, readings, s: Settings) -> str:
    """`judge_search_detail(...)["result"]`: "confirmed" or "unconfirmed"."""
    return judge_search_detail(find, readings, s)["result"]


def _independent(a: dict, b: dict) -> bool:
    """Two sightings count as independent: different sorties and anchors.

    Different sorties have different fixes, so the only anchor two sorties
    can share is "dock". A sighting with no anchor is treated as "dock".
    """
    return a["sortie_id"] != b["sortie_id"] and (a.get("anchor") or "dock") != (b.get("anchor") or "dock")


def _same_size(d1: float, d2: float, s: Settings) -> bool:
    return abs(d1 - d2) <= s.merge_size_tol_frac * max(d1, d2)


def merge_sightings(sightings, crater_map, s: Settings, next_index: int | None = None) -> list[dict]:
    """New map craters from the pool of unmatched rim sightings (draft 4.3).

    Run at the dock after each sortie on every sighting not yet used.
    `sightings`: dicts with est [x, y], d_est, bound95, sortie_id, scout,
    anchor (the `sighting` event fields). `crater_map`: (M, 3) x, y, d of
    the dock's current map, start plus added.

    1. A sighting within `merge_tol_m` of a map crater of similar size is
       already mapped and is skipped.
    2. Groups are grown greedily, best-bounded sighting first. A sighting
       joins a group when it agrees with every member (position within
       `merge_tol_m`, size within `merge_size_tol_frac`) and is independent
       of every member (different sortie and anchor). So at most one
       dock-anchored sighting is in a group.
    3. A group of `sightings_to_add` or more becomes a crater. Its position
       is the inverse-variance mean of the members, its size the mean
       diameter, its bound95 the fused bound of independent members.

    The crater keeps the observers' errors: nothing here corrects them, so a
    misplaced crater stays misplaced and can cause wrong matches later
    (`crater_view`). Returns added craters {crater_id, x, y, d, bound95,
    sightings, members, scouts, sorties}; members index into `sightings`,
    and part 1 drops them from its pool.
    """
    n = len(sightings)
    if n == 0:
        return []
    est = np.array([_xy(q["est"]) for q in sightings])
    dd = np.array([float(q["d_est"]) for q in sightings])
    bb = np.array([float(q["bound95"]) for q in sightings])
    tol = s.merge_tol_m

    from scipy.spatial import cKDTree

    usable = np.ones(n, dtype=bool)
    cm = np.asarray(crater_map, dtype=np.float64).reshape(-1, 3)
    if len(cm):
        mtree = cKDTree(cm[:, :2])
        for i, near in enumerate(mtree.query_ball_point(est, tol)):
            if any(_same_size(dd[i], cm[j, 2], s) for j in near):
                usable[i] = False

    idx = np.flatnonzero(usable)
    if len(idx) < max(1, s.sightings_to_add):
        return []
    tree = cKDTree(est[idx])
    used = np.zeros(n, dtype=bool)
    added = []
    k = len(cm) if next_index is None else next_index
    for i in idx[np.argsort(bb[idx], kind="stable")]:
        if used[i]:
            continue
        group = [i]
        cand = [idx[j] for j in tree.query_ball_point(est[i], tol) if idx[j] != i and not used[idx[j]]]
        cand.sort(key=lambda j: (math.hypot(*(est[j] - est[i])), j))
        for j in cand:
            if all(_same_size(dd[j], dd[m], s)
                   and math.hypot(*(est[j] - est[m])) <= tol
                   and _independent(sightings[j], sightings[m]) for m in group):
                group.append(j)
        if len(group) < s.sightings_to_add:
            continue
        used[group] = True
        w = 1.0 / bb[group] ** 2
        xy = (w[:, None] * est[group]).sum(0) / w.sum()
        added.append({
            "crater_id": f"C{k}",
            "x": round(float(xy[0]), 3), "y": round(float(xy[1]), 3),
            "d": round(float(dd[group].mean()), 3),
            "bound95": round(float(1.0 / math.sqrt(w.sum())), 3),
            "sightings": len(group),
            "members": [int(m) for m in group],
            "scouts": sorted({sightings[m]["scout"] for m in group}),
            "sorties": sorted({sightings[m]["sortie_id"] for m in group}),
        })
        k += 1
    return added


# ======================================================================
# World side. Takes truth. Part 1's world model and scoring only.
# ======================================================================

def _detectable(xyd: np.ndarray, p: np.ndarray, s: Settings) -> np.ndarray:
    """Mask of craters whose rim a Scout at p can detect (A24 p.10 sizes)."""
    if not len(xyd):
        return np.zeros(0, dtype=bool)
    d = xyd[:, 2]
    r = np.hypot(xyd[:, 0] - p[0], xyd[:, 1] - p[1])
    return (d >= s.rim_detect_d_min_m) & (d <= s.rim_detect_d_max_m) & (r <= s.fix_range_m + d / 2)


def _has_partner(a: np.ndarray, b: np.ndarray, s: Settings) -> np.ndarray:
    """For each row of a, is a row of b within map_match_tol_m and of similar size."""
    if not len(a):
        return np.zeros(0, dtype=bool)
    if not len(b):
        return np.zeros(len(a), dtype=bool)
    dist = np.hypot(a[:, None, 0] - b[None, :, 0], a[:, None, 1] - b[None, :, 1])
    size = np.abs(a[:, None, 2] - b[None, :, 2]) <= s.merge_size_tol_frac * np.maximum(a[:, None, 2], b[None, :, 2])
    return ((dist <= s.map_match_tol_m) & size).any(1)


def crater_view(true_xy, true_craters, map_craters, s: Settings) -> dict:
    """What a position check at the Scout's TRUE position sees, against its map.

    - in_view: rows of true_craters whose rim is detectable from here.
    - missing: those with no map crater within `map_match_tol_m` of similar
      size. These are the unmatched rims: part 1 makes a sighting of each.
    - misplaced: rows of map_craters that would be in view at their mapped
      position but have no true crater within `map_match_tol_m`. A misplaced
      added crater shows up twice: its true crater is missing, and its map
      entry is bad.
    - share_bad = (missing + misplaced) / (in_view + misplaced): the share of
      nearby craters absent from, or wrong in, the map. Part 1 uses it as
      the share in p_wrong = clip(p_wrong_base + p_wrong_per_missing * share).
    """
    p = _xy(true_xy)
    tc = np.asarray(true_craters, dtype=np.float64).reshape(-1, 3)
    mc = np.asarray(map_craters, dtype=np.float64).reshape(-1, 3)
    reach = s.fix_range_m + s.rim_detect_d_max_m / 2 + s.map_match_tol_m

    def box(a):
        return np.flatnonzero((np.abs(a[:, 0] - p[0]) <= reach) & (np.abs(a[:, 1] - p[1]) <= reach))

    ti, mi = box(tc), box(mc)
    tv = ti[_detectable(tc[ti], p, s)]
    mv = mi[_detectable(mc[mi], p, s)]
    missing = tv[~_has_partner(tc[tv], mc[mi], s)]
    misplaced = mv[~_has_partner(mc[mv], tc[ti], s)]
    den = len(tv) + len(misplaced)
    share = (len(missing) + len(misplaced)) / den if den else 0.0
    return {"in_view": tv.tolist(), "missing": missing.tolist(), "misplaced": misplaced.tolist(),
            "share_bad": share}


def wrong_match_cause(view: dict, s: Settings, rng: np.random.Generator) -> str:
    """Attribute a drawn wrong match to the term of p_wrong that produced it.

    p_wrong = base + per_missing * (n_missing + n_misplaced) / den, so the
    cause is drawn in proportion to the three terms. Returns "base",
    "missing", or "misplaced:<row of map_craters>".
    """
    nm, nx = len(view["missing"]), len(view["misplaced"])
    den = len(view["in_view"]) + nx
    parts = np.array([s.p_wrong_base,
                      s.p_wrong_per_missing * nm / den if den else 0.0,
                      s.p_wrong_per_missing * nx / den if den else 0.0])
    k = int(rng.choice(3, p=parts / parts.sum())) if parts.sum() > 0 else 0
    if k == 2:
        return f"misplaced:{int(rng.choice(view['misplaced']))}"
    return ("base", "missing")[k]


def make_sighting(true_crater, scout_true_xy, scout_err_xy, scout_bound95: float,
                  anchor: str, rng: np.random.Generator, s: Settings) -> dict:
    """The sighting a Scout logs of an unmatched rim, from the truth.

    ShadowNav sees the leading edge, a front arc (A24 p.10, C23 p.8). The
    size is estimated from that arc with a relative error `rim_size_err_frac`
    (assumed, draft 4.3). The centre is placed half the estimated diameter
    beyond the near rim, so a size error also moves the centre along the
    line of sight by half that error. Then rim noise `rim_pos_noise_m` per
    axis, then the Scout's own position error (estimate minus truth), which
    the sighting inherits whole. bound95 is what the Scout believes: its own
    bound combined with the measurement's.
    """
    c = _xy(true_crater)
    d = float(true_crater[2])
    p = _xy(scout_true_xy)
    u = c - p
    nu = float(np.hypot(*u))
    u = u / nu if nu > 1e-9 else np.array([1.0, 0.0])
    d_est = max(0.5, d * (1.0 + rng.normal(0.0, s.rim_size_err_frac)))
    seen = c + 0.5 * (d_est - d) * u + rng.normal(0.0, s.rim_pos_noise_m, 2)
    est = seen + _xy(scout_err_xy)
    meas95 = R95_2D * math.hypot(s.rim_pos_noise_m, 0.5 * s.rim_size_err_frac * d_est)
    return {"est": [round(float(est[0]), 3), round(float(est[1]), 3)],
            "d_est": round(d_est, 3),
            "bound95": round(math.hypot(scout_bound95, meas95), 3),
            "anchor": anchor}


def deposit_at(true_xy, truth_ice, s: Settings) -> int | None:
    """Index of the deposit really at this true position, or None.

    "Really there" means the noise-free concentration is at or above the
    find threshold somewhere under the sensor footprint: each deposit is
    evaluated at the footprint point nearest its centre. Same field as
    sim/ice.py: each deposit is Gaussian in r, equal to the threshold at its
    radius. Returns the largest contributor.
    """
    p = _xy(true_xy)
    best, total, conc = None, 0.0, 0.0
    th = s.find_threshold_wt
    for k, dep in enumerate(truth_ice):
        r = max(0.0, math.hypot(p[0] - dep["x"], p[1] - dep["y"]) - s.footprint_radius_m)
        r2 = r ** 2 / dep["radius"] ** 2
        c = dep["peak"] * (th / dep["peak"]) ** r2
        total += c
        if c > conc:
            best, conc = k, c
    return best if total >= th else None


def score_search(find_true_xy, result: str, truth_ice, s: Settings, match_true_xy=None) -> dict:
    """Score one search against the truth.

    - truth_has_deposit: a deposit is really at the finder's true position.
    - false_confirmed: confirmed, but no deposit is there, or the matching
      reading sits on a different deposit (when its true position is given).
    - missed: unconfirmed although the deposit is there.
    """
    dep = deposit_at(find_true_xy, truth_ice, s)
    has = dep is not None
    false_c = False
    if result == "confirmed":
        false_c = not has
        if has and match_true_xy is not None:
            false_c = deposit_at(match_true_xy, truth_ice, s) != dep
    return {"truth_has_deposit": has, "deposit": dep, "false_confirmed": false_c,
            "missed": result == "unconfirmed" and has}


def score_added(added: dict, true_craters, s: Settings) -> dict:
    """Nearest true crater to an added crater, its offset, and whether it is misplaced."""
    tc = np.asarray(true_craters, dtype=np.float64).reshape(-1, 3)
    r = np.hypot(tc[:, 0] - added["x"], tc[:, 1] - added["y"])
    j = int(np.argmin(r))
    return {"true_crater": j, "true_offset_m": round(float(r[j]), 3),
            "misplaced": bool(r[j] > s.map_match_tol_m)}
