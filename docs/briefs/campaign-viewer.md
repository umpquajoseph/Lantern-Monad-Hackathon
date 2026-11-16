# Part 4: viewer and cleanup (handle `viewer`, branch `campaign-viewer`)

Read `docs/briefs/campaign-common.md` first.

**Goal.** Spec section 6 on the local site (`python app.py`):

- a runs list, sortable and filterable, with each run's setup and results;
- a top-down map per run: sortie paths; the ice map as circles sized by
  position bound and coloured find / confirmed / drilled; the crater map
  with added craters marked; the dock; a slider through the sorties to watch
  both maps grow; a time bar per sortie; the hidden truth behind a labelled
  reveal;
- a 3D replay of one sortie from saved poses in the existing
  `web/js/world.js`.

**Cleanup.** Move chain code to `_archive/` (spec 7, left column); move, do
not delete. Remove agency, Monad and hackathon wording anywhere a viewer can
see. The Scouts are identical, named S1 to S5.

**Inputs.** Only the record files and `summary.csv` (`docs/handoffs.md`).
Until part 1 writes real ones, make a stand-in record in the exact format,
labelled as a stand-in on the page, and switch to real records when they
land.

**Done when.** On the local site you have opened a run, dragged the slider
through its sorties, revealed the truth and replayed a sortie in 3D, and
looked at 1920x1080 screenshots of each. Nothing from the chain is visible,
and `python app.py` still starts cleanly.
