// Batches the site still serves but that a later fix superseded, with every
// reason. The one place for these labels (leader's reply 20261117-132716):
// a newly found bug adds a reason here, one line per batch. The run records
// carry no field for these, so they are keyed on batch id.
//
// Every page shows a batch's reasons: Mission Control's picker, run panel and
// banner, run.html, runs.html and replay.html. Mission Control's default run
// never comes from a batch with more reasons than another camera batch.

const HOMING = "homing used the old free last hop: a Scout shown reaching the dock may never have found its way home (workflow fyi 20261117-130402)";

export const SUPERSEDED = {
  "live-nav": [HOMING],
  "live-nav-check": [HOMING],
  "live-nav-check2": [HOMING],
  "live-nav-first3": [HOMING],
  "workflow-check1": [HOMING],
  "workflow-first3": [HOMING],
  "workflow-first3b": [HOMING],
};

export const reasonsOf = (batch) => SUPERSEDED[batch] || [];

// Short form for labels: each reason up to its colon, then "superseded".
export function supersededShort(batch) {
  const r = reasonsOf(batch);
  return r.length ? `${r.map((x) => x.split(":")[0]).join("; ")}; superseded` : "";
}

// Full form for a run panel or banner: every reason in full.
export function supersededLong(batch) {
  const r = reasonsOf(batch);
  return r.length ? `Superseded. ${r.map((x) => x[0].toUpperCase() + x.slice(1)).join(". ")}.` : "";
}
