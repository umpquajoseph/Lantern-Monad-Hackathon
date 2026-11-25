"""Every constant of the campaign simulator, each with its source.

A run is (world seed, Settings). `Settings` holds the design choices that are
swept on purpose plus the fixed constants; `WorldRanges` holds the ranges the
random world is drawn from, one draw per seed (docs/campaign-spec.md, "Two
decisions": the paper's numbers come from many seeded runs).

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
import math
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
                              "floor). Swept for the site table. Default: the site whose median sortie had 100% of its "
                              "survey poses on PSR floor (measured on the sampled Campaign, 3 Scouts x "
                              "16 sorties on each site)")

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
    # A camera Scout's scheduled fix (sim/campaign_camera.py CameraSortie.check).
    cam_fix_mode: str = S("preplanned", "How a camera Scout takes its position fixes. Except in "
                                        "'opportunistic', the filter updates only on a fix's exposures, against "
                                        "that fix's landmark, and odometry carries it between fixes (draft 4.2; "
                                        "A24 sec. I, contribution 2). A fix falls due by distance driven (draft "
                                        "4.2). 'preplanned' (default): the landmark is chosen along the leg's "
                                        "route when the leg is planned, and the route drives a half survey of it "
                                        "(A24 sec. III-E2; A24 Table 2). 'straight' (the comparison case): fix on "
                                        "the first landmark-sized mapped crater that comes into view, stopping "
                                        "every fix_survey_spacing_m to face it (A24 sec. V-C1, sec. IV-B). Test "
                                        "cases: 'scheduled' (the nearest landmark ahead when a fix falls due), "
                                        "'opportunistic' (every frame with rims corrects against the whole map)")
    fix_plan_early_m: float = S(40.0, ASSUMED + "; 'preplanned': a landmark's survey may begin up to this far "
                                                "before the fix falls due (the draft's spacing is a maximum, draft "
                                                "4.2); with none in that stretch of the leg, the first one after it")
    fix_view_m: float = S(20.0, "A24 sec. IV-B perception tests, 'moving the data collection rig from approximately "
                                "20 m away to 5 m from the leading crater edge'; 'straight': a landmark is in view "
                                "when its near rim is this close and it lies inside the camera's field of view")
    fix_crater_d_m: tuple = S((7.5, 20.0), "A24 sec. V-B: 'craters of diameters between 7.5 m and 20 m can be "
                                           "detected reliably'; a landmark must be in the Scout's own map too")
    fix_reach_m: float = S(40.0, ASSUMED + "; a landmark's near rim must lie within this of the estimate when a fix "
                                           "is due; sweep 0-80")
    fix_survey_offset_m: float = S(10.0, ASSUMED + "; observation points this far outside the landmark's rim, the "
                                                   "middle of A24 sec. V-C1's half survey, 'each observation point "
                                                   "approximately 7 m-13 m from the crater rim'")
    fix_survey_spacing_m: float = S(10.0, "A24 sec. V-D, Endurance-A ConOps: 'Long exposure captures are proposed "
                                          "to be captured every 10 m'; observation points this far apart along the "
                                          "half circle (A24 sec. V-C1, A24 Table 2: 'Localization accuracy is "
                                          "optimal when a half survey around the landmark crater occurs')")
    fix_stop_model: str = S("exposure", "How long a fix keeps the Scout stopped. Answers draft item 11 from A24 "
                                        "sec. V-D. 'exposure': fix_exposure_stop_s at each observation point, "
                                        "nothing more: A24 sec. V-D, 'proposed 1 min stops every 10 m to capture "
                                        "long exposure images'; stereo and detection fit 'during the long-exposure "
                                        "capture stop or while driving between exposure captures' (A24 Table 4 "
                                        "text), so processing adds no time. Labelled test cases: 'batch' adds "
                                        "fix_batch_stop_s once per fix (A24 sec. V-D, 'stop for approximately 10 "
                                        "minutes between drives in order to perform absolute localization'); "
                                        "'draft' spends check_stop_s per fix, split over its observation points "
                                        "(draft 4.2 'if each fix takes five minutes')")
    fix_exposure_stop_s: float = S(60.0, "A24 sec. V-D: 'proposed 1 min stops every 10 m to capture long exposure "
                                         "images' (Endurance-A ConOps)")
    fix_batch_stop_s: float = S(600.0, "A24 sec. V-D: 'stop for approximately 10 minutes between drives in order "
                                       "to perform absolute localization'; fix_stop_model 'batch' only")

    # ---------------------------------------------------------- survey, ice
    reading_spacing_m: float = S(5.0, ASSUMED)
    footprint_radius_m: float = S(1.0, ASSUMED)
    # Reading noise of the neutron spectrometer, from VIPER's NSS (the draft names
    # no instrument): 0.5 wt% WEH at 3-sigma while roving at 10 cm/s, over the 5 m
    # its WEH map resolves. Counting noise falls as 1/sqrt(integration time), and a
    # Scout reads every reading_spacing_m while driving at top_speed_mps
    # (reading_stop_s 0), so sensor_noise_wt (a property below) is
    # nss_3sigma_wt / 3 * sqrt(T_ref / T): 0.204 wt% at 5 m and 0.15 m/s.
    nss_3sigma_wt: float = S(0.5, "Colaprete et al., VIPER Project Implementation Plan (NTRS 20210015009) "
                                  "sec. 3.2.1: 'detection sensitivity of 0.5% (wt) at 3-sigma while roving at "
                                  "10 cm/s'; Andrews 2020 VIPER overview (NTRS 20205000864) NSS slide: 'down to "
                                  "0.5% (wt) WEH to 3-sigma while roving'")
    nss_ref_speed_mps: float = S(0.10, "Colaprete PIP sec. 3.2.1 (same sentence): 'while roving at 10 cm/s'")
    nss_ref_length_m: float = S(5.0, "Colaprete PIP Table 2-2 L2-SPL-5: WEH 'with a horizontal spatial "
                                     "resolution of no greater than 5 m'; " + ASSUMED + ": the 3-sigma figure "
                                     "is for one 5 m stretch of track (50 s at 10 cm/s)")
    # Hydrogen in dry polar regolith: every reading is this plus the deposits'
    # concentration (sim/campaign.py read_here). It is not ice to be found: a find is
    # scored on the deposits alone (sim/crossing.py deposit_at). Test case 0.0: dry
    # ground reads zero.
    background_weh_wt: float = S(0.13, "Livengood et al., LEND polar hydrogen (NTRS 20180004020), abstract "
                                       "p.2: 'Above 88 deg north or south, the concentration increases to "
                                       "~140 ppmw, or 0.13+/-0.02 wt% water-equivalent hydrogen'; every "
                                       "Lantern site is poleward of 88 S (sim/psr_sites.py). An orbital average "
                                       "over km-scale footprints; a PSR floor's own background is not measured")
    find_threshold_wt: float = S(1.0, "brief p.9 '~1 wt% ice threshold we assumed'")
    rich_peak_wt: float = S(5.6, "brief p.12 LCROSS ~5.6% at Cabeus")
    survey_line_spacing_m: float = S(20.0, ASSUMED)
    # The Scout's slope limits by heading (sim/footprint.py severity): pitch is
    # the slope along its heading, roll the slope across it. Both are planning
    # limits: Scouts strand only where the true ground stops them.
    pitch_limit_deg: float = S(15.0, "VIPER's mobility requirement: Bluethmann, LSIC 2024 VIPER mobility "
                                     "(NTRS 20240013903) p.4 'Slopes up to 15 deg (reqt)'; Rezich et al. 2025, "
                                     "PSJ 6:169 (doi 10.3847/PSJ/add13f) sec. 3.5: 'the mobility design "
                                     "requirements of the VIPER mission defined a maximum of 40% slip up a "
                                     "maximum slope of 15 deg'")
    roll_limit_deg: float = S(15.0, "VIPER's suspension roll range: Rezich et al. 2025, PSJ 6:169 sec. 3.4: "
                                    "'adjustable chassis ground clearance (7-42 cm), pitch (+/-20 deg), and roll "
                                    "(+/-15 deg)'. Read as the cross slope the chassis can be held level on; "
                                    "no source gives VIPER a roll limit for routes")
    # When the Scout's own map closes
    # every way out of where it stands, it retraces its own track, ignoring
    # the map, until its map leaves a way out (sim/rover.py _start_retrace).
    # Its steps over the limits are still counted.
    retrace_track_m: float = S(50.0, ASSUMED + "; how much of its own recent track a Scout keeps to retrace. "
                                              "Over three times the width of a 14 m trap crater")

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

    # ------------------------------------------------- camera navigation
    nav: str = S("camera", "docs/campaign-spec.md, 'Two decisions': 'Rovers navigate by their live camera.' "
                           "'camera': render, rims, particle filter, lidar, planner at every step "
                           "(sim/campaign.py CameraSortie); 'sampled': error drawn from C23 Table 2, "
                           "the paper's comparison column only")
    cam_arrive_m: float = S(1.0, ASSUMED + "; a camera Scout has reached a waypoint when its estimate is this close")
    cam_stuck_steps: int = S(40, ASSUMED + "; a waypoint is skipped after this many steps without getting 0.5 m closer")
    cam_match_tol_m: float = S(1.0, ASSUMED + "; a rim point pairs with a crater whose edge is this close "
                                              "(record labels and sightings only; the filter scores every point)")
    cam_pass_gap_steps: int = S(3, ASSUMED + "; a crater pass (one 'check' event) ends after this many steps "
                                             "without a matched rim")
    cam_pass_max_steps: int = S(25, ASSUMED + "; a crater pass also ends after this many steps (50 m), so one "
                                              "'check' is one stretch of matching, not a whole crater field")
    cam_sighting_min_points: int = S(12, ASSUMED + "; unmatched rim points needed to fit a sighting circle, counted after thinning to 0.25 m cells")
    cam_sighting_fit_rms_m: float = S(0.5, ASSUMED + "; largest circle-fit residual (RMS) a sighting may have")
    cam_turn_rate_deg_s: float = S(5.0, ASSUMED + "; turn-in-place rate, for time and energy")
    cam_ground_margin_m: float = S(250.0, ASSUMED + "; fine ground built this far beyond the dock, corridor "
                                                    "and every lane the run could reserve")
    # The run ground (sim/site_ground.py), docs/terrain/position.md. Rocks are
    # not modelled (row 11): the ground has none and the lidar detects none
    # (sim/lidar.py DETECT_ROCKS), so driving and stranding omit rock avoidance.
    ground_roughness: bool = S(True, "docs/terrain/position.md row 3: fractal relief under 10 m is in every "
                                     "lunar rover simulator read there. False is a labelled sensitivity case: "
                                     "LOLA upsampled plus the world's craters only")
    ground_rough_rms_m: float = S(0.035, "docs/terrain/position.md row 3: rms of that relief (Hurst 1, Barker 2021 "
                                         "sec. 4). 0.035 m puts as much ground over 15 deg at CR1 as Hemmi 2025's "
                                         "1 m DTM of it; 0.021 and 0.048 m, the ends of that comparison, are "
                                         "labelled test cases")
    slope_test: str = S("footprint", "docs/terrain/position.md row 9, sim/rover.py SLOPE_TESTS: 'footprint' = the "
                                     "tilt of the Scout's 1.55 m footprint plane (brief p.6 width; sim/footprint.py, "
                                     "GESTALT's rover-sized plane fit, Goldberg 2002 sec. 4 step 3, p.6), for the planner "
                                     "and the lidar hazards. Labelled test cases: 'widened' (slope across 2 m, "
                                     "widened by the 0.83 m half width) and "
                                     "'point' (2 m slope, no widening)")
    plan_slope: str = S("orbital", "sim/rover.py SlopeMap.use_orbital: the slope camera Scouts plan and steer on. "
                                   "'orbital' = the site's LOLA 5 m slope product as the site file holds it "
                                   "(sim/psr_sites.py 'ldsm'), the orbital map VIPER's strategic plans use "
                                   "(Shirley and Balaban 2022; Ennico-Smith 2023), plus the lidar's map. "
                                   "Labelled test case 'truth': the ground's own 0.25 m footprint tilt, which "
                                   "no Scout has")
    crater_n_ge10_per_km2: float = S(336.0, "Minton 2019 sec. 1.2, Fit 1 (Apollo 15): docs/terrain/position.md "
                                            "row 4. A single run's density (camera runs); drawn batches draw "
                                            "WorldRanges.crater_n_ge10_per_km2. 106 and 1,540 are labelled test cases")
    orbital_map_cut_m: float = S(10.0, "C23 sec. 4A 'LROC has enough resolution to resolve craters of around "
                                       "10 m'; docs/terrain/position.md row 7. A single run's starting-map cut "
                                       "(WorldRanges.orbital_resolve_px pinned to it); 18 m, where ShadowCam "
                                       "counts in polar shadow are complete (Mazarico 2025), is a labelled test case")
    cam_display: str = S("detail", "sim/scene.py DISPLAY_MODES. 'detail': ShadowNav's render recipe, Hapke "
                                   "with old-highland parameters (C23 sec. 4A, Xu 2020 Table 1) plus fractal "
                                   "surface texture below the grid (C23 sec. 4A, A24 sec. IV-A), its amplitude "
                                   "the ground's own k^-4 spectrum continued (sim/terrain_display.py "
                                   "run_ground_params); no rocks, lips or albedo mottling. 'off': Lambert, "
                                   "no texture, the GPU path, a labelled test case")
    # Batch runs with nav='sampled' never read the fields below.
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
    # (sim/vision.py contour_ok). Measured on held-out frames under a rule fixed
    # before measuring, by sim/arc_share.py (removed; in git at f8de9b4).
    # "route": headings over the whole field of view, the share the runs use.
    rim_arc_share_route: float = S(0.476, "measured, rule route (commits b356a4d, 12a0ac0), batch "
                                          "campaigns/calibration/arcshare-route: median of 96 held-out frames "
                                          "on s89_014e_s2027 (quartiles 0.337-0.540); width threshold 4.76 m")
    rim_arc_share_headon: float = S(0.553, "measured, rule head-on (commit 89a6d8a), batch "
                                             "campaigns/calibration/arcshare-1: median of 120 held-out frames (quartiles "
                                             "0.484-0.620); width threshold 5.53 m")
    stereo_disparity_sigma_px: float = S(5.9, "A24 Table 1 row (SGBM, 1.5 m, -0.2 m, med), 10-20 m RMS 1.15 m "
                                              "at the bin's middle (15 m); the z^2 shape is assumed")
    # The same Table 1 row, all four 10 m bands of true range (0-10/10-20/20-30/30-40 m).
    # The z^2 law above reproduces the first two bands (RMS 0.16 / 1.06 m against 0.22 /
    # 1.15; share off by more than 0.5 / 1 m 2.2 / 26.9% against 1.58 / 24.94%) but not
    # the last two (RMS 3.13 / 6.67 m against 1.48 / 2.34): SGBM leaves the pixels it
    # cannot match as holes, and the rest stay good. So from stereo_law_max_m out, the
    # range error is Gaussian with the band's RMS (sim/vision.py noisy_range). Checked on
    # the ground pixels of 29 calibration frames.
    stereo_law_max_m: float = S(20.0, "A24 Table 1 band edge: the z^2 law reproduces the 0-10 and 10-20 m "
                                      "bands of the row above and not the 20-30 and 30-40 m bands")
    stereo_band_rms_m: tuple = S((0.22, 1.15, 1.48, 2.34), "A24 Table 1 row (SGBM, 1.5 m, -0.2 m, med), RMS (m) "
                                                           "by 10 m band 0-10/10-20/20-30/30-40; used from "
                                                           "stereo_law_max_m out, beyond 40 m the last band's (assumed)")
    stereo_hole_pct: tuple = S((6.71, 27.53, 50.30, 88.38), "A24 Table 1 row (SGBM, 1.5 m, -0.2 m, med), %hole by "
                                                            "10 m band 0-10/10-20/20-30/30-40 m; beyond 40 m the "
                                                            "last band's (assumed). Lantern's camera is 1.6 m up")
    stereo_hole_model: str = S("pixel", ASSUMED + ": 'pixel', each render pixel a hole independently with its "
                                        "band's share (one of the 640 px render's pixels spans 8 of the rig's "
                                        "5120, wider than an SGBM window); test case 'block'")
    stereo_hole_block_px: int = S(4, ASSUMED + ": the 'block' test case's square of render pixels that are "
                                           "holes together")
    # The truth: how the odometer errs (sim/rover.py). The
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
    pf_scale_walk: float = S(0.0, "A24 sec. III-E1 eqs. (3)-(6): particles move by the odometry plus zero-mean "
                                  "additive noise, with no walk in scale. 0.001 per step (assumed) is a labelled test case")
    pf_sigma0_m: float = S(1.0, "C23 sec. 1 'initial 3-sigma uncertainty of 3 m'. C23 sec. 6B ran "
                                "Table 2 with sigma_0 = 3 m; sim/calibrate.py uses that")
    pf_match: str = S("front_half", "A24 sec. III-E2: 'only the front half of a crater is used as ground truth', "
                                    "'determined dynamically based on the heading of camera', because 'using the "
                                    "entire crater rim for matching could lead to false positives'. Each rim point's "
                                    "Q-Score distance is to the near half of a mapped rim, as seen along the "
                                    "particle's heading. 'full_circle' (the whole rim) is a labelled test case")
    pf_gate_m: float = S(20.0, "A24 sec. III-E2: 'When the estimated particle filter position was not within 20 m "
                               "of the ground truth rim, the particle filter was not run' and odometry alone moved "
                               "it. Here: no update when the filter's mean is over this far from every mapped rim. "
                               "0 (no gate) is a labelled test case")

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
                                    "floor within 3 km driving, docs/paper-findings.md, the Nobile finding)")
    lane_segment_m: float = S(200.0, ASSUMED + "; lanes are planned in pieces this long")
    find_sep_m: float = S(25.0, ASSUMED + "; a reading this close to a known find adds to it, not a new find")
    # Stand-in world (site 'shoemaker'), the engine's test ground; not PSR floor.
    standin_window_px: tuple = S((2400, 2800, 1200), "code: scan of Shoemaker_final_adj_5mpp_slp.tif, the 6 km "
                                                     "window (row0, col0, size px) with most ground under 15 deg "
                                                     "(98.8%); not PSR floor")
    standin_crater_d_min_m: float = S(7.5, "A24 p.10 Fig. 6 text: 'craters of diameters between 7.5 m and "
                                           "20 m can be detected reliably'; smaller ones are not modelled")
    standin_crater_d_max_m: float = S(20.0, "A24 p.10 Fig. 6 text (upper end); code sim/terrain.py:104")
    standin_crater_sfd_b: float = S(2.0, "Minton 2019 sec. 1.2: the small-crater equilibrium slope is about 2 "
                                          "(code sim/terrain.py:106)")

    # ------------------------------------------------------------- helpers
    @property
    def sensor_noise_wt(self) -> float:
        """1-sigma noise (wt% WEH) of one ice reading: the NSS's 3-sigma over 3, scaled by
        counting statistics from its reference integration (nss_ref_length_m at
        nss_ref_speed_mps) to the Scout's (reading_spacing_m at top_speed_mps)."""
        t_ref = self.nss_ref_length_m / self.nss_ref_speed_mps
        t = self.reading_spacing_m / self.top_speed_mps
        return self.nss_3sigma_wt / 3.0 * math.sqrt(t_ref / t)

    def key(self) -> str:
        """Short stable hash of every setting: (seed, key) names a run."""
        blob = json.dumps(asdict(self), sort_keys=True, default=list)
        return hashlib.sha1(blob.encode()).hexdigest()[:8]

    def with_(self, **kw) -> "Settings":
        return replace(self, **kw)

    @property
    def rim_arc_share_measured(self) -> float:
        """The arc share the contour filter uses: the route measurement once it is set,
        else the head-on one."""
        import math as _m
        return self.rim_arc_share_route if _m.isfinite(self.rim_arc_share_route) else self.rim_arc_share_headon


