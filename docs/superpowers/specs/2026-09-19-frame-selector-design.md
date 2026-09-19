# Loom Frame Selector — design

**Date:** 2026-09-19
**Status:** approved — revised after three adversarial review rounds

## What this is for

A night's subframes are not equally good, and the bad ones cost more than they
add: a soft or elongated frame drags the stack's PSF out and buys almost no
depth. Rejecting them by hand means reading a table of numbers per channel and
deciding where the line falls, every session.

This is a second script in the Loom repository — not part of the Loom run — that
measures every sub in a folder, groups them by channel, works out where the line
falls *for that channel on that night*, and removes the frames below it.

It exists because of a concrete failure. On 2026-09-18 a re-stacked G master was
8% softer, 31% more elongated, 18% noisier and found 31% fewer stars than the G
it replaced. Nothing in the pipeline objected; the first symptom was green stars
in a finished plate, and finding the cause took hours. Bad subs are cheaper to
catch before they are integrated than after.

## What it does not do

**It does not measure anything itself.** SubframeSelector does the measuring,
because those are the numbers already seen in WBPP. A frame selector whose FWHM
disagrees with the subframe table is one nobody can act on.

**It does not copy files itself** when writing elsewhere. SubframeSelector's
output routine does that.

**It does not decide what a channel is.** Grouping is by the `FILTER` keyword
only. Splitting a channel further — by exposure, by night — is a judgement about
someone's data, and a script that quietly divides a session into two groups and
applies two different thresholds is worse than one that does nothing.

It does, however, have to **notice when a group is not comparable**. Frames of
one filter can differ in exposure, binning, image geometry or calibration state,
and a shorter exposure legitimately loses on SNR and star count while a different
sampling makes pixel FWHM incomparable. So the group's exposure, binning, geometry AND
calibration state are checked (`IMAGETYP`, and the calibration history WBPP
writes; unknown state counts as a mismatch, because raw and calibrated frames of
one filter can agree on every other field): a mixed group is reported, and Apply is blocked for that
channel until it is either accepted explicitly or the folder is narrowed. The
raw `FILTER` string is the grouping key, not `Util.channelFromFilter`, which maps
to Loom's seven canonical channels and returns null for anything else — it would
merge distinct filters and drop unfamiliar ones. Frames with no readable FILTER
form their own group and are never auto-rejected.

## Measurement

One SubframeSelector call per channel, `routine = 0` (measure), with every sub
of that channel in `subframes`.

**Results are matched back to inputs by the file path in the measurement row
(column 3), never by position.** WBPP's own analyzer does exactly this
(`BPP-SubframeAnalyzer.js:520`), and the reason is not hypothetical: if one
frame fails to measure, positional reading shifts every subsequent row onto the
wrong file, and a perfect deletion fingerprint would then verify the wrong
frame's identity against another frame's metrics. A duplicate or unexpected path
in the results aborts the channel; any input with no result row becomes an
**unmeasurable** entry. The measurement row is positional; the indices
below were read off a live run on 1.9.5 build 1702, not assumed, and are pinned
by assertions so a future reordering fails loudly rather than reporting a noise
figure as an FWHM:

| index | figure |
|---|---|
| 5 | FWHM |
| 6 | eccentricity |
| 7 | PSF signal weight |
| 9 | SNR estimate |
| 12 | noise |
| 14 | stars |
| **28** | **PSF SNR** |

An earlier draft of this spec said PSF SNR was column 8. It is not, and the
error would have been invisible: column 8 reads 0 on every frame measured here,
so the dominant term of the score would have been a constant zero. The indices
above come from WBPP's own `BPP-SubframeAnalyzer.js:481`, which carries the
comment *"fixed indexes that need to be aligned with the process
implementation"*, cross-checked against a live measurement.

