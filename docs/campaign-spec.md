# Lantern for RASC-AL: campaign simulator spec

Draft, 16 Nov 2026. Not approved. Once approved, this replaces `docs/spec.html`
as the source of truth for scope, and `CLAUDE.md` is updated to say so.

## TL;DR

**The workflow.** Lantern simulates the paper's Scout workflow. Two to five
identical Scouts share one dock and drive sorties into a shadowed crater. They:

- map ice;
- check each other's finds;
- add newly seen craters to a shared map;
- get home on one battery charge.

**The runs.** Thousands of random worlds are generated and every design choice
is run on the same worlds. All runs are saved without 3D drawing. The paper's
numbers and Figure 1 come from them. Position error is sampled from ShadowNav's
published results.

**The site.** It lists the runs, shows each run's map growing, and replays any
sortie in 3D.

**What goes.** The chain, Monad and agency code is archived.

**The team.** Four parts are built at the same time.

## 1. Intent

Lantern simulates the PROSPECT Scouts' proposed workflow for the RASC-AL 2027
paper, research area 3. Between two and five identical Scouts work from one
charging dock.
They drive sorties into a dark crater. On each sortie they map ice and log every
reading with its position and how unsure that position is. Each find is checked
by another Scout. All add newly seen craters to a shared map, so later sorties
navigate better. Every sortie must get back to the dock on its battery.

Lantern runs this workflow thousands of times with random variation. It saves
every run so any one of them can be replayed.

**Who uses it, start to finish:**

1. **The paper team** runs a batch of campaigns with one command. They get a
   table of results and the paper's Figure 1. Every number in section 4.6
   comes from here.
2. **A judge or viewer** opens the hosted site. They see the list of runs and
   their results, and click one to see its top-down map. From there they press
   play to watch a sortie as a 3D time-lapse. The same site is the Phase 2
   virtual prototype, and the 2-minute video is recorded from it.

**What it is not:**

- **It is not a source of navigation accuracy figures from Lantern's own
  camera vision.** That vision uses a perfect crater map and the renderer's
  perfect depth, so its errors come out too small. The paper's position
  errors are sampled from ShadowNav's published results (section 4).
- It has no blockchain, Monad, agencies or contract.

## 2. Words used here

| Word | Meaning |
|---|---|
| Run | One campaign with one world and one set of design choices: all Scouts, all their sorties, start to finish. The unit that is saved, listed and replayed. |
| Sortie | One round trip from the dock: descent, survey, return. One battery charge. |
| Position check | Matching seen crater rims to the map to reset the position error (a "fix" in the paper). |
| Find | A reading above the ice threshold. It is a candidate deposit until another Scout checks it. |
| Confirmed | Another Scout found a matching reading inside the search area. |
| Wrong match | A position check that pairs a rim with the wrong mapped crater (a "false match" in the paper). |
| Sampled navigation | Position error drawn from ShadowNav's published numbers. Used for every batch run. |
| Camera navigation | Lantern's existing renderer, rim detection and particle filter. Used only for the 3D showcase. |

## 3. The workflow Lantern simulates

These are the paper's rules (draft section 4). Every constant lives in one
settings file. Each one carries its source as `file/section`, or is marked
`assumed` with a default.

### 3.1 Campaign

- **Fleet.** One dock and `n_scouts` Scouts. The brief's baseline is "two to
  three Scouts sharing one charging dock", and its 2030 plan adds two more, so
  batches cover 2 to 5. A single quick run uses 3.
- **Sorties.** Each Scout makes `sorties_per_scout` sorties. The default is 16,
  one month, from the brief ("roughly 16 sorties a month").
- **Dock queue.** The dock charges one Scout at a time, 3 h each (brief).
  Scouts that arrive while it is busy wait, and the wait is logged.
- **Surveyor.** The brief's Surveyor works in sunlit terrain and at PSR edges
  (brief, "Layer 1"), so it is out of scope.
- Between sorties the dock merges what all Scouts saw (3.5) and plans the
  next sortie (3.4).
- Contact with Earth can drop for up to 11 days (brief). The Scouts follow only
  the fixed rules here, so nothing waits on Earth. The run log marks the
  contact gaps.

### 3.2 Sortie

- The legs are descent, survey lines, then return. The baseline is 1.5 km of
  descent, 4 km of survey with up to 2 drill sites, and the return, about 7 km
  in 22 h (brief).
- Top speed is a setting. The default is 15 cm/s (draft 4.1, team decision
  open).
