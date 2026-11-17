# Storage: what goes on chain, what goes in evidence, what stays in the cache

Branch `ritvik`. Files: `sim/evidence.py`, `sim/mission_db.py`,
`sim/mission_store.py`. Nothing in the deployed contract or the chain client
changes.

## The rule

Every byte lives in the one layer that is good at it.

| Layer | Where | Holds | Authority? |
|---|---|---|---|
| A. chain | Lantern contract on Monad testnet (`sim/chain.py`) | per find: agency, x, y, sigma, reading, **dataHash**, time (88 bytes of fields); checks; safety zones | yes, for who posted what and when |
| B. evidence | `data/evidence/objects/<cid>.json`, optionally an IPFS node | one **report** per science stop and one **route** per report, immutable JSON named by its hash | yes, for what was measured (checked against A) |
| C. cache | `data/lantern.db` (SQLite) | telemetry, events, survey readings, reports index, outbox with retry state, an index of the chain's finds | no: rebuildable from A and B |

Never on chain: routes, measurement profiles, telemetry, survey readings,
terrain. Never in evidence: the hidden truth (it stays in the mission folder
for the viewer's labelled reveal).

## How a find's 32 bytes point at its evidence

The report is serialised one way only (sorted keys, no spaces, UTF-8, the
same rules as `chain.canonical_json`). Its keccak256 is the find's on-chain
`dataHash`. The report's IPFS address is a CIDv1 built from that same
digest: codec raw (0x55), multihash keccak-256 (0x1b). So:

```
chain dataHash 0x7192ce79...  ->  CID bafkrwidrslhhsmcf...  ->  report  ->  refs.route  ->  route
```

Anyone with only the contract can rebuild the CID, fetch the report, re-hash
it, and see it matches. The contract needs no CID field; its existing
`bytes32 dataHash` is both the integrity check and the pointer.

`python sim/mission.py verify` walks exactly that path for every find.

## What a report holds

`lantern.report/1`: mission id, report id, site id and source, rover,
observation type (survey drill, or check of another agency's find),
`simulated: true`, sim time, position (world metres, lat/lon, sigma, and
that it is the rover's estimate), measurements with units (water by depth,
max, ilmenite, regolith class), verdict, reading, ice top, a confidence
(margin over the threshold in units of instrument noise), drill attempts,
refs (route CID, parent report CID for a check), data sources. About 1.5 KB.

A check is a new report that names the find's report as its parent. It
never edits the original.

## Efficiency, measured on one mission (2 rovers, 14 science stops)

| | Before (all JSON files) | Now |
|---|---|---|
| route points stored | 6064 (every 0.5 m step) | 325 (thinned within the rover's own sigma) |
| evidence | routes inline in every record | 28 objects, 31 KiB; identical content stored once |
| telemetry, events, survey | JSON Lines files, read by scanning | SQLite, indexed by mission, rover and time; one transaction per leg |
| on chain per find | 88 bytes of fields | 88 bytes of fields (unchanged); the CID costs nothing extra |

Routes are thinned with Douglas-Peucker to the larger of 0.25 m and the
rover's own position sigma: the estimate wanders by about sigma anyway, so
finer detail is noise.

## Posting safely

`push_outbox(store, LanternClient())`:

1. Reads the contract's finds, checks and zones first, and refreshes the
   cache's index of that contract.
2. Skips a find whose dataHash is already on chain, a check whose (find,
   agency) is already on chain, and a zone whose purpose (which names its
   report's CID) is already on chain.
3. Posts zones, then finds, then checks; a check gets its find's real id and
   waits a pass if its find was queued later (its rover was out of contact).
4. A failed call is marked `failed` with its error and retried next push.
5. Updates the cache after every call, so a crash loses nothing.

Tested against a fake chain with the client's signatures and refusal rules:
after a simulated crash that wiped the cache's statuses, a second push sent
0 transactions.

## Route reuse (the checking rover)

When a rover goes to check another agency's find, it fetches the finder's
route by CID (verified), checks every waypoint and segment against its own
slope map, drops blocked waypoints, joins where cheapest, patches blocked
segments with local plans, and falls back to its own direct plan if reuse
would be a detour. It never follows the old route blind. Logged as a
`route_reuse` event.

## Not done, or not real

- **IPFS**: the local store computes the same CIDs IPFS would (checked
  against IPFS's own CID for the empty file), but no node was available, so
  the Kubo upload path (`IPFS_API`) is written and untested. Local evidence
  is labelled "local, not pinned".
- **Chain**: tested against a fake client only. `python sim/mission.py push`
  posts for real once `sim/chain.py` (branch `step-1-chain`) and `.env` are
  present.
- **Checks on chain have no hash** in the current contract. A check's report
  is linked to its find's report off-chain. Ask for the contract owner: a
  `bytes32 dataHash` on `Check` would anchor check reports the same way.
- Gateways may not serve keccak-256 CIDs; a local Kubo node does.
