"""Step 1, chain client: the Lantern contract on Monad testnet, from Python.

The public API is the one in docs/handoffs.md (step 1):

    post_find(agency, x, y, sigma, reading, data_hash) -> find_id
    check_find(agency, find_id, x, y, sigma, reading, agrees) -> tx_hash
    get_finds() -> list[dict]   # id, agency, x, y, sigma, reading, agree, disagree, status, ...

plus, for the Artemis Accords section 11 upgrade in docs/spec.html:

    post_safety_zone(agency, x, y, radius, until, purpose) -> zone_id
    get_zones() -> list[dict]

Conventions
- `agency` is "A" or "B". The letter picks AGENCY_<letter>_PRIVATE_KEY in .env for signing,
  and maps to the same index in the contract's agency list for reading (A = 0, B = 1).
- x, y, sigma, radius are metres in the world frame (floats). On chain they are whole
  centimetres (docs/handoffs.md), rounded here.
- `reading` is the ice sensor reading as wt% water-equivalent (float). On chain it is
  hundredths of wt% (ASSUMED unit; see contracts/src/Lantern.sol). 5.6 -> 560.
- `data_hash` is bytes32: bytes, or a 0x-hex string. `hash_raw(obj)` makes one from the raw
  readings (canonical JSON, sorted keys, then keccak256).
- Statuses come back as the strings "reported", "confirmed" or "disputed".

Config is read from .env at the repo root (never committed): MONAD_RPC_URL,
AGENCY_A_PRIVATE_KEY, AGENCY_B_PRIVATE_KEY, LANTERN_CONTRACT_ADDRESS. Anything missing falls
back to the public testnet RPC and to sim/lantern_deployment.json (written by
contracts/deploy.sh), so read-only use, like the dashboard, needs no keys at all. Real
environment variables win over .env.

Monad notes
- Chain ID 10143, public RPC https://testnet-rpc.monad.xyz at 20 requests/s
  (docs.monad.xyz/developer-essentials/testnet). get_finds() is two calls, however many finds.
- Monad charges a transaction for its gas *limit*, not the gas it used
  (docs.monad.xyz/developer-essentials/differences), so limits here are a tight margin over
  the estimate instead of a round number.

Dropping it into run_demo.py (replaces sim/standins.py ChainStandin, same four calls)
    chain = lantern.client().run_scope()      # instead of ChainStandin(salt=...)
    ...                                       # post_find / check_find / get_finds / last_tx as before
    "chain": chain.run_log_chain(),           # the log's chain block: chain_id, contract, explorer
    "standins": {... no "chain" key ...}      # the viewer links real hashes only if standins.chain is absent
  A RunScope sees only the finds posted since it was made, so B goes to this run's find and
  not to one from an earlier run (the contract never forgets), and it caches get_finds()
  for a moment so a rover polling every step does not hit the public RPC's 20 req/s cap.

CLI
    python sim/chain.py finds       # every find on chain, newest last
    python sim/chain.py zones
    python sim/chain.py demo        # A posts, B confirms, A posts again, B disputes, and the
                                    # contract refuses the checks that break the rules
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from web3 import Web3
from web3.exceptions import ContractCustomError, ContractLogicError
from web3.logs import DISCARD

ROOT = Path(__file__).resolve().parents[1]
ABI_PATH = ROOT / "sim" / "lantern_abi.json"
DEPLOYMENT_PATH = ROOT / "sim" / "lantern_deployment.json"
DEFAULT_RPC = "https://testnet-rpc.monad.xyz"  # docs.monad.xyz/developer-essentials/testnet
EXPLORERS = {10143: "https://testnet.monadscan.com", 143: "https://monadscan.com"}
STATUS_NAMES = ("reported", "confirmed", "disputed")  # Lantern.Status order
GAS_MARGIN = 1.15  # ASSUMED: headroom over estimateGas; Monad bills the limit, so keep it small
RECEIPT_TIMEOUT_S = 90.0  # ASSUMED; Monad finality is well under a second, RPC lag is not
RECEIPT_POLL_S = 0.3  # Monad block time is about 0.3 s (docs.monad.xyz/ai/current-facts)

load_dotenv(ROOT / ".env")


class LanternError(Exception):
    """Base class for everything this module raises on purpose."""


class LanternRefused(LanternError, ValueError):
    """The contract refused the transaction before anything was sent.

    Also a ValueError, which is what the spine's stand-in raises, so `except ValueError`
    catches a refusal from either.

    `name` is the Solidity error (OwnFind, AlreadyChecked, TooFar, NoSuchFind, NotAnAgency),
    `values` its arguments, `raw` the revert data. (Not `args`: BaseException owns that.)
    """

    def __init__(self, name: str, values: tuple, raw: str | None = None):
        super().__init__(f"{name}{tuple(values)}")
        self.name, self.values, self.raw = name, tuple(values), raw


class LanternConfigError(LanternError):
    pass


# ------------------------------------------------------------------ unit helpers

def to_cm(metres: float) -> int:
    return int(round(float(metres) * 100.0))


def from_cm(cm: int) -> float:
    return cm / 100.0


def reading_to_chain(wt_pct: float) -> int:
    return int(round(float(wt_pct) * 100.0))


def reading_from_chain(hundredths: int) -> float:
    return hundredths / 100.0


def hash_raw(raw: Any) -> bytes:
    """keccak256 of the canonical JSON of the raw readings: sorted keys, no spaces, UTF-8.

    Save the same JSON next to the run (e.g. findings/<hash>.json) so anyone can re-hash it
    and compare with the chain.
    """
    return bytes(Web3.keccak(text=canonical_json(raw)))


def canonical_json(raw: Any) -> str:
    return json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _as_bytes32(h: bytes | str) -> bytes:
    if isinstance(h, str):
        h = bytes.fromhex(h[2:] if h.startswith("0x") else h)
    if len(h) != 32:
        raise ValueError(f"data_hash must be 32 bytes, got {len(h)}")
    return bytes(h)


# ------------------------------------------------------------------------ client

class LanternClient:
    """One connection to one Lantern contract. Signs for any agency whose key is in .env."""

    def __init__(
        self,
        rpc_url: str | None = None,
        address: str | None = None,
        abi: list | None = None,
        keys: dict[str, str] | None = None,
    ):
        deployment = self._load_deployment()
        self.deployment = deployment  # sim/lantern_deployment.json, or {} if absent
        self.rpc_url = rpc_url or os.environ.get("MONAD_RPC_URL") or deployment.get("rpcUrl") or DEFAULT_RPC
        address = address or os.environ.get("LANTERN_CONTRACT_ADDRESS") or deployment.get("address")
        if not address:
            raise LanternConfigError(
                "No contract address: set LANTERN_CONTRACT_ADDRESS in .env or run contracts/deploy.sh"
            )
        if abi is None:
            if not ABI_PATH.exists():
                raise LanternConfigError(f"{ABI_PATH} missing: run contracts/deploy.sh")
            abi = json.loads(ABI_PATH.read_text())

        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url, request_kwargs={"timeout": 30}))
        self.chain_id = self.w3.eth.chain_id
        self.address = Web3.to_checksum_address(address)
        self.contract = self.w3.eth.contract(address=self.address, abi=abi)
        self.explorer = EXPLORERS.get(self.chain_id)
        self._errors = self._error_table(abi)
        self.tx_log: list[dict] = []  # every transaction this client sent, for the dashboard

        # Signing keys by letter. Only letters with a key can write.
        self._accounts: dict[str, Any] = {}
        keys = keys if keys is not None else {
            letter: os.environ.get(f"AGENCY_{letter}_PRIVATE_KEY", "")
            for letter in "ABCDEFGH"
        }
        for letter, key in keys.items():
            if key:
                self._accounts[letter] = self.w3.eth.account.from_key(key)

        # Letter by address, from the contract's own ordering: index 0 is A, 1 is B...
        addrs, names = self.contract.functions.agencies().call()
        self.agency_addresses = [Web3.to_checksum_address(a) for a in addrs]
        self.agency_names = list(names)
        self._letter_of = {a: chr(ord("A") + i) for i, a in enumerate(self.agency_addresses)}

    # -- public API (docs/handoffs.md, step 1) ---------------------------------------------

    def post_find(self, agency: str, x: float, y: float, sigma: float, reading: float,
                  data_hash: bytes | str) -> int:
        """Post an ice find as `agency`. Returns the find ID from the FindPosted event."""
        fn = self.contract.functions.postFind(
            to_cm(x), to_cm(y), to_cm(sigma), reading_to_chain(reading), _as_bytes32(data_hash)
        )
        receipt = self._send(agency, fn, kind="post_find")
        logs = self.contract.events.FindPosted().process_receipt(receipt, errors=DISCARD)
        find_id = int(logs[0]["args"]["id"])
        self.tx_log[-1]["find_id"] = find_id
        return find_id

    def check_find(self, agency: str, find_id: int, x: float, y: float, sigma: float,
                   reading: float, agrees: bool) -> str:
        """Check another agency's find from where `agency` measured. Returns the tx hash.

        Raises LanternRefused (name OwnFind, AlreadyChecked, TooFar, NoSuchFind or
        NotAnAgency) when the contract refuses it; nothing is sent in that case.
        """
        fn = self.contract.functions.checkFind(
            int(find_id), to_cm(x), to_cm(y), to_cm(sigma), reading_to_chain(reading), bool(agrees)
        )
        receipt = self._send(agency, fn, kind="check_find", find_id=int(find_id), agrees=bool(agrees))
        logs = self.contract.events.FindChecked().process_receipt(receipt, errors=DISCARD)
        if logs:
            self.tx_log[-1]["check_id"] = int(logs[0]["args"]["checkId"])
            self.tx_log[-1]["status"] = STATUS_NAMES[int(logs[0]["args"]["status"])]
        return self.tx_log[-1]["tx_hash"]

    def get_finds(self) -> list[dict]:
        """Every find on chain, oldest first, in the handoffs.md dict format."""
        n = self.contract.functions.findCount().call()
        if n == 0:
            return []
        finds, agree, disagree, statuses = self.contract.functions.getFinds(0, n).call()
        return [
            self._find_dict(i, f, agree[i], disagree[i], statuses[i])
            for i, f in enumerate(finds)
        ]

    def get_find(self, find_id: int) -> dict:
        f, agree, disagree, status = self.contract.functions.getFind(int(find_id)).call()
        return self._find_dict(int(find_id), f, agree, disagree, status)

    def get_checks(self) -> list[dict]:
        n = self.contract.functions.checkCount().call()
        if n == 0:
            return []
        checks = self.contract.functions.getChecks(0, n).call()
        return [
            {
                "id": i,
                "find_id": int(c[0]),
                "agency": self.letter_of(c[1]),
                "address": c[1],
                "x": from_cm(c[2]),
                "y": from_cm(c[3]),
                "sigma": from_cm(c[4]),
                "reading": reading_from_chain(c[5]),
                "agrees": bool(c[6]),
                "time": int(c[7]),
            }
            for i, c in enumerate(checks)
        ]

    def post_safety_zone(self, agency: str, x: float, y: float, radius: float, until: float,
                         purpose: str) -> int:
        """Announce a temporary safety zone (Accords section 11). `until` is unix seconds."""
        fn = self.contract.functions.postSafetyZone(
            to_cm(x), to_cm(y), to_cm(radius), int(until), str(purpose)
        )
        receipt = self._send(agency, fn, kind="post_safety_zone")
        logs = self.contract.events.SafetyZonePosted().process_receipt(receipt, errors=DISCARD)
        zone_id = int(logs[0]["args"]["id"])
        self.tx_log[-1]["zone_id"] = zone_id
        return zone_id

    def get_zones(self, active_only: bool = False) -> list[dict]:
        n = self.contract.functions.zoneCount().call()
        if n == 0:
            return []
        zones = self.contract.functions.getZones(0, n).call()
        now = time.time()
        out = []
        for i, z in enumerate(zones):
            d = {
                "id": i,
                "agency": self.letter_of(z[0]),
                "address": z[0],
                "x": from_cm(z[1]),
                "y": from_cm(z[2]),
                "radius": from_cm(z[3]),
                "until": int(z[4]),
                "purpose": z[5],
                "time": int(z[6]),
            }
            d["active"] = d["until"] > now
            if not active_only or d["active"]:
                out.append(d)
        return out

    # -- helpers ---------------------------------------------------------------------------

    def letter_of(self, address: str) -> str:
        """'A', 'B', ... for a registered agency address; the address itself otherwise."""
        return self._letter_of.get(Web3.to_checksum_address(address), address)

    def address_of(self, agency: str) -> str:
        i = ord(agency.upper()) - ord("A")
        if not 0 <= i < len(self.agency_addresses):
            raise LanternConfigError(f"agency {agency!r} is not in the contract's agency list")
        return self.agency_addresses[i]

    def can_sign(self, agency: str) -> bool:
        return agency.upper() in self._accounts

    def balance(self, agency: str) -> float:
        """Native MON balance of an agency's wallet, for the dashboard and for faucet checks."""
        return float(self.w3.from_wei(self.w3.eth.get_balance(self.address_of(agency)), "ether"))

    @property
    def last_tx(self) -> str | None:
        """Hash of the last transaction sent, or None. Same name as the spine's stand-in."""
        return self.tx_log[-1]["tx_hash"] if self.tx_log else None

    def run_log_chain(self) -> dict:
        """The `chain` block of the run log (docs/handoffs.md): chain_id, contract, explorer.

        `explorer` is the explorer's base URL; the viewer links each hash as <explorer>/tx/<hash>
        (web/README.md). `contract_url` is the contract's own page.
        """
        return {
            "chain_id": self.chain_id,
            "contract": self.address,
            "explorer": self.explorer,
            "contract_url": self.address_url(),
            # "sourcify" only when contracts/deploy.sh saw Sourcify report a match for this address.
            "verified": self.deployment.get("verified")
            if self.deployment.get("address", "").lower() == self.address.lower() else None,
        }

    def run_scope(self, cache_s: float | None = None) -> "RunScope":
        """A view of this client for one demo run: see `RunScope`."""
        return RunScope(self, cache_s=cache_s)

    def tx_url(self, tx_hash: str) -> str | None:
        return f"{self.explorer}/tx/{tx_hash}" if self.explorer else None

    def address_url(self, address: str | None = None) -> str | None:
        return f"{self.explorer}/address/{address or self.address}" if self.explorer else None

    def check_rule(self, find: dict, x: float, y: float, sigma: float) -> tuple[bool, float, float]:
        """The distance rule, evaluated locally: (ok, distance_m, allowed_m)."""
        dx, dy = x - find["x"], y - find["y"]
        dist = (dx * dx + dy * dy) ** 0.5
        allowed = 3.0 * (find["sigma"] + sigma)  # docs/spec.html, contract functions
        return dist <= allowed, dist, allowed

    def _find_dict(self, i, f, agree, disagree, status) -> dict:
        return {
            "id": i,
            "agency": self.letter_of(f[0]),
            "address": f[0],
            "x": from_cm(f[1]),
            "y": from_cm(f[2]),
            "sigma": from_cm(f[3]),
            "reading": reading_from_chain(f[4]),
            "data_hash": "0x" + bytes(f[5]).hex(),
            "time": int(f[6]),
            "agree": [self.letter_of(a) for a in agree],
            "disagree": [self.letter_of(a) for a in disagree],
            "status": STATUS_NAMES[int(status)],
        }

    def _account(self, agency: str):
        letter = agency.upper()
        acct = self._accounts.get(letter)
        if acct is None:
            raise LanternConfigError(f"AGENCY_{letter}_PRIVATE_KEY is not set in .env")
        expected = self.address_of(letter)
        if acct.address != expected:
            raise LanternConfigError(
                f"AGENCY_{letter}_PRIVATE_KEY is {acct.address}, but the contract's agency "
                f"{letter} is {expected}. Redeploy, or fix .env."
            )
        return acct

    def _send(self, agency: str, fn, **log_fields) -> Any:
        acct = self._account(agency)
        try:
            gas = fn.estimate_gas({"from": acct.address})
        except (ContractCustomError, ContractLogicError) as e:
            raise self._decode_refusal(e) from None
        tx = fn.build_transaction({
            "from": acct.address,
            "nonce": self.w3.eth.get_transaction_count(acct.address, "pending"),
            "gas": int(gas * GAS_MARGIN),
            "gasPrice": self.w3.eth.gas_price,
            "chainId": self.chain_id,
        })
        signed = acct.sign_transaction(tx)
        t0 = time.time()
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(
            tx_hash, timeout=RECEIPT_TIMEOUT_S, poll_latency=RECEIPT_POLL_S
        )
        entry = {
            "kind": log_fields.pop("kind"),
            "agency": agency.upper(),
            "tx_hash": tx_hash.hex() if tx_hash.hex().startswith("0x") else "0x" + tx_hash.hex(),
            "block": int(receipt["blockNumber"]),
            "gas_used": int(receipt["gasUsed"]),
            "ok": receipt["status"] == 1,
            "seconds": round(time.time() - t0, 3),
            **log_fields,
        }
        entry["url"] = self.tx_url(entry["tx_hash"])
        self.tx_log.append(entry)
        if receipt["status"] != 1:
            # Reached only if the chain included a reverting tx (Monad can; see reserve balance docs).
            raise LanternRefused("Reverted", (), entry["tx_hash"])
        return receipt

    @staticmethod
    def _error_table(abi: list) -> dict[bytes, tuple[str, list[str]]]:
        table = {}
        for item in abi:
            if item.get("type") == "error":
                types = [i["type"] for i in item["inputs"]]
                sig = f"{item['name']}({','.join(types)})"
                table[bytes(Web3.keccak(text=sig))[:4]] = (item["name"], types)
        return table

    def _decode_refusal(self, e: Exception) -> LanternRefused:
        """Turn web3's revert into LanternRefused(name, args).

        web3 reports a custom error either as raw 0x-hex revert data or, in newer
        versions, as an already decoded "Name(arg, ...)" string. Handle both.
        """
        data = getattr(e, "data", None)
        if isinstance(data, str) and data.startswith("0x") and len(data) >= 10:
            raw = bytes.fromhex(data[2:])
            hit = self._errors.get(raw[:4])
            if hit:
                name, types = hit
                args = self.w3.codec.decode(types, raw[4:]) if types else ()
                return LanternRefused(name, tuple(args), data)
        text = data if isinstance(data, str) else str(e)
        m = re.match(r"\s*(\w+)\((.*)\)\s*$", text, re.S)
        if m and m.group(1) in {name for name, _ in self._errors.values()}:
            inner = m.group(2).strip()
            args = tuple(a.strip().strip("'\"") for a in inner.split(",")) if inner else ()
            return LanternRefused(m.group(1), args, text)
        return LanternRefused("Reverted", (text,), text)

    @staticmethod
    def _load_deployment() -> dict:
        if DEPLOYMENT_PATH.exists():
            try:
                return json.loads(DEPLOYMENT_PATH.read_text())
            except json.JSONDecodeError:
                pass
        return {}


