# Deterministic histogram stretch — design

**Date:** 2026-09-15
**Status:** design, not implemented
**Scope:** stretching the L, RGB and HSO plates, starless and stars, to a
non-linear state ready for further work in Photoshop.

## Goal

A **zero-intervention** stretch. Given any plate, produce one
HistogramTransformation that is *optimal for further processing*, where the
user's definition of optimal is exact and is the acceptance criterion:

> **all signal, no clipping**

No per-image tuning, no LLM, no sliders. The same rule runs on every frame
and every filter.

## Non-goals

- Reproducing the original by recombining the plates. Each plate is stretched
  to look right ON ITS OWN; screen-combining them in Photoshop will therefore
  not reconstruct the linear original, and the stars will read too strong or
  too weak on first drop. That is inherent to the requirement and is what
  layer opacity and curves are for.
- Multi-exposure HDR sets. One stretch per plate.
- Curves, saturation, or any tonal work past the stretch. Photoshop's job.

## Source

Two video workflows, read as transcripts:

- A BlurXTerminator/PhotoLab workflow, which contributes the plate ordering:
  the master is stretched FIRST and stars extracted afterwards, so the star
  plate inherits the master's transform.
- A histogram-method video, which contributes the stretch rule itself: black
  point at the level where the light curve begins, midtone at the level where
  it ends, closed in progressively under increasing vertical zoom, never
  cutting into the curve because cutting discards data. Detached data beyond
  the main curve is "respected" if it begins within about one curve-breadth,
  bypassed if further.

Both describe hand procedures with a human in the loop. This spec is the
deterministic equivalent, and where the hand procedure turns out to be
ill-posed the spec says so and substitutes something well-posed.

## The primitive

Every stretch is one HistogramTransformation: a black point `c0` and a
midtone balance `m`.

```
x' = max(0, (x - c0) / (1 - c0))
MTF(m, x') = (m - 1)x' / ((2m - 1)x' - m)
```

Two closed forms are used throughout; neither needs iteration:

```
midtone that sends x' to t:   m = x'(t - 1) / (2t·x' - t - x')
                              and for t = 0.5 this is exactly  m = x'
```

The `t = 0.5` identity is why "put the midtone marker at a level" and "send
that level to mid-grey" are the same instruction: in HistogramTransformation
the midtone marker's position IS `m`.

## The rule

### Black point

The level where the light curve begins: the image's own minimum.

```
c0 = max(0, minimum)
```

Nothing is clipped and no output range is wasted, both by construction. No
distributional assumption, no frame-size term, nothing to tune.

**This replaced a noise model, which measurement refuted.** The rule was
originally `c0 = median - z(N)·MADN` with `z(N) = inverseNormalCDF(1 - 1/N)`,
the level below which fewer than one pixel is expected if the sky is Gaussian.
Verified against the L master on 2026-09-15:

```
                        full frame      centre crop
darkest pixel sits      3.93 MADN       3.24 MADN    below the median
the model demanded      5.60            5.29
pixels clipped          0               0            (predicted ~1)
darkest pixel maps to   0.0902          0.1143
```

A stacked, drizzled master has a much shorter lower tail than a normal
distribution. The model did not clip; it wasted, leaving ~10% of the output
range holding nothing at all. The empirical minimum needs no assumption and
cannot be wrong about its own data.

`inverseNormalCDF` and `stretchSigmaForPixelCount` remain in `Steps.js` solely
to document this, with self-test assertions pinning the measurement, so the
Gaussian rule is not reintroduced by someone who finds it elegant.

### The minimum has to be defect-robust

The plain minimum is a single-pixel statistic, so one cold pixel or a dead
column drags it below all real data and hands back exactly the waste the
Gaussian rule was replaced for. So the minimum is taken over a **3x3
median-filtered copy**: a lone bad pixel cannot survive a 3x3 median and is
rejected by construction, while real structure — which by definition covers
more than one pixel — is not. Radius 1 is the smallest neighbourhood that
exists, not a tuned value, and it is the standard meaning of "isolated single
pixel".

Measured on the three full-frame masters, this is not a no-op:

```
          raw minimum   robust minimum   lifted by    midtone change
L         8.4332e-4     9.0673e-4        0.411 MADN     -10.45%
Ha        7.9671e-4     8.2358e-4        0.262 MADN     -10.40%
OIII      9.0112e-4     9.1478e-4        0.526 MADN     -19.20%
```

