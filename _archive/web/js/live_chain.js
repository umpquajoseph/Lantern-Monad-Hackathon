// Live chain calls during the replay: at a chain checkpoint the page asks the
// site's uplink (api/chain.js) to sign the mission's call for real, so every
// play-through writes fresh transactions to the Lantern contract on Monad.
//
//   const live = new LiveChain();            // reads ?key=... from the page URL
//   const r = await live.postFind(findEvent);          // {find_id, tx, url, ...}
//   const c = await live.checkFind(checkEvent, r.find_id);
//
// `findEvent` and `checkEvent` are the run log's `find` and `check` events
// (docs/handoffs.md, Run log): x, y, sigma, reading, data_hash / agrees. The
// values posted are exactly the precomputed mission's; only the transaction
// is new. Without the key (or when the uplink is missing) `enabled` is false
// and the page keeps the log's recorded hashes.

export class LiveChain {
  constructor(base = "api/chain", key = null) {
    this.base = base;
    this.key = key ?? new URLSearchParams(location.search).get("key");
    this.info = null;
    this.log = [];        // every call made in this play-through, in order
  }

  get enabled() { return !!this.key; }

  /** Contract, chain and whether the uplink can sign; null if the uplink is absent. */
  async probe() {
    try {
      const r = await fetch(this.base, { cache: "no-store" });
      this.info = r.ok ? await r.json() : null;
    } catch { this.info = null; }
    return this.info;
  }

  async _call(body) {
    const r = await fetch(this.base, { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ key: this.key, ...body }) });
    const j = await r.json().catch(() => ({ ok: false, error: `HTTP ${r.status}` }));
    if (!r.ok || !j.ok) throw new Error(j.error || `HTTP ${r.status}`);
    this.log.push(j);
    return j;
  }

  /** Post the Surveyor's find for real. `ev` is the log's find event. */
  postFind(ev) {
    return this._call({ call: "postFind", x: ev.x, y: ev.y, sigma: ev.sigma, reading: ev.reading, data_hash: ev.data_hash });
  }

  /** Post the Prospector's check of `findId` (the id postFind returned in this play-through). */
  checkFind(ev, findId) {
    return this._call({ call: "checkFind", find_id: findId, x: ev.x, y: ev.y, sigma: ev.sigma, reading: ev.reading, agrees: !!ev.agrees });
  }
}
