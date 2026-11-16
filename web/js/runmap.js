// One run, top down: sortie paths, the ice map, the crater map and the dock,
// with a slider through the sorties and the hidden truth behind a reveal.
// Everything drawn comes from the run record and its poses (docs/handoffs.md).
import {
  CampaignRun, SCOUT_COLOR, STATUS_COLOR, STATUS_WORD, OUTCOME_WORD, OUTCOME_SHORT, TIME_KINDS,
  fmtNum, fmtDay, fmtHM, timeBar, timeLegend, standinBanner, standinOf, bannersOf, windowFor, esc,
} from "./campaign.js";
import { Terrain } from "./data.js";

const $ = (id) => document.getElementById(id);
const P = new URLSearchParams(location.search);
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

const state = { run: null, k: 0, truth: P.get("truth") === "1", allPaths: true, playing: null, hover: null };
const view = { cx: 0, cy: 0, s: 1 };
let cv, ctx, W = 0, H = 0, dpr = 1;
let shade = [];   // [{img, x0, y0, x1, y1}] hillshade layers, world rect of the image
let estPaths = {}, truePaths = {};

async function main() {
  const batch = P.get("batch"), id = P.get("run");
  $("back").href = `index.html${batch ? `?batch=${encodeURIComponent(batch)}` : ""}`;
  let run;
  try {
    run = await CampaignRun.load(batch, id);
  } catch (e) {
    $("run-title").textContent = "Run not found";
    $("page").innerHTML = `<div class="empty">No record for this run. Go back to the runs list.</div>`;
    throw e;
  }
  state.run = run;
  const rec = run.rec;
  $("run-title").textContent = rec.run_id;
  document.title = `Lantern · ${rec.run_id}`;
  const banners = bannersOf(rec);
  if (banners) { $("standin-slot").innerHTML = banners; $("page").classList.add("has-banner"); }

  for (const s of rec.sorties) {
    const rows = run.rows(s.sortie_id), c = run.col;
    estPaths[s.sortie_id] = rows.map((r) => [r[c.est_x], r[c.est_y]]);
    truePaths[s.sortie_id] = rows.map((r) => [r[c.true_x], r[c.true_y]]);
  }
  cv = $("map");
  ctx = cv.getContext("2d");
  new ResizeObserver(resize).observe($("mapbox"));
  resize();
  fit();
  bind();
  info();

  const n = run.sorties.length;
  $("k").max = n;
  const k0 = P.has("k") ? Number(P.get("k")) : n;
  setK(Math.max(0, Math.min(n, k0)));
  ticks();
  loadShade(rec).then(draw).catch(() => {});
}

// ----------------------------------------------------------------- ground
// Hillshade under the map from the 3D viewer's terrain assets, when the run
// is on the tile they were built from. Display only.
async function loadShade(rec) {
  if (!rec.terrain || !/shoemaker/i.test(rec.terrain.site || rec.terrain.file || "")) return;
  const T = await Terrain.load("assets/terrain/");
  if (!/shoemaker/i.test(T.meta.source || "")) return;
  const far = await T.loadBackdrop("backdrop");
  const near = await T.loadBackdrop("backdrop_near");
  const win = await windowFor(rec);
  let wgrid = null;
  if (win) { T.meta.window = win; wgrid = await T.loadBackdrop("window"); }
  for (const g of [far, near, wgrid, T.coarse]) if (g) shade.push(hillshade(g));
  $("mapnote").textContent = "Ground: LOLA elevation, 20 m and 5 m (real). Inside the 1 km drive area, 1 m with made-up small craters." +
    (rec.crater_map.scope ? ` Craters: ${rec.crater_map.scope}.` : "");
}

