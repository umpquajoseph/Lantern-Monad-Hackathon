#!/usr/bin/env bash
# Deploy Lantern and record the deployment for the Python client.
#
#   contracts/deploy.sh                                   # Monad testnet, keys from ../.env
#   RPC_URL=http://127.0.0.1:8545 DEPLOYER_KEY=0x... \
#   DEPLOYMENT_OUT=/tmp/dep.json contracts/deploy.sh      # a local anvil, without touching
#                                                         # the committed deployment file
#
# Writes ../sim/lantern_abi.json (always) and the deployment record (default
# ../sim/lantern_deployment.json). Agency A's key deploys unless DEPLOYER_KEY is set.
# Never prints a private key.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd .. && pwd)"

if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
fi
RPC_URL="${RPC_URL:-${MONAD_RPC_URL:-https://testnet-rpc.monad.xyz}}"
: "${AGENCY_A_PRIVATE_KEY:?AGENCY_A_PRIVATE_KEY is not set (see .env.example)}"
: "${AGENCY_B_PRIVATE_KEY:?AGENCY_B_PRIVATE_KEY is not set (see .env.example)}"
DEPLOYER_KEY="${DEPLOYER_KEY:-$AGENCY_A_PRIVATE_KEY}"
DEPLOYMENT_OUT="${DEPLOYMENT_OUT:-$ROOT/sim/lantern_deployment.json}"

A=$(cast wallet address --private-key "$AGENCY_A_PRIVATE_KEY")
B=$(cast wallet address --private-key "$AGENCY_B_PRIVATE_KEY")
DEPLOYER=$(cast wallet address --private-key "$DEPLOYER_KEY")
CHAIN_ID=$(cast chain-id --rpc-url "$RPC_URL")

echo "Lantern -> chain $CHAIN_ID via $RPC_URL"
echo "  agency A : $A  (balance $(cast balance --ether "$A" --rpc-url "$RPC_URL") MON)"
echo "  agency B : $B  (balance $(cast balance --ether "$B" --rpc-url "$RPC_URL") MON)"
echo "  deployer : $DEPLOYER"

forge build --quiet
OUT=$(forge create src/Lantern.sol:Lantern \
  --rpc-url "$RPC_URL" --private-key "$DEPLOYER_KEY" --broadcast --json \
  --constructor-args "[$A,$B]" '["Agency A","Agency B"]')
ADDR=$(printf '%s' "$OUT" | python3 -c 'import sys,json; print(json.load(sys.stdin)["deployedTo"])')
TX=$(printf '%s' "$OUT" | python3 -c 'import sys,json; print(json.load(sys.stdin)["transactionHash"])')
BLOCK=$(cast receipt "$TX" blockNumber --rpc-url "$RPC_URL")

mkdir -p "$ROOT/sim"
forge inspect Lantern abi --json > "$ROOT/sim/lantern_abi.json"

# Verify the source on Monad's Sourcify (docs.monad.xyz/guides/verify-smart-contract/foundry),
# so explorers that read Sourcify show decoded calls. Best effort: a failure here does not
# undo the deploy. VERIFIED is recorded in the deployment file only when Sourcify reports a match.
VERIFIED=""
VERIFIED_AT=""
SOURCIFY="https://sourcify-api-monad.blockvision.org"
if [ "$CHAIN_ID" = "10143" ] || [ "$CHAIN_ID" = "143" ]; then
  if forge verify-contract "$ADDR" src/Lantern.sol:Lantern --chain "$CHAIN_ID" \
       --verifier sourcify --verifier-url "$SOURCIFY/" >/dev/null 2>&1; then
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
      sleep 5
      MATCH=$(curl -s --max-time 15 "$SOURCIFY/v2/contract/$CHAIN_ID/$ADDR" \
        | python3 -c 'import sys,json
try: d=json.load(sys.stdin); print((d.get("match") or "") + " " + (d.get("verifiedAt") or ""))
except Exception: print("")' 2>/dev/null)
      case "$MATCH" in
        exact_match*|match*) VERIFIED="sourcify"; VERIFIED_AT="${MATCH#* }"; break ;;
      esac
    done
  fi
  echo "  verified : ${VERIFIED:-no (run: forge verify-contract $ADDR src/Lantern.sol:Lantern --chain $CHAIN_ID --verifier sourcify --verifier-url $SOURCIFY/)}"
fi

python3 - "$DEPLOYMENT_OUT" "$CHAIN_ID" "$ADDR" "$TX" "$BLOCK" "$A" "$B" "$DEPLOYER" "$RPC_URL" "$VERIFIED" "$VERIFIED_AT" <<'PY'
import json, sys, datetime
out, chain_id, addr, tx, block, a, b, deployer, rpc, verified, verified_at = sys.argv[1:]
chain_id = int(chain_id)
explorer = {10143: "https://testnet.monadscan.com", 143: "https://monadscan.com"}.get(chain_id)
rec = {
    "contract": "Lantern",
    "chainId": chain_id,
    "address": addr,
    "deployTx": tx,
    "deployBlock": int(block),
    "deployer": deployer,
    "agencies": [{"letter": "A", "name": "Agency A", "address": a},
                 {"letter": "B", "name": "Agency B", "address": b}],
    "rpcUrl": rpc if chain_id in (10143, 143) else None,
    "explorer": explorer,
    "explorerContractUrl": f"{explorer}/address/{addr}" if explorer else None,
    "deployedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "verified": verified or None,  # "sourcify" when Sourcify reported a match, else null
    "verifiedAt": verified_at or None,
}
json.dump(rec, open(out, "w"), indent=2)
print(json.dumps(rec, indent=2))
PY
echo "wrote $DEPLOYMENT_OUT and $ROOT/sim/lantern_abi.json"
