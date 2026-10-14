# Narrowband normalisation (loomsiril/loom/nbnorm.py)

> **Changed 2026-10-14.** The noise match and the 60% Ha blend described below are no longer applied: on the Sh2-132
> HSO they left H, S and O with almost the same signal, so the palette came out grey-pink. `normalize` now only shifts
> each channel's sky (a low percentile of block means) to the Ha channel's and clips, `METHOD = "sky-offset-v1"`. The
> text below documents the earlier, unverified guess.

Loom normalises a narrowband palette composite by calling PixInsight's NarrowbandNormalization process once per
palette with fixed neutral settings (lib/Steps.js:2162-2199, `Steps.narrowbandNormalize`). The PixInsight module is
closed. This module is a numpy implementation of the same call for the neutral settings only.

## Scope

Only the settings Loom passes are implemented, and there are no other modes:

| Setting | Value |
|---|---|
| Palette | HOO 0, SHO 1, HSO 2 (index in `PALETTE_INDEX`) |
| Lightness | off |
| Blend mode | 1 |
| O3 boost, S2 boost | 1 |
| SCNR | 0 |
| Shadow point | 1 |
| Highlight reduction | 1 |
| Brightness | 1 |
| Ha blend | 0.6 (`HA_BLEND`) |

The input is the palette composite: three linear float planes in R, G, B order, already mapped by the palette.

| Palette | R | G | B |
|---|---|---|---|
| HOO | H | O | O |
| SHO | S | H | O |
| HSO | H | S | O |

## What is published, and what is assumed

Published, in prose only, by the tool's authors (Bill Blanshan and Mike Cranfield; the process evolved from
Blanshan's PixelMath expressions, with Cranfield's mathematical changes):

- The process sits under Process > ColorCalibration in PixInsight. Its purpose is to balance the intensities of the
  three channels of a palette-mapped narrowband RGB image (HOO, SHO, HSO and similar). Stars are best removed first.
  See https://www.astroworldcreations.com/blog/new-pixinsight-process-narrowbandnormalization and
  https://cosmicphotons.com/scripts/.
- Ha is the reference. The published summary is "offset scaling" of O3 and S2 to the Ha signal. See
  https://app.telescope.live/en/tutorials/98/exploring-pixinsight-narrowband-normalization-techniques.
- The original expressions also carry green noise reduction (SCNR), O3 and S2 boost, shadow point, highlight
  reduction and brightness. An older PixelMath version of the method, as quoted by a third-party page
  (https://astroguide.starlust.de/html/NarrowBandwidthNormalizationScri.html), describes a blackpoint setting where 1
  starts normalisation after the median; whether the process's shadow point behaves the same is not published.
- Users describe the process as intended for stretched, starless, non-linear data. See
  https://app.astrobin.com/forum/topic/157811/problem-with-narrowband-normalisation-in-192.

Not published in any page read: formulas, the exact "offset scaling" equations, the definition of "blend mode" or of
"Ha blend", and the highlight-reduction and brightness equations. The equations live in the PixelMath script files and
in the tool's own help, which were not read.

No GPL script of the same name was read, copied or paraphrased.

Loom's call (lib/Steps.js:2162-2199) passes the palette composite as it stands after the narrowband colour
calibration step, linear, with stars still in, and replaces the view in place. The Ha blend is left at the process
default of 0.6 (lib/Steps.js:2162-2199 notes that no documented neutral value exists).

The steps implemented here are an assumption. They are a robust background and noise match to the Ha channel, then
the Ha blend and clipping, which the published prose does not define. Because no formulas are published, this module
cannot be certified as faithful to the published method. Parity with PixInsight's output is not verified until the
reference pair described below is supplied. Because the published use is starless and non-linear, and Loom passes
linear data with stars, the port's behaviour on Loom's input is unverified.

## Formulae

For each plane x_c of channel c:

1. Background: b_c = median(x_c).
2. Noise: s_c = 1.4826 * median(|x_c - b_c|) (median absolute deviation scaled to a Gaussian sigma). A plane with
   s_c = 0 is rejected.
3. Reference: the channel that carries Ha for the palette (R for HOO and HSO, G for SHO), with b_r and s_r.
4. Scale to reference, for every channel c other than the reference:

       y_c = (x_c - b_c) * (s_r / s_c) + b_r

   The reference is unchanged. The map is affine with positive slope, so median(y_c) = b_r exactly, and
   sigma(y_c) = s_r.
5. Ha blend, for every channel the palette does not map to Ha (HOO: G and B; SHO: R and B; HSO: G and B):

       y_c = (1 - 0.6) * y_c + 0.6 * y_r

   This step is the documented minimum and is not confirmed against the published description. It is the least
   certain part of the implementation.
6. Clip to [0, 1] and return float32 planes in R, G, B order.

Steps 1 to 4 are exposed as `scale_to_reference`, which returns float64 planes before the blend and clip. The
background and noise properties are checked on that output because the blend moves medians slightly.

## Tests

- `tests/loom/test_nbnorm.py` checks properties on seeded synthetic palettes built from `tests/loom/synth.py`
  masters for H, S and O: backgrounds equalised within 1e-3 after scaling, noise matched, outputs float32 in [0, 1],
  deterministic, reference channel kept in its palette position, and rejection of flat or mis-shaped input.
  Output-level tests pin each non-Ha channel to 0.4 times its scaled plane plus 0.6 times the scaled Ha reference
  (the blend), and a clip test checks that values pushed outside [0, 1] are clipped.
- The reference regression reads `tests/fixtures/nbnorm/` (`input.fits`, `output.fits`, `palette.txt`). It is
  skipped with the reason "reference pair not yet supplied" until the pair is supplied. The tolerance is a
  maximum absolute difference of 0.02, to be tightened once the pair is in place.
