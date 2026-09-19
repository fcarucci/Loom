# Loom Frame Selector — design

**Date:** 2026-09-19
**Status:** approved

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

## Measurement

One SubframeSelector call per channel, `routine = 0` (measure), with every sub
of that channel in `subframes`. The measurement row is positional; the indices
below were read off a live run on 1.9.5 build 1702, not assumed, and are pinned
by assertions so a future reordering fails loudly rather than reporting a noise
figure as an FWHM:

| index | figure |
|---|---|
| 5 | FWHM |
| 6 | eccentricity |
| 8 | PSF SNR |
| 12 | noise |
| 14 | stars |

`subframeScale = 1` and `scaleUnit = 0`, so FWHM is in pixels and comparable
between frames without a plate scale.

A measurement of a 26 MP sub takes a few seconds, so results are cached in
`Cache.dir()/frame-quality.json` keyed by path, size and modification time.
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
which is the conventional weighting. The score is written to each frame's weight
so it survives into WBPP; it does **not** decide rejection.

### Clip, for rejection

A frame is rejected if **any** metric is worse than its channel's robust bound:

```
snr   < median - k*MAD          fwhm  > median + k*MAD
ecc   > median + k*MAD          stars < median - k*MAD
```

`k = 2.5` by default. MAD rather than standard deviation, because a handful of
disasters would widen a deviation-based gate and defeat the thing meant to catch
them. The bound is per channel and per run, so it adapts to the night instead of
imposing a number from a different one.

Every rejection is therefore explainable in one line, and the reason is shown
and logged:

```
FWHM 9.81 px, median 6.82, limit 7.94
```

### Knobs

Per channel, each defaulting to the above and overridable independently:

- the four weights
- `k`
- an optional hard floor or ceiling per metric, for an absolute rule that does
  not move with the night ("never keep FWHM above 9")
- an enable, so a channel can be left alone entirely

## Actions

**In place** — rejected frames are deleted where they are. This is the only
irreversible operation in the tool, and it is guarded:

- nothing is deleted until the table has been shown
- a confirmation names the exact count and the per-channel breakdown
- the full list, with each frame's metrics and the reason, is written to a log
  under the cache directory *before* anything is unlinked

**To another folder** — SubframeSelector's output routine writes the approved
frames there. `overwriteExistingFiles` is off by default.

**Delete originals** — offered only when writing to another folder, only after
the written copies are confirmed present and non-empty, and never silently.

## The dialog

Nothing touches disk until **Apply**.

```
+-----------+--------------------------------------+------------------+
| channels  | frames of the selected channel        | preview at 1:1   |
|           |                                       |                  |
| L  98/98  | name    SNR  FWHM  ecc  stars  score  |   [pannable]     |
| R  61/68  | ...                                   |                  |
| G  54/70  |                                       |                  |
| H  88/91  |                                       |  [keep this one] |
|           +--------------------------------------+------------------+
|           | weights | k | floors | enable        |                  |
+-----------+--------------------------------------+------------------+
```

The channel list carries kept/total counts. The table shows the four metrics,
the score, the verdict, and the reason when rejected.

### Preview

Selecting a row opens that sub, auto-stretches a duplicate, and renders it once
to a `Bitmap` with `Image.render()`. `onPaint` draws the visible region into a
`Control`; because the bitmap is rendered once per frame rather than per paint,
panning is a blit.

- **Mouse:** click and drag.
- **Keyboard:** arrow keys once the preview has focus — clicking it focuses it,
  so the keys work without hunting for a widget. Arrow pans a quarter of the
  visible width, Shift+arrow a full screen.
- **Wheel:** toggles 1:1 and fit.

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
numbers from the 2026-09-18 masters. The SubframeSelector column indices get the
same pinning assertions Loom already has.

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

**A channel with too few frames has no usable median.** Below roughly five
frames the MAD is meaningless and the clip must not run: the tool reports the
measurements and rejects nothing.
