# Request: wire the real chain into the demo

From the chain step (Kellen, branch `step-1-chain`). For the integrator, the lead,
Ritvik and the viewer owner; each part names who it is for.

Checked on 2026-11-15 at 11:55 PST against `joseph` at `85221d9` and
`integration` at `d559259`.

## State of step 1

- Contract deployed on Monad testnet, address and deploy block in
  `sim/lantern_deployment.json`; source verified on Monad's Sourcify (exact
  match). 25 Foundry tests pass. `python sim/chain.py demo` ran the spec's
  "done" check on chain.
- `sim/chain.py` gives the step 1 interface in `docs/handoffs.md`, plus
  `run_scope()`, a drop-in for the spine's `ChainStandin`.
- Verified end to end, twice, without editing anyone's file: the unmodified
  `run_demo.py` with `ChainStandin` swapped for `run_scope()` ran both demo
  runs against the live contract (run 1 confirmed, run 2 disputed), and
  Ritvik's `push_outbox` posted a whole mission (13 safety zones, 6 finds,
  2 cross-agency checks) through `LanternClient`.
- `step-1-chain` is up to date with `joseph` and ready to merge.

## For the integrator: one patch to `run_demo.py`

`docs/requests/run_demo.chain.patch`, three hunks, made against `integration`
at `c9c7931` (the version with `ParticleNav`). After merging `step-1-chain`
into `integration`:

```bash
git apply docs/requests/run_demo.chain.patch
```

What it does:

- Imports `sim.chain` if it can; if not (no web3, no deployment file) the
  spine stays exactly as it is.
- In `simulate()`: when the client can sign for both agencies (both keys in
  `.env`), `chain = lantern.run_scope()`; otherwise `ChainStandin` as today.
- The log's `chain` block comes from `chain.run_log_chain()` (chain ID,
  contract, explorer base URL, contract URL, verified), and the `chain`
  entry leaves `standins` when the chain is real. The viewer then links the
  hashes to the explorer instead of marking them fake (`web/js/data.js`,
  `fakeTx`). Nothing else in the log changes.

Tested on this machine with the patch applied to a scratch checkout of
`integration` (`c9c7931`, Calvin's `ParticleNav` in the loop), 12:25 PST:

| Keys in `.env` | Chain used | Run 1 | Run 2 | Hashes | `standins` left |
|---|---|---|---|---|---|
| both | live contract, finds 13 and 14 | confirmed | disputed | real, linked | ice_sensor, planner, rims |
| none (earlier, pre-ParticleNav spine) | `ChainStandin` | confirmed | disputed | fake, labelled | plus chain |

Run 2's rover A ended with sigma 15.4 m against a true error of 22.4 m,
which is review point 1 (`sqrt(trace/2)` under-reports along the drift
axis); the worst-axis form fixes that in `sim/nav.py`, not here.

What the machine running the demo needs: `pip install web3 python-dotenv`,
and a `.env` with `AGENCY_A_PRIVATE_KEY` and `AGENCY_B_PRIVATE_KEY` (the
throwaway testnet keys, never in the repo; Kellen hands them over in person
if the demo runs on another machine). Each demo run costs about 0.1 MON.
`run_scope()` caches reads for 2 s so rover B's per-step polling stays under
the public RPC's 20 requests per second; a real run takes about 6 s longer
than the stand-in.

`app.py` (branch `integration`): its docstring says the chain is a stand-in
until a contract exists; that line can go once the patch is in.
`python app.py --fresh` then reruns both runs on chain.

## Seam found while rendering on a Mac (for the integrator or the scene owner)

`sim/scene.py` `_load_display()` looks for the display assets only in
`<checkout>/data/terrain/display` and the Windows path, never under
`LANTERN_DATA`, so a checkout that reads the terrain from `LANTERN_DATA`
(the committed `lantern_data/`, or any data dir elsewhere) renders with
"display off" even though `lantern_data/terrain/display/display.json` is
there. One more candidate, `Path(terrain.TERRAIN_DIR) / "display"`, fixes it.
Workaround used here: `ln -s ../../lantern_data/terrain/display
data/terrain/display` in the checkout.

## For the lead: which path posts science to the chain

Two working paths reach the contract now:

1. **The spine's loop** (`run_demo.py`, with the patch): A posts a find when
   its pass over ice ends, B checks it. This is the demo story the viewer
   replays and the run-log asserts check.
2. **Ritvik's record outbox** (`sim/mission.py push`): each science stop
   writes a record (position, route, core profile, verdict) and queues the
   exact chain call; `push_outbox` posts safety zones, finds and checks in
   order, resolving each check's find ID from the find it depends on.

Recommendation: the demo runs post through path 1, because the viewer and
the story are built on those logs. Ritvik's record is the right thing to
stand behind each find's `data_hash`: the hash on chain is keccak256 of the
canonical JSON of his `sample`, so once his science stop is wired into the
spine, the spine should take `data_hash` and `reading` from his record
instead of hashing its own reading list. Both use the same canonical-JSON
rule (`sim/chain.py` `canonical_json`, mirrored in `sim/science.py`), so
anyone holding the record can re-hash it and match the chain. Your call.

## For Ritvik: record a refusal instead of aborting

`sim/chain.py` raises `chain.LanternRefused` when the contract would refuse
a call (`OwnFind`, `AlreadyChecked`, `TooFar`, `NoSuchFind`, `NotAnAgency`);
nothing is sent. It is also a `ValueError`. In `sim/mission_store.py`
`_post_one`, a refusal should be a recorded outcome, not a crash of the
whole push:

```python
    try:
        result = getattr(client, it["call"])(**args)
    except ValueError as e:                       # chain.LanternRefused is one
        it.update(status="refused", result=None,
                  refused=f"{getattr(e, 'name', 'Refused')}{getattr(e, 'values', ())}")
        store.save_outbox(items)
        done.append(it)
        return
```

A refused check is worth showing: it is the contract enforcing the rules.
Also: `reading` is stored on chain in hundredths of wt%, so 4.292 comes back
as 4.29.

## For the viewer owner

- `chain.explorer` in the log is the explorer's base URL, as `web/README.md`
  asks. The block also carries `contract_url` and `verified` ("sourcify").
- A live, read-only chain reader for the page is being built from
  `docs/briefs/chain-feed.md` on branch `step-5-chain-feed`: new files only
  (`web/js/chain.js`, `web/chain.html`, `web/collect_chain.py`), with a
  wiring note for you. Nothing of yours is edited.

## Before the demo

The contract keeps every find ever posted, including our tests (13 finds at
the time of writing). `contracts/README.md`, "Before the demo", has the
four-step redeploy for a clean record starting at find #0.
