# Frame Selector: approval criteria panel, filmstrip, anomaly flags, SNR

Status: draft, revised after codex review rounds 1-3, 2026-09-22
Reference: SubframeStudio's cockpit (screenshots in the conversation) and its
bundled documentation, `/Applications/PixInsight/doc/tools/SubframeStudio/`.

## Intent

Make the Frame Selector's review look and work like SubframeStudio's in the
four places that matter most when deciding which subs to keep:

1. the criteria are one framed panel where each metric's limit is a number
   you can see, and in thresholds mode edit;
2. the channel's frames are a strip of small previews, with a red cross
   through every frame Run will drop;
3. frames that look anomalous carry advisory tags (cloud, focus, tracking,
   dropped), each tag separate, on the big preview and on the thumbnails;
4. the table shows SubframeSelector's SNR estimate beside PSF SNR.

Success: a night can be reviewed by looking at the strip and the tags, the
numbers behind every verdict are on screen, and which frames get deleted
changes only when the user changes a criterion or an override.

Out of scope: ALL/ANY combination (declined; a frame is dropped if it fails
any checked criterion, as today), switches for the flag detectors (declined:
always on), a disk cache of thumbnails, session save/load.

## 0. One predicate for "Run leaves this frame out"

Everything that marks a frame as going away -- the tile's cross, the
preview's red tag, the keep counter -- uses one pure function mirroring
exactly what Run does in each of its two modes:

`Frames.leftOut( row, channelEnabled, copying )`

- **Culling in place** (output = source folder): `channelEnabled &&
  finalState == REJECTED`. This is `committableRows` + `buildManifest`
  (FrameSelector.js:1825, Frames.js:922): those files are deleted.
- **Copying out** (any other output folder): `!channelEnabled ||
  finalState == REJECTED`. This is `approvedPaths` feeding `commitCopy`
  (FrameSelector.js:1971, 2007): nothing is deleted, and a switched-off
  channel is not copied at all.

`finalState` already applies overrides, so a rescued frame is kept and a
condemned one is left out -- including an UNMEASURABLE frame condemned by
hand (FrameSelector.js:1671, Frames.js:846). Otherwise an unmeasurable
frame is kept.

The preview's red tag reads `REJECTED` when `finalState` is REJECTED, and
`CHANNEL OFF` when the frame is left out only because its channel is
switched off while copying. `N / M keep` = frames of the current channel
for which `leftOut` is false, over the channel's total
(`Frames.counts().kept` is NOT used: it excludes unmeasurable frames). The
dialog passes `copying = this.copyingOut()`, and changing the output
folder refreshes crosses, tags and counter.

## 1. Approval criteria panel

Replaces the loose knob row under the plot (FrameSelector.js `layOut`).

A `GroupBox` titled **Approval criteria**, two rows:

- Row 1: `Mode [Relative (k×MAD) | Thresholds | Both]`, `Preset
  [lenient|balanced|strict]`, `k`, `Act on this channel`, stretch,
  `N / M keep` right-aligned. Preset and k are disabled in Thresholds.
- Row 2, one criterion per scoring metric, order FWHM, Ecc, Stars, PSF SNR:
  `CheckBox  <name> <op> [Edit]  <relative hint>`. `<op>` comes from
  `Frames.OPERATOR[metric]`: `<=` for metrics worse when higher (FWHM,
  eccentricity), `>=` for worse when lower (stars, PSF SNR), derived from
  `Frames.WORSE_WHEN`.

The combo's internal values stay `Frames.MODE.RELATIVE/ABSOLUTE/BOTH`; only
the labels change. The old `reject on:` row is removed and its tooltips move
to the new checkboxes. The comparability warning and summary stay below.

### The value field

A plain `Edit`, not `NumericEdit`: `NumericEdit` defaults to a 0..1 range,
clamps `setValue`, and cannot show "no value" (NumericControl.jsh:63, 116).

- Blank means no threshold for that metric.
- Each field has a `dirty` flag, set by `onTextUpdated` (the user typed)
  and cleared whenever the program writes the field. Commit happens on
  `onEditCompleted` (Enter or focus loss) ONLY when the field is dirty; a
  clean field is never parsed, so focus loss or Run never turns the
  rounded display back into the stored limit. The text is parsed with
  `Frames.parseLimit( text )` -> positive finite number, null for blank,
  or `undefined` for invalid; invalid input reverts to the stored value
  and changes nothing.
- Read-only is `edit.readOnly`, disabled is `enabled = false` (unchecked).
- Display precision per metric, `Frames.LIMIT_DECIMALS`: FWHM 2, Ecc 3,
  Stars 0, PSF SNR 2. The STORED limit is exact; the field shows it
  rounded; only an edit replaces it with the typed number.
