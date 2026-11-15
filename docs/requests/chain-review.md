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

   Answer:

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

   Answer:

3. **Agencies are fixed at deployment.** There is no way to add a third
   agency. For investability, the pitch needs an answer to "how does a new
   agency join?", for example a vote of the existing agencies. A talking
   point, not demo work.

   Answer:

4. **Positions are in one terrain tile's frame.** `xCm`/`yCm` are south
   polar stereographic metres on the LOLA tile. A shared record between
   agencies needs a common lunar frame, such as latitude and longitude or
   the Moon ME frame. Either note the frame in the deployment record, or
   say so in the pitch.

   Answer:

5. **Test count.** I count 23 `test` functions in `Lantern.t.sol`
   (including one fuzz test). `chain-integration.md` says 25 pass. Foundry is
   not installed on the lead's machine, so I could not run them. Which is
   right?

   Answer:

6. **Two paths to the chain.** Your request names the spine's loop and
   Ritvik's outbox. For the demo, the spine's loop is the story the viewer
   replays. Whether Ritvik's path also posts during the demo is a team
   decision. Please raise it with Ritvik and Joseph rather than wiring both.

   Answer:
