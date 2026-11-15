"""Copy the Lantern deployment record and ABI into web/chain/ for the live chain feed.

    python web/collect_chain.py

The page is static with relative paths only (web/README.md), so it cannot read ../sim/.
Run this after every `contracts/deploy.sh`; the copies are committed so a static host has
them. The source of truth stays sim/lantern_deployment.json and sim/lantern_abi.json.
"""
import json
import shutil
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parent
SIM = WEB.parent / "sim"
OUT = WEB / "chain"


def main() -> int:
    OUT.mkdir(exist_ok=True)
    for name in ("lantern_deployment.json", "lantern_abi.json"):
        src = SIM / name
        if not src.exists():
            print(f"missing {src}: run contracts/deploy.sh first", file=sys.stderr)
            return 1
        shutil.copyfile(src, OUT / name)
    dep = json.loads((OUT / "lantern_deployment.json").read_text())
    print(f"web/chain/: Lantern {dep['address']} on chain {dep['chainId']}, "
          f"deployed {dep.get('deployedAt')}, verified={dep.get('verified')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
