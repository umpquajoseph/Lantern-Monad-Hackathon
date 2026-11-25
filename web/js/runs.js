// Runs list: every run in web/campaigns/index.json with its setup and results.
import { loadIndex, fmtNum, standinBanner, supersededBanner, sensitivityOf, NO_ROUGH_NOTE, esc } from "./campaign.js";

const $ = (id) => document.getElementById(id);

// [key, header, group, format, title]. Keys are summary.csv columns (docs/handoffs.md).
const COLS = [
  ["run_id", "Run", "", (v) => `<a class="mono">${esc(v)}</a>`, "World seed and settings hash"],
  ["seed", "Seed", "Setup", (v) => v, "World seed"],
  ["site", "Site", "Setup", (v) => (/shoemaker/i.test(v) ? `${esc(v)} <span class="tag x" title="Shoemaker LOLA window: a stand-in, not shadowed crater floor">stand-in</span>` : esc(v)), "Terrain site the run drove on"],
  ["n_scouts", "Scouts", "Setup", (v) => (v >= 4 ? `${v} <span class="tag x" title="Beyond the brief's fleet of 3">extrap.</span>` : v), "Fleet size. 4 and 5 go beyond the brief's 2030 fleet of 3"],
  ["check_policy", "Check policy", "Setup", (v) => `<span class="tag">${esc(v)}</span>`, "How position checks are scheduled"],
  ["map_refinement", "Map refinement", "Setup", (v) => (v ? "on" : "off"), "Do Scouts add newly seen craters to the shared map?"],
  ["top_speed_mps", "Top speed", "Setup", (v) => `${fmtNum(v * 100, 0)} cm/s`, ""],
  ["check_stop_s", "Check stop", "Setup", (v) => `${fmtNum(v, 0)} s`, "Time stopped per position check"],
  ["drill_limit_m", "Drill limit", "Setup", (v) => `${fmtNum(v, 0)} m`, "Position error allowed at a drill site"],
  ["crater_n_ge10_per_km2", "Craters ≥10 m", "World", (v) => `${fmtNum(v, 0)}/km²`, "Density of craters 10 m and larger"],
  ["drift_frac", "Drift", "World", (v) => `${fmtNum(v * 100, 1)}%`, "Odometry drift, share of distance"],
  ["sorties_ok", "Sorties ok", "Results", (v, r) => `${v}/${r.sorties}`, "Sorties home with the 25% reserve, of all sorties"],
  ["sorties_fault_return", "Fault returns", "Results", (v) => v, "Turned back on a camera, lamp or neutron fault"],
  ["scouts_lost", "Lost", "Results", (v) => v, "Scouts that never got home"],
  ["drill_err_p95_m", "Drill error p95", "Results", (v) => (v == null ? "–" : `${fmtNum(v, 1)} m`), "95th-percentile true position error at drill sites"],
  ["checks_per_sortie", "Checks/sortie", "Results", (v) => fmtNum(v, 1), ""],
  ["wrong_match_rate", "Wrong match", "Results", (v) => (v == null ? "–" : `${fmtNum(v * 100, 1)}%`), "Share of position checks that matched the wrong crater"],
  ["finds", "Finds", "Results", (v) => v, "Readings above the ice threshold"],
  ["confirmed", "Confirmed", "Results", (v) => v, "Finds a second Scout confirmed"],
  ["false_confirmed", "False conf.", "Results", (v) => v, "Confirmed, but no deposit there in the hidden truth"],
  ["deposits_missed", "Missed", "Results", (v, r) => (r.n_deposits != null ? `${v}/${r.n_deposits}` : v), "Hidden deposits with no confirmed find"],
  ["area_km2", "Area", "Results", (v) => `${fmtNum(v, 2)} km²`, "Area surveyed"],
  ["h_wait", "Dock wait", "Results", (v) => `${fmtNum(v, 1)} h`, "Hours spent queueing for the dock"],
];

const state = { batches: [], batch: null, sort: { key: "run_id", dir: 1 }, f: { scouts: "", policy: "", refine: "", q: "" } };

