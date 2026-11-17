# Changes on `step-1-chain` and `step-5-chain-feed`: what was done, how it was checked, what is left

For Joseph and the integrator, written 2026-11-15 13:05 PST by Kellen's chain
session, before Joseph's full merge. Headings follow `ops/PROTOCOL.md`.

## What was asked

Step 1 of `docs/spec.html`: a contract on Monad testnet that keeps the shared
record of ice finds and enforces the checking rules, with Foundry tests, and
a Python client with the interface in `docs/handoffs.md`. Then, from Kellen:
get the real chain into the demo, a live read of the record on the hosted
page, and a clean record before the demo.

## What was done

Branch `step-1-chain` (17 files against `joseph`, up to date with `joseph`
at `f329203`):

- `contracts/src/Lantern.sol`: agencies fixed at deployment; `postFind`,
  `checkFind` (refused for the finder's own agency, a second check by the
  same agency, a stranger, or a checker farther than 3 × (σ_find + σ_check));
  status disputed if anyone disagrees, confirmed if someone agrees and nobody
  disagrees, else reported; paged getters; `postSafetyZone` (Accords s. 11).
- `contracts/test/Lantern.t.sol`: 25 tests (24 `test_`, one fuzz), the demo's
  own numbers. `forge test`: 25 passed.
- `contracts/deploy.sh`: deploys with agency A's key, verifies on Monad's
  Sourcify, writes `sim/lantern_abi.json` and `sim/lantern_deployment.json`
  (address, block, agencies, explorer, `verified`, `frame`).
- `sim/chain.py`: `post_find`, `check_find`, `get_finds` as in the hand-off,
  metres in and out, refusals decoded before anything is sent
  (`LanternRefused`, also a `ValueError`), `tx_log`, `last_tx`,
  `run_log_chain()` (explorer base URL), `run_scope()` (drop-in for the
  spine's `ChainStandin`: finds scoped to this run, 2 s read cache),
  `post_safety_zone`, `get_zones`, `hash_raw`. Read-only use needs no keys.
- Deployed: Monad testnet, `0x67f9Bedb42C845307cB1Fd6551D07Df64bd3CE98`,
  block 80022710, source verified on Sourcify (exact match).
- `docs/requests/chain-integration.md` and `run_demo.chain.patch`: the
  three-hunk patch for `integration`'s `run_demo.py` (real chain when both
  keys are in `.env`, stand-in otherwise), who posts science, refusal
  handling for Ritvik's outbox, a scene seam, the pre-demo redeploy.
- `docs/requests/chain-review.md`: answers to the lead's six review points.
- `contracts/README.md`: run, verify, and the four-step clean redeploy.
- `.env.example`: the contract address override is optional.
- Hand-off step 1 section expanded; board rows for chain and chain feed.

Branch `step-5-chain-feed` (new files only, cut from `step-1-chain`):

- `web/js/chain.js`, `web/chain.html`, `web/collect_chain.py`, `web/chain/`:
  a read-only reader of the contract in the browser (ethers 6.13.4, pinned
  from jsdelivr like three.js), rendering finds with status and check
  counts, every check, every safety zone, and explorer links from the event
  logs. The wiring note for the viewer owner is at the top of `chain.js`.

## What it was checked against, and what was seen

- `python sim/chain.py demo` on testnet: run 1 confirmed, run 2 disputed, four
  rule-breaking checks refused before sending; receipts and event logs read
  back by RPC (Monadscan refuses automated fetches, so not by page).
- The unmodified spine (`step-4-spine`) with `ChainStandin` swapped for
  `run_scope()`: both runs on chain, confirmed and disputed, 11 s.
- The `integration` spine (`c9c7931`, Calvin's `ParticleNav`) with the patch
  applied: both runs on chain (finds 13, 14), `standins` left as ice_sensor,
  planner, rims. With keys hidden: the stand-in path, fake hashes labelled.
- Ritvik's `push_outbox` with `LanternClient`: a whole mission posted (13
  zones, 6 finds, 2 checks), find IDs written back into his records.
- Full runs with camera frames rendered on this Mac (VTK, 0.1 s a frame),
  real chain, particle filter, display layer: `data/runs/run1-real2` and
  `run2-real2` (finds 17, 18); frames looked at: regolith texture, rocks,
  rim shadows. These are what the hosted site serves after the hosting
  session's swap.
- `web/chain.html` in headless Chrome against the live contract and the live
  URL: 13 finds, 8 checks, 13 zones, 47 linked hashes, no console errors.
  The public RPC allows browser reads (CORS), accepts batched JSON-RPC, and
  caps `eth_getLogs` at 100 blocks; the page chunks accordingly.
- Hosted: https://lantern-teal.vercel.app serves the real-chain runs and
  `/chain.html` (published by Kellen's hosting session, 12:45).

## What I am blind to

- I did not open Monadscan or MonadVision in a browser myself; links are
  built from the deployment record and the hashes come from the chain.
- The frames were judged on three stills per run, not a playthrough.
- Gas is billed by limit on Monad; the margin is 15% over the estimate, not
  measured against a long demo.
- `web/chain.html` was not tried on a phone.

## Where I stopped

- Nothing uncommitted. `step-1-chain` at the merge of `f329203`;
  `step-5-chain-feed` at `915f0da`.
- Not done, by choice: the pre-demo fresh deploy (do it at freeze minus one
  hour, `contracts/README.md` "Before the demo"); the `Contested` status and
  agency voting (review points 2 and 3), only if asked.

## Open decisions, and whose

| Decision | Whose | Where it is written |
|---|---|---|
| Is the chain in the demo? The lead's state (12:31) says chain and hosting are off for now; the hosted site already runs on the real chain | Joseph | `ops/LEADER_STATE.md`, `docs/requests/chain-integration.md` |
| Sigma: worst-axis form (paper-grounded) before real posting | Joseph, Calvin | `docs/requests/chain-review.md` point 1 |
| Which path posts science: the spine's loop (wired) or Ritvik's outbox (tested, off) | Joseph, Ritvik | `chain-integration.md` |
| `LanternSurvey` (results session, `step-7-results`) in the demo or not; must be redeployed after any fresh Lantern deploy | Kellen, Joseph | `contracts/README.md` |
| Ritvik's asks of the chain teammate: deploy his registry contract on testnet; add `bytes32 dataHash` to `Check` | Kellen, Joseph | `docs/CHANGES-ritvik.md` s. 3 |

## What I changed from the brief, and why

- The hosted page reads the chain live (`chain.html`), beyond the spec's
  "dashboard reads the chain": judges can see the record is real, with no
  server and no keys.
- `run_scope()` and the 2 s read cache were added so the spine's per-step
  polling stays under the public RPC's 20 requests per second and rover B
  goes to this run's find, since the contract keeps every find ever posted.