function hillshade(g) {
  const { rows, cols, cell, z } = g;
  const c = document.createElement("canvas");
  c.width = cols; c.height = rows;
  const im = c.getContext("2d").createImageData(cols, rows);
  const az = (315 * Math.PI) / 180, el = (35 * Math.PI) / 180;
  const lx = Math.cos(el) * Math.sin(az), ly = Math.cos(el) * Math.cos(az), lz = Math.sin(el);
  for (let i = 0; i < rows; i++) for (let j = 0; j < cols; j++) {
    const jl = Math.max(0, j - 1), jr = Math.min(cols - 1, j + 1), id = Math.max(0, i - 1), iu = Math.min(rows - 1, i + 1);
    const dzdx = (z[i * cols + jr] - z[i * cols + jl]) / ((jr - jl) * cell);
    const dzdy = (z[iu * cols + j] - z[id * cols + j]) / ((iu - id) * cell);
    const n = 1 / Math.hypot(dzdx, dzdy, 1);
    const L = Math.max(0, (-dzdx * lx - dzdy * ly + lz) * n);
    const v = 14 + 70 * L;
    const p = 4 * ((rows - 1 - i) * cols + j);   // rows run north; image row 0 is the north edge
    im.data[p] = v * 0.96; im.data[p + 1] = v * 0.98; im.data[p + 2] = v; im.data[p + 3] = 255;
  }
  c.getContext("2d").putImageData(im, 0, 0);
  return { img: c, x0: g.x0 - cell / 2, y0: g.y0 - cell / 2, x1: g.x0 + (cols - 0.5) * cell, y1: g.y0 + (rows - 0.5) * cell };
}

// ------------------------------------------------------------------- view
function resize() {
  const box = $("mapbox").getBoundingClientRect();
  dpr = Math.min(2, window.devicePixelRatio || 1);
  W = box.width; H = box.height;
  cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
  draw();
}

function fit() {
  const [x0, y0, x1, y1] = state.run.bounds();
  const d = state.run.rec.dock;
  const X0 = Math.min(x0, d.x), X1 = Math.max(x1, d.x), Y0 = Math.min(y0, d.y), Y1 = Math.max(y1, d.y);
  view.cx = (X0 + X1) / 2; view.cy = (Y0 + Y1) / 2;
  view.s = 0.9 * Math.min(W / Math.max(1, X1 - X0), H / Math.max(1, Y1 - Y0));
  draw();
}

const sx = (x) => (x - view.cx) * view.s + W / 2;
const sy = (y) => H / 2 - (y - view.cy) * view.s;
const wx = (X) => (X - W / 2) / view.s + view.cx;
const wy = (Y) => view.cy - (Y - H / 2) / view.s;

function bind() {
  let drag = null;
  cv.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY, cx: view.cx, cy: view.cy }; cv.setPointerCapture(e.pointerId); cv.classList.add("drag"); });
  cv.addEventListener("pointermove", (e) => {
    if (drag) {
      view.cx = drag.cx - (e.clientX - drag.x) / view.s;
      view.cy = drag.cy + (e.clientY - drag.y) / view.s;
      draw();
    } else hover(e);
  });
  cv.addEventListener("pointerup", () => { drag = null; cv.classList.remove("drag"); });
  cv.addEventListener("wheel", (e) => {
    e.preventDefault();
    const r = cv.getBoundingClientRect(), X = e.clientX - r.left, Y = e.clientY - r.top;
    const x = wx(X), y = wy(Y);
    view.s *= Math.exp(-e.deltaY * 0.0015);
    view.s = Math.max(0.02, Math.min(40, view.s));
    view.cx = x - (X - W / 2) / view.s; view.cy = y + (Y - H / 2) / view.s;
    draw();
  }, { passive: false });
  $("k").oninput = (e) => { stop(); setK(Number(e.target.value)); };
  $("truth-btn").onclick = () => { state.truth = !state.truth; drawSide(); draw(); urlState(); };
  $("paths-btn").onclick = () => { state.allPaths = !state.allPaths; drawSide(); draw(); };
  $("fit-btn").onclick = fit;
  $("play").onclick = () => {
    if (state.playing) return stop();
    if (state.k >= state.run.sorties.length) setK(0);
    $("play").textContent = "Pause";
    state.playing = setInterval(() => {
      if (state.k >= state.run.sorties.length) return stop();
      setK(state.k + 1);
    }, 700);
  };
  window.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" && e.target.type !== "range") return;
    if (e.key === "ArrowRight") { stop(); setK(Math.min(state.run.sorties.length, state.k + 1)); e.preventDefault(); }
    if (e.key === "ArrowLeft") { stop(); setK(Math.max(0, state.k - 1)); e.preventDefault(); }
  });
}

