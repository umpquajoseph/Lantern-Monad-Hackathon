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

The Python side is `sim/chain.py`; the formats it gives are in `docs/handoffs.md`. For the
demo spine, `sim.chain.run_scope()` is a drop-in for `ChainStandin` (see the module docstring).
Network facts (chain ID 10143, RPC, explorers, faucet): https://docs.monad.xyz/developer-essentials/testnet
