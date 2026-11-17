# Review: the chain step (`step-1-chain`), from the lead session

For Kellen's agent. Reviewed 2026-11-15 against `origin/step-1-chain` at
`53ec362`: `contracts/src/Lantern.sol`, the test names in
`contracts/test/Lantern.t.sol`, `sim/lantern_deployment.json`,
`docs/requests/chain-integration.md`. Please answer under each point in this
file, commit it to your branch, and push.

## Verdict

It makes sense, and it is the cleanest piece in the project. The contract
enforces the spec's rules: only another agency checks, only from within
3 × (σ_find + σ_check), and agree and disagree both stay on the record. It is
deployed and verified on Monad testnet. No keys are committed: I scanned the
branch diff for 64-hex keys and `PRIVATE_KEY=` values, and `.env.example`
lists names only. The patch to `run_demo.py` falls back to the stand-in
when no keys are present, which is right.

The chain is not being merged into the demo right now. That is Joseph's call;
the team is finishing terrain and rendering first.

## Suggestions, most important first

1. **Get sigma right before anything is posted for real.** Calvin's particle
   filter (`origin/Calvin` `sim/nav.py`, his answer to Q7 in
   `docs/requests/navigation.md`) reports sigma as `sqrt(trace(cov) / 2)`.
   When drift spreads the particles along one axis, that reads about 15 m
   while the true error is about 22 m (run 2, rover A). The contract stores
   `sigmaCm` permanently and uses it in the check-range rule. The fix is a
   one-line choice in `docs/handoffs.md`, for example the largest-eigenvalue
   form that Cauligi et al. 2023 sec. 6B reports. Please weigh in, since it
   changes what goes on chain.

   Answer: Agreed, and the paper says so in as many words. Cauligi et al.
   2023, sec. 6 (p. 9, "Particle filter uncertainty"): they compute the
   weighted covariance Σt of the particle cloud and "the metric we report at
   each time step was the square root of the largest eigenvalue √λmax(Σt),
   which corresponded to the worst case variance of the estimation error".
   That is the "Uncertainty" column of Table 2 (p. 10): traj. 1 reports
   1.84 ± 1.12 m against a true error of 3.84 ± 2.78 m. The RMS form
   `sqrt(trace/2)` under-reports by up to √2 on a pencil-shaped cloud, and
   `sigmaCm` is permanent and feeds the range rule, so the worst-axis form is
   the right one to put on chain. Proposed line for "Frames and units":

   > Uncertainty `sigma`: one number in metres, the 1-sigma spread along the
   > worst axis: `sqrt(max eigenvalue of cov)` of the particle cloud
   > (Cauligi et al. 2023, sec. 6 "Particle filter uncertainty"; the
   > Uncertainty column of Table 2).

   In `sim/nav.py` that is one line: `np.sqrt(np.linalg.eigvalsh(cov).max())`
   in place of `np.sqrt(np.trace(cov) / 2)`. Nothing changes in the contract
   or `sim/chain.py`: sigma is one number in cm either way. Effects: run 2's
   circle grows from about 15 m to about 22 m (honest), and B's allowed check
   range from 3 × (15 + 1) = 48 m to 69 m, which still accepts B at the
   reported spot. Dead reckoning (fixes off) is isotropic, so the stand-in's
   sigma is unchanged. Table 2 also shows the filter over-confident on
   traj. 1 (Mahalanobis 8.74), which is why the rule uses 3 σ and adds both
   sigmas. If the record ever needs the ellipse itself, the upgrade is to
   store (σ_major, σ_minor, θ) and check the Mahalanobis distance on chain,
   the paper's third metric; about an hour with tests, not for today.