function stop() {
  if (state.playing) clearInterval(state.playing);
  state.playing = null;
  $("play").textContent = "Play";
}

function urlState() {
  const q = new URLSearchParams(location.search);
  q.set("k", state.k);
  if (state.truth) q.set("truth", "1"); else q.delete("truth");
  history.replaceState(null, "", `?${q}`);
}

function setK(k) {
  state.k = k;
  $("k").value = k;
  const run = state.run, n = run.sorties.length;
  const s = run.sorties[k - 1];
  $("k-lab").innerHTML = k === 0 ? `Before the first sortie <span class="dim">· 0 of ${n}</span>` :
    `After <b>${s.sortie_id}</b> <span class="dim">· ${k} of ${n} · home ${fmtDay(s.t_end)}</span>`;
  urlState();
  drawSide();
  draw();
}

function ticks() {
  const run = state.run, n = run.sorties.length;
  $("ticks").innerHTML = run.sorties.map((s, i) =>
    `<i style="left:${(100 * (i + 1)) / n}%;background:${SCOUT_COLOR[s.scout] || "#aaa"};${s.outcome !== "ok" ? "height:10px;" : ""}" title="${s.sortie_id}: ${OUTCOME_WORD[s.outcome] || s.outcome}"></i>`).join("");
  // The ticks sit under the range thumb's travel; inset them by the thumb's half width.
  const t = $("ticks"), r = $("k").getBoundingClientRect(), row = t.getBoundingClientRect();
  t.style.marginLeft = `${r.left - row.left + 8}px`;
  t.style.width = `${r.width - 16}px`;
}

