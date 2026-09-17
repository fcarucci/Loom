# Loom Sharpening — Design

**Date:** 2026-09-12
**Status:** Phase 1 agreed, not yet implemented

## Phases

**Phase 1 (build first): deterministic, no LLM.** Per-channel correction with
fixed mappings from the semantic levels to each tool's parameters. This is the
baseline and must stand on its own — it is what runs when no model is
available, and it is the control to compare any later automation against.

**Phase 2 (later): the LLM chooses.** The agent replaces the fixed mapping
table, taking Loom's measurements plus the same semantic knobs. Everything
below about determinism and bounded validation applies then.

## Phase 1

### Where it runs

Per channel, **after GraXpert and before registration**: native scale, linear
data, uninterpolated pixels. Correcting star shape before resampling is the
point — registration's Lanczos interpolation spreads whatever aberration is
already there, and doing this per channel is what stops the composite showing
halos where the channels' star profiles disagree.

### Relationship to FWHM matching

Per-channel correction is the INTENDED fix for colour halos. Convolving the
sharper channels to a common FWHM is the crude fallback: it removes halos by
degrading R and B to match G, so everything ends up as soft as the worst
channel. Correcting each channel's stars instead raises the floor.

Measured on the Elephant Trunk masters: G is 4.72" against R's 2.95", so
matching would cost R about 60% of its resolution. Per-channel aberration
correction targets the same artefact without paying that.

FWHM matching stays available as a fallback for data where correction cannot
help — badly trailed frames, or a channel whose SNR will not survive being
sharpened — but it is no longer the primary route to halo-free stars.

### Three separate operations

They are invoked separately, not as one pass:

1. **Aberration correction — ALWAYS runs.** Not optional, no level. It is the
   safe operation of the three: it fixes star shape and optical aberration
   without adding detail that was not there.
2. **Star reduction — optional.** `None | Low | High`, default `None`.
3. **Sharpening / detail — optional.** `None | Low | Medium | High`,
   default `None`.

Running them separately matters: each is a different risk profile, and a user
who wants tighter stars should not be forced into sharpening to get them.

### Halo control — this IS the FWHM matching

```
Halos: [ None | Reduce ]
```

`Reduce` runs FWHM matching: measure every channel, take the widest as the
target, and convolve the others up to it with
`sigma_add = sqrt(sigma_target^2 - sigma_channel^2)` via `Convolution`.

This has NOTHING to do with BXT or SyQon. It is a Loom operation using
PixInsight's own `Convolution` process, and it runs whether or not a
sharpening tool is selected.

**Sharpening-induced halos are NOT addressed and are out of scope for now.**
BXT's `adjust_halos` stays at 0 and is not exposed. If sharpening turns out to
introduce halos on real data, that is a separate decision to take then — not
something to pre-emptively add a knob for.

**Why this is the halo fix:** colour halos on stars come from the channels
having different star sizes. G measured 4.72" against R's 2.95" on the
Elephant Trunk masters — a green star genuinely half again as large as the red
one beneath it, so it spills past the edge of the combined star. Matching the
widths removes the halo at its source.

**Where it runs:** after registration and cropping, before the RGB combine —
all channels are then on L's grid, so FWHM in pixels is directly comparable.
It is its own cached stage.

**The cost, stated plainly:** it is destructive. Matching to the widest channel
blurs the sharpest one down. On this data R would lose about 60% of its
resolution. That is acceptable here because it is LRGB — RGB carries colour, L
carries detail, and L is untouched — but it would not be acceptable for an
RGB-only image.

### Tool detection and selection

Loom detects what is installed and offers a choice:

- **BlurXTerminator** — module `BlurXTerminator-pxm` present in
  `/Applications/PixInsight/bin`. Detect with the same
  `Steps.moduleAvailable( "BlurXTerminator" )` check used for the other
  processes.
- **SyQon Parallax** — a script wrapping the external `parallax_cli`. Loom does
  NOT need its own path setting: SyQon already stores the configured
  executable path, and Loom reads it. Verified on this machine 2026-09-12.

  ```
  script:  /Applications/PixInsight/src/scripts/SyQon_Parallax.js
  config:  <File.systemTempDirectory>/SyQonParallaxCLI/syqon_parallax_config.csv
           (single line, the executable path -- here /Applications/ParallaxAI/parallax_cli)
  ```

  Available when the script exists AND the config is readable AND the path it
  names exists and is executable. Note the config lives in the system temp
  directory, so it can be cleared by the OS -- treat a missing config as "not
  configured" and fall back to BXT rather than failing.

The dialog shows a `Sharpening tool` selector listing only what is actually
available, plus `None`. If neither is installed the whole section is disabled
and the pipeline skips it — never a hard failure.

### Parameter mapping (Phase 1 baseline)

Fixed, readable in one place, so the mapping can be reviewed and tuned:

SyQon's real ranges, read from `SyQon_Parallax.js` on 2026-09-12:

```
correctAberration : boolean                  -- always true
starReduction     : 0 = disabled, 1..6       -- discrete integer levels
sharpen           : 0.0 = disabled, 0.0..1.0 -- alpha
mode              : "classic" (Natural) | "aesthetics" (Defined)
tileSize / overlap: 512 / 128                -- defaults, leave alone
```

| Intent | BXT | SyQon Parallax |
|---|---|---|
| aberration (always) | `correctOnly = true` | `correctAberration = true` |
| star reduction None | `sharpenStars = 0` | `starReduction = 0` |
| star reduction Low / High | `sharpenStars` stepped | `starReduction = 2` / `5` |
| detail None | `sharpenNonstellar = 0` | `sharpen = 0.0` |
| detail Low / Med / High | `sharpenNonstellar` stepped | `sharpen = 0.3 / 0.6 / 0.9` |

