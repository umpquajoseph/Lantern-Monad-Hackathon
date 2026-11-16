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

    # --------------------------------------------------------------- faults
    # Per sortie, per Scout. Camera or lamp out: no fixes, Scout turns back.
    # Neutron sensor out: no readings. Module swapped at the dock, Scout out
    # of service for swap_downtime_h.
    p_fail_camera: float = S(0.01, ASSUMED)
    p_fail_lamp: float = S(0.01, ASSUMED)
    p_fail_neutron: float = S(0.01, ASSUMED)
    swap_downtime_h: float = S(24.0, ASSUMED)

    # -------------------------------------------------------------- logging
    log_spacing_m: float = S(10.0, ASSUMED + " (record size, not physics)")
    plan_cell_m: float = S(5.0, "LOLA 5 m spacing (code sim/terrain.py:57)")

    # ------------------------------------------------------------- helpers
    def key(self) -> str:
        """Short stable hash of every setting: (seed, key) names a run."""
        blob = json.dumps(asdict(self), sort_keys=True, default=list)
        return hashlib.sha1(blob.encode()).hexdigest()[:8]

    def with_(self, **kw) -> "Settings":
        return replace(self, **kw)


@dataclass(frozen=True)
class WorldRanges:
    """What the world seed draws, uniform over each range unless noted."""
    crater_n_ge10_per_km2: tuple = S((100.0, 10000.0),
                                     "A24 sec. I: mean spacing of 10 m craters 100 m (fresh) to 10 m (old) "
                                     "-> 100 to 10,000 per km^2; drawn log-uniform. Sweep axis of Figure 1a")
    orbital_map_min_d_m: float = S(10.0, ASSUMED + "; craters below this are absent from the Scouts' "
                                                   "starting map (draft 4.3: PSR DEMs 0.5-5 m/px, C23 sec. 1)")
    missing_share_large: tuple = S((0.0, 0.2), ASSUMED + "; share of larger craters also absent")
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


def sources(obj) -> list[tuple[str, object, str]]:
    return [(f.name, getattr(obj, f.name), f.metadata.get("source", "")) for f in fields(obj)]


def unlabelled(obj) -> list[str]:
    """Fields with no source tag. Must be empty (spec sec. 9 item 2)."""
    return [n for n, _, s in sources(obj) if not s]


if __name__ == "__main__":
    for obj in (Settings(), WorldRanges(), SiteBuild()):
        print(f"== {type(obj).__name__}")
        for name, val, src in sources(obj):
            print(f"{name:26} {val!s:28} {src}")
        missing = unlabelled(obj)
        print("unlabelled:", missing or "none")