- Run commits any pending edit first: `commit()` commits every DIRTY
  field through the same parse, then refreshes, before building the
  manifest.

### Per mode

- **Relative:** fields read-only, showing `gates[metric].limit` when the
  gate is active, blank otherwise. Refreshed on every recompute. No hint.
- **Thresholds:** fields editable; the stored limit is the criterion.
- **Both:** fields editable (the threshold), and the hint to the right
  shows the relative limit in grey, e.g. `(k: 4.12)`, so both numbers
  deciding the verdict are on screen. The plot band stays the intersection
  (Frames.js:483).

### Stored limits and prefill

`settings.limits[metric]` keeps its current shape but only ever holds the
worse-side bound: `{ hi: v }` for worse-when-higher metrics, `{ lo: v }`
otherwise. Every write (edit or prefill) replaces the whole object, so no
opposite-side bound can exist; blank deletes the entry. Nothing today
creates limits (the dialog has no limit UI and settings are not persisted),
so there is no legacy two-sided value to migrate.

New per-channel `settings.limitsEdited[metric]` (copied, never shared, like
`gating`), mirroring `kEdited`:

- Prefill runs (a) on entering Thresholds or Both from Relative, for every
  checked metric, and (b) when ONE metric is checked while in
  Thresholds/Both, for that metric only. Rechecking one criterion never
  touches another's threshold.
- Order, inside `refresh()` which is the only path: compute the gates for
  the current cohort and k, then prefill, then compute every verdict.
  `FrameSelector.recompute` is split so gates and verdicts are separate
  steps; row states are never left from before the prefill.
- For a prefilled metric whose limit is NOT hand-edited: if the gate is
  active, the bound is set to the gate's exact limit; if not, the entry is
  deleted (blank: no threshold until the user types one).
- A hand-edited limit is never replaced. Clearing a field by hand is an
  edit (`limitsEdited` true, value null) and is not refilled.
- Guarantee, stated exactly: at the moment of switching Relative ->
  Thresholds, every frame's verdict is unchanged for metrics without a
  hand-edited limit. (Both comparisons are strict, `>`/`<`, Frames.js:636
  and 661, so a frame exactly at the limit passes in both modes.) After a
  later k or cohort change, un-edited thresholds do not follow until the
  next switch into Thresholds; this is visible because the fields show the
  numbers.
- Switching channels only displays that channel's settings; it never
  prefills, edits or copies anything.

### Checkbox semantics

The checkbox switches the criterion off in every mode.
`Frames.absoluteFailures( metrics, limits, gating )` and
`Frames.acceptedBand` skip metrics whose `gating[metric]` is false. An
omitted `gating` argument means all metrics on, so existing callers and
tests keep their meaning; `verdict` and `absoluteReasons` pass
`settings.gating`. An unchecked metric's stored limit is kept.

New pure helpers: `Frames.OPERATOR`, `Frames.LIMIT_DECIMALS`,
`Frames.parseLimit`, `Frames.displayLimit( metric, gates, settings )`,
`Frames.relativeHint( metric, gates, settings )`,
`Frames.prefillLimits( settings, gates, metrics )` (only the listed
metrics), `Frames.leftOut`.

## 2. Filmstrip

`FrameSelector.Filmstrip`, a `Frame` subclass painting itself like
`FrameSelector.Plot`, placed between the plot and the criteria group.

- As many tiles as fit (about 11 at the default width), selected frame
  centred where possible, `‹` `›` buttons paging one screenful.
- Tile: the whole frame, stretched, scaled to fit 120 x 80 preserving
  aspect. Selected tile: yellow border. `leftOut` -> red diagonal cross.
  FWHM along the bottom edge. Flag badges along the top edge (section 3).
- Clicking a tile selects that frame in the table, preview and plot;
  selecting a frame elsewhere scrolls the strip to it.
- Criterion, override and channel-enable changes repaint crosses and
  badges from cached bitmaps; nothing is re-read.

### Loading

Opening and stretching one 26 MP frame takes about 340 ms, so a 74-frame
channel is about 25 s and cannot be one blocking call.

- The selected frame's thumbnail is taken from the preview's full bitmap
  after `PreviewControl.load` renders it, at no extra read.
- The rest come from a `Timer` (interval 50 ms, single-shot re-armed after
  each item so ticks never overlap), one frame per tick. Each tick blocks
  the UI for that one frame (~0.3 s); the interval lets events through
  between frames. This is accepted and stated.
