#!/usr/bin/env bash
# End-to-end check on a throwaway local chain, no testnet MON needed:
# deploy Lantern and LanternSurvey on anvil, build two synthetic runs (crater fixes on,
# then A on odometry only), post finds, checks, traverses, assays and run hashes, read
# everything back and verify the results hashes. Touches no committed deployment file.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
command -v anvil >/dev/null || { echo "anvil not found: install Foundry"; exit 1; }
WORK="$(mktemp -d)"
anvil --silent --chain-id 31337 --port 8547 >"$WORK/anvil.log" 2>&1 &
ANVIL=$!
trap 'kill $ANVIL 2>/dev/null || true' EXIT
sleep 1.5

# anvil's well-known dev keys (public; never use them anywhere real)
export RPC_URL=http://127.0.0.1:8547 MONAD_RPC_URL=http://127.0.0.1:8547
export AGENCY_A_PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
export AGENCY_B_PRIVATE_KEY=0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d
export DEPLOYER_KEY=$AGENCY_A_PRIVATE_KEY

DEPLOYMENT_OUT="$WORK/lantern.json" contracts/deploy.sh >/dev/null
export LANTERN_CONTRACT_ADDRESS=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['address'])" "$WORK/lantern.json")
DEPLOYMENT_OUT="$WORK/survey.json" survey/deploy.sh >/dev/null
export SURVEY_CONTRACT_ADDRESS=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['address'])" "$WORK/survey.json")
echo "anvil: Lantern $LANTERN_CONTRACT_ADDRESS  LanternSurvey $SURVEY_CONTRACT_ADDRESS"

python3 - "$WORK" <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, "sim")
import results as R
work = Path(sys.argv[1])
for rid, nav in (("e2e-run1", True), ("e2e-run2", False)):
    d = work / rid
    d.mkdir()
    log = R.synthetic_log(rid, nav_on=nav)
    if not nav:  # run 2: A's find is 20 m uncertain and B finds no ice where A says
        for s in log["steps"]:
            for e in s["events"]:
                if e["type"] == "find": e["sigma"] = 20.0
                if e["type"] == "check": e.update(agrees=False, reading=0.1)
    (d / "log.json").write_text(json.dumps(log))
PY

for rid in e2e-run1 e2e-run2; do
  python3 sim/survey.py post "$WORK/$rid" --post-finds --rebuild
  python3 sim/survey.py verify "$WORK/$rid"
done
python3 sim/survey.py show
# Idempotent: a second post sends nothing new.
python3 sim/survey.py post "$WORK/e2e-run1" | grep -c skipped: | xargs -I{} echo "re-post skipped {} lines"
# Tamper check: edit one reading, the hash must no longer match.
python3 - "$WORK/e2e-run1/results.json" <<'PY'
import json, sys
p = sys.argv[1]; r = json.load(open(p)); r["finds"][0]["reading_wt_pct"] += 1; json.dump(r, open(p, "w"))
PY
if python3 sim/survey.py verify "$WORK/e2e-run1"; then echo "TAMPER NOT DETECTED"; exit 1; else echo "tamper detected: ok"; fi
echo "e2e ok ($WORK)"