Pinning a constant does not detect a reordering — `SFS_PSF_SNR === 28` passes
happily after the process changes. The assertion must therefore check the
MEANING: a measurement of a known frame, whose FWHM, star count and PSF SNR fall
in expected ranges and disagree with each other, so a shuffle moves a value out
of its range. `SubframeSelector.toSource()` is also recorded with the cache, as
WBPP does, so a process change invalidates cached numbers rather than silently
mixing them.

`subframeScale = 1` and `scaleUnit = 0` are intended to give FWHM in pixels.
A scale of 1 makes arcseconds numerically equal to pixels, so this setting
cannot prove itself: the units enum is verified against
`SubframeSelector.Pixels` before any of this is trusted.

A measurement of a 26 MP sub takes a few seconds, so results are cached in
`Cache.dir()/frame-quality.json` keyed by path, size, modification time AND the
measurement configuration (`SubframeSelector.toSource()`), because a number
produced under different settings is not comparable with a fresh one.
Re-opening a folder is then instant, and a re-acquired frame is measured again
because its mtime changed. The cache key carries a version prefix: what is
measured has changed before, and a number from an older definition compared
against a fresh one is exactly the quiet wrongness this tool exists to prevent.

## The formula

Two separate mechanisms, deliberately. A score answers "how good is this frame",
a clip answers "is this frame bad enough to drop" — and conflating them means a
uniformly excellent night still loses its bottom 10%.

### Score, for ranking

Per frame, within its channel, every term divided by that channel's own median
so the weights are unitless and comparable:

```
score =  w_snr   * (snr / med_snr)
       + w_fwhm  * (med_fwhm / fwhm)
       + w_ecc   * (med_ecc / ecc)
       + w_stars * (stars / med_stars)
```

FWHM and eccentricity are inverted so that larger is better in every term.

Defaults: `w_snr 0.5`, `w_fwhm 0.3`, `w_ecc 0.1`, `w_stars 0.1` — SNR dominant,
which is the conventional weighting. The score does **not** decide rejection.

Two honest limits on it. Dividing by the median aligns typical levels but not
dispersion, so a frame with a near-zero eccentricity can contribute a term of 40 and swamp
the weights. Each normalised ratio is therefore clamped to **[0.25, 4]** before
it is weighted — two stops either side of typical, beyond which the frame's
ranking is not in question anyway — and a frame with an invalid metric is scored
as unmeasurable rather than given a number. And
the four metrics are **not independent** — SNR, star count and FWHM move
together — so the weights are a ranking preference, not an allocation of
"quality" between separate things.

**The score is not written into the files in the first version.** Saying it
"survives into WBPP" would be a claim about a persistence mechanism this design
has not verified: changing an in-memory SubframeSelector measurement persists
nothing, in-place mode rewrites no surviving file, and WBPP chooses its
integration weighting mode explicitly. Writing a weight would also add a second
destructive operation — rewriting every kept frame — to a tool whose safety case
rests on touching as little as possible. The score ranks the table; exporting it
is a separate feature with its own live test.

### Clip, for rejection

A frame is rejected if **any** metric is worse than its channel's robust bound.
The scale is the **normalised** MAD, `sigma = 1.4826 * MAD`, which is what makes
`k` a number of standard deviations rather than an arbitrary width:

```
snr   < median - k*sigma          fwhm  > median + k*sigma
ecc   > median + k*sigma          stars < median - k*sigma
```

MAD rather than standard deviation, because a handful of disasters would widen a
deviation-based gate and defeat the thing meant to catch them. The bound is per
channel and per run.

Every rejection is explainable in one line, and the reason is shown and logged:

```
FWHM 9.81 px, median 6.82, limit 7.94
```

### Degenerate measurements

These are not edge cases to handle later; they decide whether the tool is safe to
point at a real folder.

- **A metric is valid only if it is finite and positive.** Eccentricity of 0,
  a star count of 0, or a failed fit produce infinities and NaN through the
  score's divisions.
