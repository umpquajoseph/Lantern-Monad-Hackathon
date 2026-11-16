# Demo day: the chain part

For Kellen and Joseph. Written 2026-11-15 15:50 PST. How every play-through of
the hosted mission writes real transactions to Monad testnet, what to open in
the second tab, and what to do if it fails. Verified against the live site at
the time of writing (see "Checked").

## The demo link

    https://lantern-teal.vercel.app/?key=<DEMO_KEY>

`DEMO_KEY` is in Kellen's local `.env` (never in git) and in the Vercel
project's environment. With the key in the URL the badge top right reads
**Monad testnet · live**; without it, **Monad testnet** (recorded hashes
from the precomputed mission). Load the link before projecting: the key is
visible in the address bar. It only guards the agencies' testnet gas.

## What happens during a play-through

1. The mission is precomputed; the rovers' driving, sensing and decisions
   are replayed from the log. Nothing about the science changes.
2. When playback crosses the Surveyor's find at S2, the page pauses with a
   "Live" note, the site's uplink (`web/api/chain.js`) signs `postFind` as
   agency A and sends it to the Lantern contract, and the note shows the new
   transaction hash and find number, linked to Monadscan.
3. When playback crosses the Prospector's check, the uplink signs `checkFind`
   as agency B against the find just posted, and the note says the find is
   now **confirmed**.
4. The "On chain" pane lists both with a LIVE tag; the recorded hashes are
   replaced for this play-through only.

Each play-through leaves a new find and check on the contract. That is
intended: the record keeps every rehearsal, with its block timestamp.

## The second tab: Monadscan

Open before the pitch, refresh after each live post:

- Contract: https://testnet.monadscan.com/address/0x67f9Bedb42C845307cB1Fd6551D07Df64bd3CE98
- Agency A's wallet (sends postFind): https://testnet.monadscan.com/address/0x3671cA4854a7Ab5212e3Bd5991739753abC800b8
- Agency B's wallet (sends checkFind): https://testnet.monadscan.com/address/0x9E1288856e57189A13e57Db89B12abF61D1538aE

The proof is the timestamp: the newest two transactions on the contract page
carry the block time of the demo, minutes old. The contract's source is
verified (Sourcify, exact match), so the "Contract" tab shows `Lantern.sol`.

Third option, same proof, no explorer needed: https://lantern-teal.vercel.app/chain.html
reads the contract from the browser and prints every find with its UTC time.

## Cost and budget

Measured 2026-11-15 15:10 PST at 102 gwei: postFind about 0.015 MON from A,
checkFind about 0.032 MON from B, about 0.05 MON per play-through. Balances
then: A 9.06 MON, B 14.49 MON, so a few hundred play-throughs. Top up from the
Monad testnet faucet if B drops under 1 MON. Check any time:

    python3 sim/chain.py balances

## Rules of thumb

- **One tab playing with the key at a time.** Both agencies share one wallet
  each, so two simultaneous play-throughs collide on the nonce and one fails.
- The uplink allows one transaction every 2 s. The find and the check are
  minutes apart in playback, so this never bites in the demo.
- If the public RPC is slow, a live post can take 10 to 20 s. The page waits
  with the "Signing..." note; do not press play twice.

## If it fails during the pitch

The page never breaks. On any error the note says "Live chain: <reason>.
Showing the recorded transaction instead." and the feed shows the recorded
hashes, which are also real transactions on the same contract (from the run
that produced the mission log). Monadscan then shows the older timestamp;
say so, and show `chain.html` or the recorded hash's page instead.

Quick checks, in order:

    curl -s https://lantern-teal.vercel.app/api/chain
      # expect "signers":{"A":true,"B":true},"keyed":true
    python3 sim/chain.py balances          # gas left
    python3 sim/chain.py finds | tail -3   # the last pair landed?

If `signers` are false the Vercel env vars are gone: run
`bash contracts/uplink_env.sh` then `npx vercel redeploy <prod url>` in `web/`.

## No fresh deploy before the demo

Kellen's call (15:10): the record keeps every rehearsal pair. A clean record
is for after the demo, if ever (`contracts/README.md`, "Before the demo").

## Checked

- 15:45 PST: Vercel env vars set by Kellen (`contracts/uplink_env.sh`), the
  production build redeployed, `/api/chain` reports both signers and the key;
  a wrong key is refused with 403.
- 16:20 PST: the page fix (`chain-live-fix`, f845cc4: a forward jump over a
  chain step posts it too; the checkpoint on the find's step still pauses
  after the post; rewinds re-arm) deployed to production with the site
  rebuilt from the live copy. Headless Chrome against the live site: playing
  across the find and the check posted find #24 and its check (confirmed);
  dragging the slider past them posted #25 and its check (confirmed); both
  rows tagged LIVE in the feed with Monadscan links. Direct uplink calls at
  15:58 posted #22. Agency A = Surveyor (postFind), B = Prospector (checkFind).