// ------------------------------------------------------------------- draw
function draw() {
  if (!ctx || !state.run) return;
  const run = state.run, rec = run.rec, k = state.k;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = "#050506";
  ctx.fillRect(0, 0, W, H);
  for (const L of shade) {
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(L.img, sx(L.x0), sy(L.y1), (L.x1 - L.x0) * view.s, (L.y1 - L.y0) * view.s);
  }
  const vis = (x, y, r) => sx(x) + r * view.s > -20 && sx(x) - r * view.s < W + 20 && sy(y) + r * view.s > -20 && sy(y) - r * view.s < H + 20;

  // Crater map: the Scouts' starting map, then craters they added.
  ctx.lineWidth = 1;
  ctx.strokeStyle = "rgba(220,225,232,.42)";
  ctx.beginPath();
  for (const [x, y, d] of rec.crater_map.start) {
    const r = d / 2;
    if (r * view.s < 0.8 || !vis(x, y, r)) continue;
    ctx.moveTo(sx(x) + r * view.s, sy(y));
    ctx.arc(sx(x), sy(y), r * view.s, 0, 2 * Math.PI);
  }
  ctx.stroke();
  const added = run.addedAfter(k), justAdded = new Set((k > 0 && run.addedBy[run.sorties[k - 1].sortie_id] || []).map((c) => c.crater_id));
  ctx.strokeStyle = css("--added");
  for (const c of added) {
    const r = Math.max(c.d / 2 * view.s, 2.5);
    ctx.lineWidth = justAdded.has(c.crater_id) ? 2.2 : 1.3;
    ctx.beginPath();
    ctx.arc(sx(c.x), sy(c.y), r, 0, 2 * Math.PI);
    ctx.stroke();
  }

  // Hidden truth, behind the reveal: craters missing from the map, the real ice, true paths.
  if (state.truth) {
    // An added crater counts as found only if it is in the right place (crossing: `misplaced`).
    const addedTrue = new Set(added.filter((c) => !c.misplaced && c.true_crater != null).map((c) => c.true_crater));
    ctx.setLineDash([3, 3]);
    ctx.strokeStyle = "rgba(255,122,107,.55)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    rec.truth.craters_missing.forEach(([x, y, d], i) => {
      const r = d / 2;
      if (addedTrue.has(i) || r * view.s < 0.8 || !vis(x, y, r)) return;
      ctx.moveTo(sx(x) + r * view.s, sy(y));
      ctx.arc(sx(x), sy(y), r * view.s, 0, 2 * Math.PI);
    });
    ctx.stroke();
    // Added in the wrong place: an orange cross over the Scouts' ring, a line to the real crater.
    ctx.strokeStyle = css("--truth-miss");
    ctx.lineWidth = 1.5;
    for (const c of added) {
      if (!c.misplaced) continue;
      const X = sx(c.x), Y = sy(c.y), q = Math.max(3, c.d / 2 * view.s * 0.7);
      ctx.beginPath(); ctx.moveTo(X - q, Y - q); ctx.lineTo(X + q, Y + q); ctx.moveTo(X + q, Y - q); ctx.lineTo(X - q, Y + q);
      const t = c.true_crater != null && rec.truth.craters_missing[c.true_crater];
      if (t) { ctx.moveTo(X, Y); ctx.lineTo(sx(t[0]), sy(t[1])); }
      ctx.stroke();
    }
    ctx.setLineDash([5, 4]);
    for (const d of rec.truth.ice) {
      ctx.strokeStyle = css("--truth");
      ctx.fillStyle = "rgba(216,243,255,.10)";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(sx(d.x), sy(d.y), Math.max(3, d.radius * view.s), 0, 2 * Math.PI);
      ctx.fill(); ctx.stroke();
    }
    ctx.setLineDash([]);
  }

  // Sortie paths, where each Scout believed it drove. The picked sortie on top.
  const done = run.sorties.slice(0, k);
  const cur = k > 0 ? run.sorties[k - 1] : null;
  for (const s of done) {
    if (s === cur || !state.allPaths) continue;
    line(estPaths[s.sortie_id], SCOUT_COLOR[s.scout], 1, 0.35);
  }
  if (cur) {
    if (state.truth) line(truePaths[cur.sortie_id], "#ffffff", 1.2, 0.8, [4, 3]);
    line(estPaths[cur.sortie_id], SCOUT_COLOR[cur.scout], 2.2, 1);
  }

  // Ice map: each find as a circle of its 95% position bound, coloured by status.
  const finds = run.findsFrom(run.eventsKnown(k, run.iceEvents));
  state.finds = finds;
  for (const f of finds) {
    const col = STATUS_COLOR[f.status] || "#fff";
    const r = Math.max(3.5, f.bound95 * view.s);
    const X = sx(f.est[0]), Y = sy(f.est[1]);
    if (state.truth && f.x != null && f.y != null) {
      // Where the reading really was: the gap is the navigation error.
      ctx.strokeStyle = "rgba(216,243,255,.8)";
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(X, Y); ctx.lineTo(sx(f.x), sy(f.y)); ctx.stroke();
      ctx.fillStyle = css("--truth");
      ctx.fillRect(sx(f.x) - 2, sy(f.y) - 2, 4, 4);
    }
    ctx.fillStyle = col + "30";
    ctx.strokeStyle = col;
    ctx.lineWidth = f === state.hover ? 3 : 2;
    ctx.beginPath(); ctx.arc(X, Y, r, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
    ctx.fillStyle = col;
    ctx.beginPath(); ctx.arc(X, Y, 2, 0, 2 * Math.PI); ctx.fill();
    if (view.s > 0.6) label(`${f.find_id}`, X + r + 3, Y - r * 0.7, col);
  }

  // The dock.
  const d = rec.dock, DX = sx(d.x), DY = sy(d.y);
  ctx.fillStyle = "#fff";
  ctx.strokeStyle = "#000";
  ctx.lineWidth = 2;
  ctx.beginPath(); ctx.rect(DX - 6, DY - 6, 12, 12); ctx.fill(); ctx.stroke();
  label("Dock", DX + 10, DY + 4, "#fff");

  if (state.hover) tooltip(state.hover);
  scaleBar();
}

function line(pts, color, w, alpha, dash) {
  if (!pts || pts.length < 2) return;
  ctx.globalAlpha = alpha;
  ctx.strokeStyle = color;
  ctx.lineWidth = w;
  ctx.lineJoin = "round";
  ctx.setLineDash(dash || []);
  ctx.beginPath();
  ctx.moveTo(sx(pts[0][0]), sy(pts[0][1]));
  for (let i = 1; i < pts.length; i++) ctx.lineTo(sx(pts[i][0]), sy(pts[i][1]));
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.globalAlpha = 1;
}