All three masters carry isolated dark defects that were pulling the black
point down. The sky still lands on exactly 0.2500 in every case.

**The consequence, stated plainly:** pixels below the robust minimum now clamp
to 0. That is deliberate — they are defects, not signal — so the guarantee is
"nothing clips except isolated single-pixel defects", not "nothing clips at
all". Counted over every pixel of the three full frames (91,874,948 each):

```
              clipped to black          output >= 0.999
L             165   (1.80e-4 %)       14245  (1.55e-2 %)
Ha           1071   (1.17e-3 %)         391  (4.26e-4 %)
OIII         1568   (1.71e-3 %)        2214  (2.41e-3 %)
```

165 to 1568 pixels in 91.9 million is a defect count, not a population — which
is the evidence that the 3x3 median is rejecting artefacts rather than eating
signal. The high end is star cores, and nothing is hard-clipped there: MTF is
asymptotic to 1 and only reaches it for an input already at 1. `MorphologicalTransformation.Median` is 4, read from the module; the
default structure is already a full 3x3, so neither it nor `structureSize` is
set.

### Midtone

```
m such that MTF(m, norm(median)) = 0.25
```

i.e. the sky median lands at 0.25, PixInsight's own autostretch target.

**Why an anchor on the sky rather than on the top edge, which is what the
video describes.** Measured on the L master (4096^2 crop), sweeping the
vertical-zoom threshold the method relies on:

```
Z:        1      10     100    1000   10000  100000
hi:    1.50e-3 1.79e-3 2.18e-3 3.45e-3 9.35e-3 4.54e-2      (stars excluded)
sky ->: 1.0000  0.3167  0.2298  0.1251  0.0408  0.0082
```

The upper edge drifts monotonically by 30x across five decades of zoom and
never converges. "Keep zooming until it stops moving" does not terminate, so
**the right edge of the light curve is not a well-defined quantity** and any
chosen zoom is an arbitrary constant in disguise. The same measurement kills
the obvious fallback of taking the maximum of the star-excluded pixels: that
is the Z -> infinity limit, leaving the sky at 0.008 and the frame nearly
unstretched.

The sky median, by contrast, is robust, cheap and well defined on every
frame. And the two rules agree: at the zoom range the video states (30-50,
then 100-200) the sky lands at 0.27, 0.23, 0.18 — bracketing 0.25. That
agreement is corroboration, not the source of the constant; 0.25 comes from
PixInsight and exists independently of this data.

### No constants are taken from the test images

`c0` is the frame's own minimum. `median` is measured per frame. `0.25` is
PixInsight's published autostretch target. The frames used here falsify the
rule; they do not calibrate it — and they did: the first black-point rule did
not survive contact with them.

## Per-plate application

| plate | channels | stretch |
|---|---|---|
| L starless | mono | unlinked |
| L stars | mono | inherited from the master's transform |
| RGB starless | 3 | **linked** |
| RGB stars | 3 | inherited |
| HSO starless | 3 | **linked** |

**Linked** means one `c0` and one `m` for all three channels. RGB is
photometrically calibrated by SPCC and HSO has been through
NarrowbandNormalization; an independent per-channel stretch would undo both.
Statistics come from the equal-weight channel mean, with one guard:

```
c0 = min over channels of (median_c - z(N)·MADN_c)
```

so a single shared black point cannot clip whichever channel sits lowest.

## Pipeline placement

```
linear master ──HT(c0, m_stars)──► extract ──► stars plate   (final, no second stretch)
linear master ──extract──► starless ──HT(c0, m_starless)──►  starless plate
```

Extraction runs TWICE on any plate needing both halves — once on a stretched
clone for the stars, once on the linear original for the starless. That is
the cost of the chosen ordering and matches the source workflow.

Implemented in `Steps.extractStars`: it clones the master, stretches the
clone, clones THAT as the difference reference, removes stars from the
stretched clone, and unscreens to get the stars frame; then removes stars from
the untouched linear master to get the starless. The starless is stretched
afterwards by its own pipeline stage, so the two plates carry independent
transforms — which is what "each must look right on its own" requires, and why
screening them back together does not reconstruct the original.

