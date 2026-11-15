"""Python client for LunarDiscoveryRegistry (registry/src/LunarDiscoveryRegistry.sol).

Three modes, always labelled, never confused:

  local     a local anvil chain (Foundry), started here if none is running, with
            the registry deployed fresh. Real contract, real transactions, real
            reads; not Monad. Accounts are anvil's unlocked dev accounts, so no
            keys are handled: 0 = admin, 1 = Surveyor, 2 = Prospector.
  monad     Monad testnet (chain 10143), from sim/registry_deployment.json
            (written by registry/deploy.sh) and the agency keys in .env
            (A = Surveyor, B = Prospector). Checks the chain id before sending.
  offline   no chain at all: an in-memory registry with the same rules, so the
            demo runs anywhere. Labelled "OFFLINE MOCK"; its tx hashes are
            fake and say so.

Every write returns {"tx", "block", "report_id", "mode"}; every read comes from
the contract (or the mock), never from local mission state, so the Prospector's
targets really come from the registry.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ABI_PATH = REPO / "sim" / "registry_abi.json"
DEPLOYMENT_PATH = REPO / "sim" / "registry_deployment.json"
ARTIFACT = REPO / "registry" / "out" / "LunarDiscoveryRegistry.sol" / "LunarDiscoveryRegistry.json"
ANVIL_PORT = 8546          # not 8545, so it never collides with another local node
MONAD_TESTNET = 10143

STAGES = ["None", "Candidate", "SurveyorInspected", "ProspectorAnalyzed"]
RESULTS = ["Unknown", "Positive", "Negative", "Inconclusive"]
MAX_ABS_COORD_CM = 200_000_000


class RegistryError(Exception):
    """The contract (or the mock, by the same rules) refused a call."""


def site_id(mission_id, target_id) -> str:
    from web3 import Web3
    return "0x" + Web3.keccak(text=f"{mission_id}/{target_id}").hex().removeprefix("0x")


def mission_key(mission_id) -> str:
    from web3 import Web3
    return "0x" + Web3.keccak(text=str(mission_id)).hex().removeprefix("0x")


def _foundry(tool):
    p = shutil.which(tool) or str(Path.home() / ".foundry" / "bin" / tool)
    if not Path(p).exists():
        raise FileNotFoundError(f"{tool} not found; install Foundry (getfoundry.sh)")
    return p


def _port_open(port):
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


# ---------------------------------------------------------------------------
# Real contract (local anvil or Monad testnet).
# ---------------------------------------------------------------------------

class ChainRegistry:
    def __init__(self, mode="local"):
        from web3 import Web3
        self.mode = mode
        self.abi = json.loads(ABI_PATH.read_text())
        self._anvil = None
        if mode == "local":
            if not _port_open(ANVIL_PORT):
                self._anvil = subprocess.Popen([_foundry("anvil"), "--port", str(ANVIL_PORT), "--silent"],
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                for _ in range(50):
                    if _port_open(ANVIL_PORT):
                        break
                    time.sleep(0.1)
            self.w3 = Web3(Web3.HTTPProvider(f"http://127.0.0.1:{ANVIL_PORT}"))
            accts = self.w3.eth.accounts
            self.admin, self.surveyor, self.prospector = accts[0], accts[1], accts[2]
            self.signers = None   # anvil accounts are unlocked: no keys here
            self.address = self._deploy_local()
            self.explorer = None
            self.label = f"LOCAL CHAIN (anvil, chain {self.w3.eth.chain_id}): real contract, not Monad"
        elif mode == "monad":
            from eth_account import Account
            dep = json.loads(DEPLOYMENT_PATH.read_text())
            self.w3 = Web3(Web3.HTTPProvider(os.environ.get("MONAD_RPC_URL") or dep["rpcUrl"]))
            if self.w3.eth.chain_id != MONAD_TESTNET:
                raise RegistryError(f"chain id {self.w3.eth.chain_id} is not Monad testnet ({MONAD_TESTNET})")
            ka, kb = os.environ.get("AGENCY_A_PRIVATE_KEY"), os.environ.get("AGENCY_B_PRIVATE_KEY")
            if not (ka and kb):
                raise RegistryError("AGENCY_A_PRIVATE_KEY and AGENCY_B_PRIVATE_KEY are needed in .env")
            self.signers = {"surveyor": Account.from_key(ka), "prospector": Account.from_key(kb)}
            self.surveyor = self.signers["surveyor"].address
            self.prospector = self.signers["prospector"].address
            self.address = dep["address"]
            self.explorer = dep.get("explorer")
            self.label = f"MONAD TESTNET (chain {MONAD_TESTNET})"
        else:
            raise ValueError(mode)
        self.c = self.w3.eth.contract(address=self.address, abi=self.abi)
        self.chain_id = self.w3.eth.chain_id

    def _deploy_local(self):
        if not ARTIFACT.exists():
            subprocess.run([_foundry("forge"), "build", "-q"], cwd=REPO / "registry", check=True)
        art = json.loads(ARTIFACT.read_text())
        C = self.w3.eth.contract(abi=self.abi, bytecode=art["bytecode"]["object"])
        rc = self.w3.eth.wait_for_transaction_receipt(C.constructor().transact({"from": self.admin}))
        c = self.w3.eth.contract(address=rc.contractAddress, abi=self.abi)
        for fn, who in ((c.functions.setSurveyor, self.surveyor), (c.functions.setProspector, self.prospector)):
            self.w3.eth.wait_for_transaction_receipt(fn(who, True).transact({"from": self.admin}))
        return rc.contractAddress

    def _send(self, role, fn):
        frm = self.surveyor if role == "surveyor" else self.prospector
        try:
            fn.call({"from": frm})   # surface a revert with its reason before sending
        except Exception as e:
            raise RegistryError(_revert_name(e)) from e
        if self.signers is None:
            h = fn.transact({"from": frm})
        else:
            acct = self.signers[role]
            tx = fn.build_transaction({"from": acct.address, "nonce": self.w3.eth.get_transaction_count(acct.address),
                                       "chainId": self.chain_id})
            h = self.w3.eth.send_raw_transaction(acct.sign_transaction(tx).raw_transaction)
        rc = self.w3.eth.wait_for_transaction_receipt(h, timeout=120)
        if rc.status != 1:
            raise RegistryError(f"transaction {h.hex()} reverted")
        rid = None
        for ev in ("CandidateRegistered", "SurveyorInspectionSubmitted", "ProspectorAnalysisSubmitted"):
            for log in getattr(self.c.events, ev)().process_receipt(rc, errors=_discard()):
                rid = int(log["args"]["reportId"])
        return {"tx": "0x" + bytes(rc.transactionHash).hex(), "block": rc.blockNumber,
                "report_id": rid, "gas_used": rc.gasUsed, "mode": self.mode}

    # ---- writes ---------------------------------------------------------
    def register_candidate(self, sid, mission, x, y, confidence, evidence_hash, report_cid):
        return self._send("surveyor", self.c.functions.registerCandidate(
            _b32(sid), _b32(mission), int(round(x * 100)), int(round(y * 100)),
            int(round(confidence * 10_000)), _b32(evidence_hash), report_cid))

    def submit_surveyor_inspection(self, sid, result, evidence_hash, report_cid, route_hash, route_cid):
        return self._send("surveyor", self.c.functions.submitSurveyorInspection(
            _b32(sid), RESULTS.index(result), _b32(evidence_hash), report_cid, _b32(route_hash), route_cid))

    def submit_prospector_analysis(self, sid, result, evidence_hash, report_cid, route_hash, route_cid, parent):
        return self._send("prospector", self.c.functions.submitProspectorAnalysis(
            _b32(sid), RESULTS.index(result), _b32(evidence_hash), report_cid, _b32(route_hash), route_cid,
            int(parent)))

    # ---- reads ----------------------------------------------------------
    def get_site(self, sid):
        s = self.c.functions.getSite(_b32(sid)).call()
        return _site_dict(sid, s)

    def get_report(self, rid):
        r = self.c.functions.getReport(int(rid)).call()
        return _report_dict(rid, r)

    def sites(self):
        n = self.c.functions.siteCount().call()
        ids = self.c.functions.getSiteIds(0, n).call() if n else []
        return [self.get_site("0x" + bytes(i).hex()) for i in ids]

    def report_count(self):
        return self.c.functions.reportCount().call()

    def info(self):
        return {"mode": self.mode, "label": self.label, "chain_id": self.chain_id, "contract": self.address,
                "explorer": self.explorer, "surveyor": self.surveyor, "prospector": self.prospector}

    def close(self):
        if self._anvil is not None:
            self._anvil.terminate()


def _b32(h):
    return bytes.fromhex(str(h).removeprefix("0x").rjust(64, "0"))


def _discard():
    from web3.logs import DISCARD
    return DISCARD


def _error_selectors():
    """4-byte selector -> custom error name, from the ABI."""
    from web3 import Web3
    out = {}
    for e in json.loads(ABI_PATH.read_text()):
        if e["type"] == "error":
            sig = f"{e['name']}({','.join(i['type'] for i in e['inputs'])})"
            out[Web3.keccak(text=sig)[:4].hex().removeprefix("0x")] = e["name"]
    return out


def _revert_name(e):
    import re
    s = str(e) + " " + " ".join(map(str, getattr(e, "args", ())))
    sel = _error_selectors()
    for m in re.finditer(r"0x([0-9a-fA-F]{8})", s):
        if m.group(1).lower() in sel:
            return sel[m.group(1).lower()]
    return f"reverted: {s[:200]}"


def _site_dict(sid, s):
    return {"site_id": sid, "mission_id": "0x" + bytes(s[0]).hex(), "x": s[1] / 100, "y": s[2] / 100,
            "confidence": s[3] / 10_000, "stage": STAGES[s[4]], "surveyor_result": RESULTS[s[5]],
            "prospector_result": RESULTS[s[6]], "registered_by": s[7], "registered_at": s[8],
            "latest_report_id": s[9]}


def _report_dict(rid, r):
    return {"report_id": int(rid), "site_id": "0x" + bytes(r[0]).hex(), "stage": STAGES[r[1]],
            "result": RESULTS[r[2]], "evidence_hash": "0x" + bytes(r[3]).hex(),
            "route_hash": "0x" + bytes(r[4]).hex(), "report_cid": r[5], "route_cid": r[6],
            "submitter": r[7], "time": r[8], "parent_report_id": r[9]}


# ---------------------------------------------------------------------------
# Offline mock: the same rules, no chain.
# ---------------------------------------------------------------------------

class OfflineRegistry:
    """In-memory, same checks and errors as the contract. Labelled as a mock."""

    def __init__(self):
        self.mode = "offline"
        self.label = "OFFLINE MOCK: no blockchain; tx hashes are fake"
        self.address, self.chain_id, self.explorer = None, None, None
        self.surveyor, self.prospector = "surveyor (mock)", "prospector (mock)"
        self._sites, self._order, self._reports, self._block = {}, [], [None], 0

    def _tx(self, rid):
        self._block += 1
        h = hashlib.sha256(f"offline-{rid}".encode()).hexdigest()
        return {"tx": "0xFAKE" + h[:60], "block": self._block, "report_id": rid, "gas_used": 0, "mode": "offline"}

    def _refs(self, h, cid):
        if not h or int(str(h), 16) == 0:
            raise RegistryError("EmptyHash")
        if not cid or len(cid) < 10 or len(cid) > 100 or cid[0] != "b":
            raise RegistryError("BadCid")

    def _append(self, sid, stage, result, eh, rh, rc, rtc, parent, who):
        self._reports.append({"report_id": len(self._reports), "site_id": sid, "stage": stage, "result": result,
                              "evidence_hash": eh, "route_hash": rh or "0x" + "0" * 64, "report_cid": rc,
                              "route_cid": rtc or "", "submitter": who, "time": int(time.time()),
                              "parent_report_id": parent})
        return len(self._reports) - 1

    def register_candidate(self, sid, mission, x, y, confidence, evidence_hash, report_cid):
        if sid in self._sites:
            raise RegistryError("SiteExists")
        if abs(x * 100) > MAX_ABS_COORD_CM or abs(y * 100) > MAX_ABS_COORD_CM:
            raise RegistryError("BadCoordinates")
        if not 0 <= confidence <= 1:
            raise RegistryError("BadConfidence")
        self._refs(evidence_hash, report_cid)
        rid = self._append(sid, "Candidate", "Unknown", evidence_hash, None, report_cid, None, 0, self.surveyor)
        self._sites[sid] = {"site_id": sid, "mission_id": mission, "x": round(x, 2), "y": round(y, 2),
                            "confidence": round(confidence, 4), "stage": "Candidate",
                            "surveyor_result": "Unknown", "prospector_result": "Unknown",
                            "registered_by": self.surveyor, "registered_at": int(time.time()),
                            "latest_report_id": rid}
        self._order.append(sid)
        return self._tx(rid)

    def submit_surveyor_inspection(self, sid, result, evidence_hash, report_cid, route_hash, route_cid):
        s = self._get(sid)
        if s["stage"] != "Candidate":
            raise RegistryError("BadTransition")
        if result == "Unknown":
            raise RegistryError("BadResult")
        self._refs(evidence_hash, report_cid)
        self._refs(route_hash, route_cid)
        rid = self._append(sid, "SurveyorInspected", result, evidence_hash, route_hash, report_cid, route_cid,
                           s["latest_report_id"], self.surveyor)
        s.update(stage="SurveyorInspected", surveyor_result=result, latest_report_id=rid)
        return self._tx(rid)

    def submit_prospector_analysis(self, sid, result, evidence_hash, report_cid, route_hash, route_cid, parent):
        s = self._get(sid)
        if s["stage"] != "SurveyorInspected":
            raise RegistryError("BadTransition")
        if result == "Unknown":
            raise RegistryError("BadResult")
        if int(parent) != s["latest_report_id"]:
            raise RegistryError("BadParent")
        self._refs(evidence_hash, report_cid)
        self._refs(route_hash, route_cid)
        rid = self._append(sid, "ProspectorAnalyzed", result, evidence_hash, route_hash, report_cid, route_cid,
                           int(parent), self.prospector)
        s.update(stage="ProspectorAnalyzed", prospector_result=result, latest_report_id=rid)
        return self._tx(rid)

    def _get(self, sid):
        if sid not in self._sites:
            raise RegistryError("NoSuchSite")
        return self._sites[sid]

    def get_site(self, sid):
        return dict(self._get(sid))

    def get_report(self, rid):
        if not 0 < int(rid) < len(self._reports):
            raise RegistryError("NoSuchReport")
        return dict(self._reports[int(rid)])

    def sites(self):
        return [dict(self._sites[s]) for s in self._order]

    def report_count(self):
        return len(self._reports) - 1

    def info(self):
        return {"mode": self.mode, "label": self.label, "chain_id": None, "contract": None, "explorer": None,
                "surveyor": self.surveyor, "prospector": self.prospector}

    def close(self):
        pass


def connect(mode="auto"):
    """'local' (anvil), 'monad', 'offline', or 'auto': Monad if a registry deployment
    and keys exist, else a local anvil if Foundry is installed, else offline."""
    if mode == "auto":
        if DEPLOYMENT_PATH.exists() and os.environ.get("AGENCY_A_PRIVATE_KEY"):
            mode = "monad"
        else:
            try:
                _foundry("anvil")
                mode = "local"
            except FileNotFoundError:
                mode = "offline"
    return OfflineRegistry() if mode == "offline" else ChainRegistry(mode)