# ---------------------------------------------------------------------- run scope

FINDS_CACHE_S = 2.0  # ASSUMED: how long a RunScope trusts its last get_finds() between its own writes


class RunScope:
    """One demo run's view of the contract, with the stand-in's interface.

    The contract keeps every find ever posted, including other runs' and the smoke test's.
    A scope remembers the find count when it was created and `get_finds()` returns only the
    finds posted since, with their real on-chain IDs, so "the first reported find" is this
    run's find. Writes go straight to the chain. `get_finds()` is cached for `cache_s`
    seconds, refreshed at once after any write through this scope.
    """

    STANDIN = None  # not a stand-in: the real contract on Monad testnet

    def __init__(self, client: "LanternClient", cache_s: float | None = None):
        self.client = client
        self.cache_s = FINDS_CACHE_S if cache_s is None else float(cache_s)
        self.first_id = int(client.contract.functions.findCount().call())
        self.tx_log: list[dict] = []
        self.last_tx: str | None = None
        self._cached: list[dict] | None = None
        self._cached_at = 0.0

    def post_find(self, agency, x, y, sigma, reading, data_hash) -> int:
        find_id = self.client.post_find(agency, x, y, sigma, reading, data_hash)
        self._wrote()
        return find_id

    def check_find(self, agency, find_id, x, y, sigma, reading, agrees) -> str:
        tx = self.client.check_find(agency, find_id, x, y, sigma, reading, agrees)
        self._wrote()
        return tx

    def get_finds(self) -> list[dict]:
        now = time.time()
        if self._cached is None or now - self._cached_at > self.cache_s:
            self._cached = [f for f in self.client.get_finds() if f["id"] >= self.first_id]
            self._cached_at = now
        return [dict(f) for f in self._cached]

    def post_safety_zone(self, agency, x, y, radius, until, purpose) -> int:
        zone_id = self.client.post_safety_zone(agency, x, y, radius, until, purpose)
        self._wrote()
        return zone_id

    def get_zones(self, active_only: bool = False) -> list[dict]:
        return self.client.get_zones(active_only)

    def run_log_chain(self) -> dict:
        return self.client.run_log_chain()

    def _wrote(self) -> None:
        entry = self.client.tx_log[-1]
        self.tx_log.append(entry)
        self.last_tx = entry["tx_hash"]
        self._cached = None