New cached stages, following the existing conventions in `Pipeline.js`:
`stretchStars` before the extraction that feeds the stars plate, and
`stretchL` / `stretchRGB` / `stretchPalette` on the starless plates. Stage
parameters are the computed `c0` and `m`, so a plate whose statistics are
unchanged hits cache.

## Control

One checkbox, "Stretch the results (non-linear output)", persisted as
`Loom/stretch` and **off by default** so existing runs are unchanged. There is
nothing else to set: the rule takes its black point and midtone from the plate
in front of it.

## Outputs

`L_starless`, `L_stars`, `RGB_starless`, `RGB_stars`, `HSO_starless` — same
names as today, now non-linear. Loom does not save; the user exports.

## Rejected alternatives, with the measurement that rejected each

| alternative | why rejected |
|---|---|
| Pin faint-star percentile to a level (StarDetector) | Faintest detections sit 1.5-4.8 MADN above sky, so lifting them lifts the sky with them. Star/sky ratio came out 2.56 vs 2.64 against the sky-anchored rule — i.e. the midtone does not control which stars are visible, only overall brightness. |
| Use "the faintest real star" as the black point | Not well defined. Sweeping `minSNR` 0 -> 40 moves it 14x (1.73e-3 -> 4.20e-3) with no natural stopping point; the star count merely halves each step. Renames the free parameter, does not remove it. |
| Pin a bright-star percentile to protect colour | Pinning p99 to 0.80 puts the sky at 0.0022 and half the stars below 0.03. Star colour and faint stars cannot both be had from one monotone curve. |
| Maximise entropy of the output histogram | The optimiser was invalid — golden section on a non-unimodal objective, demonstrated by an Ha case where the returned optimum (1.058 bits) lost to an arbitrary competitor (2.002 bits). |
| Maximise entropy of the star population | Flat. Entropy varied 0.5% (7.42-7.46 bits) while the midtone varied 4.5x, so the argmax is undetermined. |
| Density threshold at a chosen vertical zoom | No plateau exists; see the sweep above. |

**Where StarDetector remains useful:** not for choosing the stretch, but for
*reporting* afterwards — how many stars saturated, how many fell below
visibility. Diagnosis, not decision.

## Risks and open questions

1. **0.25 is a display convention.** It is defensible, universal and
   corroborated by the video's own zoom range, but it is still an
   output-space choice. The image cannot say how bright it should look.
2. **The stars plate inherits the master's stretch**, chosen by a sky-anchored
   rule. Whether a sky-anchored stretch produces a good star plate is the
   main thing verification must answer.
3. **Denoise placement is unresolved.** The source says denoise after
   stretching; Loom denoises linear, and SyQon documents Prism for linear
   data. Deliberately deferred; the stretch is designed to work wherever it
   is placed.
4. **Hot pixels** can set the top of a distribution. Not load-bearing here,
   since the rule anchors on the median rather than the maximum.

## Verification

Run against the L master on 2026-09-15, full frame (11966 x 7678) and a
4096^2 centre crop.

**The sky lands exactly on target.** `MTF(m, norm(median))` = 0.2500000000 on
both, with an independent round-trip check of the closed form. The algebra is
correct.

**Nothing clips at either end.** Zero pixels at or below `c0`; the true
maximum maps to 0.99938 (full) and 0.99940 (crop), so nothing is hard-clipped
at the top. The fraction reaching >= 0.999 is 1.15e-4 and 1.37e-4 — star
cores, not a soft-clipping artefact.

**The original black-point rule failed and was replaced.** See above. The
figures in this spec are from the rejected rule's run; the empirical rule
maps the darkest pixel to exactly 0 by construction and has not yet been
re-verified on a real frame.

**The rule is NOT framing-independent, contrary to the original claim here.**
Crop against full frame: median differs by +6.3%, MADN by -18.5%, midtone by
29.6%, of which the frame-size term explained only ~5.7%. The statistics
genuinely differ because a crop holds a different mix of sky and nebula. Any
image-derived rule behaves this way, and arguably it should — a differently
framed image deserves its own stretch — but the claim of framing independence
was wrong and is withdrawn.

### Still to verify

- The empirical black point on a real frame: darkest pixel to exactly 0, no
  wasted range, still nothing clipped.
- Whether sky-at-0.25 actually looks right, on L, on RGB and on a palette.
  No measurement settles this; it needs the user's eye.
- Whether a sky-anchored stretch produces a good STAR plate, since the stars
  inherit the master's transform (risk 2 above).