`mode` stays at `"classic"` (Natural). Exposing "aesthetics" is a later choice,
not part of Phase 1.

BXT's values must still come from a real parameter dump rather than memory —
the rule that produced `docs/verified-parameters.md`, after `correction` was
assumed wrong once already. SyQon's are recorded above because they were read
from its source.

### Caching

Each operation becomes its own stage in the chain
(`aberration`, `starreduction`, `sharpen`), between `graxpert` and `register`
in `Pipeline.STAGE_ORDER`. Their parameters are the tool name and the chosen
level, so changing the tool or a level invalidates that stage and everything
after it — which is exactly the behaviour wanted while comparing settings.

### Why this ordering is worth stating

Sharpening before registration means the cached registered channels are
invalidated whenever a sharpening setting changes. That is correct but costly:
about 9 s per channel to re-register. The alternative — sharpening after
registration — would preserve those entries but operate on interpolated
pixels. Correctness wins; the cache absorbs the rest.



## Intent

> **NOTE (2026-09-14):** the LLM sections below have been superseded by
> `docs/superpowers/specs/2026-09-14-llm-integration-design.md`, which governs
> all three LLM features — sharpening parameters, SNR region selection, and
> the end-of-run report — under one set of rules. The Phase 1 deterministic
> design above is unchanged and remains what runs today.


Sharpening is driven by two **semantic** knobs in Loom's dialog, not numeric
parameters. The user states intent; an LLM agent maps that intent plus Loom's
own measurements onto concrete process parameters; the pipeline runs
autonomously. There is **no mid-run approval step** — the user judges the
finished image.

```
Dialog:   Star reduction  [ None | Low | High ]
          Detail          [ Low  | Medium | High ]
```

`Star reduction: None` leaves stars untouched — the default, and the only
setting that is unambiguously safe. `Low` and `High` shrink them. Stating it as
reduction rather than "star size" removes the ambiguity about which direction
the knob runs, and makes "do nothing" an explicit, named choice rather than one
end of a scale.

`Detail` is the sharpening strength.

Three values each. Numeric sliders were rejected: a number like
`sharpenNonstellar = 0.42` is meaningless to state as intent, and meaningless
for a language model to reason about.

## Flow

```
Loom  →  measurements
           per channel: FWHM, elongation (sx/sy), noise (MAD), star count
           global: pixel scale, sampling (FWHM in px vs Nyquist),
                   drizzle factor, LRGB vs narrowband palette
       +  intent  { starReduction: "none|low|high",
                    detail:        "low|medium|high" }
         ↓
Agent →  { correctOnly, sharpenStars, sharpenNonstellar,
           nonstellarPSFDiameter, autoNonstellarPSF,
           starReduction, applyTo, rationale }
         ↓
Pipeline runs. User evaluates the output image.
```

The agent **never sees pixel data** — only a dozen numbers and two words. One
call per run: fast, cheap, and auditable.

## Hard requirements

**Determinism.** The same data plus the same knobs MUST produce the same
parameters, or reprocessing a dataset next month yields a different image.
Two measures, both required:

- temperature 0
- the decision is cached against `(measurements + intent)` and pinned into the
  stage cache key, so a re-run reuses the recorded choice rather than asking
  again

This reuses the chained-key machinery in `lib/Cache.js`.

**Bounded, validated response.** The agent returns fields with declared legal
ranges. Anything out of range, malformed, or missing is rejected and a safe
default used instead. These parameters touch the user's data; a hallucinated
value must never reach BlurXTerminator.

**The rationale is logged, not gated.** It is a record for when an image comes
out wrong, not a checkpoint that interrupts the run.

## What the agent decides, and why it is a judgement

- **Whether to sharpen at all.** If FWHM is already near the sampling limit
  there is nothing to recover and sharpening only manufactures artefacts.
- **At which scale.** A star that is 3 px at native is 6 px on the 2x drizzled
  grid; BXT behaves differently in each case.
- **Scope.** In LRGB, sharpen L and leave the colour soft — sharpening chroma
  costs noise and buys nothing visible. In a narrowband palette every channel
  carries structure.
- **Per-channel targets.** With G measured 60% wider than R, sharpening each
  channel toward a common PSF is an alternative to blurring R down — it raises
  the floor rather than lowering the ceiling. Safe only if the wider channel's
  SNR supports it.
- **Tool split.** Aberration correction (BXT `correctOnly`, SyQon
  `--correct-aberration`) is nearly always safe; star reduction is aesthetic;
  sharpening is the risky one. These are separate decisions, not one slider.

## Measurable quality metrics (Loom computes, agent consumes)

- star FWHM before/after — how much it actually tightened
- **annulus undershoot** around bright stars — negative values against local
  background mean over-sharpening. This is BXT's characteristic failure and it
  is a number, not an opinion
- MAD change — noise gain
- star count change — a large jump means noise promoted into false stars

## Tooling

Works for both BlurXTerminator (`correctOnly`, `sharpenStars`,
`sharpenNonstellar`, `nonstellarPSFDiameter`, `autoNonstellarPSF` — module
`BlurXTerminator-pxm`, installed) and SyQon Parallax (`--correct-aberration`,
`--star-reduction`, `--sharpen` via `parallax_cli`, path not yet configured).
Same measurements, same metrics, different knobs — which also allows comparing
the two on real data rather than on opinion.

## Explicitly out of scope

- The agent judging whether the result *looks* right. Ringing is measurable;
  "over-processed" is the user's eye at print scale.
- Any approval gate during the run.
- Numeric knobs in the dialog.
- Fixing halos introduced by sharpening itself (BXT `adjust_halos`). Out of
  scope. The Halos knob addresses cross-channel halos only.
- Star reduction as a continuous scale. It is none / low / high, with `None`
  the default.
