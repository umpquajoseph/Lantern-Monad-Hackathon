// Checkpoints for a Mission Control replay: while playing, crossing a
// checkpoint pauses there at once and shows its label until Play goes on
// (the hackathon page's rule: "when it reaches a checkpoint it hits the pause
// button and doesnt wait at all"). Jumping backwards re-arms the checkpoints
// after the new time, so a second pass pauses again; jumping forward over one
// does not pause.
//
// mission.js builds the list from the campaign record's events (a find, a
// second Scout's search result, a drill) and calls tick(prevT, t) from its
// loop, rewound(t) on any backward jump, restart() at the sortie's start.
export class MissionPlayer {
  // checkpoints: [{t, label, say}], hooks: {pause(t), note(html | null)}
  constructor(checkpoints, hooks) {
    this.list = checkpoints.slice().sort((a, b) => a.t - b.t);
    this.hooks = hooks;
    this.restart();
  }

  restart() { this.next = 0; this.hooks.note(null); }

  rewound(t) {
    this.next = this.list.findIndex((c) => c.t > t);
    if (this.next < 0) this.next = this.list.length;
    this.hooks.note(null);
  }

  // A forward jump: checkpoints passed over are spent without pausing.
  jumped(t) {
    while (this.next < this.list.length && this.list[this.next].t <= t) this.next++;
  }

  tick(prevT, t) {
    const c = this.list[this.next];
    if (!c || !(prevT < c.t && t >= c.t)) return false;
    this.next++;
    this.hooks.pause(c.t);
    this.hooks.note(`<span class="cp-tag">Checkpoint</span> <b>${c.label}</b>${c.say ? ` · ${c.say}` : ""} <span class="cp-hint">Play goes on.</span>`);
    return true;
  }
}

// The checkpoint card's look (the hackathon page added it from here).
export function ensureStyles() {
  if (document.getElementById("mp-styles")) return;
  const st = document.createElement("style");
  st.id = "mp-styles";
  st.textContent = `.cp-float{position:fixed;left:50%;transform:translateX(-50%);bottom:calc(var(--bar-h) + 70px);z-index:13;
    background:var(--glass-2);border:1px solid #d9b44a;border-radius:10px;padding:8px 14px;max-width:min(640px,90vw);font-size:14px;cursor:pointer}
  .cp-tag{color:#d9b44a;font-weight:600;text-transform:uppercase;font-size:11px;letter-spacing:.12em;margin-right:4px}
  .cp-hint{color:var(--dim);font-size:12px;margin-left:6px}`;
  document.head.appendChild(st);
}