- Energy covers driving, lamp, computer, instruments and drilling. Battery
  energy is a setting. The 270 W versus 205 W question is team decision 3, so
  both are settings.
- **Turn-back rule.** The Scout turns for home when its remaining energy
  reaches what it needs to drive back with a 25% reserve (brief). A Scout that
  arrives below the reserve marks a failed sortie. One that cannot arrive at
  all is lost, and the run continues with one Scout.

### 3.3 Navigation

- **Drift.** Odometry drifts by a share of distance driven. The default is 2%
  (ShadowNav sec. 1), varied from 1% to 4% across runs.
- **Check scheduling.** Position checks are scheduled by distance driven. The
  spacing comes from the drill-site limit and the expected error after a check
  (draft Table 1). Policies to compare:
  - fixed spacing;
  - spacing from the 95th-percentile error;
  - checks only before readings and drilling.
- **Check cost.** A check takes `check_stop_s` stationary. The default is 300 s,
  assumed, swept from 0 to 600. Whether ShadowNav needs a stop at all is open
  (future-work item 11).
- **Error after a check, sampled navigation:**
  - Each check is a wrong match with probability `p_wrong`.
  - A wrong match draws its error from ShadowNav trajectory 1: 3.84 ± 2.78 m
    (Table 2).
  - Otherwise the error comes from trajectories 2 and 3 pooled: about
    1.7 ± 0.7 m (Table 2).
  - `p_wrong` grows with the share of nearby craters missing from the Scout's
    current map. That link is `assumed`: the two ShadowNav cases are its only
    anchor points, and the model is listed as such in the paper.
- **Heading.** A star tracker removes heading drift (draft 4.6). It is a
  setting so it can be turned off.
- **Dock position error.** Each run draws one registration error for the dock.
  It is shared by every sortie and every Scout, and is never seen by them.
  Without it, the cross-check between Scouts looks better than it is.

### 3.4 Survey and ice

- **Survey lines.** The dock plans each sortie's lines to cover ground not yet
  mapped. Lines avoid ground steeper than 15° (existing planner rule).
- **Hidden ice.** Each run places hidden deposits at random. The number, size
  and strength are settings. The Scouts never see the hidden truth.
- **Readings.** The neutron sensor takes a reading every `reading_spacing_m`.
  Its footprint and noise are settings. Each reading is stored with the
  Scout's position estimate and its 95th-percentile position bound.
- **Finds.** A reading above the threshold becomes a find.

### 3.5 Cross-checks (the paper's two contributions)

**Ice finds:**

1. The next Scout whose sortie passes near the find searches around it. It is
   never the Scout that made the find. The search radius is set by both
   Scouts' position bounds.
2. A matching reading marks the find confirmed. No match marks it unconfirmed.
   Lantern also records whether the deposit is really there, so false
   confirmations and missed deposits can be counted.
3. Drilling goes only to confirmed finds, up to 2 per sortie.

**Craters:**

1. Each Scout logs every rim it saw but could not match: estimated position
   and size.
2. At the dock, sightings from different sorties and Scouts are compared.
3. A crater joins the shared map only after `sightings_to_add` independent
   sightings agree within a tolerance. Both are settings (item 12).
4. Added craters carry the error of the Scouts that saw them. Craters added in
   the wrong place raise `p_wrong` later, so bad additions hurt, as they would
   in reality.

## 4. Random worlds and design choices

Two kinds of things vary, and they vary differently.

- **The world varies at random, from a seed.** This covers what nobody
  controls: terrain, ice, drift, wrong matches and failures (table below).
- **Design choices are swept on purpose.** Every choice runs on the same set of
  worlds. A batch might be 1,000 worlds, each run with every fleet size from 2
  to 5. Any difference in results then comes from the choice, not from one
  group getting easier ground.

The design choices swept are:

- fleet size;
- position-check policy;
- crater-map refinement on or off;
- top speed;
- stop time per position check;
- the drill-site limit (5 or 10 m).

A run is defined by its world seed plus its design choices, so any run can be
redone exactly.

What varies at random in each world:

| What varies | How | Source |
|---|---|---|
| Terrain patch | A window of real LOLA terrain that covers PSR floor plus a rim for the dock (see 11.1) | LOLA |
| Small craters | Stamped at a random density, existing method | `sim/terrain.py`, assumed density range |
| Craters missing from the orbital map | Every crater smaller than the map resolution is left out of the Scouts' starting map, plus a random share of larger ones | assumed |
| Hidden ice | Count, position, size, strength | assumed |
| Drift | 1% to 4% | ShadowNav sec. 1, range assumed |
| Wrong-match model | Within its stated range | assumed |
| Dock position error | One draw per run | assumed, item 15 |
| Sensor failure | Probability per sortie for the camera, lamp and neutron sensor | assumed |

## 5. What a run saves

### 5.1 Run record

One file per run, replayable. It extends the existing run-log format in
`docs/handoffs.md` ("Run log") rather than replacing it, so the current 3D
viewer keeps working. Contents:

- The settings, the seed and the terrain window.
- Per sortie, per Scout, a pose every `log_spacing_m`: true position,
  estimated position, 95th-percentile bound, energy, and what the Scout is
  doing.
- Events:
  - readings and finds;
  - search started and find confirmed or not;
  - drill;
  - position check, with wrong match yes or no;
  - crater sighted and crater added;
  - turn back, dock arrival, failure;
  - contact lost and regained.
- The crater map and ice map as they stand after each sortie, so the viewer
  can show them growing.
- The hidden truth (ice and missing craters), labelled as truth, never shown
  as something a Scout saw.

### 5.2 Run summary

One row per run:

| Group | Fields |
|---|---|
| Sorties | Completed with the reserve; failed; Scouts lost |
| Position | 95th-percentile error at drill sites; position checks per sortie, by sortie number; wrong-match rate, by sortie number |
| Ice | Finds; confirmed; false confirmations; deposits missed |
| Coverage and time | Area surveyed; time split into driving, checks, readings, drilling and waiting |

### 5.3 Batch outputs

- **Figure 1a.** Share of sorties completed with the reserve, and drill-site
  error, against crater density, one line per check policy.
- **Figure 1b.** Wrong-match rate and checks per sortie against sortie number,
  with and without crater-map refinement.
- **Fleet table.** Area surveyed, confirmed deposits and dock waiting time per
  month, for 2 to 5 Scouts.