- **A zero weight does not neutralise an invalid term**: JavaScript evaluates
  `0 * Infinity` as `NaN`. Validity is checked before weighting, not after.
- **NaN escapes every gate**, because all comparisons against it are false. A
  frame with an invalid measurement is therefore *not* silently kept: it is
  marked **unmeasurable** and reported as its own state, neither approved nor
  auto-rejected, and it is never deleted automatically.
- **Zero or near-zero MAD rejects perfection.** With FWHM `[4, 4, 4, 4,
  4.000001]` the MAD is 0 and every preset rejects the last frame. When
  `sigma` is 0, or below **1% of the absolute median**, the clip for that metric
  is DISABLED and says so, rather than rejecting on noise. At that point the
  spread is smaller than the measurement's own repeatability.
- **A lower bound at or below zero cannot reject anything**, so a gate that
  computes a non-positive limit is reported as inactive rather than passing
  everything silently.
- **Below 10 valid measurements in a channel the robust clip does not run.** A
  median exists at 3 frames; a dispersion worth clipping on does not, and at 10
  frames the loss rate is already double its asymptotic value. Hard limits still
  apply — they do not depend on the sample.

### Presets

Three, because `k` is the one number that decides how much is dropped and nobody
should have to reason about a robust sigma width to use this:

| preset | `k` | asymptotic | at 20 frames | at 10 frames |
|---|---|---|---|---|
| Lenient | 3.0 | ~0.5% | ~2.8% | ~6% |
| Balanced | 2.5 | ~2.5% | ~6.1% | ~10% |
| Strict | 2.0 | ~9% | ~13.1% | ~17% |

The asymptotic column is `1 - P(z < k)^4` for four one-sided Gaussian gates on
the normalised MAD. **It is not what a real run does.** Median and MAD are
estimated from the same small sample being clipped, so the cutoffs are estimates,
not population parameters, and the loss is markedly higher at realistic frame
counts — the two right-hand columns come from a simulation of this exact
estimator. An earlier draft quoted 1% / 5% / 15%, which matched neither the raw
MAD nor the normalised one nor any sample size.

All three columns remain an expectation and not a promised yield. The metrics are
also **not independent** — SNR, star count and FWHM move together through
detection and fitting — which shifts the real figure in a direction that depends
on the sign of that dependence and is not predictable from here. The dialog shows
the actual count, which is the only number that is true.

**A clip on an ACTIVE gate's median and MAD is scale-invariant, and that has a
consequence worth stating plainly.** Shrink a night's FWHM spread by a factor of
a hundred and the MAD shrinks with it: the same frames are rejected. A uniformly
excellent night therefore loses about the same *fraction* as a poor one. An
earlier draft claimed the opposite — that a tight night would lose almost
nothing — and that was simply wrong. (The qualifier matters: shrink the spread
far enough and `sigma` crosses the floor below, which disables the gate
entirely. That is a different mechanism, not the clip being generous.)

This is the right default for the tool's purpose, which is "drop this night's
worst frames". It is **not** "drop frames that are bad in absolute terms", and
the two cannot be served by the same rule.

### Relative, absolute, or both

Per channel, one of three modes, because an earlier draft promised that hard
limits could preserve an excellent night and they cannot: a frame at FWHM 5.0
with a robust limit of 4.5 and a ceiling of 9.0 is still rejected by the robust
gate, and no additional criterion can rescue it.

The rejection predicate is stated per mode, leaving nothing to infer:

- **Relative** (default) — rejected iff an **active relative gate** fails. Hard
  limits are ignored entirely, even if values are still configured from a
  previous mode.
- **Absolute** — rejected iff a **configured hard limit** fails. The robust clip
  is off. This is the mode that keeps an entire good night, and the only one
  that can.
- **Both** — rejected iff **either** condition fails.

