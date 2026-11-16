#!/usr/bin/env bash
# Deploy LunarDiscoveryRegistry, authorise the Surveyor and Prospector, and record it.
#
#   registry/deploy.sh                       # Monad testnet (chain 10143), keys from ../.env
#   RPC_URL=http://127.0.0.1:8545 registry/deploy.sh   # a local anvil (chain 31337)
#
# Roles: agency A's key is the admin and the Surveyor, agency B's key is the Prospector
# (AGENCY_A_PRIVATE_KEY, AGENCY_B_PRIVATE_KEY in .env, the same throwaway testnet keys the
# Lantern contract uses). Writes sim/registry_abi.json and sim/registry_deployment.json
# (or REGISTRY_DEPLOYMENT_OUT). Refuses any chain but Monad testnet or a local anvil, so
# it can never touch mainnet. Never prints a private key.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd .. && pwd)"
export PATH="$HOME/.foundry/bin:$PATH"

if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
fi
RPC_URL="${RPC_URL:-${MONAD_RPC_URL:-https://testnet-rpc.monad.xyz}}"
: "${AGENCY_A_PRIVATE_KEY:?AGENCY_A_PRIVATE_KEY is not set (see .env.example)}"
: "${AGENCY_B_PRIVATE_KEY:?AGENCY_B_PRIVATE_KEY is not set (see .env.example)}"
OUTFILE="${REGISTRY_DEPLOYMENT_OUT:-$ROOT/sim/registry_deployment.json}"

CHAIN_ID=$(cast chain-id --rpc-url "$RPC_URL")
if [ "$CHAIN_ID" != "10143" ] && [ "$CHAIN_ID" != "31337" ]; then
  echo "refusing chain $CHAIN_ID: only Monad testnet (10143) or a local anvil (31337)" >&2
  exit 1
fi
SURVEYOR=$(cast wallet address --private-key "$AGENCY_A_PRIVATE_KEY")
PROSPECTOR=$(cast wallet address --private-key "$AGENCY_B_PRIVATE_KEY")
echo "LunarDiscoveryRegistry -> chain $CHAIN_ID via $RPC_URL"
echo "  admin + surveyor : $SURVEYOR  ($(cast balance --ether "$SURVEYOR" --rpc-url "$RPC_URL") MON)"
echo "  prospector       : $PROSPECTOR  ($(cast balance --ether "$PROSPECTOR" --rpc-url "$RPC_URL") MON)"

forge test -q   # never deploy what does not pass its tests
OUT=$(forge create src/LunarDiscoveryRegistry.sol:LunarDiscoveryRegistry \
  --rpc-url "$RPC_URL" --private-key "$AGENCY_A_PRIVATE_KEY" --broadcast --json)
ADDR=$(printf '%s' "$OUT" | python3 -c 'import sys,json; print(json.load(sys.stdin)["deployedTo"])')
TX=$(printf '%s' "$OUT" | python3 -c 'import sys,json; print(json.load(sys.stdin)["transactionHash"])')
cast send "$ADDR" "setSurveyor(address,bool)" "$SURVEYOR" true \
  --rpc-url "$RPC_URL" --private-key "$AGENCY_A_PRIVATE_KEY" >/dev/null
cast send "$ADDR" "setProspector(address,bool)" "$PROSPECTOR" true \
  --rpc-url "$RPC_URL" --private-key "$AGENCY_A_PRIVATE_KEY" >/dev/null

forge inspect LunarDiscoveryRegistry abi --json > "$ROOT/sim/registry_abi.json"
EXPLORER=""
[ "$CHAIN_ID" = "10143" ] && EXPLORER="https://testnet.monadscan.com"
python3 - "$OUTFILE" <<PY
import json, sys
json.dump({"contract": "LunarDiscoveryRegistry", "chainId": $CHAIN_ID, "address": "$ADDR",
           "deployTx": "$TX", "rpcUrl": "$RPC_URL", "explorer": "$EXPLORER" or None,
           "surveyor": "$SURVEYOR", "prospector": "$PROSPECTOR"}, open(sys.argv[1], "w"), indent=2)
PY
echo "deployed $ADDR (tx $TX); wrote $OUTFILE"
