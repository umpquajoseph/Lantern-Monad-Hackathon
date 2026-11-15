# Brief: the live chain feed (`web/js/chain.js`)

You own a read-only, browser-side reader of the Lantern contract on Monad
testnet, so the hosted viewer can show that the record is real: finds,
checks and safety zones read straight from the chain, not replayed from a
log. Read `CLAUDE.md`, `docs/spec.html` (steps 1 and 5), `docs/handoffs.md`
(step 1 and the run log), `web/README.md` and `contracts/README.md` first.

## What it is for

The web viewer (`web/`, Joseph's `viewer` agent, merged into `joseph`) is
the page judges open from the demo link. It replays run logs. Its chain
panel (`#chain-hint`, `#feed` in `web/index.html`, drawn by `web/js/main.js`)
shows the transaction hashes recorded in the log, linked to the explorer.
Nothing on the page reads the chain itself. "The map lives on Monad" is
claimed, not shown.

A live read closes that gap: a judge sees the finds the rovers posted,
their status, who checked them, and the safety zones active right now, all
coming from the contract while they watch.

## Done means

- A new ES module `web/js/chain.js` that reads the contract with plain
  JSON-RPC `eth_call` (or ethers v6 pinned from a CDN, the way the viewer
  pins three.js in `web/index.html`): `agencies()`, `findCount()`,
  `getFinds(0, n)`, `zoneCount()`, `getZones(0, n)`, decoded with the ABI.
  Units as `sim/chain.py` converts them (`_find_dict`, `get_zones`):
  centimetres to metres, hundredths to wt%, the status enum to `reported`,
  `confirmed`, `disputed`, and agency address to letter by its index in
  `agencies()` (A is 0, B is 1).
- It never hardcodes the address or RPC. It reads them from copies of
  `sim/lantern_deployment.json` and `sim/lantern_abi.json`: write
  `web/collect_chain.py` to copy the two files into `web/chain/`, and have
  the module fetch them from there with relative paths (the site is static,
  relative paths only, see `web/README.md`). The contract will be
  redeployed shortly before the demo for a clean record, so re-running the
  copy script must be all it takes.
- Polling stays under the public RPC's 20 requests per second: one
  `findCount` and one `zoneCount` per poll, every few seconds; fetch the
  pages only when a count changes. Show the block number or time of the
  last successful read.
- A standalone page `web/chain.html` that renders the live record using the
  viewer's own stylesheet `web/app.css` and classes (`.panel`, `.panel-head`,
  `.overline`, `.find`, `.status.reported/.confirmed/.disputed`, `.ev`,
  `.tx`, `.c-A`, `.c-B`): finds with status badges and who agrees or
  disagrees, zones active now, and links to the explorer (`explorer` in the
  deployment file; `<explorer>/address/<contract>` for the contract,
  `<explorer>/tx/<hash>` for transactions). Per-find transaction hashes are
  not stored in the contract; if time allows, get them from `eth_getLogs`
  on the `FindPosted` and `FindChecked` events (`FindChecked` also carries
  the status after the check, so the feed can say "B agreed, now
  confirmed").
- A short note at the top of `chain.js` telling the viewer owner how to
  wire it into `index.html`: for example a "Live on Monad testnet: N finds,
  M confirmed, K zones active" line under `#chain-hint`, or a replay/live
  toggle on the feed. You do not make that change yourself.
- You have opened `web/chain.html` in a browser (`python web/serve.py`,
  then http://localhost:8000/chain.html) against the live contract and
  looked at it. At 11:50 PST the contract held 11 finds and 13 zones from
  our tests; the numbers will have grown. Say exactly what you saw.

## What is known

- Contract: `sim/lantern_deployment.json` (address, chain ID 10143, RPC,
  explorer, deploy block, the two agency addresses). ABI:
  `sim/lantern_abi.json`. The Python client `sim/chain.py` is the reference
  for every call and conversion; mirror it.
- Source is verified on Monad's Sourcify (exact match).
- First check whether the public RPC (`rpcUrl` in the deployment file)
  answers a browser `fetch` from localhost (CORS). If it does not, the Monad
  docs list other testnet RPCs: https://docs.monad.xyz/developer-essentials/testnet.
  If none allows browser reads, the page shows "live read unavailable" and
  says why; it never pretends.
- Monadscan refuses automated fetches (HTTP 403). Links opened by a person
  in a browser work.
- Node may not be installed. Stay with plain ES modules and pinned CDN
  scripts, like the viewer.
- Joseph's integrator agent is wiring the pieces into one app on branch
  `integration`; the viewer there is the same `web/`.

## Hard limits

- Work in your own git worktree on a new branch `step-5-chain-feed`, cut
  from `step-1-chain` (it has `web/` merged from `joseph` plus the chain
  files). From the repo:
  `git worktree add ../lantern-chain-feed -b step-5-chain-feed step-1-chain`.
  Commit and push that branch often. Never merge into `joseph`, `main` or
  `step-1-chain`.
- New files only: `web/js/chain.js`, `web/chain.html`, `web/collect_chain.py`
  and the copies under `web/chain/`. Do not edit the viewer's existing files
  or anything under `sim/` or `contracts/`. If you need a change there,
  write the exact ask, with file and line, in your report.
- Read-only. No keys, no signing, nothing reads `.env`.
- Real versus made-up stays visible: label the panel "live from Monad
  testnet" and show when it last read the chain.
- No commit message names a person.

## Report

Report to Kellen in chat with the headings in `ops/PROTOCOL.md` under
"Reports": what was asked, what you changed (files and lines), what you
checked it against and what you saw, what you are blind to, where you
stopped, what you changed from the brief and why. Include the command to
run it and what the page showed.