# ------------------------------------------------------- module-level convenience API

_default: LanternClient | None = None


def client() -> LanternClient:
    """The shared default client, built on first use from .env and the deployment file."""
    global _default
    if _default is None:
        _default = LanternClient()
    return _default


def post_find(agency, x, y, sigma, reading, data_hash) -> int:
    return client().post_find(agency, x, y, sigma, reading, data_hash)


def check_find(agency, find_id, x, y, sigma, reading, agrees) -> str:
    return client().check_find(agency, find_id, x, y, sigma, reading, agrees)


def get_finds() -> list[dict]:
    return client().get_finds()


def post_safety_zone(agency, x, y, radius, until, purpose) -> int:
    return client().post_safety_zone(agency, x, y, radius, until, purpose)


def get_zones(active_only: bool = False) -> list[dict]:
    return client().get_zones(active_only)


def run_scope(cache_s: float | None = None) -> RunScope:
    return client().run_scope(cache_s)


# -------------------------------------------------------------------------------- CLI

def _print_finds(c: LanternClient) -> None:
    finds = c.get_finds()
    print(f"{len(finds)} find(s) on {c.address} (chain {c.chain_id})")
    for f in finds:
        print(
            f"  #{f['id']} {f['agency']}  ({f['x']:.2f}, {f['y']:.2f}) ±{f['sigma']:.2f} m  "
            f"{f['reading']:.2f} wt%  {f['status']:<9} agree={f['agree']} disagree={f['disagree']}"
        )


