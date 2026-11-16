// Checkpoints and live chain calls for a mission replay (docs/handoffs.md,
// "Mission log"). The player owns two things the plain replay does not:
//
//  1. Checkpoints: while playing, crossing a checkpoint step with pause=true
//     pauses there at once and shows the checkpoint's label and line until
//     Play goes on (asked for: "when it reaches a checkpoint it hits the pause
//     button and doesnt wait at all"). Jumping backwards re-arms the checkpoints after the new time,
//     so a second pass through pauses again.
//  2. Live chain: with ?key=... in the URL, crossing a step that holds a chain
//     event (find, check) pauses, posts that call for real through the site's
//     uplink (js/live_chain.js -> api/chain.js), swaps the event's hash for
//     the new transaction, and resumes. Jumping forward over a chain step (the
//     slider, the step button) posts it too, in order, paused at the jump's
//     target: a play-through never skips a post. Going back re-arms the posts
//     after the new time (a second check of the same live find is not re-armed:
//     the contract refuses it), so every pass writes fresh transactions.
//     Without the key the recorded hashes stay and the feed says "recorded".
//
// main.js calls: new MissionPlayer(run, hooks), player.tick(prevT, t) from its
// loop, player.jumped(prevT, t) on any forward jump, player.rewound(t) on any
// backward jump, player.restart() at t = 0.
import { LiveChain } from "./live_chain.js";

export class MissionPlayer {
  constructor(run, hooks) {
    this.run = run;
    this.hooks = hooks;          // { pause(), resume(), note(html|null), rerender(), checkpoint()? }
    const m = run.log.mission || {};
    this.checkpoints = (m.checkpoints || []).filter((c) => c.pause).sort((a, b) => a.step - b.step);
    this.live = new LiveChain();
    this.liveFindIds = new Map();          // log find_id -> find_id posted in this play-through
    this.liveChecked = new Set();          // live find ids that already got B's check (one per agency; a second is refused)
    this.busy = false;
    this.restart();
    if (!this.live.enabled) {
      // No ?key= in the link: say so loudly at the start, so a rehearsal is not
      // mistaken for a live run (the feed would show the recorded hashes).
      const box = document.getElementById("checkpoint");
      const keep = box && !box.hidden ? box.innerHTML + " " : "";
      this.hooks.note(`${keep}<span class="cp-tag err">Not live</span> This link has no demo key: nothing will be posted to Monad, the feed shows the recorded transactions. Open the demo link with <code>?key=…</code> to post live.`);
    }
    if (this.live.enabled) this.live.probe().then((info) => {
      if (!info || !info.signers || !info.signers.A || !info.signers.B || !info.keyed) {
        const why = !info ? "api/chain is missing" : !info.keyed ? "DEMO_KEY is not set on the server" : "the uplink has no signing keys yet";
        const box = document.getElementById("checkpoint");
        const keep = this.active && this.active.step === 0 && box ? box.innerHTML + " " : "";
        this.hooks.note(`${keep}<span class="cp-tag err">Live chain off</span> ${why}; showing the recorded transactions.`);
        this.live.key = null;
      }
    });
  }

  get liveOn() { return this.live.enabled; }

  /** Arm everything: a fresh play-through from the start. */
  restart() {
    // A checkpoint at step 0 is the overview: shown now, never "crossed".
    const c0 = this.checkpoints.find((c) => c.step === 0);
    this.armed = new Set(this.checkpoints.filter((c) => c.step > 0).map((c) => c.id));
    this.armedTx = new Set(this.run.events.map((e, i) => i));
    this.active = c0 || null;
    this.hooks.note(c0 ? `<span class="cp-tag">Start</span> <b>${esc(c0.label)}</b> · ${esc(c0.say)}` : null);
  }

  /** A backward jump: arm the checkpoints and the live posts after t again. */
  rewound(t) {
    const k = this.run.stepAt(t);
    for (const c of this.checkpoints) if (c.step > k) this.armed.add(c.id);
    if (t <= 0.5) this.restart();
    else if (this.live.enabled) {
      // Re-arm the finds after t; a check only if its find is re-armed too, or
      // its live find has not been checked yet (the contract refuses a second
      // check from the same agency, so one would only produce an error note).
      const finds = new Set();
      this.run.events.forEach((e, i) => { if (e.k > k && e.type === "find") { this.armedTx.add(i); finds.add(e.find_id); } });
      this.run.events.forEach((e, i) => {
        if (e.k <= k || e.type !== "check") return;
        const fid = this.liveFindIds.get(e.find_id);
        if (finds.has(e.find_id) || fid == null || !this.liveChecked.has(fid)) this.armedTx.add(i);
      });
    }
    this.active = null;
    this.hooks.note(null);
  }

  /** Armed live events on steps in (k0, k1], in log order. */
  _crossed(k0, k1) {
    if (!this.live.enabled) return [];
    return [...this.run.events.entries()].filter(([i, e]) => this.armedTx.has(i) && e.k > k0 && e.k <= k1);
  }

  /** Called each animation frame while playing, before render. Returns true when it paused. */
  tick(prevT, t) {
    if (this.busy) return true;
    const k0 = this.run.stepAt(prevT), k1 = this.run.stepAt(t);
    if (k1 <= k0) return false;
    // Live chain calls first: a chain event on a step just crossed. The post
    // pauses on its step; when it is done, a checkpoint on that same step
    // shows (with the transaction) instead of playing straight on.
    const [hit] = this._crossed(k0, k1);
    if (hit) {
      const [i, e] = hit;
      this.armedTx.delete(i);
      this.postLive(e, { at: this.run.times[e.k], then: () => this._checkpointAt(e.k) || this.hooks.resume() });
      return true;
    }
    return this._checkpointIn(k0, k1);
  }

