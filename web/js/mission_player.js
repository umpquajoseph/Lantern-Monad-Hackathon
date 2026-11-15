// Checkpoints and live chain calls for a mission replay (docs/handoffs.md,
// "Mission log"). The player owns two things the plain replay does not:
//
//  1. Checkpoints: while playing, crossing a checkpoint step with pause=true
//     stops the clock there and shows the checkpoint's label and line. Play
//     goes on. Jumping backwards re-arms the checkpoints after the new time,
//     so a second pass through pauses again.
//  2. Live chain: with ?key=... in the URL, crossing a step that holds a chain
//     event (find, check) pauses, posts that call for real through the site's
//     uplink (js/live_chain.js -> api/chain.js), swaps the event's hash for
//     the new transaction, and resumes. Going back to the start arms them
//     again, so every play-through writes fresh transactions. Without the key
//     the recorded hashes stay and the feed says "recorded".
//
// main.js calls: new MissionPlayer(run, hooks), player.tick(prevT, t) from its
// loop, player.rewound(t) on any backward jump, player.restart() at t = 0.
import { LiveChain } from "./live_chain.js";

export class MissionPlayer {
  constructor(run, hooks) {
    this.run = run;
    this.hooks = hooks;          // { pause(), resume(), note(html|null), rerender() }
    const m = run.log.mission || {};
    this.checkpoints = (m.checkpoints || []).filter((c) => c.pause).sort((a, b) => a.step - b.step);
    this.live = new LiveChain();
    this.liveFindIds = new Map();          // log find_id -> find_id posted in this play-through
    this.busy = false;
    this.restart();
    if (this.live.enabled) this.live.probe().then((info) => {
      if (!info || !info.signers || !info.signers.A || !info.signers.B) {
        this.hooks.note(`<b>Live chain off:</b> the uplink has no signing keys yet (${info ? "api/chain reachable" : "api/chain missing"}).`);
        this.live.key = null;
      }
    });
  }

  get liveOn() { return this.live.enabled; }

  /** Arm everything: a fresh play-through from the start. */
  restart() {
    this.armed = new Set(this.checkpoints.map((c) => c.id));
    this.armedTx = new Set(this.run.events.map((e, i) => i));
    this.active = null;
    this.hooks.note(null);
  }

  /** Arm the checkpoints after t again (a backward jump); live posts only re-arm from the start. */
  rewound(t) {
    const k = this.run.stepAt(t);
    for (const c of this.checkpoints) if (c.step > k) this.armed.add(c.id);
    if (t <= 0.5) this.restart();
    this.active = null;
    this.hooks.note(null);
  }

  /** Called each animation frame while playing, before render. Returns true when it paused. */
  tick(prevT, t) {
    if (this.busy) return true;
    const k0 = this.run.stepAt(prevT), k1 = this.run.stepAt(t);
    if (k1 <= k0) return false;
    // Live chain calls first: a chain event on a step just crossed.
    if (this.live.enabled) {
      for (const [i, e] of this.run.events.entries()) {
        if (this.armedTx.has(i) && e.k > k0 && e.k <= k1) {
          this.armedTx.delete(i);
          this.postLive(e);
          return true;
        }
      }
    }
    for (const c of this.checkpoints) {
      if (this.armed.has(c.id) && c.step > k0 && c.step <= k1) {
        this.armed.delete(c.id);
        this.active = c;
        this.hooks.pause(this.run.times[c.step]);
        this.hooks.note(`<span class="cp-tag">Checkpoint</span> <b>${esc(c.label)}</b> · ${esc(c.say)} <span class="cp-hint">Play to go on.</span>`);
        return true;
      }
    }
    return false;
  }

  async postLive(e) {
    this.busy = true;
    const run = this.run;
    this.hooks.pause(run.times[e.k]);
    const what = e.type === "find" ? `postFind for ${e.site_id || "the find"}` : `checkFind #${e.find_id}`;
    this.hooks.note(`<span class="cp-tag live">Live</span> Signing <b>${esc(what)}</b> as agency ${esc(e.agency)} and sending it to Monad testnet…`);
    try {
      let r;
      if (e.type === "find") {
        r = await this.live.postFind(e);
        this.liveFindIds.set(e.find_id, r.find_id);
      } else {
        const fid = this.liveFindIds.get(e.find_id);
        if (fid == null) throw new Error(`no live find to check (the find was not posted in this play-through)`);
        r = await this.live.checkFind(e, fid);
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
      this.hooks.resume();
    } catch (err) {
      this.hooks.note(`<span class="cp-tag err">Live chain</span> ${esc(err.message)}. Showing the recorded transaction instead.`);
      this.hooks.resume();
    } finally {
      this.busy = false;
    }
  }
}

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