def _demo(c: LanternClient) -> None:
    """The spec's 'done' check: post, check, read back, and refuse rule-breaking checks."""
    for letter in "AB":
        print(f"agency {letter}: {c.address_of(letter)}  balance {c.balance(letter):.4f} MON")
    raw = {"sensor": "neutron", "counts": [812, 790, 805], "t_s": 1234.5}

    # Run 1 of the demo: a ±3 m find, confirmed by the other agency.
    print("\nrun 1: A posts a find at ±3 m ...")
    fid = c.post_find("A", 120.00, -45.50, 3.0, 5.6, hash_raw(raw))
    print(f"  find #{fid}  tx {c.tx_log[-1]['url'] or c.tx_log[-1]['tx_hash']}  "
          f"{c.tx_log[-1]['seconds']} s")
    print("  B checks it from 2 m away and agrees ...")
    tx = c.check_find("B", fid, 121.50, -44.20, 3.0, 5.1, True)
    print(f"  tx {c.tx_url(tx) or tx}  status now {c.get_find(fid)['status']}")

    # Run 2: a ±20 m find, checked at the reported spot, nothing there.
    print("\nrun 2: A posts a find at ±20 m ...")
    fid2 = c.post_find("A", 530.00, 210.00, 20.0, 4.8, hash_raw({**raw, "t_s": 5678.0}))
    print(f"  find #{fid2}  tx {c.tx_log[-1]['url'] or c.tx_log[-1]['tx_hash']}")
    print("  B measures at the reported spot, finds nothing, disagrees ...")
    tx = c.check_find("B", fid2, 530.00, 210.00, 3.0, 0.0, False)
    print(f"  tx {c.tx_url(tx) or tx}  status now {c.get_find(fid2)['status']}")

    # A third find, so the distance rule can be shown on a find nobody has checked.
    print("\nA posts a third find at ±3 m, left unchecked ...")
    fid3 = c.post_find("A", 300.00, 300.00, 3.0, 2.1, hash_raw({**raw, "t_s": 9999.0}))
    print(f"  find #{fid3}  tx {c.tx_log[-1]['url'] or c.tx_log[-1]['tx_hash']}")

    # The rules: each of these must be refused before anything is sent.
    print("\nrefusals (nothing is sent):")
    for label, call in [
        ("A checks its own find", lambda: c.check_find("A", fid, 120.0, -45.5, 3.0, 5.0, True)),
        ("B checks the same find twice", lambda: c.check_find("B", fid, 120.0, -45.5, 3.0, 5.0, True)),
        (f"B checks #{fid3} from 100 m away (limit 18 m)",
         lambda: c.check_find("B", fid3, 400.0, 300.0, 3.0, 0.0, False)),
        ("check of a find that does not exist", lambda: c.check_find("B", 10**6, 0, 0, 1.0, 0.0, True)),
    ]:
        try:
            call()
            print(f"  {label}: ACCEPTED (unexpected)")
        except LanternRefused as e:
            print(f"  {label}: refused with {e}")

    print()
    _print_finds(c)
    sent = [t for t in c.tx_log if t["ok"]]
    print(f"\n{len(sent)} transactions sent, {sum(t['gas_used'] for t in sent)} gas, "
          f"mean {sum(t['seconds'] for t in sent) / len(sent):.2f} s to receipt")
    print(f"contract: {c.address_url() or c.address}")


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "finds"
    c = LanternClient()
    if cmd == "finds":
        _print_finds(c)
    elif cmd == "zones":
        for z in c.get_zones():
            print(z)
    elif cmd == "demo":
        _demo(c)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