- Order: `Frames.thumbnailOrder( count, selected, first, visible )` ->
  visible range first, then outward from the selection. The queue is
  rebuilt on selection, page and channel change.
- One tick: open the source (`ImageWindow.open`), and IN that throwaway
  window's own image (inside `beginProcess( UndoFlag_NoSwapFile )` /
  `endProcess`), `resample( s )` with the single-factor overload,
  s = `min(120/w, 80/h)`, then stretch with the preview's median/MAD rule
  applied to the small image, `render()`, store. No full-size copy is
  made. (`resample` also has an absolute-pixels overload,
  `ResizeMode_AbsolutePixels`; the factor form is chosen because it
  preserves aspect by construction.) The source
  window and every temporary window are closed in `finally`, on success and
  failure alike. The stretch-and-render step is one helper shared with the
  preview, which also gets its duplicate window closed in `finally` (today
  it is closed only on success, FrameSelector.js:1062).
- Peak memory while a tick runs: the preview's full bitmap (~104 MB for
  26 MP) plus one source image (~104 MB as 32-bit float) plus resample
  workspace, bounded by one more full-size buffer: about 312 MB worst
  case. An estimate: PJSR exposes no process memory figure, so it is not
  measured. Nothing full-size survives a tick.
- Cache: in memory, keyed by path, all channels, capped at 600 thumbnails
  (about 24 MB), least recently used evicted.
- A frame that fails to load is marked failed, not retried in this dialog,
  and its tile shows a grey box with `!`. Until a tile loads it is a grey
  box; its cross and badges are drawn regardless.

### Lifetime and teardown

- The timer belongs to the dialog, created when the filmstrip is built but
  NOT armed during construction: `refresh()` during `layOut()` only
  rebuilds the queue. It is first armed from the dialog's `onShow`, i.e.
  after the constructor has returned and the entry point holds the object.
- The constructor body is wrapped in `try/catch`: on any exception it calls
  `release()` on the partly built dialog and rethrows, so a failed
  construction never leaves a timer, handler or cached bitmap behind.
- `release()` is idempotent (a `released` flag) and, first of all, stops
  the timer, nulls `onTimeout`, empties the queue, nulls the filmstrip's
  paint/mouse handlers and drops the thumbnail cache.
- It is reached from the Close button, `onClose`, the constructor's catch,
  and a `try/finally` around `execute()` in the entry point, so an
  exception during construction or review still releases.
- `onTimeout` body is in `try/catch` (a thrown tick logs and stops loading,
  never propagates into the core) and checks `released` first. Each queued
  item carries the channel key and a generation counter bumped by every
  queue rebuild; a result for a stale generation or a released dialog is
  discarded, not stored or painted.
- Scroll handlers: the preview's two existing, documented-safe handlers
  stay. What caused the v0.1.4 crash was assigning `onViewportScrolled`
  specifically (FrameSelector.js:701); it is not used, and the filmstrip
  installs no scroll handlers.

## 3. Anomaly flags

Always on: every detector runs on every channel; there is no switch,
preference or checkbox for any of them (decided 2026-09-22).

Advisory only: a flag never rejects, never changes a verdict, never enters
the manifest. The tags describe symptoms ("looks like"), not diagnoses.

### Rules

sigma = 1.4826 x MAD. `Frames.FLAG_K = 3`, `Frames.FLAG_MIN_FRAMES = 5`,
`Frames.DROPPED_FRACTION = 0.1`.

| Flag | Tag | Badge | Fires when |
|---|---|---|---|
| Focus | FOCUS | F | fwhm > median + 3 sigma |
| Tracking | TRACKING | T | eccentricity > median + 3 sigma |
| Cloud | CLOUD | C | background > median + 3 sigma, or stars < median - 3 sigma |
| Dropped | DROPPED | D | stars < 10 % of the median star count |

Per metric, separately:

- **Baseline:** the channel's rows with a finite value for that metric,
  positive for FWHM, eccentricity and background, non-negative for stars
  (a count of 0 is a real measurement). Not `frameValid`, which needs all
  four scoring metrics. At least `FLAG_MIN_FRAMES` baseline values, or
  that metric raises nothing. `[1000,1000,1000,1000,0]` is five values and
  flags the last frame DROPPED.
- **Dispersion (focus, tracking, cloud only):** sigma must be at least
  `Frames.SIGMA_FLOOR_FRACTION` (1 %) of the median, the same floor the
  rejection gates use (Frames.js:183), else that metric raises nothing.
  Dropped does not use sigma: `[1000,1000,1000,1000,1]` flags the last
  frame as DROPPED.
