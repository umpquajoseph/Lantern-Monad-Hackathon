// Lantern demo uplink: a Vercel serverless function that signs the mission's two
// chain calls when the replay reaches them, so every play-through writes real
// transactions to the Lantern contract on Monad testnet.
//
//   POST /api/chain   {"key": "<demo key>", "call": "postFind",
//                      "x": m, "y": m, "sigma": m, "reading": wt%, "data_hash": "0x..64 hex"}
//   POST /api/chain   {"key": "<demo key>", "call": "checkFind", "find_id": n,
//                      "x": m, "y": m, "sigma": m, "reading": wt%, "agrees": true}
//   GET  /api/chain   -> {ok, contract, chain_id, explorer, agencies: {A, B}}  (no key needed)
//
// Units mirror sim/chain.py: metres -> whole cm (int64), wt% -> hundredths (uint32),
// data_hash -> bytes32. postFind signs as agency A (the Surveyor), checkFind as B
// (the Prospector). Keys live only in Vercel's environment (AGENCY_A_PRIVATE_KEY,
// AGENCY_B_PRIVATE_KEY, DEMO_KEY, optional MONAD_RPC_URL); nothing here is in git.
// The demo key keeps strangers from spending the agencies' testnet gas: the page
// gets it from ?key=... in the demo link.
const { ethers } = require("ethers");
const abi = require("../chain/lantern_abi.json");
const deployment = require("../chain/lantern_deployment.json");

const RATE_MS = 2000;           // at most one transaction every 2 s per instance
let lastSend = 0;

const cm = (m) => BigInt(Math.round(Number(m) * 100));
const hundredths = (wt) => Math.round(Number(wt) * 100);
const num = (x) => (typeof x === "number" && Number.isFinite(x));

function provider() {
  const url = process.env.MONAD_RPC_URL || deployment.rpcUrl;
  return new ethers.JsonRpcProvider(url, deployment.chainId, { staticNetwork: true });
}

function signer(agency) {
  const key = process.env[`AGENCY_${agency}_PRIVATE_KEY`];
  if (!key) throw new Error(`no key for agency ${agency} in the environment`);
  return new ethers.Wallet(key, provider());
}

function txUrl(hash) {
  return deployment.explorer ? `${deployment.explorer}/tx/${hash}` : null;
}

async function send(agency, fn, args) {
  const now = Date.now();
  if (now - lastSend < RATE_MS) throw Object.assign(new Error("one transaction every 2 s; try again"), { status: 429 });
  lastSend = now;
  const w = signer(agency);
  const c = new ethers.Contract(deployment.address, abi, w);
  const tx = await c[fn](...args);
  const rcpt = await tx.wait(1);
  const logs = rcpt.logs.map((l) => { try { return c.interface.parseLog({ topics: [...l.topics], data: l.data }); } catch { return null; } }).filter(Boolean);
  return { hash: rcpt.hash, block: rcpt.blockNumber, logs, from: w.address };
}

module.exports = async (req, res) => {
  res.setHeader("Cache-Control", "no-store");
  if (req.method === "GET") {
    const [addrs] = await new ethers.Contract(deployment.address, abi, provider()).agencies();
    return res.status(200).json({ ok: true, contract: deployment.address, chain_id: deployment.chainId,
      explorer: deployment.explorer, agencies: { A: addrs[0], B: addrs[1] },
      signers: { A: !!process.env.AGENCY_A_PRIVATE_KEY, B: !!process.env.AGENCY_B_PRIVATE_KEY }, keyed: !!process.env.DEMO_KEY });
  }
  if (req.method !== "POST") return res.status(405).json({ ok: false, error: "POST a call" });
  let body = req.body;
  if (typeof body === "string") { try { body = JSON.parse(body); } catch { body = null; } }
  if (!body || typeof body !== "object") return res.status(400).json({ ok: false, error: "JSON body expected" });
  if (process.env.DEMO_KEY && body.key !== process.env.DEMO_KEY) return res.status(403).json({ ok: false, error: "wrong or missing demo key" });
  try {
    if (body.call === "postFind") {
      const { x, y, sigma, reading, data_hash } = body;
      if (![x, y, sigma, reading].every(num) || !/^0x[0-9a-fA-F]{64}$/.test(String(data_hash))) {
        return res.status(400).json({ ok: false, error: "postFind needs x, y, sigma, reading (numbers) and data_hash (bytes32 hex)" });
      }
      const r = await send("A", "postFind", [cm(x), cm(y), cm(sigma), hundredths(reading), data_hash]);
      const ev = r.logs.find((l) => l.name === "FindPosted");
      const find_id = ev ? Number(ev.args.id) : null;
      return res.status(200).json({ ok: true, call: "postFind", agency: "A", find_id, tx: r.hash, block: r.block, url: txUrl(r.hash) });
    }
    if (body.call === "checkFind") {
      const { find_id, x, y, sigma, reading, agrees } = body;
      if (!Number.isInteger(find_id) || find_id < 0 || ![x, y, sigma, reading].every(num) || typeof agrees !== "boolean") {
        return res.status(400).json({ ok: false, error: "checkFind needs find_id (int), x, y, sigma, reading (numbers), agrees (bool)" });
      }
      const r = await send("B", "checkFind", [BigInt(find_id), cm(x), cm(y), cm(sigma), hundredths(reading), agrees]);
      const ev = r.logs.find((l) => l.name === "FindChecked");
      const status = ev ? ["reported", "confirmed", "disputed"][Number(ev.args.status)] : null;
      return res.status(200).json({ ok: true, call: "checkFind", agency: "B", find_id, check_id: ev ? Number(ev.args.checkId) : null,
        status, tx: r.hash, block: r.block, url: txUrl(r.hash) });
    }
    return res.status(400).json({ ok: false, error: "call must be postFind or checkFind" });
  } catch (e) {
    const msg = e.shortMessage || e.reason || e.message || String(e);
    return res.status(e.status || 502).json({ ok: false, error: msg });
  }
};
