"""Post a run's results to LanternSurvey on Monad: paths, ice quantities, volatiles.

    python sim/survey.py post data/runs/<run_id>      # builds results.json if needed, posts
    python sim/survey.py show                         # every run, traverse and assay on chain
    python sim/survey.py verify data/runs/<run_id>    # re-hash results.json, compare to chain

What goes on chain (contract survey/src/LanternSurvey.sol), per run:
  - one Traverse per rover: the path it believes it drove, downsampled to at most
    Model.chain_waypoints points in whole centimetres, with keccak256 of the full path and
    its worst sigma;
  - one Assay per find, signed by the finder: lat/lon, ice wt%, modelled area, depth and
    mass, and 8 volatiles in ppm (species from LanternSurvey.volatileNames());
  - one Run record: keccak256 of results.json (see sim/results.py, "Hashing").

Finds and checks are Lantern's (sim/chain.py). The rovers post them during the run. If a
results file has finds without a transaction (a run made with the stand-in chain),
`post --post-finds` posts them and their checks to Lantern first, so assays can refer to
real find ids. The ids and tx hashes in results.json are updated and the file re-hashed.

Config, as sim/chain.py: .env at the repo root (MONAD_RPC_URL, AGENCY_<L>_PRIVATE_KEY),
plus SURVEY_CONTRACT_ADDRESS or sim/survey_deployment.json (written by survey/deploy.sh).
Read-only use needs no keys.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from web3 import Web3
from web3.exceptions import ContractCustomError, ContractLogicError

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chain  # noqa: E402  sim/chain.py, step 1
import results as R  # noqa: E402  sim/results.py

ROOT = Path(__file__).resolve().parents[1]
ABI_PATH = ROOT / "sim" / "survey_abi.json"
DEPLOYMENT_PATH = ROOT / "sim" / "survey_deployment.json"
GAS_MARGIN = chain.GAS_MARGIN  # Monad bills the gas limit: same tight margin as step 1
load_dotenv(ROOT / ".env")


def _hex(b: bytes | str) -> str:
    if isinstance(b, str):
        return b if b.startswith("0x") else "0x" + b
    return "0x" + bytes(b).hex()


def _b32(h: str | None) -> bytes:
    if not h:
        return b"\x00" * 32
    raw = bytes.fromhex(h[2:] if h.startswith("0x") else h)
    if len(raw) != 32:
        raise ValueError(f"not 32 bytes: {h}")
    return raw


def run_id_bytes(run_id: str) -> bytes:
    """bytes32 run id on chain: keccak256 of the run_id string."""
    return bytes(Web3.keccak(text=run_id))


class SurveyClient:
    def __init__(self, rpc_url: str | None = None, address: str | None = None,
                 lantern: chain.LanternClient | None = None):
        dep = json.loads(DEPLOYMENT_PATH.read_text()) if DEPLOYMENT_PATH.exists() else {}
        address = address or os.environ.get("SURVEY_CONTRACT_ADDRESS") or dep.get("address")
        if not address:
            raise chain.LanternConfigError(
                "No survey address: set SURVEY_CONTRACT_ADDRESS or run survey/deploy.sh")
        rpc_url = rpc_url or os.environ.get("MONAD_RPC_URL") or dep.get("rpcUrl") or chain.DEFAULT_RPC
        abi = json.loads(ABI_PATH.read_text())
        self.w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
        self.chain_id = self.w3.eth.chain_id
        self.address = Web3.to_checksum_address(address)
        self.contract = self.w3.eth.contract(address=self.address, abi=abi)
        lantern_addr = self.contract.functions.lantern().call()
        self.lantern = lantern or chain.LanternClient(rpc_url=rpc_url, address=lantern_addr)
        if self.lantern.address != Web3.to_checksum_address(lantern_addr):
            raise chain.LanternConfigError(
                f"survey points at Lantern {lantern_addr}, client at {self.lantern.address}")
        self.explorer = chain.EXPLORERS.get(self.chain_id)
        self._errors = chain.LanternClient._error_table(abi)
        self.tx_log: list[dict] = []
        self.species = list(self.contract.functions.volatileNames().call())
        if self.species != list(R.VOLATILES):
            raise chain.LanternConfigError(f"species order {self.species} != {R.VOLATILES}")

    # ----------------------------------------------------------------- sending
    def tx_url(self, h: str) -> str | None:
        return f"{self.explorer}/tx/{h}" if self.explorer else None

    def _send(self, agency: str, fn, **log_fields) -> Any:
        acct = self.lantern._account(agency)  # same keys and address check as step 1
        try:
            gas = fn.estimate_gas({"from": acct.address})
        except (ContractCustomError, ContractLogicError) as e:
            raise self._refusal(e) from None
        tx = fn.build_transaction({
            "from": acct.address,
            "nonce": self.w3.eth.get_transaction_count(acct.address, "pending"),
            "gas": int(gas * GAS_MARGIN),
            "gasPrice": self.w3.eth.gas_price,
            "chainId": self.chain_id,
        })
        t0 = time.time()
        h = self.w3.eth.send_raw_transaction(acct.sign_transaction(tx).raw_transaction)
        rec = self.w3.eth.wait_for_transaction_receipt(
            h, timeout=chain.RECEIPT_TIMEOUT_S, poll_latency=chain.RECEIPT_POLL_S)
        entry = {"kind": log_fields.pop("kind"), "agency": agency.upper(), "tx_hash": _hex(h),
                 "block": int(rec["blockNumber"]), "gas_used": int(rec["gasUsed"]),
                 "ok": rec["status"] == 1, "seconds": round(time.time() - t0, 3), **log_fields}
        entry["url"] = self.tx_url(entry["tx_hash"])
        self.tx_log.append(entry)
        if rec["status"] != 1:
            raise chain.LanternRefused("Reverted", (), entry["tx_hash"])
        return entry

    def _refusal(self, e: Exception) -> chain.LanternRefused:
        data = getattr(e, "data", None)
        if isinstance(data, str) and data.startswith("0x") and len(data) >= 10:
            raw = bytes.fromhex(data[2:])
            hit = self._errors.get(raw[:4]) or self.lantern._errors.get(raw[:4])
            if hit:
                name, types = hit
                return chain.LanternRefused(name, tuple(self.w3.codec.decode(types, raw[4:])), data)
        return chain.LanternRefused("Reverted", (str(data or e),), str(data or e))

    # ----------------------------------------------------------------- writes
    def post_traverse(self, agency: str, run_id: str, rover: dict) -> dict:
        xy = rover["chain_path"]["xy"]
        xs = [chain.to_cm(p[0]) for p in xy]
        ys = [chain.to_cm(p[1]) for p in xy]
        fn = self.contract.functions.postTraverse(
            run_id_bytes(run_id), _b32(rover["path_hash"]), rover["n_points"],
            chain.to_cm(rover["length_m"]), chain.to_cm(rover["max_sigma_m"]), xs, ys)
        return self._send(agency, fn, kind="traverse", run_id=run_id, n=len(xs))

    def post_assay(self, f: dict, run_id: str) -> dict:
        ice = f["ice"]
        inp = (
            int(f["find_id"]),
            round(f["lat"] * 1e6), round(((f["lon"] + 180.0) % 360.0 - 180.0) * 1e6),  # -180..180
            chain.reading_to_chain(f["reading_wt_pct"]),
            round(ice["mass_kg"]), round(ice["area_m2"]), chain.to_cm(ice["depth_m"]),
            run_id_bytes(run_id), _b32(f["assay_hash"]),
            [round(f["volatiles_ppm"][s]) for s in R.VOLATILES],
        )
        fn = self.contract.functions.postAssay(inp)
        return self._send(f["agency"], fn, kind="assay", find_id=int(f["find_id"]))

    def post_run(self, agency: str, res: dict) -> dict:
        t = res["totals"]
        fn = self.contract.functions.postRun(
            run_id_bytes(res["run_id"]), _b32(res["results_hash"]), t["n_finds"],
            round(t["ice_mass_kg"]), res.get("label", "")[:96])
        return self._send(agency, fn, kind="run", run_id=res["run_id"])

    def ensure_finds(self, res: dict) -> int:
        """Post to Lantern every find (and its checks) that has no transaction yet."""
        n = 0
        for f in res["finds"]:
            if f.get("tx"):
                continue
            raw = {k: f[k] for k in ("x", "y", "sigma", "reading_wt_pct", "t", "agency")}
            raw["run_id"] = res["run_id"]
            fid = self.lantern.post_find(f["agency"], f["x"], f["y"], f["sigma"],
                                         f["reading_wt_pct"], chain.hash_raw(raw))
            f["find_id"], f["tx"] = fid, self.lantern.tx_log[-1]["tx_hash"]
            for c in f["checks"]:
                if c.get("tx"):
                    continue
                c["tx"] = self.lantern.check_find(c["agency"], fid, c["x"], c["y"], c["sigma"],
                                                  c["reading_wt_pct"], c["agrees"])
            n += 1
        return n

    def publish(self, res: dict, run_agency: str = "A", post_finds: bool = False) -> dict:
        """Post everything for one run. Skips what is already on chain, so it can be re-run."""
        run_id = res["run_id"]
        if post_finds and self.ensure_finds(res):
            R.rehash(res)
        block: dict[str, Any] = {"survey": self.address, "lantern": self.lantern.address,
                                 "chain_id": self.chain_id, "explorer": self.explorer,
                                 "traverses": {}, "assays": {}, "run": None, "skipped": []}
        rid = run_id_bytes(run_id)
        posted = {(t["agency"], t["runId"]) for t in self.traverses()}
        for ag, rover in res["rovers"].items():
            addr = self.lantern.address_of(ag)
            if (addr, "0x" + rid.hex()) in posted:
                block["skipped"].append(f"traverse {ag}: already on chain")
                continue
            if not self.lantern.can_sign(ag):
                block["skipped"].append(f"traverse {ag}: no key")
                continue
            block["traverses"][ag] = self.post_traverse(ag, run_id, rover)
        for f in res["finds"]:
            found, _ = self.contract.functions.assayOfFind(int(f["find_id"])).call()
            if found:
                block["skipped"].append(f"assay {f['find_id']}: already on chain")
                continue
            try:
                block["assays"][str(f["find_id"])] = self.post_assay(f, run_id)
            except chain.LanternRefused as e:
                block["skipped"].append(f"assay {f['find_id']}: refused {e.name}{tuple(e.values)}")
        try:
            on = self.contract.functions.getRun(rid).call()
            block["skipped"].append(f"run: already on chain with hash {_hex(on[5])}")
        except (ContractCustomError, ContractLogicError):
            block["run"] = self.post_run(run_agency, res)
        res["chain"] = block
        return res

    # ----------------------------------------------------------------- reads
    def _agency_letter(self, addr: str) -> str:
        return self.lantern.letter_of(addr)

    def assays(self) -> list[dict]:
        n = self.contract.functions.assayCount().call()
        out = []
        for a in self.contract.functions.getAssays(0, n).call() if n else []:
            out.append({
                "find_id": a[0], "agency": self._agency_letter(a[1]),
                "lat": a[2] / 1e6, "lon": a[3] / 1e6, "reading_wt_pct": a[4] / 100,
                "ice_mass_kg": a[5], "area_m2": a[6], "depth_m": a[7] / 100, "time": a[8],
                "run_id": _hex(a[9]), "data_hash": _hex(a[10]),
                "volatiles_ppm": dict(zip(self.species, a[11])),
            })
        return out

    def traverses(self) -> list[dict]:
        out = []
        for i in range(self.contract.functions.traverseCount().call()):
            t = self.contract.functions.getTraverse(i).call()
            out.append({"id": i, "agency": Web3.to_checksum_address(t[0]),
                        "letter": self._agency_letter(t[0]), "n_full": t[1],
                        "max_sigma_m": t[2] / 100, "length_m": t[3] / 100, "time": t[4],
                        "runId": _hex(t[5]), "path_hash": _hex(t[6]),
                        "xy": [[x / 100, y / 100] for x, y in zip(t[7], t[8])]})
        return out

    def runs(self) -> list[dict]:
        out = []
        for i in range(self.contract.functions.runCount().call()):
            r = self.contract.functions.getRunAt(i).call()
            out.append({"poster": self._agency_letter(r[0]), "n_finds": r[1],
                        "ice_mass_kg": r[2], "time": r[3], "runId": _hex(r[4]),
                        "results_hash": _hex(r[5]), "label": r[6]})
        return out

    def verify(self, res: dict) -> tuple[bool, str, str]:
        local = R.keccak_hex(R.hashed_body(res))
        on = self.contract.functions.getRun(run_id_bytes(res["run_id"])).call()
        return local == _hex(on[5]), local, _hex(on[5])


# ---------------------------------------------------------------------- CLI

def _load_results(run: str) -> tuple[dict, Path]:
    p = Path(run)
    run_dir = p if p.is_dir() else p.parent
    rp = run_dir / "results.json"
    if p.name == "results.json" or (p.is_dir() and rp.exists()):
        return json.loads(rp.read_text()), run_dir
    log, run_dir = R.load_log(p)
    return R.build_results(log), run_dir


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("post")
    p.add_argument("run")
    p.add_argument("--run-agency", default="A", help="agency that signs the Run record")
    p.add_argument("--post-finds", action="store_true",
                   help="post finds and checks without a tx to Lantern first")
    p.add_argument("--rebuild", action="store_true", help="rebuild results.json from log.json")
    sub.add_parser("show")
    v = sub.add_parser("verify")
    v.add_argument("run")
    a = ap.parse_args(argv)

    c = SurveyClient()
    if a.cmd == "post":
        if a.rebuild:
            log, run_dir = R.load_log(a.run)
            res = R.build_results(log)
        else:
            res, run_dir = _load_results(a.run)
        c.publish(res, run_agency=a.run_agency, post_finds=a.post_finds)
        R.write_results(res, run_dir)
        print(R.summary(res))
        for e in c.lantern.tx_log + c.tx_log:
            print(f"  {e['kind']:9s} {e['agency']}  gas {e['gas_used']:>8}  {e['url'] or e['tx_hash']}")
        for s in res["chain"]["skipped"]:
            print(f"  skipped: {s}")
        return 0
    if a.cmd == "show":
        print(f"LanternSurvey {c.address} on chain {c.chain_id} (Lantern {c.lantern.address})")
        for r in c.runs():
            print(f"  run {r['label']!r}: {r['n_finds']} finds, {r['ice_mass_kg']} kg H2O, "
                  f"hash {r['results_hash'][:18]}.. by {r['poster']}")
        for t in c.traverses():
            print(f"  traverse {t['id']} by {t['letter']}: {t['length_m']:.0f} m, "
                  f"{len(t['xy'])}/{t['n_full']} pts on chain, max sigma {t['max_sigma_m']:.2f} m")
        for s in c.assays():
            v = s["volatiles_ppm"]
            print(f"  assay of find {s['find_id']} by {s['agency']}: {s['lat']:.5f}, {s['lon']:.5f} "
                  f"{s['reading_wt_pct']:.2f} wt%, {s['ice_mass_kg']} kg H2O over {s['area_m2']} m2; "
                  f"H2S {v['H2S']} NH3 {v['NH3']} CO2 {v['CO2']} ppm")
        return 0
    if a.cmd == "verify":
        res, _ = _load_results(a.run)
        ok, local, on = c.verify(res)
        print(("MATCH " if ok else "MISMATCH ") + f"local {local} chain {on}")
        return 0 if ok else 1
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
