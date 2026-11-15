#!/usr/bin/env bash
# Deploy LanternSurvey next to an existing Lantern and record it for sim/survey.py.
#
#   survey/deploy.sh                       # Monad testnet; Lantern from sim/lantern_deployment.json
#   RPC_URL=http://127.0.0.1:8545 DEPLOYER_KEY=0x... LANTERN_ADDRESS=0x... \
#   DEPLOYMENT_OUT=/tmp/survey.json survey/deploy.sh    # a local anvil
#
# Writes ../sim/survey_abi.json (always) and the deployment record (default
# ../sim/survey_deployment.json). Agency A's key deploys unless DEPLOYER_KEY is set.
# Never prints a private key.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd .. && pwd)"
if [ -f "$ROOT/.env" ]; then set -a; . "$ROOT/.env"; set +a; fi
RPC_URL="${RPC_URL:-${MONAD_RPC_URL:-https://testnet-rpc.monad.xyz}}"
DEPLOYER_KEY="${DEPLOYER_KEY:-${AGENCY_A_PRIVATE_KEY:?AGENCY_A_PRIVATE_KEY is not set}}"
DEPLOYMENT_OUT="${DEPLOYMENT_OUT:-$ROOT/sim/survey_deployment.json}"
LANTERN_ADDRESS="${LANTERN_ADDRESS:-${LANTERN_CONTRACT_ADDRESS:-$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["address"])' "$ROOT/sim/lantern_deployment.json")}}"
# Order must match sim/results.py VOLATILES and the LCROSS table it cites.
SPECIES='["H2S","NH3","SO2","C2H4","CO2","CH3OH","CH4","OH"]'

CHAIN_ID=$(cast chain-id --rpc-url "$RPC_URL")
DEPLOYER=$(cast wallet address --private-key "$DEPLOYER_KEY")
echo "LanternSurvey -> chain $CHAIN_ID via $RPC_URL, Lantern $LANTERN_ADDRESS, deployer $DEPLOYER"

forge build --quiet
OUT=$(forge create src/LanternSurvey.sol:LanternSurvey \
  --rpc-url "$RPC_URL" --private-key "$DEPLOYER_KEY" --broadcast --json \
  --constructor-args "$LANTERN_ADDRESS" "$SPECIES")
ADDR=$(printf '%s' "$OUT" | python3 -c 'import sys,json; print(json.load(sys.stdin)["deployedTo"])')
TX=$(printf '%s' "$OUT" | python3 -c 'import sys,json; print(json.load(sys.stdin)["transactionHash"])')
BLOCK=$(cast receipt "$TX" blockNumber --rpc-url "$RPC_URL")
forge inspect LanternSurvey abi --json > "$ROOT/sim/survey_abi.json"

python3 - "$DEPLOYMENT_OUT" "$CHAIN_ID" "$ADDR" "$TX" "$BLOCK" "$LANTERN_ADDRESS" "$DEPLOYER" "$RPC_URL" <<'PY'
import json, sys, datetime
out, chain_id, addr, tx, block, lantern, deployer, rpc = sys.argv[1:]
chain_id = int(chain_id)
explorer = {10143: "https://testnet.monadscan.com", 143: "https://monadscan.com"}.get(chain_id)
rec = {"contract": "LanternSurvey", "chainId": chain_id, "address": addr, "deployTx": tx,
       "deployBlock": int(block), "lantern": lantern, "deployer": deployer,
       "rpcUrl": rpc if chain_id in (10143, 143) else None,
       "explorer": explorer, "deployedAt": datetime.datetime.now(datetime.timezone.utc).isoformat()}
json.dump(rec, open(out, "w"), indent=2)
print(f"LanternSurvey at {addr}" + (f"  {explorer}/address/{addr}" if explorer else ""))
PY