  /**
   * A forward jump from prevT to t (slider, step button): post every armed chain
   * event jumped over, in order, paused at t. Resolves when the posts are done.
   * Returns false at once when there was nothing to post.
   */
  jumped(prevT, t) {
    if (this.busy) return false;
    const k0 = this.run.stepAt(prevT), k1 = this.run.stepAt(t);
    const hits = this._crossed(k0, k1);
    if (!hits.length) return false;
    for (const [i] of hits) this.armedTx.delete(i);
    // Checkpoints jumped over are spent (the clock will not cross them again).
    for (const c of this.checkpoints) if (c.step > k0 && c.step <= k1) this.armed.delete(c.id);
    const run = async () => {
      for (const [n, [, e]] of hits.entries()) {
        if (n) await new Promise((r) => setTimeout(r, LIVE_GAP_MS));   // the uplink allows one call every 2 s
        await this.postLive(e, { at: t, then: null });
      }
    };
    return run();
  }

  /** Show and pause at an armed checkpoint on a step in (k0, k1]. True when one was found. */
  _checkpointIn(k0, k1) {
    for (const c of this.checkpoints) {
      if (this.armed.has(c.id) && c.step > k0 && c.step <= k1) {
        this._showCheckpoint(c, "");
        return true;
      }
    }
    return false;
  }

  /** The armed checkpoint exactly on step k, if any: show it after a live post there. */
  _checkpointAt(k) {
    const c = this.checkpoints.find((c) => this.armed.has(c.id) && c.step === k);
    if (!c) return false;
    const box = document.getElementById("checkpoint");
    this._showCheckpoint(c, box && !box.hidden ? box.innerHTML + "<br>" : "");
    return true;
  }

  _showCheckpoint(c, prefix) {
    this.armed.delete(c.id);
    this.active = c;
    const at = this.run.times[c.step];
    // Pause here and wait for Play. A page may decide where to pause (the
    // mission page lets the final-site drill play out first).
    if (this.hooks.checkpoint) this.hooks.checkpoint(at);
    else this.hooks.pause(at);
    this.hooks.note(`${prefix}<span class="cp-tag">Checkpoint</span> <b>${esc(c.label)}</b> · ${esc(c.say)}`);
  }

  /**
   * Sign and send one chain event through the uplink. `at`: where the clock
   * pauses meanwhile. `then`: what to do when it is done (default: play on);
   * null leaves the page paused with the result showing.
   */
  async postLive(e, { at, then } = {}) {
    this.busy = true;
    const run = this.run;
    this.hooks.pause(at ?? run.times[e.k]);
    const what = e.type === "find" ? `postFind for ${e.site_id || "the find"}` : `checkFind #${e.find_id}`;
    this.hooks.note(`<span class="cp-tag live">Live</span> Signing <b>${esc(what)}</b> as agency ${esc(e.agency)} and sending it to Monad testnet…`);
    let ok = false;
    try {
      let r;
      if (e.type === "find") {
        r = await this.live.postFind(e);
        this.liveFindIds.set(e.find_id, r.find_id);
      } else {
        const fid = this.liveFindIds.get(e.find_id);
        if (fid == null) throw new Error(`no live find to check (the find was not posted in this play-through)`);
        r = await this.live.checkFind(e, fid);
        this.liveChecked.add(fid);
      }
      e.recorded_tx = e.recorded_tx || e.tx;
      e.tx = r.tx;
      e.live = true;
      e.live_find_id = e.type === "find" ? r.find_id : this.liveFindIds.get(e.find_id);
      if (r.status) e.live_status = r.status;
      run.chain.explorer = run.chain.explorer || (this.live.info && this.live.info.explorer) || null;
      this.hooks.note(`<span class="cp-tag live">Live</span> <b>${esc(what)}</b> is on Monad testnet` +
        (e.type === "find" ? ` as find #${r.find_id}` : r.status ? `, now <b>${esc(r.status)}</b>` : "") +
        `. <a href="${r.url}" target="_blank" rel="noopener">${esc(short(r.tx))}</a>`);
      this.hooks.rerender();
      ok = true;
    } catch (err) {
      this.hooks.note(`<span class="cp-tag err">Live chain</span> ${esc(err.message)}. Showing the recorded transaction instead.`);
    } finally {
      this.busy = false;
    }
    if (then === undefined) this.hooks.resume();
    else if (then) then(ok);
    return ok;
  }
}

const LIVE_GAP_MS = 2200;

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const short = (h) => (h ? h.slice(0, 10) + "…" + h.slice(-6) : "");

/** Inject the player's small styles once; the design owner can move them to app.css. */
export function ensureStyles() {
  if (document.getElementById("mission-player-css")) return;
  const s = document.createElement("style");
  s.id = "mission-player-css";
  s.textContent = `
    .checkpoint { margin-top: 6px; padding: 8px 12px; border-left: 3px solid #d9b44a; background: rgba(217,180,74,.08); font-size: 13px; line-height: 1.4; }
    .checkpoint[hidden] { display: none; }
    .cp-tag { display: inline-block; font-size: 10px; letter-spacing: .08em; text-transform: uppercase; font-weight: 700; color: #d9b44a; margin-right: 6px; }
    .cp-tag.live { color: #4fd18b; } .cp-tag.err { color: #ff6b6b; }
    .cp-hint { opacity: .7; margin-left: 6px; }
    .checkpoint a { color: #8fb3ec; }
    .ticks .tick.cp { background: #d9b44a !important; width: 2px; height: 10px; top: -2px; }
    .feed .ev.live .tx::before { content: "LIVE "; color: #4fd18b; font-size: 10px; font-weight: 700; letter-spacing: .08em; }
  `;
  document.head.appendChild(s);
}