function label(text, x, y, color) {
  ctx.font = "600 11.5px Barlow, sans-serif";
  ctx.lineWidth = 3;
  ctx.strokeStyle = "rgba(0,0,0,.85)";
  ctx.strokeText(text, x, y);
  ctx.fillStyle = color;
  ctx.fillText(text, x, y);
}

function scaleBar() {
  const target = 120 / view.s;
  const p = Math.pow(10, Math.floor(Math.log10(target)));
  const m = [1, 2, 5, 10].map((f) => f * p).filter((v) => v <= target).pop() || p;
  $("scale").innerHTML = `<i style="width:${m * view.s}px"></i>${m >= 1000 ? `${m / 1000} km` : `${m} m`} · north up · drag to pan, scroll to zoom`;
}

function hover(e) {
  const r = cv.getBoundingClientRect(), X = e.clientX - r.left, Y = e.clientY - r.top;
  let best = null, bd = 12;
  for (const f of state.finds || []) {
    const d = Math.hypot(sx(f.est[0]) - X, sy(f.est[1]) - Y);
    const rr = Math.max(3.5, f.bound95 * view.s);
    if (d < Math.max(bd, rr)) { best = f; bd = d; }
  }
  if (best !== state.hover) { state.hover = best; draw(); }
}

function tooltip(f) {
  const X = sx(f.est[0]), Y = sy(f.est[1]);
  const lines = [`Find ${f.find_id} by ${f.scout}, ${f.sortie_id}`, `${STATUS_WORD[f.status]}`,
    `reading ${fmtNum(f.reading_wt, 2)} wt%, bound ±${fmtNum(f.bound95, 1)} m (95%)`];
  for (const s of f.searches) lines.push(`searched by ${s.scout}, ${s.sortie_id}: ${s.result}`);
  if (f.drill) lines.push(`drilled by ${f.drill.scout}, ${f.drill.sortie_id}`);
  if (state.truth && f.x != null) {
    lines.push(`truth: reading ${fmtNum(Math.hypot(f.x - f.est[0], f.y - f.est[1]), 1)} m from its estimate`);
    const se = f.searches[f.searches.length - 1];
    if (se) lines.push(`truth: ${se.truth_has_deposit ? "a deposit is there" : "no deposit there"}`);
    if (f.drill) lines.push(`truth: drill ${fmtNum(f.drill.true_err_m, 1)} m from the reading`);
  }
  ctx.font = "13px Barlow, sans-serif";
  const w = Math.max(...lines.map((l) => ctx.measureText(l).width)) + 18, h = lines.length * 17 + 10;
  let x = X + 14, y = Y + 14;
  if (x + w > W - 8) x = X - 14 - w;
  if (y + h > H - 8) y = Y - 14 - h;
  ctx.fillStyle = "rgba(21,23,26,.95)";
  ctx.strokeStyle = "rgba(255,255,255,.16)";
  ctx.lineWidth = 1;
  ctx.beginPath(); ctx.roundRect(x, y, w, h, 5); ctx.fill(); ctx.stroke();
  lines.forEach((l, i) => {
    ctx.fillStyle = i === 0 ? "#fff" : l.startsWith("truth") ? css("--truth") : "#c5c9cf";
    ctx.fillText(l, x + 9, y + 20 + i * 17);
  });
}