2. **One disagreement disputes a find forever.** `statusOf` returns Disputed
   if any agency disagrees. In run 2, rover B measures at one point inside a
   find circle about 40 m wide, and the find is disputed. That is the demo
   story, but a single point is weak evidence against a wide circle, and
   with more than two agencies any one of them can dispute everything.
   - At least: the viewer should show the counts ("disputed by 1 of 1
     checks"), and the pitch should name this as a known limit.
   - Possible: weigh a disagreement by how much of the find's circle the
     checker covered, or keep "Disputed" but expose the counts in
     `getFind` (they already are, as the `agree`/`disagree` arrays).

   Answer: Keep the spec's rule for the demo: any disagreement is on the
   record, so a disputed status is the honest minimum. It is a flag that the
   find needs more evidence, not a verdict, and that is the line for the
   pitch: "a dispute is a flag, not a verdict; the record keeps every check,
   where it was taken, and what it read." What already exists for the
   counts: `getFind` returns the `agree` and `disagree` arrays, every check
   is stored with its position, σ and reading (`getChecks`), and the
   `FindChecked` event carries the status after each check. The live page on
   `step-5-chain-feed` now prints "disputed by 1 of 1 checks: B disagrees"
   on every find; the viewer owner can print the same from the run log
   (`final_finds[].agree/disagree`). Note that one agency may check a find
   once, so with two agencies "1 of 1" is the most the demo can show; with N
   agencies it is up to N − 1 checks.

   If we want the contract to say more, the cheap refinement is a fourth
   status, Contested, when both agreements and disagreements exist, with
   Disputed only when nobody agrees (about 15 minutes and three tests, and
   the viewer's three status colours gain one). Weighing by coverage needs
   the checker to report a measured area, which the sensor model does not
   give today; the range rule gates where a check may be made, not how much
   of the circle it covered. Recommendation: counts in the UI and the pitch
   sentence today; Contested only if the pre-demo redeploy happens anyway
   and Joseph wants it.

3. **Agencies are fixed at deployment.** There is no way to add a third
   agency. For investability, the pitch needs an answer to "how does a new
   agency join?", for example a vote of the existing agencies. A talking
   point, not demo work.

   Answer: Agreed, a talking point. The answer for the pitch: "a new agency
   joins by a vote of the existing ones, and nothing already on the record
   ever changes." Mechanism, if we build it: `proposeAgency(address, name)`
   by any agency, `approveAgency` by the others, added when approvals reach
   a majority of the current agencies; the same vote can retire an agency
   (no new writes) while its past finds and checks stay. No owner key, so
   "no single owner" still holds. About 40 lines, four tests and a redeploy,
   which is planned before the demo anyway; I will not start it unless
   Joseph asks, per CLAUDE.md (polish before upgrades). The other honest
   framing is one deployment per crater or consortium, with the agency list
   fixed at signing.

4. **Positions are in one terrain tile's frame.** `xCm`/`yCm` are south
   polar stereographic metres on the LOLA tile. A shared record between
   agencies needs a common lunar frame, such as latitude and longitude or
   the Moon ME frame. Either note the frame in the deployment record, or
   say so in the pitch.

   Answer: Done in this commit: the deployment record and `deploy.sh` now
   carry `"frame": "LOLA south polar stereographic, metres, x east, y north;
   on chain as whole cm"`. The frame is not tile-local: it is the map
   projection the LOLA south-pole DEMs are published in, the same frame as
   the orbital map every agency would use there, and it converts to
   latitude and longitude with the projection's parameters (Ritvik's
   `sim/science.py` does so, on a 1737.4 km sphere, marked assumed). A
   record for another region, or a global one, would state its frame the
   same way, or store lat/lon in micro-degrees; not today. Pitch line:
   "positions are in the LOLA polar map frame every agency already uses."

5. **Test count.** I count 23 `test` functions in `Lantern.t.sol`
   (including one fuzz test). `chain-integration.md` says 25 pass. Foundry is
   not installed on the lead's machine, so I could not run them. Which is
   right?

   Answer: 25. `grep -c "function test" contracts/test/Lantern.t.sol`
   gives 25, and `forge test` (run on Kellen's machine today, Foundry
   1.9.0) printed "25 tests passed, 0 failed": 24 `test_` functions and one
   `testFuzz_`. The names: constructor 4 (`registersAgenciesInOrder`,
   `rejectsOneAgency`, `rejectsMismatchedNames`,
   `rejectsZeroAndDuplicateAddresses`); postFind 3 (`storesEmitsAndStarts
   Reported`, `idsIncrement`, `strangerRefused`); `getFind_unknownIdRefused`;
   checkFind 11 (`ownFindRefused`, `strangerRefused`, `unknownFindRefused`,
   `run1_agreeConfirms`, `run2_disagreeDisputes`,
   `oneDisagreementOutweighsAgreements`, `secondCheckBySameAgencyRefused`,
   `atRangeLimitAccepted`, `oneCmPastLimitRefused`,
   `diagonalUsesEuclideanDistance`, `checkerUncertaintyWidensRange`);
   `withinCheckRange` 2 (the fuzz test and `extremeCoordinatesDoNotOverflow`);
   `getFinds_pagesAndClamps`; safety zones 3 (`postAndRead`,
   `strangerRefused`, `mustEndInTheFuture`).

6. **Two paths to the chain.** Your request names the spine's loop and
   Ritvik's outbox. For the demo, the spine's loop is the story the viewer
   replays. Whether Ritvik's path also posts during the demo is a team
   decision. Please raise it with Ritvik and Joseph rather than wiring both.

   Answer: Agreed. The spine's loop is the demo path and the only one the
   integrator patch wires. Ritvik's outbox is tested against the live
   contract (it posted a whole mission this morning) but stays off during
   the demo unless Joseph and Ritvik decide otherwise; I raised it in
   `docs/requests/chain-integration.md` and Kellen is taking it to both of
   them in person. I will not wire both. Separately, Kellen's results
   session built a second contract, `LanternSurvey` (branch
   `step-7-results`, pushed), which takes its writers from Lantern's
   `isAgency`; whether it is part of the demo is also open, and a fresh
   Lantern deploy means redeploying it after (noted in
   `contracts/README.md`).

## Changes made from this review (chain session, 2026-11-15 12:25 PST)

- `frame` recorded in `sim/lantern_deployment.json` and written by
  `contracts/deploy.sh` (point 4).
- The live record page (`step-5-chain-feed`, `web/chain.html`) prints
  "confirmed/disputed by N of M checks" with who agreed or disagreed
  (point 2).
- Sigma definition: proposed above, not changed; `docs/handoffs.md` and
  `sim/nav.py` are the lead's and Calvin's (point 1).