- **Targets:** any row with a finite value for the metric, including
  unmeasurable rows; stars = 0 counts (it is the clearest dropped frame).
- **Cloud vs dropped:** a DROPPED frame is not also tagged CLOUD for the
  low-star reason; CLOUD from high background still applies. Other
  overlaps (FOCUS with TRACKING, CLOUD with FOCUS) are allowed and shown
  as separate tags.
- **Comparability:** no flags at all for a channel with comparability
  problems (`ch.problems` non-empty) or the no-FILTER group; the summary
  says so ("not flagged: frames are not comparable"). Raw background
  compared across unlike frames is meaningless.

`Frames.anomalyFlags( metricsList, comparable )` -> per entry an array of
flag keys in the fixed order focus, tracking, cloud, dropped;
`comparable` false returns all-empty. The caller passes
`ch.problems.length == 0 && Frames.autoRejectAllowed( ch.key )`. Recomputed when measurements change,
not on knob changes.

### Where tags show

- **Big preview:** top-right, stacked vertically, each tag its own box:
  the red tag from section 0 when `leftOut`, then one amber box per flag. Drawn in
  VIEWPORT coordinates at a single exit of `paintViewport`, after the
  image, in all three paths (no bitmap, fit, 1:1). Today fit and no-bitmap
  return early and 1:1 translates the origin (FrameSelector.js:928-961);
  the overlay is drawn after resetting the transformation. The preview
  viewport is repainted on every `refresh()`, since criterion and override
  changes do not reload the bitmap.
- **Thumbnails:** one lettered box per flag (`C`, `F`, `T`, `D`), side by
  side along the top edge.
- **Summary:** `N flagged: a cloud, b focus, c tracking, d dropped`, where
  N counts unique frames and only non-zero kinds are listed.
- **Frame row tooltip:** flag names after the verdict.

## 4. Background and SNR estimate from SubframeSelector

### Columns

Known positions, from installed WBPP (`BPP-SubframeAnalyzer.js:487`:
`iSNREstimate = 9`, `iMedian = 10`) and Loom's own
`docs/verified-parameters.md` (9 = SNR estimate): `Frames.COL.snr = 9`,
`Frames.COL.median = 10`. Confirmed on the running core before commit.

Confirmation is not a plausibility check of the column against itself:

- **Median (background):** compared with an independent figure, the
  frame's own `Image.median()` computed in PixInsight on the same file, in
  the same units (the test establishes whether SubframeSelector reports
  0..1 or data units and records it in `verified-parameters.md`).
  Negative control: column 12 (noise) must NOT match it.
- **SNR estimate:** there is no independent PJSR figure for it. Checked
  for shape (finite, positive) and against negative controls: it must
  differ from PSF SNR (column 28) and from column 8 (the documented
  zero-valued trap). This limit is written down, not hidden.

### Optional fields never abandon a channel

`measure` abandons the channel when `Frames.meaningProblems` finds anything
(FrameSelector.js:116). Background and SNR are NOT added to it, and a Node
test pins that: a row with NaN in columns 9 and 10 and valid scoring
columns gives no `meaningProblems`.

Two scopes, deliberately different:

- **Schema** (is this column what we think it is): `Frames.optionalProblems(
  firstRow )`, run on the first row only, like `meaningProblems`. A problem
  there means the column is wrong for this core, so that field is null for
  EVERY row of the run, with one warning.
