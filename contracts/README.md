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

The Python side is `sim/chain.py`; the formats it gives are in `docs/handoffs.md`.
Network facts (chain ID 10143, RPC, explorers, faucet): https://docs.monad.xyz/developer-essentials/testnet
