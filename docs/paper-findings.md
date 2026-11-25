# Findings for the paper (area 3)

Working notes for the team, not proposal text. Each finding names the runs
it comes from. Runs launched by `sim/live_runs.py` carry
`paper_statistics: false`: quote them as examples, not statistics.

## Camera runs

1. **ShadowNav comparison.** On the approved terrain, Lantern ends its three
   ShadowNav-style traverses 3.9-6.3 m off, against C23 Table 2's
   1.7-3.8 m (`python -m sim.calibrate`). ShadowNav's map is not public, so
   this is reported, not tuned away.
2. **Fixes cost time.** At the draft's spacing (every 79 m at the 10 m
   limit, draft sec. 4.2) a preplanned fix costs a median 0.34 h: 6 min
   stopped plus a 99 m survey drive. Fixes fill the sortie, and the Scout
   surveys a seventh of the ground a Scout without fixes does, inside the
   10 m limit. The draft's "88 x 5 min" leaves out the drive. Haynes seed 3,
   one sortie per mode:

   | run | fix mode | hours | survey m | true error p50 / max m |
   |---|---|---|---|---|
   | drive-full-s3 | opportunistic | 14.1 | 3,861 | 5.0 / 32.8 |
   | nav-pre-s3a | preplanned | 19.5 | 569 | 1.4 / 5.1 |
   | nav-str-s3a | straight, 20 m | 17.1 | 3,786 | 44.2 / 102.7 |
   | nav-str12-s3a | straight, 12 m | 15.9 | 3,800 | 41.1 / 84.1 |

   Straight through, on a route not built to pass landmarks, saw rims in 4%
   of exposures and did not hold position.
3. **Corroboration fails on the 6 m rule.** In `proof-2x2-fix` (2 Scouts x 2
   sorties, preplanned fixes) S1 logged a find with a 1.8 m bound (true
   error 2.4 m; 26.3 m without scheduled fixes). S2 searched it: unconfirmed,
   combined bound 7.83 m against the 6 m rule, over a real deposit. S2
   arrived about 10 m off: 24 of its 42 fixes saw no rim.
4. **A fix inherits the map's error.** With repeated fixes on one crater the
   filter settled 4.8 m off with a 1.6 m bound: the map's own 3.5 m error on
   that landmark. C23 Alg. 1 treats the map as truth, so this is faithful.
5. **Orbital planning misses some steep ground.** A Scout stepped onto
   19.4 deg ground that LOLA read as 4.0 deg, hidden from its lidar by
   shadow. The ground is built from LOLA, so the real Moon likely has more.
6. **The haynes return ramp is LOLA's own steepness**, not an artefact.

## Draft corrections

7. Draft 4.4 asks for a combined bound "well below the deposit radius and
   the spectrometer footprint". The footprint is about 1 m and the best
   95th-percentile error after a fix is 2.8 m (draft Table 1), so this
   cannot hold as worded (draft item 14). The code uses 6 m, assumed.
8. Table 1, false-match row at a 10 m limit: 79.3 m from the unrounded
   8.41 m; the draft's 80 m comes from the rounded 8.4 m. The battery-limited
   load is 269.3 W (7.9 kWh x 0.75 / 22 h); the draft writes 270 W.

## Sites

9. **The descent is shorter than the brief's 1.5 km.** On haynes, s89_014e
   and s89_134e the shortest route under 15 deg from a sunlit dock to PSR
   floor is 0.86-1.10 km.
10. **A 22 h sortie reaches small PSRs, not the famous crater floors.**
    Screening every PSR from 87 to 90 S at 40 m (LOLA 5 m heights, Barker
    2021; PSR map, Barker 2023; sun visibility, Mazarico 2011) for a dock
    lit at least 30% of the time (assumed) with at least 1 km2 of floor
    within 3 km of driving under 15 deg: Shoemaker, Haworth, Faustini,
    Shackleton and de Gerlache fail. Three small unnamed PSRs pass:

    | site | where | PSR | reachable floor |
    |---|---|---|---|
    | haynes | 88.9 S 151 E, outside Haynes' rim | 15.3 km2 | 7.2 km2 |
    | s89_014e | 89.3 S 20 E | 7.7 km2 | 4.1 km2 |
    | s89_134e | 89.0 S 130 E | 5.9 km2 | 4.3 km2 |

    From 85 to 87 S, Cabeus fails (no ground lit 30% of the time within
    1.5 km of its large floor PSRs). Nobile passes: a sunlit rise on its
    floor (85.428 S 53.64 E) reaches 6.0 km2 of a 138 km2 floor PSR within
    3 km. The 85-87 S sun map is 120 m, so a small lit rise could be missed.
    Nothing here says these PSRs hold ice.