async function main() {
  try {
    state.batches = await loadIndex();
  } catch (e) {
    $("body").innerHTML = `<div class="empty">No runs yet. Run <code>python app.py</code>, which collects them from the campaign records.</div>`;
    throw e;
  }
  const p = new URLSearchParams(location.search);
  const bsel = $("batch");
  const groups = [["psr", "PSR terrain"], ["shoemaker", "Shoemaker stand-in terrain"], ["showcase", "Showcase (camera navigation)"], ["site", "This site's own stand-in"]];
  bsel.innerHTML = groups.map(([g, label]) => {
    const bs = state.batches.filter((b) => kind(b) === g).sort((x, y) => y.runs.length - x.runs.length || (y.made > x.made ? 1 : -1));
    return bs.length ? `<optgroup label="${label}">${bs.map((b) => `<option value="${esc(b.batch_id)}">${esc(b.batch_id)} · ${b.runs.length} run${b.runs.length === 1 ? "" : "s"}</option>`).join("")}</optgroup>` : "";
  }).join("");
  const want = p.get("batch");
  // Default: the largest batch the engine ran on PSR terrain; the latest wins a tie.
  const psr = state.batches.filter((b) => kind(b) === "psr").sort((x, y) => y.runs.length - x.runs.length || (y.made > x.made ? 1 : -1));
  state.batch = state.batches.find((b) => b.batch_id === want) || psr[0] ||
    state.batches.filter((b) => kind(b) === "shoemaker").sort((x, y) => y.runs.length - x.runs.length)[0] || state.batches[0];
  if (!state.batch) {
    $("body").innerHTML = `<div class="empty">No campaign batches found. The campaign engine writes them to <code>data/campaigns/</code>: run <code>python -m sim.live_runs</code>.</div>`;
    return;
  }
  bsel.value = state.batch.batch_id;
  bsel.onchange = () => pick(bsel.value);
  for (const [id, k] of [["f-scouts", "scouts"], ["f-policy", "policy"], ["f-refine", "refine"]]) $(id).onchange = (e) => { state.f[k] = e.target.value; draw(); };
  $("f-q").oninput = (e) => { state.f.q = e.target.value.trim().toLowerCase(); draw(); };
  head();
  fillFilters();
  draw();
}

// Which kind of batch: the engine on PSR terrain, the engine on the Shoemaker stand-in, the showcase, or this site's stand-in.
function kind(b) {
  if (b.showcase) return "showcase";
  if (b.batch_id === "standin" || /make_standin_campaign/.test(b.standin || "")) return "site";
  const sites = b.sites || [];
  return sites.length && sites.every((s) => !/shoemaker/i.test(s)) ? "psr" : "shoemaker";
}

function pick(id) {
  state.batch = state.batches.find((b) => b.batch_id === id) || state.batch;
  $("batch").value = state.batch.batch_id;
  const u = new URL(location.href);
  u.searchParams.set("batch", state.batch.batch_id);
  history.replaceState(null, "", u);
  fillFilters();
  draw();
}

function about(b) {
  const n = b.runs.length, maps = b.maps ?? n;
  const k = kind(b);
  const what = { psr: "The campaign engine on PSR terrain", shoemaker: "The campaign engine on the Shoemaker stand-in terrain, not shadowed crater floor",
    showcase: "The showcase run, with camera navigation; not a source of the paper's numbers", site: "This site's own stand-in, scripted; no number here is a result" }[k];
  const sites = (b.sites || []).length ? ` Sites: <b>${b.sites.map(esc).join(", ")}</b>.` : "";
  const made = b.made ? ` Written ${fmtDay(b.made)}.` : "";
  const withMaps = state.batches.filter((x) => x !== b && kind(x) === "psr" && x.maps);
  const m = maps === n ? "Click a run for its map and 3D replay." : maps ? `${maps} of ${n} have a map and replay; the rest list results only.` :
    "This batch saved results only, so its runs have no map or replay." +
    (withMaps.length ? ` PSR runs with maps: ${withMaps.map((x) => `<a href="#" data-b="${esc(x.batch_id)}">${esc(x.batch_id)}</a>`).join(", ")}.` : "");
  return `<span class="${k === "psr" ? "" : "warn"}">${what}.</span> <b>${esc(b.batch_id)}</b>: ${n} run${n === 1 ? "" : "s"}${b.worlds ? ` on ${b.worlds} worlds` : ""}.${sites}${made} ${m}`;
}