@dataclass(frozen=True)
class WorldRanges:
    """What the world seed draws, uniform over each range unless noted."""
    crater_n_ge10_per_km2: tuple = S((106.0, 1540.0),
                                     "Minton 2019 sec. 1.2: small-crater equilibrium is 0.69-10% of geometric "
                                     "saturation (0.385 r^-2), so 106 to 1,540 per km^2 of 10 m and up; drawn "
                                     "log-uniform. Sweep axis of Figure 1a. Replaces A24 sec. I's 100-10,000, "
                                     "whose top is 65% of saturation (docs/terrain/position.md row 4)")
    # The Scouts' starting map is what the orbital product resolves
    # (sim/orbital_map.py, used by the campaign and every camera run): a
    # crater is in it when it spans orbital_resolve_px pixels of the orbital
    # DEM, and then only if it is not among the random share left out.
    orbital_dem_px_m: float = S(2.0, ASSUMED + "; inside C23 sec. 1 'LROC provides DEMs with a resolution "
                                               "between 0.5 m-5 m per pixel ... there are some DEMs within PSRs' "
                                               "(draft 4.3 cites the same)")
    orbital_resolve_px: float = S(5.0, "pixels across for a crater to be mapped. With 2 m pixels the cut is "
                                       "10 m, as C23 sec. 4A 'LROC has enough resolution to resolve craters of "
                                       "around 10 m' (docs/terrain/position.md row 7; Hemmi 2025 sec. 4.2 agrees). "
                                       "A single run pins it to Settings.orbital_map_cut_m")
    missing_share_large: tuple = S((0.0, 0.2), ASSUMED + "; share of resolved craters also absent")
    orbital_pos_err_px: float = S(1.0, ASSUMED + "; 1-sigma error per axis of a mapped crater's centre, in "
                                                 "orbital DEM pixels (2 m). C23 sec. 1 and 4A give the pixel, "
                                                 "not the error. No source gives a crater-position error for a "
                                                 "DEM-derived map; the frame itself is far better: co-adjusted "
                                                 "LOLA tracks are geolocated to 10-20 cm horizontally over each "
                                                 "16 km site (Barker 2021 abstract); over 80-90 S, 1.00/1.00/0.15 m "
                                                 "along/cross/radial, conservative (Barker 2023 sec. 3, p.10), and "
                                                 "1 m NAC SfS DTMs are aligned to LOLA (Hemmi 2025 sec. 2.2, 4.3)")
    orbital_diam_err_px: float = S(1.0, ASSUMED + "; 1-sigma error of a mapped crater's diameter, in orbital "
                                                  "DEM pixels (2 m). ShadowCam diameters from detection boxes "
                                                  "'may currently be slight overestimates' (Mazarico 2025), "
                                                  "unquantified, so not modelled")
    # A map cut at ShadowCam's completeness is a map made from ShadowCam
    # images of the shadowed floor (docs/terrain/position.md row 7): 1 m SfS
    # DTMs need sunlight (Hemmi 2025 keeps NoData in deep shadow, sec. 4.5),
    # so a sub-5 m map of a PSR floor would come from ShadowCam. Its images
    # are geolocated to about 20 m, so each image's craters share one offset.
    shadowcam_map_from_cut_m: float = S(18.0, "Mazarico 2025: ShadowCam crater counts in polar shadow complete "
                                              "from ~18 m. A starting map cut this large or larger is taken "
                                              "to come from ShadowCam images and gets shadowcam_geoloc_m")
    shadowcam_geoloc_m: float = S(20.0, "Mazarico 2025: 'the geolocation accuracy of the images is on a par "
                                        "with the size of the smallest detected craters (~20 m)'. Read as a "
                                        "1-sigma offset per axis, shared by every crater of one image "
                                        "(assumed reading)")
    shadowcam_image_m: float = S(5200.0, "ShadowCam factsheet (docs/sources/ShadowCam-factsheet.pdf): 'Image "
                                         "Footprint (100 km alt.) 5.2 km x 144 km'; the cross-track width as the "
                                         "side of a square patch that shares one offset (square " + ASSUMED + ")")
    n_deposits: tuple = S((3, 12), ASSUMED)
    deposit_radius_m: tuple = S((3.0, 12.0), ASSUMED)
    deposit_peak_wt: tuple = S((1.0, 5.6), "upper end brief p.12 (LCROSS); lower end the find threshold")
    drift_frac: tuple = S((0.01, 0.04), "draft 4.6 'odometry drift from 1 to 4%'")
    dock_reg_sigma_m: tuple = S((0.5, 5.0), ASSUMED + "; draft item 15")
    # ---- part 3 (sim/terrain.py campaign_world). The true crater list holds
    # the sizes ShadowNav can use, so the share missing from the starting map
    # is a share of craters a Scout could have matched.
    crater_d_m: tuple = S((7.5, 20.0), "A24 sec. V-B 'craters of diameters between 7.5 m and 20 m can be "
                                       "detected reliably'")
    crater_sfd_b: float = S(2.0, "Minton 2019 sec. 1.2: cumulative N(>D) ~ D^-b, equilibrium slope about 2 "
                                  "(Fit 2 gives 1.8); docs/terrain/position.md row 5")
    ice_reach_m: float = S(3000.0, ASSUMED + "; hidden deposits lie on PSR floor within this driving "
                                            "distance of the dock (1.5 km descent + half the 4 km survey, "
                                            "brief p.6)")
    dock_clear_m: float = S(10.0, ASSUMED + "; the lander sets the dock down on ground clear of craters: no "
                                           "seeded crater's rim comes this close to the dock (uniform draws "
                                           "put one within 5 m on about 1 seed in 5, and a Scout docked in a "
                                           "crater bowl cannot drive out). Applied after every draw, so the "
                                           "other craters, the map cut and the ice are unchanged")

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
    slope_limit_deg: float = S(15.0, "brief p.7 '15 deg slope limit for routes (VIPER's own design limit)'; "
                                     "VIPER: Bluethmann, LSIC 2024 (NTRS 20240013903) p.4 'Slopes up to "
                                     "15 deg (reqt)'. Site choice only, heading-free")
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
    """Fields with no source tag. Must be empty (CLAUDE.md, 'Assert only what is grounded')."""
    return [n for n, _, s in sources(obj) if not s]


if __name__ == "__main__":
    for obj in (Settings(), WorldRanges(), SiteBuild(), GroundBuild()):
        print(f"== {type(obj).__name__}")
        for name, val, src in sources(obj):
            print(f"{name:26} {val!s:28} {src}")
        missing = unlabelled(obj)
        print("unlabelled:", missing or "none")
