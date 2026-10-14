# LHSORGBPrep — Design

**Date:** 2026-09-11
**Status:** Approved, pending implementation plan

## Purpose

A PixInsight PJSR script that takes integrated, linear master frames for
L, H, S, O, R, G and B, prepares each according to its type, registers
everything to L, and produces a combined RGB image. It replaces a long
sequence of manual, error-prone GUI steps that is run identically after
every WBPP session.

## Scope

In scope: channel selection dialog, saveable process-instance support, flux and gradient correction of the
broadband channels, registration of all channels to L, linear fit of the
narrowband channels, RGB channel combination, astrometric solution and
spectrophotometric calibration of the combined RGB, and saving all
results.

Out of scope: calibration and integration of sub-frames (WBPP's job),
cropping (done manually afterwards), stretching, LRGB combination,
narrowband palette construction, and any non-linear processing.

## Inputs

Seven file slots. Only `L` is required, since it is the registration
reference. The rest form two independent groups:

- **RGB group (R, G, B).** All three or none. A partial set is an error,
  because ChannelCombination needs all three. When absent, the run
  prepares the narrowband channels and produces no RGB image.
- **Narrowband group (H, S, O).** Any subset, including one channel. `S`
  in particular is frequently absent (HOO work) and that is not an error.

At least one group must be present; `L` alone is nothing to do. All inputs are integrated masters
in the linear state. Input files are opened as working copies and are
never modified on disk.

`L` is always present and is the registration reference. It is never
resampled.

## Pipeline

| Stage | Applies to | Action |
|---|---|---|
| preflight | — | validate files, FITS keywords, modules, output folder |
| load | all | open masters as working copies |
| solve | L, R, G, B | ImageSolver, one astrometric solution per channel |
| correct | L, R, G, B | SPFC → MGC → GraXpert (optional), in that order |
| register | R, G, B, H, S, O | StarAlignment, reference = L |
| linearfit | H, S, O | fit to the min-median member of {H, S, O} |
| combine | R, G, B | ChannelCombination → RGB; skipped if the RGB group is absent |
| solve RGB | RGB | ImageSolver on the combined image |
| calibrate RGB | RGB | SPFC, then SPCC, on the combined image |
| save | all | write every channel and RGB to the output folder |
| cleanup | — | close intermediates; leave L, H, S, O, RGB open |

Correction runs **before** registration, so the flux and gradient models
see native, uninterpolated pixels. The cost is four plate solves instead
of one; this was chosen deliberately over the register-first ordering.

Narrowband channels receive no background extraction. Broadband channels
receive no linear fit.

ChannelCombination does not carry an astrometric solution through from
its source channels, so the combined RGB is solved again in its own right
before calibration. It then receives SpectrophotometricFluxCalibration
followed by SpectrophotometricColorCalibration. SPFC accepting a
three-channel target was verified manually in the installed version on
2026-09-11. Both require the
astrometric solution from the preceding stage, and both take their filter
and sensor configuration from the ASI2600MM default plus the FITS
metadata of the contributing channels.

### LinearFit reference selection

The reference is whichever of the supplied narrowband channels has the
lowest median. Medians are measured over the **central 60% of the frame**
via `selectedRect`, because registration leaves a border of zeros whose
extent differs per channel and would otherwise bias the comparison. The
reference channel is not fitted to itself. With fewer than two narrowband
channels present, the stage is skipped entirely.

### GraXpert mode

GraXpert is **optional**, enabled by a dialog checkbox and on by default.
When disabled, broadband correction is SPFC → MGC only and GraXpert is
neither invoked nor required to be installed.

When enabled it runs in **Background Extraction** mode only. Its denoise
and deconvolution modes are out of scope; the only exposed parameter is
smoothing.

### Sensor defaults

Wherever a process exposes a sensor, camera or QE-curve selection, it is
preset to the ASI2600MM's sensor. PixInsight ships no "ASI2600MM" entry;
the correct curve is the shared IMX571 family entry:

```
deviceQECurveName = "Sony IMX411/455/461/533/571"
```

verified in `/Applications/PixInsight/library/filters.xspd`. The FITS
`INSTRUME` keyword is read and displayed; a disagreement with ASI2600MM
is flagged in the dialog rather than silently overridden.

## Dialog

Seven rows of *label · path field · Browse · Clear*. Selecting a
broadband file immediately reads and displays its `FILTER`, `TELESCOP`
and `INSTRUME` keywords — resolved values in green, unreadable in red.

SPFC filter metadata comes from the FITS headers only. A missing or
unmatched keyword is a hard error naming the offending file; the script
never guesses a filter.

Further controls: output folder (defaults to L's folder), output format
(XISF or FITS), GraXpert smoothing, StarAlignment distortion correction,
and a **Validate only** checkbox.

Run is disabled until all required slots are filled. Options and the
output folder persist across launches via `Settings.read`/`write`.

### Validate only

Runs every preflight check — files readable, FITS keywords present, all
required modules loaded, output folder writable — prints the resolved
plan to the console, and executes nothing. This is the primary way to
sanity-check a new dataset before committing to a full run.

### Output naming

The combined image is named `RGB`. If a window with that identifier
already exists, the first free `RGB_<n>` is used, starting at `n = 1`.
Existing windows are never overwritten or renamed.

This rule lives in `Util.js` as `uniqueWindowId(base)` and is asserted by
`selftest.js`.

## Saveable process instance

The script uses the PJSR `Parameters` API so that its configuration can
be saved as a process icon: configure once, drag the instance to the
workspace, and re-apply it to later datasets without retyping anything.

`Parameters.set` writes every dialog setting on execution; on startup the
dialog repopulates from `Parameters.has`/`get*` when launched from a saved
instance, and from `Settings` otherwise. This is the same mechanism used
throughout PixInsight's own shipped scripts.

This gets the ergonomics of a process without a compiled module. The
limitation, stated plainly: the script will not appear in Process
Explorer, because that requires a native PCL module in C++. That was
considered and rejected as disproportionate for this pipeline, which must
call the ImageSolver *script* regardless.

## Architecture

Installed as a user Feature Script, not into
`/Applications/PixInsight/src/scripts`, which needs admin rights and is
overwritten on upgrade. Registered via **Script → Feature Scripts → Add**.

```
~/PixInsight/scripts/LHSORGBPrep/
  LHSORGBPrep.js      #feature-id entry point; wires dialog to pipeline
  lib/Util.js         logging, window registry, FITS keyword reads,
                      min-median selection, uniqueWindowId, filename rules
  lib/Steps.js        one thin wrapper per PixInsight process
  lib/Pipeline.js     stage orchestration
  lib/UI.js           the dialog
  selftest.js         asserts the pure functions in Util.js
```

The split isolates risk. Every function that is pure arithmetic on plain
values lives in `Util.js` and is testable without PixInsight. Every call
into a PixInsight process is a small wrapper in `Steps.js`, so a wrong
parameter name has exactly one place to be fixed.

A `Channel` object is the spine of the data flow:

```js
{ key, path, window, isBroadband, filter, telescope }
```

The pipeline is an ordered list of transforms over an array of these.

## Error handling

Preflight is a wall: nothing is opened or processed until every check
passes, so a missing `FILTER` keyword costs a dialog box rather than a
half-finished run.

Every window the script creates is entered in a registry at creation. On
any exception the handler closes everything in the registry, reports
which stage failed and on which channel, and leaves the input files
untouched. A failed run costs time, never data. A debug option keeps
windows open on error for diagnosis.

Module availability is checked by name during preflight, so a missing
module names itself instead of surfacing as an undefined-symbol error
mid-run. Note the modules are not where their names suggest: MGC lives in
`MultiscaleProcessing-pxm`, not `GradientCorrection-pxm`. MGC's reference-data download is a
known failure mode and gets an explicit check and message.

`console.abortRequested` is checked between stages so the script is
genuinely interruptible.

## Testing

PJSR has no test harness, and most of this script is process invocation
that can only be exercised inside PixInsight against real frames. The
strategy is therefore to maximise the testable surface:

- `selftest.js` asserts the pure functions in `Util.js`: min-median
  selection, central-rect computation, `uniqueWindowId` clash resolution,
  filename generation, and FITS keyword parsing.
- **Validate only** covers the entire preflight path against real files
  without executing any process.
- One manual run against a real LRGB+HSO dataset is the acceptance test.

## Known risks

**Parameter value types and enums are still unverified.** Parameter
*names* have been recovered from the module binaries (Appendix A), but
their types, and the enumeration values for parameters such as GraXpert's
`correction` and SPFC/SPCC's `narrowbandMode`, have not. These must be
confirmed against a real process instance before the corresponding
wrapper in `Steps.js` is considered done.

**Five plate solves.** The chosen ordering requires solving L, R, G and B
separately, plus one on the combined RGB. If ImageSolver fails on a
channel, the run aborts at that channel with its name; neither broadband
correction nor RGB calibration can proceed without an astrometric
solution.

## Appendix A — Verified process identifiers

Parameter names recovered on 2026-09-11 by extracting string tables from
the installed module binaries in `/Applications/PixInsight/bin`. Names
are authoritative; types and enum values are not yet confirmed.

### Module map

| Process | Module |
|---|---|
| SpectrophotometricFluxCalibration | `ImageCalibration-pxm` |
| SpectrophotometricColorCalibration | `ColorCalibration-pxm` |
| MultiscaleGradientCorrection | `MultiscaleProcessing-pxm` |
| GraXpert | `GraXpert-pxm` |
| StarAlignment | `ImageRegistration-pxm` |
| LinearFit | `ColorCalibration-pxm` and `ImageIntegration-pxm` |
| ChannelCombination | `ColorSpaces-pxm` |

MGC is **not** in `GradientCorrection-pxm`, despite the name; that module
holds the older GradientCorrection process.

### SPFC

```
catalogId, deviceQECurve, deviceQECurveName,
grayFilterName, grayFilterWavelength, grayFilterBandwidth, grayFilterTrCurve,
redFilterName,   redFilterWavelength,   redFilterBandwidth,   redFilterTrCurve,
greenFilterName, greenFilterWavelength, greenFilterBandwidth, greenFilterTrCurve,
blueFilterName,  blueFilterWavelength,  blueFilterBandwidth,  blueFilterTrCurve,
narrowbandMode, narrowbandIntegrationSteps,
psfHotPixelFilterRadius, psfNoiseReductionFilterRadius
```

The presence of both `grayFilterName` and the red/green/blue triplet is
why SPFC accepts either a mono or an RGB target, matching the manual
verification.

### SPCC

Shares the filter and device parameters above, minus the gray set, plus:

```
autoCatalog, structureDetection, psfAllowClusteredSources,
whiteReferenceId, whiteReferenceName, whiteReferenceSpectrum,
whiteReferenceViewId, whiteHigh,
whiteUseROI, whiteROIX0, whiteROIY0, whiteROIX1, whiteROIY1,
manualWhiteBalance, manualRedFactor, manualGreenFactor, manualBlueFactor,
narrowbandMode, narrowbandIntegrationSteps, narrowbandOptimizeStars,
outputWhiteReferenceMask
```

### MGC

```
useMARSDatabase, marsDatabaseFiles,
grayMARSFilter, redMARSFilter, greenMARSFilter, blueMARSFilter,
referenceImageId, centerRA, centerDec, searchRadius,
minFieldRatio, maxFieldRatio, enforceFieldLimits,
scaleFactorRK, scaleFactorG, scaleFactorB,
gradientScale, modelSmoothness, structureSeparation,
fluxScaleFactor, showGradientModel, generateGradientModel,
downsampledWidth, downsampledHeight
```

MGC depends on the MARS all-sky reference database being present and
downloaded; `useMARSDatabase` and `marsDatabaseFiles` are the preflight
check surface for this.

### GraXpert

```
correction, smoothing, strength,
backgroundExtraction, backgroundExtractionAIModel,
backgroundExtractionDefaultModel, backgroundExtractionModels,
createBackground, replaceImage,
denoiseAIModel, deconvolutionMode,
batchSize, disableGPU, showLogs,
appRecommendedVersion, appRecommendedURL
```

Background Extraction mode is selected via `correction`; the exposed knob
is `smoothing`. The denoise and deconvolution parameters are out of scope.