The minimum-count rule disables **relative gates only**. In Relative mode a
channel below the minimum therefore rejects nothing — it does not fall through
to hard limits, which that mode ignores by definition. In Absolute or Both, hard
limits still apply, because they are a statement about the data rather than
about the sample size.

Absolute mode with no hard limits configured is a valid "keep everything"
setting, not an error, and the dialog says so rather than blocking Apply.

### How presets, knobs, modes and overrides compose

The opening preset is **Balanced**. A preset sets `k` for every channel whose
`k` has not been edited by hand; an edited value is kept and marked as such, and
a **Reset channel** action returns it to the preset. Editing one knob does not
pin the others — a channel whose weight was changed still inherits `k` from a
later preset change.

The combinations that would otherwise be guesses:

| situation | rule |
|---|---|
| channel `k` edited, then a preset selected | the edited `k` stands; the preset marks it as overridden |
| only a weight edited, then a preset selected | `k` follows the preset |
| Absolute mode, no hard limits set | valid: keeps everything, and says so |
| channel disabled with manual condemnations pending | disabling suppresses **all** action on that channel, condemnations included; re-enabling restores them unchanged |
| disabled channel, "another folder" mode | its frames are **not** copied; a disabled channel is untouched in every mode |
| unmeasurable frame | a third state, never auto-deleted; it may be condemned by hand, and cannot be "approved" because there is no measurement to approve |

### Knobs

Per channel, each defaulting to the preset and overridable independently:

- the four weights
- `k`, when the preset is not the right width for this channel
- an optional hard floor or ceiling per metric, for an absolute rule that does
  not move with the night ("never keep FWHM above 9")
- an enable, so a channel can be left alone entirely

## Actions

Applying builds a **frozen manifest** first: for every file, its path, size,
modification time, the **full-content digest** taken when the frame was measured, the
verdict, and the reason. Nothing is deleted or copied except through that manifest.

A digest of the whole file, not of the header and a sample of pixels: a change
outside the sampled region would pass a partial hash without any collision. The
digest is computed once, at measurement, and stored WITH the measurement in the
cache. A cached measurement whose digest no longer matches the file is not
reused — it is re-measured — so the numbers that produced a verdict always
describe the bytes that verdict will be applied to.

Immediately before each destructive operation the digest is recomputed and
compared. Path, size and modification time are not identity; a replacement can
preserve all three. A frame that changed, vanished or became unreadable is
skipped and reported.

**This does not close the window between the check and the unlink.** Nothing in
PJSR locks a file, so a replacement in that instant is undetectable. The design
narrows the window to microseconds and says so here rather than claiming a
guarantee it cannot make. The mitigation that actually matters is the audit log,
which records the fingerprint of every file deleted.

### Three layers, and what may change in each

The word "frozen" is not enough on its own, so the lifecycle is explicit:

1. **The cohort** — the set of frames and their measurements, fixed when the
   folder is scanned. Channel medians and MADs are computed from the cohort's
   valid measurements and **never** from survivors of a partial run.
2. **The review** — verdicts, presets, knobs, modes and manual overrides. Fully
   editable, recomputed freely from the cohort, and worth nothing until
   committed.
3. **The execution manifest** — a copy of the review taken at the moment Apply
   is confirmed, immutable thereafter, carrying each file's digest and each
   entry's outcome as it completes.

Once an execution begins, the review is **locked** until that execution is
finished or abandoned. A stopped run offers exactly two choices: *resume*, which
continues the same manifest and skips entries already recorded done, or
*abandon*, which discards it and unlocks the review. There is no path in which
editing a knob silently alters a manifest that is already deleting files, and no
path in which pressing Apply twice runs two different manifests over one cohort.