- **Site table.** The same results for each candidate PSR. The team has not
  chosen its target PSR yet (brief, "Which PSR and which rim site do we target
  first?"), and this table feeds that choice.

## 6. The viewer

It builds on the existing site in `web/` (hosted on Vercel) and its 3D world
(`web/js/world.js`).

1. **Runs list.** Every hosted run with its setup and results, sortable and
   filterable. The hosted set is a chosen subset, so the site stays small; the
   full batch stays local.
2. **Top-down map**, opened by clicking a run:
   - sortie paths;
   - the ice map as circles sized by position bound, coloured as find,
     confirmed or drilled;
   - the crater map, with added craters marked;
   - the dock;
   - a slider through the sorties to watch both maps grow;
   - a time bar per sortie showing where the 22 h went;
   - the hidden truth behind a labelled reveal button.
3. **3D replay.** Pick a sortie and press play. It is a time-lapse of the
   saved poses in the existing 3D world, with speed control. Nothing is
   re-simulated, so what is shown is exactly what was counted.
4. **Showcase run.** One run is made with camera navigation and pre-rendered
   first-person frames. This is the run for the video. The page says plainly
   that its navigation is Lantern's own and is not the source of the paper's
   numbers.

## 7. Changes to existing code

| Remove (moved to `_archive/`, not deleted) | Keep and extend | Fix (camera navigation realism, future-work items 22–24) |
|---|---|---|
| `contracts/`, `registry/`, `survey/` | `sim/terrain.py`: windows anywhere in the 20 km tile and Haworth; larger windows for 7 km sorties | Starting crater map misses small craters |
| `sim/chain.py`, `registry_client.py`, `survey.py`, `results.py`, ABI and deployment files | `sim/rover.py`: the planner and drive | Filter's noise model guessed, not copied from the truth (`sim/nav.py:47-48`) |
| Chain pages and chain JS in `web/` | `sim/nav.py`, `vision.py`, `scene.py`: camera navigation for the showcase | Camera at 1.6 m with the lamp below (brief) |
| Agency, Monad and hackathon wording | `sim/ice.py`, `sim/mission.py`, `roles.py`: reused where they fit, rewritten where roles differ (all Scouts are identical) | Terrain windows not chosen for gentle slopes |
| | `web/`: runs list, top-down map, replay | Top speed from settings |

Work happens on new branches from `main` (`337f247`). `main` takes merges only
when the team lead says so.

## 8. Speed targets

These are assumed, to be checked on a laptop in the first week:

| Job | Target |
|---|---|
| One run, sampled navigation, 3 Scouts × 16 sorties, no rendering | Under 1 minute |
| A batch of 1,000 runs, all CPU cores | Overnight |
| One showcase run with camera navigation and rendered frames | Hours |

To get there:

- Sampled navigation skips the particle filter and the renderer.
- Planning uses a 5 m grid, the LOLA spacing, for long legs.
- The existing 2 m planner is kept for local steering only.

## 9. What done looks like

1. One command runs a batch and writes the run records, the summary table and
   Figures 1a and 1b.
2. Every constant in the settings file has a source or an `assumed` label.
3. Re-running a run from its seed gives the same record.
4. On the hosted site you can list the runs, open one and see its map grow
   sortie by sortie. You can replay a sortie in 3D and reveal the hidden truth.
5. The showcase run plays with first-person frames and is labelled as camera
   navigation.
6. No chain, Monad or agency wording remains anywhere a viewer can see.
7. Someone has looked at the real outputs: the figures, a replay and the
   hosted page. A passing exit code doesn't count.

## 10. Team split (after approval)

The record format (5.1) and the settings file are written first, together, and
added to `docs/handoffs.md`. After that the parts can run in parallel:

| Part | Files | Done when |
|---|---|---|
| 1. Campaign engine | New `sim/campaign.py`, `sim/settings.py`; batch runner | A run produces a record and a summary row with sampled navigation |
| 2. Cross-checks and map refinement | New `sim/crossing.py` (ice search, crater merge) | Find confirmation and crater adding work inside part 1's loop |
| 3. Terrain and realism | `sim/terrain.py`, `sim/nav.py`, `sim/scene.py` | Terrain covering PSR floor and rim at 2 or 3 candidate craters (11.1 first); missing-crater map; camera-navigation fixes |
| 4. Viewer and cleanup | `web/`, removal of chain code | Runs list, top-down map and replay work on saved records |

Lead: integration, the batch, Figures 1a and 1b, and the final look at the
hosted site.

### Rules for the leader and the team

- **Project rules.** Follow `CLAUDE.md` and the global rules:
  - one branch per part, from `main`;
  - nothing merges to `main` without the go-ahead;
  - no secrets in the repo;
  - never broad-revert;
  - no commit message names a person.
- **Workers.** Each part runs in its own session. At most 4 run at once, and
  no worker starts further agents.
- **Sources.** A number taken from ShadowNav or the brief carries its source in
  a comment. Anything else is a named setting marked `assumed`.
- **Hand-offs.** A change to the record format or settings is made in
  `docs/handoffs.md` in the same commit, and the other parts are told.
- **Checking.** Look at real output before calling a part done: a run record,
  a figure, the page. Write no new test suites.

## 11. Open questions the spec does not settle

### 11.1 Terrain: first task of part 3

The LOLA tiles in `data/lola/` are not shadowed crater floor:

- **They are landing-site tiles.** The README says they cover "candidate South
  Pole landing sites", and landing sites are chosen for sunlight.
- **They sit outside the craters they are named for.** In the tiles' own
  coordinates, the Shoemaker tile's centre is about 35 km from Shoemaker
  crater's centre. The crater's radius is about 25 km. Haworth gives the same
  result. This assumes my reading of the map projection is right, so it is
  UNVERIFIED.

Part 3 starts here:

1. Confirm the tiles' coordinates.
2. Find a public LOLA elevation model that covers a PSR floor and its rim. A
   20 m per pixel south-pole mosaic is the likely source (UNVERIFIED).
3. Find a published map of permanent shadow.
4. Build terrain windows for 2 or 3 candidate PSRs.

Until then, the engine and viewer develop on the existing Shoemaker tile.

### 11.2 Numbers waiting on the team

| # | Question | Status |
|---|---|---|
| 2 | Average speed: 7.4 cm/s (team baseline) or 8.8 cm/s (7 km in 22 h). Top speed 15 cm/s? | Team decisions 1 and 2 |
| 3 | Does a position check need a stop, and for how long? | Item 11; swept until known |
| 4 | Drill-site limit (5 or 10 m), deposit size, neutron footprint | Item 4, area 1 |
| 5 | How many months a campaign covers | The brief gives 16 sorties a month; the mission length is the team's call |
| 6 | Power: 270 W or 205 W | Team decision 3 |
