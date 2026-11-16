# LanternSurvey: a run's results on Monad

Lantern (`../contracts/`) holds each ice find, its position uncertainty and the
other agencies' checks. LanternSurvey holds the rest of a run's results: the
path each rover drove, how much ice and which volatiles at each find, and a hash
of the whole results file. Formats are in `docs/handoffs.md`, "Results ledger".

    forge test                 # 8 tests, against the real Lantern source
    ./e2e_anvil.sh             # whole pipeline on a local anvil chain, no MON needed
    ./deploy.sh                # Monad testnet, after contracts/deploy.sh
    python ../sim/survey.py post ../data/runs/<run_id>
    python ../sim/survey.py show
    python ../sim/survey.py verify ../data/runs/<run_id>

Gas per run on anvil: about 0.92 M per traverse (120 waypoints), 0.22 M per
assay, 0.18 M per run record. Monad bills the gas limit, so lower
`Model.chain_waypoints` in `sim/results.py` if MON is short.