**Apply never recomputes a verdict.** Recomputing after a partial run would be **iterative
clipping**: delete the worst frame, and the median and MAD of the survivors
tighten, so a second Apply deletes the next-worst. With a cohort of sixteen frames whose FWHM runs
`[4.0 … 4.7, 5.3]` plus one at `10`, the first pass drops `10`; recomputed on
the survivors the median and MAD both tighten and the next pass takes `5.3` — a
frame nobody condemned. (The example needs a cohort above the ten-frame minimum,
or the minimum-count rule would stop the second pass for an unrelated reason and
hide the defect.) Recomputation is a new review, entered
explicitly, never a consequence of pressing Apply twice.

**In place** — rejected frames are deleted where they are. This is the only
irreversible operation in the tool:

- nothing is deleted until the table has been shown
- a confirmation names the exact count and the per-channel breakdown
- the manifest is written to a durable log **before** any unlink, and each
  file's outcome is appended as it happens, so the record says what actually
  died rather than only what was intended
- if the log cannot be written, Apply **stops**; an unrecorded deletion is worse
  than a deferred one
- the log does NOT live where `Cache.clear` can remove it, and not under the
  system temporary directory

**To another folder** — SubframeSelector's output routine writes the approved
frames there. Export success has to be well defined even though no original is
deleted, because otherwise "it worked" is a guess:

- the destination mapping is computed **before** writing and must be
  collision-free. Two sources that would produce one output name — two formats
  sharing a stem, say — abort the channel rather than silently overwrite one
  with the other
- the destination must not be, contain, or be an alias or symlink of any source
  directory
- `overwriteExistingFiles` is off by default; with it off, an existing
  destination file aborts that entry and is reported, rather than being counted
  as a success
- every entry's outcome is recorded per file, so a retry knows which outputs
  this run produced and does not mistake an unrelated pre-existing file for its
  own work

**Delete originals — NOT in the first version.**

It was in the first draft, guarded by "the copies are present and non-empty".
That guard is worthless: a stale file from an earlier run, a truncated write, or
a different frame sharing a basename all satisfy it. Tightening it to "opens as
an image with the expected geometry" is no better — another exposure from the
same camera passes that too.

Making it safe needs a collision-free source-to-output mapping (two sources
mapping to one destination would delete both originals while one output
survives), per-file confirmation that THIS run wrote THAT file, and content
equivalence between source and output strong enough to survive SubframeSelector
rewriting metadata. That is a feature with its own design and its own live test,
not a checkbox on this one.

Until then the workflow is: write approved frames elsewhere, look at them, and
delete the source folder yourself. The tool will not delete a file it did not
verify.

## The dialog

Nothing in the **source folders** changes, and nothing destructive happens,
until **Apply**. Measurements and their digests are cached during the scan, so
the cache directory is written before the table appears; that is the only disk
activity before Apply.

```
+-----------+--------------------------------------+------------------+
| channels  | frames of the selected channel        | preview at 1:1   |
|           |                                       |                  |
| L  98/98  | name    SNR  FWHM  ecc  stars  score  |   [pannable]     |
| R  61/68  | ...                                   |                  |
| G  54/70  |                                       |                  |
| H  88/91  |                                       |  [keep this one] |
|           +--------------------------------------+------------------+
|  preset:  | weights | k | floors | enable        |                  |
|  lenient  |                                       |                  |
|  balanced |                                       |                  |
|  strict   |                                       |                  |
+-----------+--------------------------------------+------------------+
```

The channel list carries kept/total counts. The table shows the four metrics,
the score, the verdict, and the reason when rejected.

### Preview

**`Image.render()` does not apply a screen stretch** — its own documentation
excludes it. Setting an STF on a view therefore renders nothing different. The
duplicate's PIXELS are stretched, with a HistogramTransformation built from the
frame's own median and MAD, and that stretched duplicate is what gets rendered.
An earlier draft said "auto-stretches a duplicate" while meaning an STF, which
would have produced a black preview.

The stretched duplicate is rendered once to a `Bitmap` with `Image.render()`.
`onPaint` draws the visible region into a `Control`; because the bitmap is
rendered once per frame rather than once per paint, panning is a blit.

