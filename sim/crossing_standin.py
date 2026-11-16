"""Stand-in for part 2's sim/crossing.py, until it lands on campaign-crossing.

Same names and signatures as part 2 announced (mail 20261116-140656). The
engine imports `sim.crossing` first and falls back to this file, and says so
in each record's `standins`. Delete this file when part 2 merges.

Constants part 2 adds to sim/settings.py are read with getattr and a
fallback, so this file runs before those fields exist. The fallbacks are
STANDIN values, not sourced, and the record lists them.
"""

from __future__ import annotations

import math

import numpy as np

NAME = "crossing stand-in (sim/crossing_standin.py), replaced by part 2's sim/crossing.py"

# Fallbacks for part 2's settings fields. Used only while those fields are
# missing from Settings; listed in the record under standins.
_FALLBACK = {
    "rim_detect_d_min_m": 7.5,      # part 2 cites A24 p.10
    "rim_detect_d_max_m": 20.0,     # part 2 cites A24 p.10
    "rim_pos_noise_m": 0.5,         # standin
    "rim_size_err_frac": 0.1,       # standin
    "map_match_tol_m": 1.0,         # standin
    "merge_size_tol_frac": 0.3,     # standin
    "search_line_spacing_m": 2.0,   # standin
    "confirm_bound_max_m": 1e9,     # standin: no cap
    "dock_reg_bound95_m": 5.0,      # standin
}


def setting(s, name):
    return getattr(s, name, _FALLBACK[name])


def fallbacks_used(s) -> dict:
    return {k: v for k, v in _FALLBACK.items() if not hasattr(s, k)}


# ------------------------------------------------------------ Scout and dock

def search_area(find, searcher_bound95, s) -> float:
    return s.search_radius_k * math.hypot(find["bound95"], searcher_bound95) + s.footprint_radius_m


def pick_searcher(find, scouts_ready):
    if find["status"] != "find":
        return None
    for sid in scouts_ready:
        if sid != find["scout"]:
            return sid
    return None


def search_points(find, radius_m, s) -> np.ndarray:
    """Lawnmower lines across the disc, in the searcher's estimated frame."""
    sp = setting(s, "search_line_spacing_m")
    cx, cy = find["est"]
    pts = []
    ys = np.arange(-radius_m + sp / 2, radius_m, sp)
    for k, y in enumerate(ys):
        half = math.sqrt(max(radius_m ** 2 - y ** 2, 0.0))
        xs = np.arange(-half, half + 1e-9, max(s.reading_spacing_m, sp))
        if len(xs) == 0:
            xs = np.array([0.0])
        if k % 2:
            xs = xs[::-1]
        pts.extend((cx + x, cy + y) for x in xs)
    return np.array(pts, dtype=float).reshape(-1, 2)


def judge_search_detail(find, readings, s) -> dict:
    """readings: (est_x, est_y, bound95, reading_wt, anchor)."""
    best = None
    for i, (x, y, b, wt, _a) in enumerate(readings):
        if wt >= s.find_threshold_wt and (best is None or wt > readings[best][3]):
            best = i
    if best is None:
        return {"result": "unconfirmed", "reason": "no reading above threshold", "match": None}
    x, y, b, wt, _a = readings[best]
    return {"result": "confirmed", "reason": "reading above threshold in area", "match": best,
            "dist_m": float(math.hypot(x - find["est"][0], y - find["est"][1])),
            "est": [float(x), float(y)], "bound95": float(b)}


def judge_search(find, readings, s) -> str:
    return judge_search_detail(find, readings, s)["result"]


def merge_sightings(sightings, crater_map, s, next_index):
    """Greedy clusters of sightings with different anchors, within merge_tol_m."""
    from scipy.spatial import cKDTree
    if not sightings:
        return []
    tol = s.merge_tol_m
    xy = np.array([sg["est"] for sg in sightings], dtype=float)
    cm = np.asarray(crater_map, dtype=float).reshape(-1, 3)
    mtree = cKDTree(cm[:, :2]) if len(cm) else None
    tree = cKDTree(xy)
    used = np.zeros(len(sightings), bool)
    added = []
    for i in range(len(sightings)):
        if used[i]:
            continue
        cand = sorted(j for j in tree.query_ball_point(xy[i], tol) if not used[j])
        anchors, members = set(), []
        for j in cand:
            a = sightings[j]["anchor"]
            if a not in anchors:
                anchors.add(a)
                members.append(int(j))
        if len(anchors) < s.sightings_to_add:
            continue
        cx, cy = xy[members].mean(axis=0)
        if mtree is not None and mtree.query((cx, cy))[0] <= tol:
            continue
        used[members] = True
        added.append({
            "crater_id": f"C{next_index + len(added)}",
            "x": float(cx), "y": float(cy),
            "d": float(np.mean([sightings[j]["d_est"] for j in members])),
            "bound95": float(np.mean([sightings[j]["bound95"] for j in members])),
            "sightings": len(members), "members": members,
            "scouts": sorted({sightings[j]["scout"] for j in members}),
            "sorties": sorted({sightings[j]["sortie_id"] for j in members}),
        })
    return added


