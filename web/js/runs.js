// Runs list: every run in web/campaigns/index.json with its setup and results.
import { loadIndex, fmtNum, standinBanner, esc } from "./campaign.js";

const $ = (id) => document.getElementById(id);

// [key, header, group, format, title]. Keys are summary.csv columns (docs/handoffs.md).
const COLS = [
  ["run_id", "Run", "", (v) => `<a class="mono">${esc(v)}</a>`, "World seed and settings hash"],
  ["seed", "Seed", "Setup", (v) => v, "World seed"],
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
  bsel.innerHTML = state.batches.map((b) => `<option value="${esc(b.batch_id)}">${esc(b.batch_id)} (${b.runs.length} runs)${b.batch_id === "standin" ? " · site stand-in" : ""}</option>`).join("");
  const want = p.get("batch");
  // Default: the biggest batch the engine wrote, not the site's own stand-in.
  const real = state.batches.filter((b) => b.batch_id !== "standin").sort((x, y) => y.runs.length - x.runs.length);
  state.batch = state.batches.find((b) => b.batch_id === want) || real[0] || state.batches[0];
  if (!state.batch) {
    $("body").innerHTML = `<div class="empty">No campaign batches found. The campaign engine writes them to <code>data/campaigns/</code>; <code>python web/make_standin_campaign.py</code> makes a labelled stand-in.</div>`;
    return;
  }
  bsel.value = state.batch.batch_id;
  bsel.onchange = () => { state.batch = state.batches.find((b) => b.batch_id === bsel.value); fillFilters(); draw(); };
  for (const [id, k] of [["f-scouts", "scouts"], ["f-policy", "policy"], ["f-refine", "refine"]]) $(id).onchange = (e) => { state.f[k] = e.target.value; draw(); };
  $("f-q").oninput = (e) => { state.f.q = e.target.value.trim().toLowerCase(); draw(); };
  head();
  fillFilters();
  draw();
}

function fillFilters() {
  const runs = state.batch.runs;
  const opts = (vals, fmt) => `<option value="">all</option>` + [...new Set(vals)].sort((a, b) => (a > b ? 1 : -1)).map((v) => `<option value="${esc(v)}">${esc(fmt(v))}</option>`).join("");
  $("f-scouts").innerHTML = opts(runs.map((r) => r.n_scouts), (v) => v);
  $("f-policy").innerHTML = opts(runs.map((r) => r.check_policy), (v) => v);
  $("f-refine").innerHTML = opts(runs.map((r) => String(r.map_refinement)), (v) => (v === "true" ? "on" : "off"));
  state.f = { scouts: "", policy: "", refine: "", q: $("f-q").value.trim().toLowerCase() };
  $("standin-slot").innerHTML = standinBanner(state.batch.showcase, "Showcase") + standinBanner(state.batch.standin);
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
  $("tbody").innerHTML = runs.map((r) => `<tr data-href="run.html?batch=${b}&run=${encodeURIComponent(r.run_id)}">${COLS.map((c, i) =>
    `<td class="${i === 0 ? "run" : ""}${i && c[2] !== COLS[i - 1][2] ? " gl" : ""}">${r[c[0]] == null && i ? "–" : c[3](r[c[0]], r)}</td>`).join("")}</tr>`).join("") ||
    `<tr><td colspan="${COLS.length}" class="empty">No run matches these filters.</td></tr>`;
  for (const tr of document.querySelectorAll("#tbody tr[data-href]")) tr.onclick = () => { location.href = tr.dataset.href; };
  $("count").textContent = `${runs.length} of ${state.batch.runs.length} runs`;
}

main();