// ------------------------------------------------------------------- side
function drawSide() {
  const run = state.run, rec = run.rec, k = state.k;
  $("truth-btn").classList.toggle("on", state.truth);
  $("truth-btn").setAttribute("aria-pressed", String(state.truth));
  $("truth-btn").textContent = state.truth ? "Hide hidden truth" : "Reveal hidden truth";
  $("paths-btn").textContent = state.allPaths ? "This sortie's path only" : "All paths";

  // Legend.
  const scouts = rec.scouts.map((s) => `<li><i class="sw line" style="--c:${SCOUT_COLOR[s]}"></i>${s}${k > 0 && run.sorties[k - 1].scout === s ? " <span class='dim'>(this sortie)</span>" : ""}</li>`).join("");
  $("legend").innerHTML = `<h3>Paths, as each Scout believed</h3><ul>${scouts}</ul>
    <h3>Ice map</h3><ul>${["find", "confirmed", "unconfirmed", "drilled"].map((s) => `<li><i class="sw ring" style="--c:${STATUS_COLOR[s]}"></i>${STATUS_WORD[s]}</li>`).join("")}
    <li class="dim" style="font-size:11.5px">Circle: the Scout's 95% position bound</li></ul>
    <h3>Crater map</h3><ul><li><i class="sw ring" style="--c:rgba(220,225,232,.6)"></i>Starting map, from orbit</li>
    <li><i class="sw ring" style="--c:${css("--added")}"></i>Added by the Scouts</li>
    <li><i class="sw sq" style="--c:#fff"></i>Dock</li></ul>
    ${state.truth ? `<h3 style="color:var(--truth)">Hidden truth: the Scouts never see this</h3><ul>
    <li class="truthrow"><i class="sw ring dash" style="--c:var(--truth)"></i>Real ice deposit</li>
    <li class="truthrow"><i class="sw ring dash" style="--c:var(--truth-miss)"></i>Crater missing from their map</li>
    <li class="truthrow"><span style="color:var(--truth-miss);width:14px;text-align:center;font-weight:600">✕</span>Added in the wrong place, line to the real crater</li>
    <li class="truthrow"><i class="sw line" style="--c:#fff"></i>Where this sortie really drove</li>
    <li class="truthrow"><i class="sw sq" style="--c:var(--truth)"></i>Where a find's reading really was</li></ul>` : ""}`;

  // This sortie.
  const s = k > 0 ? run.sorties[k - 1] : null;
  if (!s) {
    $("sortie").innerHTML = `<p class="dim">No sortie flown yet. The map shows the Scouts' starting crater map from orbit. Drag the slider to fly the sorties.</p>`;
  } else {
    const ev = run.events.filter((e) => e.sortie_id === s.sortie_id);
    const cnt = (t, f = () => true) => ev.filter((e) => e.type === t && f(e)).length;
    const add = (run.addedBy[s.sortie_id] || []).length;
    const total = Object.values(s.time_s).reduce((a, b) => a + b, 0);
    $("sortie").innerHTML = `<div class="card full">
      <h3><i class="sdot" style="background:${SCOUT_COLOR[s.scout]}"></i>${s.sortie_id} <span class="outcome ${s.outcome}">${OUTCOME_WORD[s.outcome] || s.outcome}</span>
        <span class="grow" style="flex:1"></span><a class="btn sm" href="replay.html?batch=${encodeURIComponent(run.batch)}&run=${encodeURIComponent(rec.run_id)}&sortie=${encodeURIComponent(s.sortie_id)}">Replay in 3D</a></h3>
      <dl class="kv">
        <dt>Launched</dt><dd>${fmtDay(s.t_start)}</dd>
        <dt>${s.outcome === "showcase" ? "Ended" : "Home"}</dt><dd>${fmtDay(s.t_end)} <span class="dim">(${fmtHM(s.t_end - s.t_start)})</span></dd>
        <dt>Distance</dt><dd>${fmtNum(s.distance_m / 1000, 2)} km</dd>
        <dt>Position checks</dt><dd>${s.checks}, ${s.wrong_matches} wrong match${s.wrong_matches === 1 ? "" : "es"}</dd>
        <dt>Battery at the dock</dt><dd>${fmtNum(s.energy_end_kwh, 2)} kWh <span class="dim">of ${fmtNum(rec.settings.battery_kwh, 1)}</span></dd>
        ${s.faults.length ? `<dt>Faults</dt><dd>${s.faults.join(", ")}</dd>` : ""}
      </dl>
      <div style="margin-top:10px">${timeBar(s.time_s, total, true)}${timeLegend(s.time_s)}</div>
      <p class="evlist" style="margin:10px 0 0">Finds <b>${cnt("find")}</b> · searches <b>${cnt("search_end")}</b>
        (${cnt("search_end", (e) => e.result === "confirmed")} confirmed) · drills <b>${cnt("drill")}</b> ·
        unmatched rims logged <b>${cnt("sighting")}</b> · craters added at the dock <b>${add}</b></p>
      ${checksHtml(run, s)}
    </div>`;
  }

  // Maps after k sorties.
  const finds = run.findsFrom(run.eventsKnown(k, run.iceEvents));
  const by = (st) => finds.filter((f) => f.status === st).length;
  const addedList = run.addedAfter(k), added = addedList.length;
  const misplaced = addedList.filter((c) => c.misplaced).length;
  $("mapstate").innerHTML = `<div class="cards">
    <div class="card"><h3>Ice map</h3><dl class="kv">
      ${["find", "confirmed", "unconfirmed", "drilled"].map((st) => `<dt><i class="sdot" style="background:${STATUS_COLOR[st]}"></i> ${st}</dt><dd>${by(st)}</dd>`).join("")}
    </dl></div>
    <div class="card"><h3>Crater map</h3><dl class="kv">
      <dt>from orbit</dt><dd>${rec.crater_map.start.length}</dd>
      <dt>added</dt><dd>${added}</dd>
      ${state.truth ? `<dt style="color:var(--truth)">added in the wrong place (truth)</dt><dd style="color:var(--truth)">${misplaced}</dd>
      <dt style="color:var(--truth)">still missing (truth)</dt><dd style="color:var(--truth)">${rec.truth.craters_missing.length - (added - misplaced)}</dd>` : ""}
    </dl></div></div>`;

  // Every sortie with its time bar.
  const scale = Math.max(...run.sorties.map((x) => Object.values(x.time_s).reduce((a, b) => a + b, 0)));
  $("tl-legend").innerHTML = timeLegend(run.sorties.reduce((acc, x) => { for (const [kk] of TIME_KINDS) acc[kk] = (acc[kk] || 0) + (x.time_s[kk] || 0); return acc; }, {}))
    .replace('class="tlegend"', 'class="tlegend" title="Totals over the whole run" style="margin:0 0 8px"');
  $("sorties").innerHTML = run.sorties.map((x, i) => `<li data-k="${i + 1}" class="${i + 1 === k ? "cur" : ""}${i + 1 > k ? " future" : ""}">
      <span class="k">${i + 1}</span>
      <span class="id"><i class="sdot" style="background:${SCOUT_COLOR[x.scout]}"></i> ${x.sortie_id}${x.outcome !== "ok" ? ` <span class="o" title="${OUTCOME_WORD[x.outcome]}">${OUTCOME_SHORT[x.outcome] || x.outcome}</span>` : ""}</span>
      ${timeBar(x.time_s, scale)}
      <a href="replay.html?batch=${encodeURIComponent(run.batch)}&run=${encodeURIComponent(rec.run_id)}&sortie=${encodeURIComponent(x.sortie_id)}" title="Replay ${x.sortie_id} in 3D">3D ›</a></li>`).join("");
  for (const li of document.querySelectorAll("#sorties li")) li.onclick = (e) => { if (e.target.tagName !== "A") { stop(); setK(Number(li.dataset.k)); } };
}

