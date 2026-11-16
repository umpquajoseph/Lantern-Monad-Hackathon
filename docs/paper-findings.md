# Findings for the paper (area 3, section 4)

Notes for the team from the campaign simulator. They are working notes, not
proposal text: no sentence here goes into the proposal as written. Each
finding names the run or record it comes from. Every number is on the
stand-in Shoemaker terrain until the PSR windows land (spec 11.1).

## 1. The deposit check depends on the false-match rate, so the paper states it

Decided 16 Nov 2026: the paper states the false-match rate it assumes, and
says that above a threshold, ShadowNav's accuracy is not enough for one
Scout to confirm another's find.

What the engine found (engine reply 20261116-144513, branch campaign-engine
6013751):

- A find is confirmed only when the two Scouts' combined 95th-percentile
  position bound is small enough. With both Scouts just after a fix, the
  combined bound is about sqrt(2) times one Scout's error after a fix.
- That error depends on the false-match rate p, from the C23 Table 2 rows:

  | p | 95th-pct error after a fix | best combined bound |
  |---|---|---|
  | 0 | 2.94 m | 4.16 m |
  | 0.10 | 3.94 m | 5.57 m |
  | 0.20 | 5.72 m | 8.08 m |
  | 0.30 | 6.53 m | 9.23 m |

- At a 6 m confirmation limit, a check can succeed only if p is about 11%
  or less. At a 10 m limit, about 41% or less.
- p is an assumed model: a base rate plus a term for the share of nearby
  craters missing from the orbital map. Its only anchors are the two C23
  cases. With every crater under 10 m missing from the map (the default),
  p comes out at 20 to 30%, and no find can be confirmed.
- A confirmation becomes possible if any one of these holds: the orbital
  map includes every crater the cameras detect (7.5 m and up, A24); the
  false-match rate is under about 11%; or the confirmation limit is about
  10 m or more.

Still to do: the simulated sweep that checks this table (engine and
crossing), then a curve of confirmation rate against p for the paper.

## 2. Draft 4.4's criterion cannot be met as worded

Draft 4.4 asks for a combined bound "well below the deposit radius and the
spectrometer footprint". The footprint is about 1 m (assumed,
`sim/ice.py`) and the best 95th-percentile error after a fix is 2.8 m
(draft Table 1), so the footprint half can never hold. The team needs to
reword this and set the number (draft item 14). The code uses 6 m for now.
(crossing report 20261116-141208)

## 3. The draft's own stop budget fails with two drill sites

Draft 4.2: 88 fixes at 5 min leave 1.7 h of the 9.0 h stop budget. Two
drill sites at 1 h each (assumed, `drill_h`) need 2 h, so the false-match
traverse cannot complete its planned work. The engine now counts a sortie
as completed only when its survey and drill sites are done, inside 22 h,
with the reserve. Seed 42 (3 Scouts, 'fixed' policy): 40 of 48 sorties
complete, 6 fail on time, 2 on a short survey. (engine reply
20261116-144513)

## 4. Small corrections to the draft's arithmetic

- Table 1, false-match row at a 10 m limit: 79.3 m from the unrounded
  8.41 m; the draft's 80 m comes from the rounded 8.4 m.
- The battery-limited load is 269.3 W (7.9 kWh x 0.75 / 22 h); the draft
  writes 270 W.

## 5. On real PSR terrain the descent is shorter than the brief's 1.5 km

On the three PSR sites built from LOLA 5 m terrain (haynes, s89_014e,
s89_134e), the shortest route under 15 degrees from a sunlit dock to PSR
floor is 0.86 to 1.10 km. Sorties come out at 5.5 to 6.1 km, not the
brief's 7 km, so the stop budget is looser than draft 4.1 assumes. The
simulator keeps the real routes. Whether these sites are the large crater
floor the brief pictures or small shadows near landing sites is being
checked. (engine fyi 20261116-145247, batch "sitecheck", seed 42)

## 6. The brief's sortie reaches only small PSRs, not the famous crater floors

Terrain screened every PSR from 87 to 90 S at 40 m (LOLA 5 m heights,
Barker 2021; PSR map, Barker 2023; sun visibility, Mazarico 2011). It
looked for a dock lit at least 30% of the time (assumed threshold) with
at least 1 km2 of PSR floor within 3 km of driving under 15 degrees.
Shoemaker, Haworth, Faustini, Shackleton and de Gerlache all failed. One
large PSR near Henson passed with only 1.1 km2 reachable.

What passed are three unnamed small PSRs, each a closed low 47 to 111 m
deep:

| site | where | PSR size | reachable floor |
|---|---|---|---|
| haynes | 88.9 S 151 E, outside Haynes' rim | 15.3 km2 | 7.2 km2 |
| s89_014e | 89.3 S 20 E, 25.7 km from Shackleton | 7.7 km2 | 4.1 km2 |
| s89_134e | 89.0 S 130 E, 21.4 km from Shackleton | 5.9 km2 | 4.3 km2 |

So a 22 h sortie from a sunlit rim dock fits small PSRs, not the large
named crater floors. This is a finding for area 2 (sortie routes) and for
the target choice (brief p.7). Nothing here says these PSRs hold ice.
Not yet screened: 85 to 87 S, which includes Cabeus (the LCROSS site the
brief cites) and Nobile. Also corrects spec 11.1: the Haworth tile does
overlap western Haworth by about 10 km. (terrain report 20261116-150109)