function fmtDay(iso) {
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

function fillFilters() {
  $("batch-about").innerHTML = about(state.batch);
  for (const a of document.querySelectorAll("#batch-about a")) a.onclick = (e) => { e.preventDefault(); pick(a.dataset.b); };
  const runs = state.batch.runs;
  const opts = (vals, fmt) => `<option value="">all</option>` + [...new Set(vals)].sort((a, b) => (a > b ? 1 : -1)).map((v) => `<option value="${esc(v)}">${esc(fmt(v))}</option>`).join("");
  $("f-scouts").innerHTML = opts(runs.map((r) => r.n_scouts), (v) => v);
  $("f-policy").innerHTML = opts(runs.map((r) => r.check_policy), (v) => v);
  $("f-refine").innerHTML = opts(runs.map((r) => String(r.map_refinement)), (v) => (v === "true" ? "on" : "off"));
  state.f = { scouts: "", policy: "", refine: "", q: $("f-q").value.trim().toLowerCase() };
  $("standin-slot").innerHTML = standinBanner(state.batch.showcase, "Showcase") + standinBanner(state.batch.standin) + supersededBanner(state.batch.batch_id)
    + (state.batch.runs.some((r) => sensitivityOf(r)) ? standinBanner(`Some runs in this batch are a ${NO_ROUGH_NOTE}.`, "Sensitivity") : "");
}

function head() {
  const groups = [];
  for (const c of COLS) {
    const g = groups[groups.length - 1];
    if (g && g.name === c[2]) g.n++; else groups.push({ name: c[2], n: 1 });
  }
  $("thead").innerHTML =
    `<tr class="groups">${groups.map((g) => `<th colspan="${g.n}">${g.name}</th>`).join("")}</tr>` +
    `<tr class="cols">${COLS.map((c, i) => `<th data-k="${c[0]}" title="${esc(c[4])}" class="${i && c[2] !== COLS[i - 1][2] ? "gl" : ""}">${c[1]}</th>`).join("")}</tr>`;
  for (const th of document.querySelectorAll("tr.cols th")) {
    th.onclick = () => {
      const k = th.dataset.k;
      state.sort = { key: k, dir: state.sort.key === k ? -state.sort.dir : 1 };
      draw();
    };
  }
}

function draw() {
  const { scouts, policy, refine, q } = state.f;
  let runs = state.batch.runs.filter((r) =>
    (!scouts || String(r.n_scouts) === scouts) && (!policy || r.check_policy === policy) &&
    (!refine || String(r.map_refinement) === refine) && (!q || r.run_id.toLowerCase().includes(q)));
  const { key, dir } = state.sort;
  runs = runs.slice().sort((a, b) => {
    const x = a[key], y = b[key];
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    return (x > y ? 1 : x < y ? -1 : 0) * dir;
  });
  for (const th of document.querySelectorAll("tr.cols th")) th.setAttribute("aria-sort", th.dataset.k === key ? (dir > 0 ? "ascending" : "descending") : "none");
  const b = encodeURIComponent(state.batch.batch_id);
  $("tbody").innerHTML = runs.map((r) => `<tr ${r.map === false ? 'class="nomap" title="Results only: this run saved no record, so it has no map"' : `data-href="index.html?batch=${b}&run=${encodeURIComponent(r.run_id)}"`}>${COLS.map((c, i) =>
    `<td class="${i === 0 ? "run" : ""}${i && c[2] !== COLS[i - 1][2] ? " gl" : ""}">${r[c[0]] == null && i ? "–" : c[3](r[c[0]], r)}</td>`).join("")}</tr>`).join("") ||
    `<tr><td colspan="${COLS.length}" class="empty">No run matches these filters.</td></tr>`;
  for (const tr of document.querySelectorAll("#tbody tr[data-href]")) tr.onclick = () => { location.href = tr.dataset.href; };
  $("count").textContent = `${runs.length} of ${state.batch.runs.length} runs`;
  // A run id typed here that lives in another batch: say where, and offer to go there.
  const hits = q ? state.batches.filter((x) => x !== state.batch && x.runs.some((r) => r.run_id.toLowerCase().includes(q))) : [];
  $("elsewhere").innerHTML = hits.length ? `"${esc(q)}" is in ` + hits.map((x) =>
    `<a href="#" data-b="${esc(x.batch_id)}">${esc(x.batch_id)}</a> (${x.runs.filter((r) => r.run_id.toLowerCase().includes(q)).length})`).join(", ") +
    (runs.length ? "" : `, not in ${esc(state.batch.batch_id)}.`) : "";
  for (const a of document.querySelectorAll("#elsewhere a")) a.onclick = (e) => { e.preventDefault(); pick(a.dataset.b); };
}

main();
