# Lantern contract (step 1)

`src/Lantern.sol` is the shared record of ice finds and cross-agency checks, deployed on
Monad testnet. `test/Lantern.t.sol` holds the Foundry tests for its checking rules.

```bash
cd contracts
forge install foundry-rs/forge-std --no-git   # once: lib/ is git-ignored
forge test -vv
./deploy.sh                                   # reads ../.env, writes ../sim/lantern_abi.json
                                              # and ../sim/lantern_deployment.json
```

## Deployed

Monad testnet (chain 10143), address and deploy block in `../sim/lantern_deployment.json`.
Source verified on Monad's Sourcify (exact match), so explorers that read Sourcify show
decoded calls and events. To verify a new deployment:

```bash
forge verify-contract <address> src/Lantern.sol:Lantern --chain 10143 \
  --verifier sourcify --verifier-url https://sourcify-api-monad.blockvision.org/
```

(docs.monad.xyz/guides/verify-smart-contract/foundry; Monadscan's own verifier needs an
API key.) Redeploying gives a clean record: every find ever posted stays on the contract.

## Before the demo: a clean record

Every find ever posted stays on the contract, including the smoke tests. Shortly before the
demo, from the repo root:

1. `contracts/deploy.sh` deploys a fresh contract, verifies it on Sourcify and rewrites
   `sim/lantern_deployment.json`. Costs about 0.3 MON from agency A. Check balances first:
   `cast balance --ether <address> --rpc-url https://testnet-rpc.monad.xyz` (each demo run
   is about 0.1 MON more).
2. Commit `sim/lantern_deployment.json` (and `sim/lantern_abi.json` if the contract changed),
   push, and have the lead merge it. Any copy of the deployment file elsewhere (for example
   under `web/`) must be refreshed from this one. If the results ledger (`survey/`,
   `LanternSurvey`, branch `step-7-results`) is in use, redeploy it too, after this one: its
   writers come from this Lantern's `isAgency`.
3. Re-run `run_demo.py` so the run logs carry the new address and real hashes, then the
   viewer's `web/collect_runs.py` (or `python app.py --fresh` on the integration branch).
4. `python sim/chain.py finds` should list only the demo's finds, starting at #0.

The Python side is `sim/chain.py`; the formats it gives are in `docs/handoffs.md`. For the
demo spine, `sim.chain.run_scope()` is a drop-in for `ChainStandin` (see the module docstring).
Network facts (chain ID 10143, RPC, explorers, faucet): https://docs.monad.xyz/developer-essentials/testnet