- **Value** (one frame's reading): after a clean schema check, a row whose
  optional value is not finite, or is negative, gets null for that field
  in that row only.

So background `[NaN, 0.01, 0.02]`: the schema check fails on the first row
(not finite), and background is null for all three. `[0.01, NaN, 0.02]`:
only the second is null. Null background: CLOUD uses the star test only. Null SNR: the
column shows `-`.

### Where the values flow

- `Frames.metricsFromRow` reads `background` and `snrWeight` (named
  `snrWeight` in code, headed `SNR` in the table).
- `storedMetrics` (FrameSelector.js:365), which feeds both the cache and
  the channel, moves to `Frames.storedMetrics` so the Node suite can test
  it, and carries both fields (number or null).
- Cache: `Frames.MEASURE_VERSION` "v1" -> "v2", invalidating every old
  entry once. A v2 entry is usable when it has all seven keys; a null
  optional value is a valid, cached "unavailable", not a miss, so no frame
  is re-measured forever. `Frames.cacheEntryUsable( entry )` decides,
  called by `cachedMeasurement`.

### SNR column

Display only: not in `METRICS`, not gated, weighted, scored or in any
reason. `Frames.DISPLAY_METRICS = [ "psfSNR", "snrWeight", "fwhm",
"eccentricity", "stars" ]` (SNR right after PSF SNR). Every display
consumer switches to it: `FRAME_COLUMNS`, `metricColumn`, `SCORE_COLUMN`,
the table's value loop (FrameSelector.js:1725), the plot combo's items and
`plotMetric()` (FrameSelector.js:1365). `METRIC_HEADING.snrWeight = "SNR"`,
`METRIC_LABEL.snrWeight = "SNR estimate"`. A null value shows `-` and is
not plotted. The plot band for SNR is empty (not a criterion).

## Files

- `script/lib/Frames.js`: everything pure named above, `COL.snr`,
  `COL.median`, `MEASURE_VERSION` v2.
- `script/FrameSelector.js`: criteria group box, filmstrip and loader,
  preview overlay, summary and tooltip text, cache hit through
  `cacheEntryUsable`, shared stretch helper, release/teardown, entry-point
  `try/finally`, display consumers.
- `script/selftest.js`, `docs/verified-parameters.md`, `README.md`.
- `ci/run-tests.js` is not changed: the Node suite loads the libraries but
  not `FrameSelector.js`, so every rule above that needs a Node test lives
  in `Frames.js`.

## Testing

Written before the code they cover.

Node suite (both builds), on a fixture channel of 20 frames with real
spread, active gates and at least two genuine rejects, plus a frame exactly
at a limit:
- `leftOut`, both modes: culling, a disabled channel leaves nothing out;
  copying, a disabled channel leaves every frame out; unmeasurable kept
  unless condemned, condemned unmeasurable left out in both modes;
  rescued kept; counter = total - leftOut. Cross-checked against
  `buildManifest` (culling) and the same filter `approvedPaths` applies
  (copying) on one fixture, so predicate and Run cannot disagree.
- Prefill: Relative -> Thresholds leaves every verdict equal, the boundary
  frame included; a hand-edited limit survives a switch and a k change;
  an inactive gate prefills blank; round trip Thresholds -> Relative ->
  Thresholds refreshes un-edited limits; channels are independent.
- Unchecked threshold rejects nothing and does not narrow the band;
  omitted gating = all on.
- `parseLimit` blank/invalid/valid; `displayLimit` and `relativeHint` per
  mode; `OPERATOR` agrees with `WORSE_WHEN`.
- `anomalyFlags`: each rule fires on a constructed outlier and not on its
  neighbours; per-metric minimum; sigma floor; `[1000,1000,1000,1000,1]`
  gives DROPPED and not CLOUD; `[1000,1000,1000,1000,0]` gives DROPPED; null background falls
  back; non-comparable channel gives none; unique-frame count.
- SNR: `DISPLAY_METRICS` order; column index of SNR is right after PSF SNR;
  a frame with terrible SNR is still approved and its score unchanged.
- `storedMetrics` carries both fields; `cacheEntryUsable`: v1 entry
  unusable, v2 with null optional usable, v2 missing a key unusable.
- Schema vs value scope for optional fields, both examples above;
  `meaningProblems` ignores columns 9 and 10.
- Prefill: rechecking Stars leaves a prefilled FWHM threshold alone after
  a k change; after prefill the row states match the new thresholds
  (verdicts computed after prefill); writing a limit replaces the whole
  object (no opposite bound survives).
- `anomalyFlags( list, false )` is all empty for rows that flag with
  `true`.
- `thumbnailOrder`: visible first, every index exactly once, stable when
  selection is at either end.
- Every test above is checked to fail with the guarded code removed.

PixInsight suite (running core):
- Fixture: 20 distinct frames from the `~/Downloads/Light` O set, copied to
  `/tmp/agent-scratch` (the originals are never touched).
- Column confirmation on the first of them, as specified in section 4.
- Dialog on the copy: fields read-only in Relative, editable in
  Thresholds, hint shown in Both; a clean field survives focus loss and
  Run unchanged; counter text equals `leftOut` counts.
- Crosses: two rows condemned by override and one threshold set to reject
  at least one more, so the expected crossed set is non-empty; crossed
  tiles equal `leftOut` rows exactly.
- Filmstrip: full load of 20 frames observes at least 19 loader ticks and
  20 cached thumbnails; clicking a tile selects its row.
- Teardown: five cycles of build, show, wait until at least 3 thumbnails
  are loaded, assert the queue is NOT empty, close. Then, from the shell
  after the dispatch finished, the PixInsight process is still alive and
  accepts a second dispatched script (the v0.1.4 crash happened after the
  script returned).

Then a visual check in the running PixInsight.