# ------------------------------------------------------------- world, truth

def crater_view(true_xy, true_craters, map_craters, s) -> dict:
    """Craters around the Scout's true position. Truth side.

    true_craters / map_craters: (N, 3) arrays already cut to the neighbourhood
    by the caller. in_view: map rows within fix_range_m (what a fix matches
    against). missing: true rows within range with no map crater within
    map_match_tol_m. misplaced: map rows in range with no true crater within
    the tolerance. share_bad = (missing + misplaced) / (true in range + misplaced).
    """
    r = s.fix_range_m
    tol = setting(s, "map_match_tol_m")
    dmin, dmax = setting(s, "rim_detect_d_min_m"), setting(s, "rim_detect_d_max_m")
    tx, ty = true_xy
    tc = np.asarray(true_craters, float).reshape(-1, 3)
    mc = np.asarray(map_craters, float).reshape(-1, 3)
    t_in = np.flatnonzero((np.hypot(tc[:, 0] - tx, tc[:, 1] - ty) <= r)
                          & (tc[:, 2] >= dmin) & (tc[:, 2] <= dmax))
    m_in = np.flatnonzero(np.hypot(mc[:, 0] - tx, mc[:, 1] - ty) <= r)
    missing, matched_map = [], set()
    for i in t_in:
        if len(m_in):
            dd = np.hypot(mc[m_in, 0] - tc[i, 0], mc[m_in, 1] - tc[i, 1])
            k = int(np.argmin(dd))
            if dd[k] <= tol:
                matched_map.add(int(m_in[k]))
                continue
        missing.append(int(i))
    misplaced = [int(j) for j in m_in if int(j) not in matched_map]
    denom = len(t_in) + len(misplaced)
    share = (len(missing) + len(misplaced)) / denom if denom else 0.0
    return {"in_view": [int(j) for j in m_in], "seen": [int(i) for i in t_in],
            "missing": missing, "misplaced": misplaced, "share_bad": float(share)}


def wrong_match_cause(view, s, rng) -> str:
    w_base = s.p_wrong_base
    w_miss = s.p_wrong_per_missing * view["share_bad"]
    if rng.random() * (w_base + w_miss) < w_base:
        return "base"
    if view["misplaced"] and rng.random() < len(view["misplaced"]) / max(
            len(view["misplaced"]) + len(view["missing"]), 1):
        return f"misplaced:{view['misplaced'][0]}"
    return "missing"


def make_sighting(true_crater, scout_true_xy, scout_err_xy, scout_bound95, anchor, rng, s) -> dict:
    x, y, d = true_crater
    noise = setting(s, "rim_pos_noise_m")
    ex, ey = scout_err_xy
    return {"est": [float(x + ex + rng.normal(0, noise)), float(y + ey + rng.normal(0, noise))],
            "d_est": float(d * (1 + setting(s, "rim_size_err_frac") * rng.standard_normal())),
            "bound95": float(scout_bound95), "anchor": anchor}


def deposit_at(true_xy, truth_ice, s) -> bool:
    x, y = true_xy
    for dep in truth_ice:
        if math.hypot(x - dep["x"], y - dep["y"]) <= dep["radius"]:
            return True
    return False


def score_added(added, true_craters, s):
    """true_crater index (or None) and true_offset_m for each added crater."""
    from scipy.spatial import cKDTree
    tc = np.asarray(true_craters, float).reshape(-1, 3)
    if not len(tc):
        return [(None, None) for _ in added]
    d, k = cKDTree(tc[:, :2]).query([(a["x"], a["y"]) for a in added])
    return [(int(kk), float(dd)) if dd <= s.merge_tol_m * 2 else (None, float(dd)) for dd, kk in zip(d, k)]