- **Mouse:** click and drag.
- **Keyboard:** arrow keys once the preview has focus. Focus is not automatic —
  the control sets `focusStyle` so a click focuses it — and `onKeyPress` must
  **consume** the keys it handles, or the TreeBox will act on the same arrow
  press and move the selection underneath the preview.
- **Wheel:** toggles 1:1 and fit. `Image.render()` takes integer zoom levels, so
  "fit" is a scaled bitmap, not a render argument.

"1:1" means one image pixel per **physical** display pixel, which on a Retina
screen is not one logical pixel: `Bitmap.physicalPixelRatio` is applied, and the
prototype verifies it with a checkerboard rather than by eye.

Exactly one frame's windows and bitmaps are held at a time — a 26 MP ARGB bitmap
is about 104 MB — and selecting another frame disposes of the previous one
before opening the next. Browsing a hundred frames must leave the open-window
count and memory flat, which the prototype checks.

### Overrides

A verdict can be overridden by hand: select a rejected frame, look at it, and
take it off the delete list — space bar on the row, or the button under the
preview. The same mechanism condemns a frame the formula kept.

- An override is explicit and **wins over the formula**. Changing `k` or the
  weights recomputes every verdict but leaves overrides standing, because
  someone looked at that frame and the formula did not.
- Overridden rows are marked and counted separately, so a summary never hides a
  hand edit: `H: 61 kept (+2 rescued, -1 condemned)`.
- A **Clear overrides** button.
- The deletion log records overrides beside the automatic verdicts. If a rescued
  frame turns out to be the bad one, the record says who chose it.
- Overrides last for the session only. Persisting them would put a second source
  of truth on disk to silently contradict the formula next time.

## Where the code goes

- `script/FrameSelector.js` — `#feature-id Loom Frame Selector : Batch
  Processing > Loom Frame Selector`, the dialog, and the apply step.
- `script/lib/Frames.js` — measurement, grouping, score, clip, verdicts. Pure
  arithmetic apart from the SubframeSelector call.
- `Util`, `Cache` and `Steps` are reused unchanged.

## Testing

The score, the clip, the per-channel overrides, the reason strings and the
verdict/override interaction are pure and belong in `selftest.js`, with real
numbers from the 2026-09-18 masters. Pinning the column indices as constants is **not** sufficient and is not the
acceptance criterion: `SFS_PSF_SNR === 28` passes unchanged after the process
reorders its table. The required test measures a fixture frame and asserts that
each column's value falls in a range only that metric can occupy, so a shuffle
moves a value out of its range and fails. This is an explicit acceptance
requirement, not a nicety — an earlier draft of this spec had PSF SNR on column
8, which reads 0 on every frame, and no constant-pinning test would have caught
it.

The dialog, the preview control and the file operations need PixInsight and stay
in the `IN_PIXINSIGHT` set. The delete path is tested against a directory of
copies, never against real subs.

## Risks

**Deletion is irreversible**, and a preview makes it easier to trust a threshold
that was only skimmed. The guards above are the mitigation. A first release
defaulting to "another folder" would make a mistake cost disk space instead of
data; the owner chose in-place deletion as the default action and it stays
available either way.

**The preview is the biggest unknown.** Rendering a stretched 26 MP sub to a
bitmap should be well under a second, but that is an expectation, not a
measurement, and a sluggish preview would make the whole dialog feel broken. It
is built first, as a standalone prototype, and its timing confirmed before the
rest is written.

**A channel with too few frames has no usable dispersion.** Below 10 valid
measurements the robust clip does not run: the tool reports the numbers and rejects
nothing. A median exists at 3 frames; a trustworthy MAD does not.

**The safety rule is narrower than "nothing touches disk".** Measurements and
digests are cached during the scan. What holds is that nothing in the source
folders changes, and nothing destructive happens, before Apply.