// Cross-checks in this sortie: this Scout searching another's find, or
// another Scout searching one of this sortie's finds. Each opens the replay
// just before the search, following the searcher.
function checksHtml(run, s) {
  const finds = new Map(run.events.filter((e) => e.type === "find").map((e) => [e.find_id, e]));
  const rows = run.events.filter((e) => e.type === "search_end" &&
    (e.sortie_id === s.sortie_id || (finds.get(e.find_id) || {}).sortie_id === s.sortie_id));
  if (!rows.length) return "";
  const li = rows.map((e) => {
    const st = run.events.find((x) => x.type === "search_start" && x.find_id === e.find_id && x.sortie_id === e.sortie_id) || e;
    const f = finds.get(e.find_id) || {};
    const href = `replay.html?batch=${encodeURIComponent(run.batch)}&run=${encodeURIComponent(run.rec.run_id)}&sortie=${encodeURIComponent(e.sortie_id)}&t=${Math.round(st.t - 120)}&follow=${e.scout}`;
    return `<li>${e.scout} searched ${f.scout}'s find ${e.find_id}: <span style="color:${STATUS_COLOR[e.result] || "#ccc"}">${e.result === "confirmed" ? "confirmed" : "not confirmed"}</span> · <a href="${href}">watch in 3D</a></li>`;
  }).join("");
  return `<ul class="evlist" style="margin:8px 0 0;padding-left:18px">${li}</ul>`;
}

