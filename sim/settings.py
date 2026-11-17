"""Every constant of the campaign simulator, each with its source.

A run is (world seed, Settings). `Settings` holds the design choices that are
swept on purpose plus the fixed constants; `WorldRanges` holds the ranges the
random world is drawn from, one draw per seed (docs/campaign-spec.md sec. 4).

Source tags, one per field, in `metadata["source"]`:

- `brief p.N`: PROSPECT Team Brief, RASC-AL 2027 Theme 3
  (docs/sources/PROSPECT-Team-Brief-RASC-AL-2027-Theme-3-1.pdf), page N.
- `draft N.N` / `draft item N`: the team's area 3 draft, section or numbered
  future-work item. Unpublished and kept outside this repo; never copy it in.
- `C23 ...`: Cauligi et al. 2023, ShadowNav, arXiv:2301.04630
  (docs/sources/ShadowNav-Cauligi-2023.pdf).
- `A24 ...`: Atha et al. 2024, arXiv:2405.01673 (docs/sources/ShadowNav-Atha-2024.pdf).
- `code file:line`: a value already used by Lantern, itself sourced there.
- `assumed`: no source. A named setting with a default, to be swept or replaced.

`python sim/settings.py` prints every field with its value and source.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, fields, asdict, replace

ASSUMED = "assumed"


def S(default, source: str):
    """A setting with its source. Tuples only for sequences (frozen)."""
    return field(default=default, metadata={"source": source})


# ShadowNav final position error over a whole simulated traverse, mean and
# s.d. in metres, 25 Monte Carlo seeds each. C23 Table 2. The PDF text layer
# scrambles the row labels; C23 sec. 6B text identifies trajectory 1 as the
# poor case ("spurious measurements from unlabeled crater"), and draft
# Table 1 reads the rows the same way.
C23_TRAJ1_FALSE_MATCH = (3.84, 2.78)
C23_TRAJ2 = (1.75, 0.78)
C23_TRAJ3 = (1.68, 0.70)


@dataclass(frozen=True)
class Settings:
    # ----------------------------------------------------------------- site
    site: str = S("s89_134e", "sim/psr_sites.py SITES: 'haynes', 's89_014e', 's89_134e' (real LOLA 5 m PSR "
                              "windows), 'nobile' (LOLA 10 m, 84.5-87 S; docs/handoffs.md Part 3), or 'shoemaker' (engine stand-in, not PSR "
                              "floor). Swept for the site table (spec 5.3). Default: the site with 100% of "
                              "survey poses on PSR floor (terrain fyi 20261116-145039)")

    # ---------------------------------------------------------------- fleet
    n_scouts: int = S(3, "brief p.5 baseline 'two to three Scouts sharing one charging dock'. "
                         "Sweep 1-5: 1 = brief p.6 2029 pathfinder; 4-5 is an extrapolation "
                         "beyond the brief's 2030 fleet of 3 (p.6-7), labelled as such")
    sorties_per_scout: int = S(16, "brief p.6 'roughly 16 sorties a month'")
    dock_charge_h: float = S(3.0, "brief p.6 '~3 h at the 1.5 kW dock'")
    dock_power_kw: float = S(1.5, "brief p.6 '1.5 kW dock'")
    contact_gap_max_days: float = S(11.0, "brief p.6 'lose contact with Earth for up to 11 days'")
    contact_gaps_per_month: float = S(1.0, ASSUMED)

    # --------------------------------------------------------------- sortie
    sortie_h: float = S(22.0, "brief p.6 '22 h sortie'")
    descent_m: float = S(1500.0, "brief p.6 '1.5 km descent'")
    survey_m: float = S(4000.0, "brief p.6 '4 km survey'")
    drill_sites_max: int = S(2, "brief p.6 '2 drill sites'")
    drill_h: float = S(1.0, ASSUMED)
    top_speed_mps: float = S(0.15, "draft 4.1 baseline 15 cm/s; draft item 2 open. Sweep 0.10-0.20 (draft 4.1)")

    # --------------------------------------------------------------- energy
    battery_kwh: float = S(7.9, "brief p.6 '4 swappable Li-ion cartridges, ~7.9 kWh'")
    reserve_frac: float = S(0.25, "brief p.6 'return with at least 25 % battery in reserve'")
    # Draft 4.6 / item 3: the battery allows 7.9 kWh x 0.75 / 22 h = 269 W;
    # the dock's 1.5 kW x 3 h / 22 h = 205 W. Both are runs, not one number.
    avg_load_w: float = S(205.0, "draft 4.6 and item 3: 205 W (dock: 1.5 kW x 3 h / 22 h) "
                                 "or 270 W (battery: 7.9 kWh x 0.75 / 22 h)")
    # Per-activity draws. Unknown until area 5 reports (draft item 17).
    # Defaults are placeholders; calibrate so a baseline sortie averages
    # avg_load_w, and say so in the run record.
    drive_w: float = S(150.0, ASSUMED)
    hotel_w: float = S(60.0, ASSUMED + " (lamp + computer + instruments, always on during a sortie)")
    drill_w: float = S(300.0, ASSUMED)

    # ----------------------------------------------------------- navigation
    drift_frac: float = S(0.02, "C23 sec. 1 'around 2% drift'; draft 4.2")
    star_tracker: bool = S(True, "draft 4.6 (heading from star tracker + IMU); draft item 5 open")
    heading_drift_rad_per_m: float = S(0.0005, ASSUMED + "; used only when star_tracker is False")
    check_policy: str = S("fixed", "draft 4.2 / 4.5. One of 'fixed' (spacing from Table 1), "
                                   "'p95' (fix when the predicted 95th-pct error reaches the limit), "
                                   "'before_work' (only before readings that matter and drilling), "
                                   "'fixed_time' (every check_interval_h)")
    check_interval_h: float = S(1.0, ASSUMED + "; used by 'fixed_time' only")
    drill_limit_m: float = S(10.0, "draft Table 1, 5 or 10 m; draft item 4 open")
    check_stop_s: float = S(300.0, "draft 4.2 'if each fix takes five minutes'; draft item 11 open. "
                                   "Assumed; sweep 0-600")
    # Error after a fix is sampled, not computed: draft 4.2 model
    # e(d) = e_fix + drift_frac * d, e_fix drawn from the C23 Table 2 rows.
    # C23 rows are end-of-traverse errors, not per-fix errors (draft item 10):
    # using them per fix is the draft's modelling choice, stated in the paper.
    fix_err_good: tuple = S((C23_TRAJ2, C23_TRAJ3), "C23 Table 2, trajectories 2 and 3 (mean, s.d.) m")
    fix_err_wrong_match: tuple = S(C23_TRAJ1_FALSE_MATCH, "C23 Table 2, trajectory 1 (mean, s.d.) m")
    # p_wrong = clip(p_wrong_base + p_wrong_per_missing * share of nearby
    # craters absent from the Scout's current map). Assumed model; the two
    # C23 cases are its only anchors.
    p_wrong_base: float = S(0.02, ASSUMED)
    p_wrong_per_missing: float = S(0.5, ASSUMED)
    fix_range_m: float = S(20.0, ASSUMED + "; A24 sec. V-B: craters 7.5-20 m across detected reliably; "
                                           "A24 sec. V-C1: observation points 7-13 m from the rim")
    fix_min_craters: int = S(2, ASSUMED + "; A24 Table 2 traverses used 2-3 landmarks")

    # ---------------------------------------------------------- survey, ice
    reading_spacing_m: float = S(5.0, ASSUMED)
    footprint_radius_m: float = S(1.0, "code sim/ice.py:28 (assumed there)")
    sensor_noise_wt: float = S(0.15, "code sim/ice.py:29 (assumed there)")
    find_threshold_wt: float = S(1.0, "brief p.9 '~1 wt% ice threshold we assumed' (code sim/ice.py:20)")
    rich_peak_wt: float = S(5.6, "brief p.12 LCROSS ~5.6% at Cabeus (code sim/ice.py:23)")
    survey_line_spacing_m: float = S(20.0, ASSUMED)
    slope_limit_deg: float = S(15.0, "existing planner rule (docs/handoffs.md, Step 2)")

    # --------------------------------------------------------- cross-checks
    search_radius_k: float = S(1.0, ASSUMED + "; search radius = k * hypot(both 95th-pct bounds) "
                                              "+ footprint (draft 4.4, item 14)")
    map_refinement: bool = S(True, "draft 4.3; swept on/off for Figure 1b")
    sightings_to_add: int = S(2, ASSUMED + "; draft item 12")
    merge_tol_m: float = S(3.0, ASSUMED + "; draft item 12")
    merge_size_tol_frac: float = S(0.3, ASSUMED + "; two sightings agree on size when their diameters "
                                                  "differ by at most this share of the larger (draft item 12)")
    # Rim sightings (sim/crossing.py make_sighting). ShadowNav sees the
    # leading edge, a front arc, not the whole rim: A24 p.10 Fig. 6b plots
    # "percent front arc of ground truth crater detected"; C23 p.8 finds the
    # far edge in stereo at 5 m and only sparsely at 10 m.
    rim_detect_d_min_m: float = S(7.5, "A24 p.10: 'craters of diameters between 7.5 m and 20 m "
                                       "can be detected reliably'")
    rim_detect_d_max_m: float = S(20.0, "A24 p.10 (same sentence)")
    rim_pos_noise_m: float = S(1.0, ASSUMED + "; 1-sigma per axis of the located arc. Anchor: A24 p.10 "
                                             "'average pixel detection is within 2 m of the crater rim', "
                                             "and an arc averages many pixels")
    rim_size_err_frac: float = S(0.2, ASSUMED + "; 1-sigma diameter error from a partial rim, as a share of "
                                                "the diameter. Draft 4.3: size from a partial rim is not "
                                                "demonstrated by ShadowNav; draft item 12")
    map_match_tol_m: float = S(3.0, ASSUMED + "; a map crater further than this from the true crater "
                                              "does not help a fix and counts as a bad map entry. Set at "
                                              "the fix's own 95th-pct error, 2.8-3.0 m (draft Table 1): "
                                              "a smaller offset is within fix noise")
    # Ice cross-check (draft 4.4).
    search_line_spacing_m: float = S(4.0, ASSUMED + "; grid of search readings. A deposit of radius "
                                                    ">= spacing/sqrt(2) always holds a point; "
                                                    "deposit radii are WorldRanges.deposit_radius_m")
    confirm_bound_max_m: float = S(6.0, ASSUMED + "; a check is meaningful only when the combined bound is "
                                                  "'well below the deposit radius and the spectrometer "
                                                  "footprint' (draft 4.4); draft item 14 open")
    dock_reg_bound95_m: float = S(6.0, ASSUMED + "; the 95th-pct registration bound the Scouts believe "
                                                 "(draft 4.2, item 15). The world's actual error is "
                                                 "WorldRanges.dock_reg_sigma_m and stays hidden")

    # --------------------------------------------------------------- faults
    # Per sortie, per Scout. Camera or lamp out: no fixes, Scout turns back.
    # Neutron sensor out: no readings. Module swapped at the dock, Scout out
    # of service for swap_downtime_h.
    p_fail_camera: float = S(0.01, ASSUMED)
    p_fail_lamp: float = S(0.01, ASSUMED)
    p_fail_neutron: float = S(0.01, ASSUMED)
    swap_downtime_h: float = S(24.0, ASSUMED)

    # ------------------------------------- camera navigation (showcase only)
    # Batch runs sample navigation (above) and never read these.
    camera_height_m: float = S(1.6, "brief p.6 '1.6 m mast'; draft item 24. Camera assumed at the mast top. "
                                    "A24 sec. IV tested 1.5, 2.5 and 3 m")
    lamp_below_camera_m: float = S(0.2, "A24 sec. IV 'Light offsets of 0.2 m below the camera'")
    # Stereo depth noise for rim points (sim/vision.py). The brief names
    # stereo cameras (p.5) but no model, so the camera is ShadowNav's rig.
    stereo_baseline_m: float = S(0.26, "A24 sec. IV-B: 'a stereo camera with a 0.26 m baseline'")
    stereo_width_px: int = S(5120, "A24 sec. IV-B: '5,120-by-3,840 pixel resolution'")
    stereo_hfov_deg: float = S(60.0, "A24 sec. IV-B: '60 horizontal field-of-view' (code sim/scene.py HFOV_DEG)")
    # Depth error grows as z^2 sigma_d / (f B), f = 4434 px, f B = 1153 px m.
    # A24 Table 1 (SGBM, camera 1.5 m, light 0.2 m below, medium power)
    # measured RMS range error 0.22 m over 0-10 m and 1.15 m over 10-20 m.
    # sigma_d = 1.15 x 1153 / 15^2 = 5.9 rig px (0.74 px at the 640 px
    # render). The z^2 law then gives 0.19 m RMS over the ground pixels of
    # 3-10 m against the measured 0.22 m. Gaussian: Table 1's outliers
    # (25% beyond 1 m at 10-20 m) are folded into one spread.
    # The contour filter's width threshold is this share x the map cut
    # (sim/vision.py contour_ok). Measured by sim/arc_share.py on held-out
    # frames under a rule fixed before measuring; NaN until measured, and
    # then C23 sec. 1's 0.8 is used.
    # Second and last measurement (rule "route", sim/arc_share.py): headings over the
    # whole field of view, as route frames see craters. NaN until measured; then it is
    # rim_arc_share_measured, the share the runs use.
    rim_arc_share_route: float = S(float("nan"), "pending: python -m sim.arc_share --rule route "
                                                 "(rule committed before measuring)")
    rim_arc_share_headon: float = S(0.553, "sim/arc_share.py rule (commit 89a6d8a), batch "
                                             "campaigns/calibration/arcshare-1, `python -m sim.arc_share --batch "
                                             "arcshare-1 --frames 120`: median of 120 held-out frames (quartiles "
                                             "0.484-0.620); width threshold 5.53 m")
    stereo_disparity_sigma_px: float = S(5.9, "A24 Table 1 row (SGBM, 1.5 m, -0.2 m, med), 10-20 m RMS 1.15 m "
                                              "at the bin's middle (15 m); the z^2 shape is assumed")
    # The truth: how the odometer errs (sim/rover.py, sim/mission.py). The
    # bias is drift_frac above.
    odom_noise_frac: float = S(0.005, ASSUMED + "; white distance noise per step, share of the step")
    odom_heading_noise_rad: float = S(0.0005, ASSUMED + "; heading noise per step (~0.7 deg after 1 km)")
    # The filter's own model (sim/nav.py), set from ShadowNav's motion model
    # and never from the truth above, as a real filter is tuned without
    # knowing it. sim/calibrate.py draws the truth's drift per seed from
    # WorldRanges.drift_frac (1-4%), so the filter is right only on average.
    pf_drift_sigma: float = S(0.02, "C23 sec. 6B 'utilizing 2% odometry noise'; 1-sigma of each "
                                    "particle's odometer scale error")
    pf_step_noise_frac: float = S(0.02, "A24 sec. III-E1 eqs. (3)-(6): additive noise w_k, zero mean, "
                                        "'standard deviation based on p', p = 2% (A24 Table 2 '2% added error'). "
                                        "Per axis, in x and y, times the step length")
    pf_heading_walk_rad: float = S(0.0, "A24 sec. III-A: heading from the star tracker, the filter "
                                        "estimates '2D position' only; so no heading spread")
    pf_scale_walk: float = S(0.001, ASSUMED + "; per-step jitter of each particle's scale error, so "
                                              "resampled copies spread again (no source; not from the truth)")
    pf_sigma0_m: float = S(1.0, "C23 sec. 1 'initial 3-sigma uncertainty of 3 m'. C23 sec. 6B ran "
                                "Table 2 with sigma_0 = 3 m; sim/calibrate.py uses that")

    # -------------------------------------------------------------- logging
    log_spacing_m: float = S(10.0, ASSUMED + " (record size, not physics)")
    plan_cell_m: float = S(5.0, "LOLA 5 m spacing (code sim/terrain.py:57)")

    # ------------------------------------------- engine (part 1, sim/campaign.py)
    turnaround_h: float = S(6.0, "brief p.6 'one full cycle takes about 31 h', minus the 22 h sortie "
                                 "and the ~3 h recharge (derived)")
    # The filter's own reported uncertainty after a fix, sqrt of the largest
    # covariance eigenvalue (C23 sec. 6B), 1-sigma. Used only by the 'filter'
    # policy, the one the draft rejects (draft 4.2: reported 1.84 m against a
    # true 3.84 m on the false-match traverse).
    fix_sigma_good: tuple = S(((1.32, 0.76), (1.39, 0.61)),
                              "C23 Table 2 Uncertainty column, trajectories 2 and 3 (mean, s.d.) m")
    fix_sigma_wrong_match: tuple = S((1.84, 1.12), "C23 Table 2 Uncertainty column, trajectory 1 (mean, s.d.) m")
    fix_z95: float = S(1.645, "draft 4.2 '95th percentile ... mean plus 1.645 standard deviations'")
    fixed_row: str = S("false_match", "draft 4.2 / Table 1: 'fixed' policy spacing from the false-match row "
                                      "(80 m at 10 m) or 'best' (358 m)")
    check_min_spacing_m: float = S(20.0, ASSUMED + "; floor on fix spacing when the limit is not "
                                                   "achievable (Table 1 'not achievable' cells)")
    bound_model: str = S("mixture", ASSUMED + "; what a Scout believes its 95th-pct error is just after a fix: "
                                         "'mixture' (C23 rows mixed at its estimated wrong-match rate) or "
                                         "'good_rows' (C23 trajectories 2 and 3 only, as if every fix were good)")
    nav_limit_m: float = S(30.0, ASSUMED + "; 'before_work' also fixes when its bound reaches this")
    p_est_window_rims: int = S(20, ASSUMED + "; the Scout estimates its false-match rate from the share "
                                            "of the last N rims seen that its map could not match")
    missing_share_prior: float = S(1.0, ASSUMED + "; that share before any rim is seen (conservative)")
    drift_mode: str = S("fixed_direction", ASSUMED + "; 'fixed_direction': drift_frac * d along one random "
                                                     "direction per fix interval (the draft's linear bound, "
                                                     "conservative); 'along_track': scale error along travel")
    reading_stop_s: float = S(0.0, ASSUMED + "; neutron readings taken while driving")
    searches_per_sortie_max: int = S(4, ASSUMED)
    survey_reach_m: float = S(1500.0, ASSUMED + "; stand-in world only: deposits within this of the descent end")
    lane_reach_m: float = S(3000.0, ASSUMED + "; survey lanes on PSR floor within this straight-line distance of "
                                    "the descent end, on an approach under the slope limit. Nearest lanes go "
                                    "first; this only matters once nearer floor is surveyed (Nobile: 6.0 km2 of "
                                    "floor within 3 km driving, terrain fyi 20261116-151941)")
    lane_segment_m: float = S(200.0, ASSUMED + "; lanes are planned in pieces this long")
    find_sep_m: float = S(25.0, ASSUMED + "; a reading this close to a known find adds to it, not a new find")
    # Stand-in world, used until part 3's campaign_world lands (spec 11.1).
    standin_window_px: tuple = S((2400, 2800, 1200), "code: scan of Shoemaker_final_adj_5mpp_slp.tif, the 6 km "
                                                     "window (row0, col0, size px) with most ground under 15 deg "
                                                     "(98.8%); not PSR floor (spec 11.1)")
    standin_crater_d_min_m: float = S(7.5, "A24 p.10 Fig. 6 text: 'craters of diameters between 7.5 m and "
                                           "20 m can be detected reliably'; smaller ones are not modelled")
    standin_crater_d_max_m: float = S(20.0, "A24 p.10 Fig. 6 text (upper end); code sim/terrain.py:104")
    standin_crater_sfd_b: float = S(2.0, "code sim/terrain.py:106 (assumed there)")

    # ------------------------------------------------------------- helpers
    def key(self) -> str:
        """Short stable hash of every setting: (seed, key) names a run."""
        blob = json.dumps(asdict(self), sort_keys=True, default=list)
        return hashlib.sha1(blob.encode()).hexdigest()[:8]

    def with_(self, **kw) -> "Settings":
        return replace(self, **kw)

    @property
    def rim_arc_share_measured(self) -> float:
        """The arc share the contour filter uses: the route measurement once it is set,
        else the head-on one (sim/arc_share.py)."""
        import math as _m
        return self.rim_arc_share_route if _m.isfinite(self.rim_arc_share_route) else self.rim_arc_share_headon


@dataclass(frozen=True)
class WorldRanges:
    """What the world seed draws, uniform over each range unless noted."""
    crater_n_ge10_per_km2: tuple = S((100.0, 10000.0),
                                     "A24 sec. I: mean spacing of 10 m craters 100 m (fresh) to 10 m (old) "
                                     "-> 100 to 10,000 per km^2; drawn log-uniform. Sweep axis of Figure 1a")
    # The Scouts' starting map is what the orbital product resolves
    # (sim/orbital_map.py, used by the campaign and every camera run): a
    # crater is in it when it spans orbital_resolve_px pixels of the orbital
    # DEM, and then only if it is not among the random share left out.
    orbital_dem_px_m: float = S(2.0, ASSUMED + "; inside C23 sec. 1 'LROC provides DEMs with a resolution "
                                               "between 0.5 m-5 m per pixel ... there are some DEMs within PSRs' "
                                               "(draft 4.3 cites the same)")
    orbital_resolve_px: float = S(5.0, ASSUMED + "; pixels across for a crater to be mapped. With 2 m pixels "
                                                 "the cut is 10 m, as C23 sec. 4A 'LROC has enough resolution "
                                                 "to resolve craters of around 10 m'")
    missing_share_large: tuple = S((0.0, 0.2), ASSUMED + "; share of resolved craters also absent")
    orbital_pos_err_px: float = S(1.0, ASSUMED + "; 1-sigma error per axis of a mapped crater's centre, in "
                                                 "orbital DEM pixels (2 m). C23 sec. 1 and 4A give the pixel, "
                                                 "not the error")
    orbital_diam_err_px: float = S(1.0, ASSUMED + "; 1-sigma error of a mapped crater's diameter, in orbital "
                                                  "DEM pixels (2 m)")
    n_deposits: tuple = S((3, 12), ASSUMED)
    deposit_radius_m: tuple = S((3.0, 12.0), ASSUMED + "; code sim/ice.py:30 uses 6 m")
    deposit_peak_wt: tuple = S((1.0, 5.6), "upper end brief p.12 (LCROSS); lower end the find threshold")
    drift_frac: tuple = S((0.01, 0.04), "draft 4.6 'odometry drift from 1 to 4%'")
    dock_reg_sigma_m: tuple = S((0.5, 5.0), ASSUMED + "; draft item 15")
    # ---- part 3 (sim/terrain.py campaign_world). The true crater list holds
    # the sizes ShadowNav can use, so the share missing from the starting map
    # is a share of craters a Scout could have matched.
    crater_d_m: tuple = S((7.5, 20.0), "A24 sec. V-B 'craters of diameters between 7.5 m and 20 m can be "
                                       "detected reliably'")
    crater_sfd_b: float = S(2.0, ASSUMED + "; cumulative N(>D) ~ D^-b (code sim/terrain.py:106)")
    ice_reach_m: float = S(3000.0, ASSUMED + "; hidden deposits lie on PSR floor within this driving "
                                            "distance of the dock (1.5 km descent + half the 4 km survey, "
                                            "brief p.6)")

    @property
    def orbital_map_min_d_m(self) -> float:
        """Smallest crater the orbital map resolves: pixel size x pixels across (10 m by default)."""
        return self.orbital_dem_px_m * self.orbital_resolve_px


@dataclass(frozen=True)
class SiteBuild:
    """How the candidate PSR terrain windows were chosen (sim/psr_sites.py).

    Build-time only: a run reads the built window, never these. Kept here so
    the choice of site is sourced like every other number.
    """
    window_m: float = S(8000.0, ASSUMED + "; square window centred on the dock, 4 km each way, "
                                          "so all ground within ice_reach_m of the dock is inside it")
    cell_m: float = S(5.0, "PGDA product 81 (ldem_87s_5mpp): native 5 m/pixel LOLA mosaic")
    slope_limit_deg: float = S(15.0, "brief p.7 '15 deg slope limit for routes (VIPER's own design limit)'")
    reach_m: float = S(3000.0, ASSUMED + "; driving distance from the dock to PSR floor counted as "
                                        "surveyable (1.5 km descent + half the 4 km survey, brief p.6)")
    dock_max_from_psr_m: float = S(1500.0, "brief p.6 '1.5 km descent'")
    dock_min_sun: float = S(0.30, ASSUMED + "; average sunlit fraction at the ground (Mazarico, "
                                          "AVGVISIB_85S_060M) the dock site must reach. The 20 m Power "
                                          "Tower (brief p.3) sees more sun than the ground")
    psr_min_km2: float = S(1.0, ASSUMED + "; smallest PSR screened")
    screen_px_m: float = S(40.0, ASSUMED + "; screening resolution (level-3 overview of the 5 m mosaic)")
    dock_samples: int = S(60, ASSUMED + "; dock positions tried per site at 5 m")


@dataclass(frozen=True)
class GroundBuild:
    """The ground a campaign run is drawn on (sim/site_ground.py): the site's
    LOLA window upsampled over everywhere the run drove, with that world's
    craters stamped in. Display only; the run itself used the LOLA window."""
    cell_m: float = S(1.0, ASSUMED + "; the smallest campaign crater (WorldRanges.crater_d_m, 7.5 m) "
                                     "spans 7.5 cells")
    margin_m: float = S(100.0, ASSUMED + "; ground kept beyond the run's furthest pose and the dock")
    tile_m: float = S(256.0, ASSUMED + "; square tiles the viewer can load one at a time")


def sources(obj) -> list[tuple[str, object, str]]:
    return [(f.name, getattr(obj, f.name), f.metadata.get("source", "")) for f in fields(obj)]


def unlabelled(obj) -> list[str]:
    """Fields with no source tag. Must be empty (spec sec. 9 item 2)."""
    return [n for n, _, s in sources(obj) if not s]


if __name__ == "__main__":
    for obj in (Settings(), WorldRanges(), SiteBuild(), GroundBuild()):
        print(f"== {type(obj).__name__}")
        for name, val, src in sources(obj):
            print(f"{name:26} {val!s:28} {src}")
        missing = unlabelled(obj)
        print("unlabelled:", missing or "none")