function info() {
  const rec = state.run.rec, s = rec.settings || {}, w = rec.world || {}, m = rec.summary || {};
  // A row whose value the record does not hold (the showcase run has no world draws) is left out.
  const kv = (rows) => rows.filter(([, b]) => b != null && !/undefined|NaN/.test(String(b)))
    .map(([a, b]) => `<dt>${a}</dt><dd>${b}</dd>`).join("");
  $("setup").innerHTML = kv([
    ["Seed", rec.seed], ["Scouts", `${s.n_scouts ?? rec.scouts.length}${s.n_scouts >= 4 ? ' <span class="tag x">beyond the brief\'s fleet of 3</span>' : ""}`],
    ["Sorties per Scout", s.sorties_per_scout], ["Check policy", s.check_policy], ["Map refinement", s.map_refinement ? "on" : "off"],
    ["Top speed", `${fmtNum(s.top_speed_mps * 100, 0)} cm/s`], ["Check stop", `${fmtNum(s.check_stop_s, 0)} s`],
    ["Drill limit", `${fmtNum(s.drill_limit_m, 0)} m`], ["Battery", `${fmtNum(s.battery_kwh, 1)} kWh, ${fmtNum(s.reserve_frac * 100, 0)}% reserve`],
    ["Navigation", rec.nav === "sampled" ? "sampled from ShadowNav results" : esc(rec.nav)], ["Terrain", esc(rec.terrain && rec.terrain.site)],
  ]);
  $("world").innerHTML = kv([
    ["Craters ≥10 m", `${fmtNum(w.crater_n_ge10_per_km2, 0)} per km²`], ["Odometry drift", `${fmtNum(w.drift_frac * 100, 2)}%`],
    ["Large craters missing from map", `${fmtNum((w.missing_share_large || 0) * 100, 1)}%`], ["Hidden deposits", w.n_deposits ?? rec.truth.ice.length],
    ["Dock position error", w.dock_reg_err ? `${fmtNum(Math.hypot(...w.dock_reg_err), 2)} m <span class="dim">(never seen by the Scouts)</span>` : undefined],
  ]);
  $("results").innerHTML = kv([
    ["Sorties home with reserve", `${m.sorties_ok} of ${m.sorties}`], ["Below reserve", m.sorties_below_reserve], ["Fault returns", m.sorties_fault_return],
    ["Scouts lost", m.scouts_lost], ["Drill-site error, p95 <span class=\"dim\">(true)</span>", m.drill_err_p95_m == null || m.drill_err_p95_m === "" ? "–" : `${fmtNum(m.drill_err_p95_m, 1)} m`],
    ["Checks per sortie", fmtNum(m.checks_per_sortie, 1)], ["Wrong-match rate", m.wrong_match_rate == null || m.wrong_match_rate === "" ? "–" : `${fmtNum(m.wrong_match_rate * 100, 1)}%`],
    ["Finds", m.finds], ["Confirmed", m.confirmed], ["False confirmations <span class=\"dim\">(vs truth)</span>", m.false_confirmed],
    ["Deposits missed <span class=\"dim\">(vs truth)</span>", `${m.deposits_missed}${w.n_deposits != null ? ` of ${w.n_deposits}` : ""}`], ["Area surveyed", `${fmtNum(m.area_km2, 3)} km²`],
    ["Hours: drive / checks / readings / drill / dock queue", `${fmtNum(m.h_drive, 0)} / ${fmtNum(m.h_check, 0)} / ${fmtNum(m.h_reading, 0)} / ${fmtNum(m.h_drill, 0)} / ${fmtNum(m.h_wait, 0)}`],
  ]);
}

main();
