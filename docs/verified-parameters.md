# Verified process parameters

Captured 2026-09-11 against a live PixInsight Core 1.9.4 (Lockhart, arm64)
automation instance, using `toSource()` on `new <Process>` for each of the
eight identifiers in Appendix A. This is the ground truth for Task 7; where
this document and Appendix A disagree, this document wins.

Probe script and raw dump lived only in `/tmp/agent-scratch/` (`probe-params.js`,
`params-dump.txt`) and are not part of this repository.

## SpectrophotometricFluxCalibration

```javascript
var P = new SpectrophotometricFluxCalibration;
P.narrowbandMode = false;
P.grayFilterTrCurve = "";
P.grayFilterName = "";
P.redFilterTrCurve = "";
P.redFilterName = "";
P.greenFilterTrCurve = "";
P.greenFilterName = "";
P.blueFilterTrCurve = "";
P.blueFilterName = "";
P.grayFilterWavelength = 656.3;
P.grayFilterBandwidth = 3.0;
P.redFilterWavelength = 656.3;
P.redFilterBandwidth = 3.0;
P.greenFilterWavelength = 500.7;
P.greenFilterBandwidth = 3.0;
P.blueFilterWavelength = 500.7;
P.blueFilterBandwidth = 3.0;
P.deviceQECurve = "";
P.deviceQECurveName = "";
P.broadbandIntegrationStepSize = 0.50;
P.narrowbandIntegrationSteps = 10;
P.rejectionLimit = 0.30;
P.catalogId = "GaiaDR3SP";
P.minMagnitude = 0.00;
P.limitMagnitude = 12.00;
P.autoLimitMagnitude = true;
P.psfStructureLayers = 5;
P.saturationThreshold = 0.75;
P.saturationRelative = true;
P.saturationShrinkFactor = 0.10;
P.psfNoiseLayers = 1;
P.psfHotPixelFilterRadius = 1;
P.psfNoiseReductionFilterRadius = 0;
P.psfMinStructureSize = 0;
P.psfMinSNR = 40.00;
P.psfAllowClusteredSources = false;
P.psfType = SpectrophotometricFluxCalibration.PSFType_Auto;
P.psfGrowth = 1.75;
P.psfMaxStars = 24576;
P.psfSearchTolerance = 4.00;
P.psfChannelSearchTolerance = 2.00;
P.generateGraphs = true;
P.generateStarMaps = false;
P.generateTextFiles = false;
P.outputDirectory = "";
```

## SpectrophotometricColorCalibration

```javascript
var P = new SpectrophotometricColorCalibration;
P.applyCalibration = true;
P.narrowbandMode = false;
P.narrowbandOptimizeStars = false;
P.whiteReferenceSpectrum = "200.5,0.0715066,201.5,0.0689827,202.5,0.0720216,...[full 2500+ wavelength/value CSV pairs to 2505,0.1598265, elided here — see raw dump]";
P.whiteReferenceName = "Average Spiral Galaxy";
P.redFilterTrCurve = "400,0.088,402,0.084,...,698,0.649,700,0.649"; // Sony Color Sensor R-UVIRcut transmission curve, 400-700nm @ 2nm steps
P.redFilterName = "Sony Color Sensor R-UVIRcut";
P.greenFilterTrCurve = "400,0.089,402,0.086,...,698,0.282,700,0.289"; // Sony Color Sensor G-UVIRcut transmission curve, 400-700nm @ 2nm steps
P.greenFilterName = "Sony Color Sensor G-UVIRcut";
P.blueFilterTrCurve = "400,0.438,402,0.469,...,698,0.073,700,0.073"; // Sony Color Sensor B-UVIRcut transmission curve, 400-700nm @ 2nm steps
P.blueFilterName = "Sony Color Sensor B-UVIRcut";
P.redFilterWavelength = 656.3;
P.redFilterBandwidth = 3.0;
P.greenFilterWavelength = 500.7;
P.greenFilterBandwidth = 3.0;
P.blueFilterWavelength = 500.7;
P.blueFilterBandwidth = 3.0;
P.deviceQECurve = "1,1,500,1,1000,1,1500,1,2000,1,2500,1";
P.deviceQECurveName = "Ideal QE curve";
P.broadbandIntegrationStepSize = 0.50;
P.narrowbandIntegrationSteps = 10;
P.catalogId = "GaiaDR3SP";
P.limitMagnitude = 12.00;
P.autoLimitMagnitude = true;
P.targetSourceCount = 8000;
P.psfStructureLayers = 5;
P.saturationThreshold = 0.75;
P.saturationRelative = true;
P.saturationShrinkFactor = 0.10;
P.psfNoiseLayers = 1;
P.psfHotPixelFilterRadius = 1;
P.psfNoiseReductionFilterRadius = 0;
P.psfMinStructureSize = 0;
P.psfMinSNR = 40.00;
P.psfAllowClusteredSources = true;
P.psfType = SpectrophotometricColorCalibration.PSFType_Auto;
P.psfGrowth = 1.25;
P.psfMaxStars = 24576;
P.psfSearchTolerance = 4.00;
P.psfChannelSearchTolerance = 2.00;
P.neutralizeBackground = true;
P.backgroundReferenceViewId = "";
P.backgroundLow = -2.80;
P.backgroundHigh = 2.00;
P.backgroundUseROI = false;
P.backgroundROIX0 = 0;
P.backgroundROIY0 = 0;
P.backgroundROIX1 = 0;
P.backgroundROIY1 = 0;
P.generateGraphs = true;
P.generateStarMaps = false;
P.generateTextFiles = false;
P.outputDirectory = "";
```

(The full literal `whiteReferenceSpectrum` and `*FilterTrCurve` default CSV
strings are hundreds of comma-separated wavelength/value pairs each; they
are PixInsight defaults and are elided here for readability — they were
captured verbatim in the raw probe dump. They are not touched by the
pipeline: SPCC's device/filter parameters are left at their library
defaults for a color camera pipeline, since SPCC in this pipeline runs on
the combined **RGB** image, not raw OSC filters.)

## MultiscaleGradientCorrection

```javascript
var P = new MultiscaleGradientCorrection;
P.useMARSDatabase = false;
P.marsDatabaseFiles = [ // enabled, path
];
P.grayMARSFilter = "L";
P.redMARSFilter = "R";
P.greenMARSFilter = "G";
P.blueMARSFilter = "B";
P.referenceImageId = "";
P.gradientScale = 1024;
P.structureSeparation = 3;
P.modelSmoothness = 1.00;
P.minFieldRatio = 0.017;
P.maxFieldRatio = 0.167;
P.enforceFieldLimits = true;
P.scaleFactorRK = 1.0000;
P.scaleFactorG = 1.0000;
P.scaleFactorB = 1.0000;
P.showGradientModel = true;
P.command = "";
```

## GraXpert

```javascript
var P = new GraXpert;
P.backgroundExtraction = true;
P.smoothing = 0.0;
P.correction = "Subtraction";
P.createBackground = false;
P.backgroundExtractionAIModel = "";
P.denoising = false;
P.strength = 1.00;
P.batchSize = 4;
P.denoiseAIModel = "";
P.disableGPU = false;
P.replaceImage = false;
P.showLogs = false;
P.appPath = "";
P.deconvolution = false;
P.deconvolutionMode = "Object-only";
P.deconvolutionObjectStrength = 0.5;
P.deconvolutionObjectPSFSize = 5.0;
P.deconvolutionObjectAIModel = "";
P.deconvolutionStarsAIModel = "";
```

## StarAlignment

```javascript
var P = new StarAlignment;
P.structureLayers = 5;
P.noiseLayers = 0;
P.hotPixelFilterRadius = 1;
P.noiseReductionFilterRadius = 0;
P.minStructureSize = 0;
P.sensitivity = 0.50;
P.peakResponse = 0.50;
P.brightThreshold = 3.00;
P.maxStarDistortion = 0.60;
P.allowClusteredSources = false;
P.localMaximaDetectionLimit = 0.75;
P.upperLimit = 1.000;
P.invert = false;
P.distortionModel = "";
P.undistortedReference = false;
P.rigidTransformations = false;
P.distortionCorrection = false;
P.distortionMaxIterations = 20;
P.distortionMatcherExpansion = 1.00;
P.rbfType = StarAlignment.DDMThinPlateSpline;
P.maxSplinePoints = 4000;
P.splineOrder = 2;
P.splineSmoothness = 0.005;
P.splineOutlierDetectionRadius = 160;
P.splineOutlierDetectionMinThreshold = 4.0;
P.splineOutlierDetectionSigma = 5.0;
P.matcherTolerance = 0.0500;
P.ransacTolerance = 1.9000;
P.ransacMaxIterations = 2000;
P.ransacMaximizeInliers = 1.00;
P.ransacMaximizeOverlapping = 1.00;
P.ransacMaximizeRegularity = 1.00;
P.ransacMinimizeError = 1.00;
P.maxStars = 0;
P.fitPSF = StarAlignment.FitPSF_DistortionOnly;
P.psfTolerance = 0.50;
P.useTriangles = false;
P.polygonSides = 5;
P.descriptorsPerStar = 20;
P.restrictToPreviews = true;
P.intersection = StarAlignment.MosaicOnly;
P.useBrightnessRelations = false;
P.useScaleDifferences = false;
P.scaleTolerance = 0.100;
P.referenceImage = "";
P.referenceIsFile = false;
P.targets = [ // enabled, isFile, image
];
P.targetCSVFilePath = "";
P.inputHints = "";
P.outputHints = "";
P.mode = StarAlignment.RegisterMatch;
P.writeKeywords = true;
P.generateMasks = false;
P.generateDrizzleData = true;
P.generateDistortionMaps = false;
P.generateHistoryProperties = true;
P.inheritAstrometricSolution = false;
P.frameAdaptation = false;
P.randomizeMosaic = false;
P.pixelInterpolation = StarAlignment.Auto;
P.clampingThreshold = 0.30;
P.outputDirectory = "";
P.outputExtension = ".xisf";
P.outputPrefix = "";
P.outputPostfix = "_r";
P.maskPostfix = "_m";
P.distortionMapPostfix = "_dm";
P.outputSampleFormat = StarAlignment.SameAsTarget;
P.overwriteExistingFiles = false;
P.onError = StarAlignment.Continue;
P.useFileThreads = true;
P.fileThreadOverload = 1.00;
P.maxFileReadThreads = 0;
P.maxFileWriteThreads = 0;
P.memoryLoadControl = true;
P.memoryLoadLimit = 0.85;
/*
 * Read-only properties
 *
P.outputData = [ // outputImage, outputMask, totalPairMatches, inliers, overlapping, regularity, quality, rmsError, rmsErrorDev, peakErrorX, peakErrorY, H11, H12, H13, H21, H22, H23, H31, H32, H33, frameAdaptationBiasRK, frameAdaptationBiasG, frameAdaptationBiasB, frameAdaptationSlopeRK, frameAdaptationSlopeG, frameAdaptationSlopeB, frameAdaptationAvgDevRK, frameAdaptationAvgDevG, frameAdaptationAvgDevB, referenceStarX, referenceStarY, targetStarX, targetStarY, outputDistortionMap
];
 */
```

Note: `targets` is an array of `[enabled, isFile, image]` triplets — `image`
is a **view id string**, not a file path, when `isFile` is `false`. This is
how StarAlignment registers against in-memory views instead of files.
`referenceImage` + `referenceIsFile = false` is how the *reference* is a
view id string rather than a file path.

## LinearFit

```javascript
var P = new LinearFit;
P.referenceViewId = "";
P.rejectLow = 0.000000;
P.rejectHigh = 0.920000;
```

LinearFit is far smaller than Appendix A implied — it has exactly three
parameters. `referenceViewId` is the (string) view id of the reference
image.

## ChannelCombination

```javascript
var P = new ChannelCombination;
P.colorSpace = ChannelCombination.RGB;
P.channels = [ // enabled, id
   [true, ""],
   [true, ""],
   [true, ""]
];
P.inheritAstrometricSolution = true;
```

`channels` is an array of three `[enabled, id]` pairs in R, G, B order,
where `id` is a view id string.

## ImageSolver

```
EXCEPTION for ImageSolver: ReferenceError: ImageSolver is not defined
```

Confirmed: `ImageSolver` is **not** a core process and has no global PJSR
constructor of that name at all — not even a stub that throws
`Error: not a constructor`. It is a plain `ReferenceError`, meaning no
symbol `ImageSolver` exists in the global scope until something defines it.

Investigation of the installed script tree
(`/Applications/PixInsight/src/scripts/ImageSolver/`) shows why: ImageSolver
is a PJSR **script** (feature id `Astrometry > ImageSolver`), not a process
module. Its entry point is `ImageSolver.js`, which `#include`s
`ImageSolverDialog.js` and `ImageSolverEngine.js`. The reusable engine class
*is* named `ImageSolver` (`var ImageSolver = class { constructor() {...} }`,
`ImageSolverEngine.js:149`), but it only exists after that file has been
`#include`d — it is not registered with the core the way a process module
is.

**How Task 7 must invoke it:**
```javascript
#include <pjsr/astrometry/AstrometricMetadata.js>
#include "<path-to>/ImageSolver/ImageSolverEngine.js"

let engine = new ImageSolver;
engine.initialize( window, false /*prioritizeSettings*/ );
// engine.metadata / engine.solverCfg hold the astrometric metadata and
// solver configuration objects (SolverConfiguration, AstrometricMetadata)
engine.solveImage( window );        // throws on failure
engine.saveImage( window );         // optional, writes WCS keywords/output
```
**The path to write (verified 2026-09-17).** `#include` is resolved when the
script is parsed, so it can never take a runtime value such as
`CoreApplication.srcDirPath`. Write it with angle brackets and a relative
path instead:

```javascript
#include <../src/scripts/ImageSolver/ImageSolverEngine.js>
```

Angle-bracket includes resolve against the core's `include` directory
(`<base>/include`), so `../src/scripts/...` lands on `<base>/src/scripts/...`
whatever `<base>` is and on whatever platform. Probed on macOS: it resolves,
and `typeof ImageSolver` is `"function"` afterwards. An absolute
`/Applications/...` path is macOS-only, and a path that does not resolve is
not reported — PixInsight discards the entire script with no message, no
console output and exit status 0.

`ImageSolverEngine.js` itself further `#include`s
`AstrometricMetadata.js`, `AstronomicalCatalogs.js`,
`SearchCoordinatesDialog.js`, `CatalogDownloaderDialog.js`,
`ProjectionConfigurationDialog.js`, `UtilityControls.js`,
`VizierMirrorDialog.js` from `<pjsr/astrometry/...>`, plus
`DateTimeEditor.js` and `GeodeticCoordinatesEditor.js` from
`<pjsr/controls/...>` — all standard PJSR include paths, so a `#include`
of `ImageSolverEngine.js` alone should pull in everything it needs
transitively, provided its own `#include`s resolve (they use `<pjsr/...>`
absolute-from-repository paths, which work from any script location).
`engine.solveImage(window)` throws a plain `Error` on failure (see
`ImageSolver.js`'s own driver code, which wraps it in `try/catch` and
aborts per-file on exception) — Task 7 should do the same and abort the
run with the failing channel's name, per the design's "Five plate solves"
requirement.

This is a materially different integration shape than the other seven
processes: it cannot be built with `new ImageSolver; P.someParam = x;
P.executeOn(view)`. It must be driven by including the engine script and
calling `initialize()` / `solveImage()` directly on a live `ImageWindow`.

### ImageSolver — `solverCfg.recursiveSplines` (PixInsight 1.9.5)

The one solver setting Loom overrides. Everything else is left to
`solverCfg.LoadSettings()`, i.e. to the user's own ImageSolver
configuration.

| Parameter | Value | Where the value comes from |
| --- | --- | --- |
| `solverCfg.recursiveSplines` | `true` | Declared `ImageSolverEngine.js:72` (persisted, `DataType.Boolean`), defaulted to `false` at `:129`, read at `:565` and `:939` where it is the last argument to `new ReferSpline`. |

Recursive surface splines model distortion as a partition of unity of local
surface splines over a quadtree on top of the projective model, using every
matched star after robust outlier rejection, rather than fitting one global
function to a capped set of control points. On the mosaic panel with 34,000
control points published in the PixInsight 1.9.5 announcement, the median
deviation against Gaia DR3 improved from **0.315 px to 0.091 px** — roughly
one arcsecond to about a third of one — and the solve was faster, not
slower.

**Measured on the owner's own data**, not quoted from the announcement.
NGC 5907 `masterLight_..._FILTER-L_mono_autocrop.xisf`, 5710x3182 at 0.9664
arcsec/px, PixInsight 1.9.5 build 1702, 2026-09-18. The frame was opened
read-only, verified as shipped, re-solved with `recursiveSplines = true`,
and verified again:

| | matched stars | median | RMS | max |
| --- | --- | --- | --- | --- |
| solution as shipped by WBPP | 230 | 0.0190 px / 0.0178" | 0.0414 px / 0.0386" | 0.1411 px / 0.1273" |
| re-solved, `recursiveSplines = true` | 237 | 0.0157 px / 0.0152" | 0.0338 px / 0.0325" | 0.1099 px / 0.1061" |

Median −17%, RMS −18%, maximum −22%, and seven more stars matched. The
improvement is an order of magnitude smaller than the published mosaic-panel
figure, which is what one should expect: this is a single well-corrected
field, not a mosaic panel with 34,000 control points and real distortion to
model. The solve took **959 ms** and each verification **762 ms**.

**Ordering is part of the parameter.** `engine.initialize( window, false )`
calls `this.solverCfg.LoadSettings()` internally
(`ImageSolverEngine.js:183`), so an override applied before `initialize()`
is silently replaced by whatever the user last saved from the ImageSolver
dialog. It must be set *after* `initialize()` and before `solveImage()`.

Setting it does not disturb the user's saved configuration:
`ImageSolverEngine.js` contains no call to `SaveSettings` or
`SaveParameters` anywhere (verified by grep — only the front-end
`ImageSolver.js` persists), so the override lives and dies with the
`ImageSolver` instance.

---

## AstrometricResiduals — verifying a solution (PixInsight 1.9.5)

`<pjsr/astrometry/AstrometricResiduals.js>` is the measurement half of
astrometric solution analysis: star detection, PSF fitting, Gaia retrieval,
matching, and the deviations between measured centroids and catalog
positions. It is the base class of the `AstrometricSolutionVerifier`
script's engine, and its own header states it is shared "so that every
script that measures an astrometric solution measures it in exactly the
same way".

Unlike `ImageSolverEngine.js` it ships under `include/`, not `src/scripts/`,
so it needs no `../src/scripts/...` path, and it carries its own
`#ifndef __PJSR_AstrometricResiduals_js` guard. It defines no `VERSION`,
`TITLE` or `SETTINGS_MODULE`, so it cannot collide with the `#define`s
Steps.js must make for ImageSolver (verified by grep: none of those three
tokens appears in the file). Its only dependencies are
`AstrometricMetadata.js` and `AstronomicalCatalogs.js`, both of which
Steps.js already includes.

### The "silent discard" when both engines are included — what it actually is

Including `ImageSolverEngine.js` and `AstrometricSolutionVerifierEngine.js`
in one script produced no output, no error and exit status 0. The obvious
suspect was a preprocessor collision: PJSR's preprocessor has one define
table for the whole unit, Steps.js must `#define VERSION "6.4.2"` / `TITLE`
/ `SETTINGS_MODULE` for ImageSolver, and the verifier's front end defines
its own `VERSION "1.0.0"` / `TITLE` / `SETTINGS_MODULE`.

**That is not the cause.** Bisected on 1.9.5 build 1702, 2026-09-18:

* **Redefining a macro is not an error.** A script that defines `VERSION`,
  `TITLE` and `SETTINGS_MODULE` twice runs to completion; the last
  definition simply wins (`VERSION=1.0.0`, and so on). No warning, no
  discard.
* **Both engines coexist in one preprocessed unit.** With the ImageSolver
  define set in force and `<pjsr/astrometry/AstrometricResiduals.js>` and
  `<pjsr/astrometry/AstrometricPlot.js>` included, `ImageSolver`,
  `AstrometricSolutionVerifier` and `AstrometricResiduals` are all
  `function` afterwards.
* **The real cause is a missing dependency include.**
  `AstrometricSolutionVerifierEngine.js` does not include the two files it
  needs — its front end does. Without
  `<pjsr/astrometry/AstrometricResiduals.js>`, the statement
  `var AstrometricSolutionVerifier = class extends AstrometricResiduals`
  inside the engine fails when the unit is evaluated, and nothing after it
  runs.

Proved by writing a marker file *before* the engine's `#include` and
another after it: the first file appears, the second does not. The script
therefore parsed and started running — it was not discarded — but because a
dispatched script's only output is normally written at the end, the
observable symptom is identical to a discard. **"No output file" means
"something stopped before the write", and that is a parse failure OR a
load-time exception; the two are only distinguishable by writing a marker
early.**

A third thing produces the same symptom and is worth ruling out first: the
shared instance being busy. A script dispatched with `-x=1:` while another
long script is running is queued, not dropped, and its output appears
whenever the instance gets to it — which can be an hour.

```javascript
#include <pjsr/astrometry/AstrometricResiduals.js>

let M = ( new AstrometricResiduals( config ) ).measure( window );
// M.metadata.resolution is DEGREES per pixel; 3600*resolution is the
// plate scale in arcsec/px (AstrometricSolutionVerifierEngine.js:274).
// M.retained is the set after sigma clipping:
//   M.retained.n
//   M.retained.px.{median,sigma,rms,p90,p99,max}
//   M.retained.as.{median,sigma,rms,p90,p99,max}
//   M.retained.bias.{dx,dy,dra,ddec}
```

`measure()` throws when the window is null, has no astrometric solution, or
no star matches a catalog star.

**`M.result.retained`, not `M.retained`.** `measure()` returns the retained
*matches* as `M.retained` (a plain array) and their *statistics* as
`M.result.retained`. The names are one property apart and reading the wrong
one costs a run: an array has no `.px`.

**Cost, measured** on the frame above: **762 ms** for 230 matched stars on a
5710x3182 image, against 959 ms for the solve itself. It scales with the
number of matched stars. That is cheap enough to verify every solve rather
than only the reference, which matters because Loom solves each channel
independently and SPFC then calibrates each channel against that channel's
own solution.

### Settings the pipeline must make

The configuration is a plain object, **not** a `VerifierConfiguration`.
`VerifierConfiguration extends PersistentObject`, so constructing it and
calling `LoadSettings()` / `SaveSettings()` reads and writes the user's own
saved AstrometricSolutionVerifier settings. `AstrometricResiduals` asks only
for the properties below (listed in its own header comment), so a literal
satisfies it completely and touches no `Settings` at all.

Every value is copied verbatim from `VerifierConfiguration`'s defaults,
`AstrometricSolutionVerifierEngine.js:105-126`, which are in turn the
ImageSolver defaults for star detection and PSF fitting.

| Parameter | Value | Source line |
| --- | --- | --- |
| `structureLayers` | `5` | `:105` |
| `minStructureSize` | `0` | `:106` |
| `hotPixelFilterRadius` | `1` | `:107` |
| `noiseReductionFilterRadius` | `0` | `:108` |
| `sensitivity` | `0.5` | `:109` |
| `peakResponse` | `0.5` | `:110` |
| `brightThreshold` | `3.0` | `:111` |
| `maxStarDistortion` | `0.6` | `:112` |
| `autoPSF` | `false` | `:113` |
| `autoMagnitude` | `true` | `:118` |
| `magnitude` | `16` | `:119` |
| `restrictToHQStars` | `false` | `:120` |
| `matchingTolerance` | `3.0` px | `:125` |
| `rejectionSigma` | `5.0` | `:126` |

The `AstrometricSolutionVerifier` **engine** class is deliberately not used.
Its `verify()` is the reporting half: it prints a 98-column report and
per-cell grid tables, and with the defaults above it also opens a
false-colour deviation map (`mapMode = MapMode.FalseColor`, `:133`) and a
graphs window (`showGraphs = true`, `:139`). Loom runs unattended; two
windows per solve over the user's workspace is not acceptable. The
measurement is identical either way, because it is the same code.

### Residual thresholds

Both are compared against the **median** deviation in pixels. Pixels is the
scale-relative form: `arcsec / (arcsec/px)`.

| Threshold | Value | Where the number comes from |
| --- | --- | --- |
| solution is wrong | `>= 3.0` px | The `matchingTolerance` above — the radius inside which a detected star is accepted as a catalog star at all, hence the largest deviation the measurement can represent. |
| solution is poor | `> 0.315` px | The median deviation against Gaia DR3 that PixInsight's pre-1.9.5 global-surface-spline solver achieved on the 34,000-control-point mosaic panel published in the 1.9.5 announcement (the same measurement that gave 0.091 px with recursive splines). |

No threshold is set on the RMS. Both published figures are medians, RMS is
`>= median` by construction, and reusing a median limit on an RMS would fire
on solutions that are fine. The RMS is reported — it is the number that
shows a tail of bad corners a median hides — but it is reported, not judged.

---

## Findings — divergences from Appendix A / the plan's assumptions

1. **GraXpert `correction` is a string enum, not a numeric constant.**
   Confirmed values (from `strings` on `GraXpert-pxm.dylib`):
   `"Subtraction"` and `"Division"` (usage text: `-correction=[substraction|division]`,
   error text: `Invalid argument (substraction or division expected)`).
   The plan's Task 7 skeleton comment claiming `correction` *selects*
   Background Extraction mode is wrong on two counts: (a) it's a string,
   not an enum constant; (b) it selects the *correction method* used
   during background extraction (subtractive vs. divisive), not whether
   background extraction happens at all.

2. **Background Extraction is enabled by the boolean `backgroundExtraction`**,
   confirmed `true`/`false`. This parameter is entirely absent from
   Appendix A's GraXpert parameter list.

3. **GraXpert exposes several parameters absent from Appendix A**:
   `appPath` (string, path to the external GraXpert executable — empty by
   default, meaning it resolves at runtime or requires configuration),
   `denoising` (boolean, distinct from `strength`), `deconvolution`
   (boolean), `deconvolutionMode` (string, default `"Object-only"`),
   `deconvolutionObjectStrength`, `deconvolutionObjectPSFSize`,
   `deconvolutionObjectAIModel`, `deconvolutionStarsAIModel`,
   `backgroundExtractionAIModel`, `denoiseAIModel`, `disableGPU`,
   `showLogs`. Appendix A's `backgroundExtractionDefaultModel` and
   `backgroundExtractionModels` do **not** appear in the live dump —
   those names do not exist on this instance; only
   `backgroundExtractionAIModel` (singular, current selection) exists.
   Appendix A's `appRecommendedVersion` and `appRecommendedURL` also do
   not appear; only `appPath` exists.

4. **SPFC does not have `deviceQECurveName` as a free-standing display-only
   field** — it's a real settable string parameter, and it must be set
   *together with* `deviceQECurve` (the actual CSV wavelength/value pairs).
   Confirmed by the live default: `deviceQECurve = ""` in SPFC but
   `deviceQECurve = "1,1,500,1,1000,1,1500,1,2000,1,2500,1"` /
   `deviceQECurveName = "Ideal QE curve"` in SPCC — the two travel
   together as a matched pair. Setting only the name string with no
   matching curve data would silently leave PixInsight using whatever
   `deviceQECurve` was previously set to (or empty), not the ASI2600MM
   curve. **This is a real risk for Task 7: the GUI auto-fills both
   fields when you pick a library curve by name; a script must fill both
   explicitly.**

5. **SPFC's parameter list matches Appendix A closely** but Appendix A
   omits several real parameters that exist: `broadbandIntegrationStepSize`,
   `rejectionLimit`, `minMagnitude`, `limitMagnitude`, `autoLimitMagnitude`,
   `psfStructureLayers`, `saturationThreshold`, `saturationRelative`,
   `saturationShrinkFactor`, `psfNoiseLayers`, `psfMinStructureSize`,
   `psfMinSNR`, `psfType` (enum, default `PSFType_Auto`), `psfGrowth`,
   `psfMaxStars`, `psfSearchTolerance`, `psfChannelSearchTolerance`,
   `generateGraphs`, `generateStarMaps`, `generateTextFiles`,
   `outputDirectory`. None of these are pipeline-blocking but Task 7
   should not assume Appendix A's list is exhaustive for any process.

6. **SPCC's parameter list also has real fields missing from Appendix A**:
   `applyCalibration` (boolean — must be `true` for SPCC to actually
   modify the image, not just report), `targetSourceCount`,
   `psfStructureLayers`, `saturationThreshold`/`saturationRelative`/
   `saturationShrinkFactor`, `psfNoiseLayers`, `psfMinStructureSize`,
   `psfMinSNR`, `psfType`, `psfGrowth`, `psfMaxStars`,
   `psfSearchTolerance`, `psfChannelSearchTolerance`,
   `neutralizeBackground`, `backgroundReferenceViewId`, `backgroundLow`,
   `backgroundHigh`, `backgroundUseROI` + ROI bounds, `generateGraphs`,
   `generateStarMaps`, `generateTextFiles`, `outputDirectory`.
   Appendix A's `whiteReferenceId`, `whiteReferenceViewId`, `whiteHigh`,
   `whiteUseROI`, `whiteROIX0/Y0/X1/Y1`, `manualWhiteBalance`,
   `manualRedFactor/GreenFactor/BlueFactor`, `outputWhiteReferenceMask`
   do **not** appear in the live default dump — either they don't exist
   under those names, or `toSource()` does not emit parameters left at a
   default that equals "unset" in a way it can't serialize. Given SPFC's
   dump also omitted parameters it's known to have (none observed missing
   there), the safer read is that Appendix A invented some SPCC parameter
   names that don't exist. **Do not use `manualRedFactor` /
   `manualGreenFactor` / `manualBlueFactor` / `whiteUseROI` / `whiteROIX0`
   etc. in Task 7 without re-verifying** — they were not observed on the
   live instance.

7. **MGC's real parameter list is close to Appendix A but not identical.**
   Confirmed real: `useMARSDatabase`, `marsDatabaseFiles` (array of
   `[enabled, path]` pairs, not a flat file list), `grayMARSFilter`,
   `redMARSFilter`, `greenMARSFilter`, `blueMARSFilter`,
   `referenceImageId`, `gradientScale`, `structureSeparation`,
   `modelSmoothness`, `minFieldRatio`, `maxFieldRatio`,
   `enforceFieldLimits`, `scaleFactorRK`, `scaleFactorG`, `scaleFactorB`,
   `showGradientModel`, `command` (string — undocumented purpose, empty by
   default). **Not present in the live dump despite being in Appendix A**:
   `centerRA`, `centerDec`, `searchRadius`, `fluxScaleFactor`,
   `generateGradientModel`, `downsampledWidth`, `downsampledHeight`. Do
   not assume these exist; MGC's astrometric center/radius appear to come
   from the image's own WCS solution (hence the "Five plate solves"
   requirement in the design — MGC needs the image already plate-solved,
   it does not take RA/Dec as direct parameters here).

8. **StarAlignment**: `targets` is `[enabled, isFile, image]` triplets and
   `referenceImage` + `referenceIsFile` is how a *view* (not just a file)
   is supplied as either target or reference — confirmed real and exactly
   what Task 7 needs for "register a view to a reference view." This
   matches Appendix A's module attribution but Appendix A did not list
   any of StarAlignment's ~60 real parameters (it only names the module).
   Nothing here contradicts Appendix A since Appendix A made no per-field
   claims for StarAlignment.

9. **LinearFit has only 3 parameters**: `referenceViewId`, `rejectLow`,
   `rejectHigh`. Appendix A only asserted the module attribution
   (`ColorCalibration-pxm` and `ImageIntegration-pxm`), not parameter
   names, so there's no contradiction — but Task 7 should not look for
   anything beyond these three.

10. **ChannelCombination**: `channels` is `[enabled, id]` pairs (view id
    strings) for R, G, B in that order, plus `colorSpace` (enum,
    `ChannelCombination.RGB`) and `inheritAstrometricSolution` (boolean,
    default `true`). Simple and matches what Task 7 needs; Appendix A made
    no specific claims here either.

11. **ImageSolver is not a process at all** — see the ImageSolver section
    above. This is the single biggest structural gap: Appendix A lists it
    alongside seven true processes as if it had the same
    `new X; P.param = v; P.executeOn(view)` shape. It does not. Task 7's
    invocation code for ImageSolver must be written completely differently
    from the other six wrappers.

12. **MGC's MARS database is not present on this machine.** See the
    dedicated section below.

13. **GraXpert's `replaceImage` defaults to `false`**, meaning by default
    the corrected image is written to a *new* window and the original
    working view is left untouched. This pipeline corrects each broadband
    channel in place on its working window and then registers and saves
    that same window downstream (per the design's ordering), so leaving
    `replaceImage` at its default would silently detach the correction
    from the rest of the pipeline — the working view would still carry
    the uncorrected background. Task 7 must set `P.replaceImage = true`
    explicitly; see the Settings section below.

## Settings the pipeline must make

### SPFC — mono channel (per filter, from FITS `FILTER` keyword)

```javascript
var P = new SpectrophotometricFluxCalibration;
P.narrowbandMode = false;               // set true only for narrowband filters (see note)
P.catalogId = "GaiaDR3SP";
P.autoLimitMagnitude = true;
P.deviceQECurveName = "Sony IMX411/455/461/533/571";
P.deviceQECurve = "402,0.7219,404,0.7367,406,0.75,408,0.7618,410,0.7751,412,0.787,414,0.7944,416,0.8018,418,0.8112,420,0.8214,422,0.8343,424,0.8462,426,0.8536,428,0.8595,430,0.8639,432,0.8713,434,0.8757,436,0.8802,438,0.8861,440,0.8905,442,0.895,444,0.8994,446,0.9038,448,0.9068,450,0.9112,452,0.9142,454,0.9172,456,0.9168,458,0.9151,460,0.9134,462,0.9117,464,0.91,466,0.9083,468,0.9066,470,0.9049,472,0.9032,474,0.9015,476,0.8997,478,0.898,480,0.8963,482,0.8946,484,0.8929,486,0.8912,488,0.8876,490,0.8846,492,0.8877,494,0.8904,496,0.893,498,0.8964,500,0.8964,502,0.895,504,0.8945,506,0.8922,508,0.8899,510,0.8876,512,0.8853,514,0.883,516,0.8807,518,0.8784,520,0.8761,522,0.8743,524,0.8728,526,0.8698,528,0.8669,530,0.8624,532,0.858,534,0.855,536,0.8506,538,0.8476,540,0.8432,542,0.8402,544,0.8358,546,0.8328,548,0.8284,550,0.8254,552,0.821,554,0.8166,556,0.8136,558,0.8092,560,0.8062,562,0.8023,564,0.7983,566,0.7944,568,0.7899,570,0.787,572,0.7825,574,0.7781,576,0.7751,578,0.7707,580,0.7663,582,0.7618,584,0.7559,586,0.75,588,0.7441,590,0.7396,592,0.7337,594,0.7278,596,0.7219,598,0.716,600,0.7101,602,0.7056,604,0.6997,606,0.695,608,0.6905,610,0.6852,612,0.6808,614,0.6763,616,0.6719,618,0.6675,620,0.663,622,0.6583,624,0.6553,626,0.6509,628,0.6464,630,0.642,632,0.6376,634,0.6317,636,0.6272,638,0.6213,640,0.6154,642,0.6109,644,0.6036,646,0.5962,648,0.5902,650,0.5843,652,0.5799,654,0.574,656,0.5695,658,0.5636,660,0.5592,662,0.5545,664,0.5504,666,0.5462,668,0.542,670,0.5378,672,0.5328,674,0.5286,676,0.5244,678,0.5203,680,0.5163,682,0.5133,684,0.5089,686,0.5044,688,0.4985,690,0.4926,692,0.4867,694,0.4793,696,0.4719,698,0.4645,700,0.4586,702,0.4541,704,0.4497,706,0.4453,708,0.4408,710,0.4364,712,0.432,714,0.4275,716,0.4216,718,0.4186,720,0.4142,722,0.4127,724,0.4103,726,0.4078,728,0.4053,730,0.4024,732,0.3979,734,0.3935,736,0.3891,738,0.3831,740,0.3802,742,0.3772,744,0.3743,746,0.3713,748,0.3669,750,0.3624,752,0.3595,754,0.3559,756,0.3526,758,0.3494,760,0.3462,762,0.3429,764,0.3397,766,0.3364,768,0.3332,770,0.33,772,0.3267,774,0.3235,776,0.3203,778,0.317,780,0.3138,782,0.3106,784,0.3073,786,0.3041,788,0.3009,790,0.2976,792,0.2937,794,0.2905,796,0.2873,798,0.284,800,0.2808,802,0.2776,804,0.2743,806,0.2731,808,0.2703,810,0.2674,812,0.2646,814,0.2618,816,0.2589,818,0.2561,820,0.2533,822,0.2504,824,0.2476,826,0.2456,828,0.2439,830,0.2433,832,0.2427,834,0.2421,836,0.2416,838,0.2411,840,0.2382,842,0.2322,844,0.2278,846,0.2219,848,0.2175,850,0.2114,852,0.2069,854,0.2023,856,0.1978,858,0.1932,860,0.1918,862,0.1911,864,0.1904,866,0.1897,868,0.189,870,0.1883,872,0.1879,874,0.1834,876,0.179,878,0.1731,880,0.1672,882,0.1612,884,0.1568,886,0.1524,888,0.1479,890,0.1464,892,0.1464,894,0.1464,896,0.1464,898,0.1481,900,0.1494,902,0.1494,904,0.1494,906,0.1464,908,0.1435,910,0.1391,912,0.1346,914,0.1302,916,0.1257,918,0.1228,920,0.1183,922,0.1139,924,0.1109,926,0.1093,928,0.1085,930,0.108,932,0.108,934,0.108,936,0.108,938,0.108,940,0.1058,942,0.1039,944,0.1021,946,0.0998,948,0.0958,950,0.0918,952,0.0888,954,0.0828,956,0.0769,958,0.074,960,0.0714,962,0.0695,964,0.0677,966,0.0658,968,0.0651,970,0.0636,972,0.0626,974,0.0616,976,0.0606,978,0.0596,980,0.0586,982,0.0576,984,0.0567,986,0.0557,988,0.0547,990,0.0537,992,0.0527,994,0.0517,996,0.0507";
// Mono target: set the gray filter fields, leave red/green/blue*
// filter fields at their empty defaults.
P.grayFilterName = "<filter name from FITS FILTER keyword, e.g. 'Astrodon Ha 3nm'>";
P.grayFilterTrCurve = "<transmission curve data for that filter from filters.xspd, if a library match exists>";
P.grayFilterWavelength = <central wavelength for that filter, nm>;
P.grayFilterBandwidth = <bandwidth for that filter, nm>;
P.executeOn( monoView );
```
Narrowband note: `narrowbandMode` must be `true` for Ha/OIII/SII filters
(this pipeline is "LHSO", so H/S/O channels are narrowband, L is
broadband). Broadband (L) targets use `narrowbandMode = false`.

**Filter-name-to-curve lookup is a prerequisite Task 7 must implement,
and it is not yet resolved by this document.** `grayFilterName` /
`grayFilterTrCurve` / `grayFilterWavelength` / `grayFilterBandwidth` are
left as placeholders above because the real filter is resolved at
runtime from the FITS `FILTER` keyword of the frame being processed, not
a fixed constant this document can supply. The mapping source is the
same file used for the device QE curve:
`/Applications/PixInsight/library/filters.xspd` is a general "PixInsight
Spectrum Database" (confirmed by its own `<!-- Filters Database -->`
header and root `<xspd>` element) that holds **both** device QE curves
(`channel="Q"` entries, e.g. the Sony IMX411/455/461/533/571 entry above)
**and** actual filter transmission curves (entries like `Astrodon E-series
R`, `Baader B`, `Antlia ALP-T`, etc., `channel="R"/"G"/"B"/"L"/"PAN"` and
similar), all as sibling `<Filter name="..." channel="..." data="...">`
elements in one flat list (256 `<Filter>` entries total as of this
verification). Task 7 must implement a lookup that takes the FITS
`FILTER` keyword string and finds a matching `<Filter name="...">` entry
in this file (exact or fuzzy string match against the equipment's actual
filter brand/model — this was not tested here), then populates
`grayFilterTrCurve` with that entry's `data` attribute verbatim, the way
`deviceQECurve` was populated above. **Not every narrowband filter brand
will have a library match** (a spot check of this dump found no
generic/bare "Ha", "OIII", or "SII" entries, only brand-qualified ones,
e.g. `Antlia ALP-T 5nm SII Hb`) — if no match exists, the only remaining option exposed by the parameter
set is to leave `grayFilterTrCurve = ""` and rely on
`grayFilterWavelength` / `grayFilterBandwidth` alone (presumably a
narrowband/Gaussian bandpass approximation, inferred from the parameter
shape — this fallback behavior itself was not exercised or confirmed
against a live SPFC run in this task). Task 7's design must decide and
state which path applies per filter, and if it relies on the
wavelength/bandwidth-only fallback, that should be verified against a
real SPFC run before being trusted for narrowband filters without a
library match.

### SPFC — RGB image (after ChannelCombination, if run again on the color image)

```javascript
var P = new SpectrophotometricFluxCalibration;
P.narrowbandMode = false;
P.catalogId = "GaiaDR3SP";
P.autoLimitMagnitude = true;
P.deviceQECurveName = "Sony IMX411/455/461/533/571";
P.deviceQECurve = "402,0.7219,404,0.7367,406,0.75,408,0.7618,410,0.7751,412,0.787,414,0.7944,416,0.8018,418,0.8112,420,0.8214,422,0.8343,424,0.8462,426,0.8536,428,0.8595,430,0.8639,432,0.8713,434,0.8757,436,0.8802,438,0.8861,440,0.8905,442,0.895,444,0.8994,446,0.9038,448,0.9068,450,0.9112,452,0.9142,454,0.9172,456,0.9168,458,0.9151,460,0.9134,462,0.9117,464,0.91,466,0.9083,468,0.9066,470,0.9049,472,0.9032,474,0.9015,476,0.8997,478,0.898,480,0.8963,482,0.8946,484,0.8929,486,0.8912,488,0.8876,490,0.8846,492,0.8877,494,0.8904,496,0.893,498,0.8964,500,0.8964,502,0.895,504,0.8945,506,0.8922,508,0.8899,510,0.8876,512,0.8853,514,0.883,516,0.8807,518,0.8784,520,0.8761,522,0.8743,524,0.8728,526,0.8698,528,0.8669,530,0.8624,532,0.858,534,0.855,536,0.8506,538,0.8476,540,0.8432,542,0.8402,544,0.8358,546,0.8328,548,0.8284,550,0.8254,552,0.821,554,0.8166,556,0.8136,558,0.8092,560,0.8062,562,0.8023,564,0.7983,566,0.7944,568,0.7899,570,0.787,572,0.7825,574,0.7781,576,0.7751,578,0.7707,580,0.7663,582,0.7618,584,0.7559,586,0.75,588,0.7441,590,0.7396,592,0.7337,594,0.7278,596,0.7219,598,0.716,600,0.7101,602,0.7056,604,0.6997,606,0.695,608,0.6905,610,0.6852,612,0.6808,614,0.6763,616,0.6719,618,0.6675,620,0.663,622,0.6583,624,0.6553,626,0.6509,628,0.6464,630,0.642,632,0.6376,634,0.6317,636,0.6272,638,0.6213,640,0.6154,642,0.6109,644,0.6036,646,0.5962,648,0.5902,650,0.5843,652,0.5799,654,0.574,656,0.5695,658,0.5636,660,0.5592,662,0.5545,664,0.5504,666,0.5462,668,0.542,670,0.5378,672,0.5328,674,0.5286,676,0.5244,678,0.5203,680,0.5163,682,0.5133,684,0.5089,686,0.5044,688,0.4985,690,0.4926,692,0.4867,694,0.4793,696,0.4719,698,0.4645,700,0.4586,702,0.4541,704,0.4497,706,0.4453,708,0.4408,710,0.4364,712,0.432,714,0.4275,716,0.4216,718,0.4186,720,0.4142,722,0.4127,724,0.4103,726,0.4078,728,0.4053,730,0.4024,732,0.3979,734,0.3935,736,0.3891,738,0.3831,740,0.3802,742,0.3772,744,0.3743,746,0.3713,748,0.3669,750,0.3624,752,0.3595,754,0.3559,756,0.3526,758,0.3494,760,0.3462,762,0.3429,764,0.3397,766,0.3364,768,0.3332,770,0.33,772,0.3267,774,0.3235,776,0.3203,778,0.317,780,0.3138,782,0.3106,784,0.3073,786,0.3041,788,0.3009,790,0.2976,792,0.2937,794,0.2905,796,0.2873,798,0.284,800,0.2808,802,0.2776,804,0.2743,806,0.2731,808,0.2703,810,0.2674,812,0.2646,814,0.2618,816,0.2589,818,0.2561,820,0.2533,822,0.2504,824,0.2476,826,0.2456,828,0.2439,830,0.2433,832,0.2427,834,0.2421,836,0.2416,838,0.2411,840,0.2382,842,0.2322,844,0.2278,846,0.2219,848,0.2175,850,0.2114,852,0.2069,854,0.2023,856,0.1978,858,0.1932,860,0.1918,862,0.1911,864,0.1904,866,0.1897,868,0.189,870,0.1883,872,0.1879,874,0.1834,876,0.179,878,0.1731,880,0.1672,882,0.1612,884,0.1568,886,0.1524,888,0.1479,890,0.1464,892,0.1464,894,0.1464,896,0.1464,898,0.1481,900,0.1494,902,0.1494,904,0.1494,906,0.1464,908,0.1435,910,0.1391,912,0.1346,914,0.1302,916,0.1257,918,0.1228,920,0.1183,922,0.1139,924,0.1109,926,0.1093,928,0.1085,930,0.108,932,0.108,934,0.108,936,0.108,938,0.108,940,0.1058,942,0.1039,944,0.1021,946,0.0998,948,0.0958,950,0.0918,952,0.0888,954,0.0828,956,0.0769,958,0.074,960,0.0714,962,0.0695,964,0.0677,966,0.0658,968,0.0651,970,0.0636,972,0.0626,974,0.0616,976,0.0606,978,0.0596,980,0.0586,982,0.0576,984,0.0567,986,0.0557,988,0.0547,990,0.0537,992,0.0527,994,0.0517,996,0.0507"; // identical to the SPFC mono curve above — same physical sensor
P.redFilterName   = "<R filter name>";  P.redFilterTrCurve   = "<...>"; P.redFilterWavelength = <...>; P.redFilterBandwidth = <...>;
P.greenFilterName = "<G filter name>";  P.greenFilterTrCurve = "<...>"; P.greenFilterWavelength = <...>; P.greenFilterBandwidth = <...>;
P.blueFilterName  = "<B filter name>";  P.blueFilterTrCurve  = "<...>"; P.blueFilterWavelength = <...>; P.blueFilterBandwidth = <...>;
P.executeOn( rgbView );
```
Leave `grayFilterName` etc. empty for the RGB case — the presence of both
sets is what lets SPFC accept either shape, per Appendix A's own note,
confirmed by the live dump (both sets of fields exist simultaneously on
the same process object). `redFilterTrCurve`/`greenFilterTrCurve`/
`blueFilterTrCurve` are placeholders here for the same reason
`grayFilterTrCurve` was above — they are resolved per-filter at runtime,
by the same `filters.xspd` lookup described in the mono section
immediately above.

### SPCC — combined RGB

```javascript
var P = new SpectrophotometricColorCalibration;
P.applyCalibration = true;              // REQUIRED — without this SPCC only analyzes, does not correct
P.narrowbandMode = false;               // this runs on the broadband-calibrated RGB composite
P.catalogId = "GaiaDR3SP";
P.autoLimitMagnitude = true;
P.neutralizeBackground = true;
// deviceQECurve / deviceQECurveName and the redFilter*/greenFilter*/blueFilter*
// fields may be left at their library defaults (Sony color-sensor curves)
// UNLESS Task 7 needs SPCC itself to be device-aware for a OSC-simulated
// RGB from a mono camera; if so, set the same pair as SPFC above:
P.deviceQECurveName = "Sony IMX411/455/461/533/571";
P.deviceQECurve = "402,0.7219,404,0.7367,406,0.75,408,0.7618,410,0.7751,412,0.787,414,0.7944,416,0.8018,418,0.8112,420,0.8214,422,0.8343,424,0.8462,426,0.8536,428,0.8595,430,0.8639,432,0.8713,434,0.8757,436,0.8802,438,0.8861,440,0.8905,442,0.895,444,0.8994,446,0.9038,448,0.9068,450,0.9112,452,0.9142,454,0.9172,456,0.9168,458,0.9151,460,0.9134,462,0.9117,464,0.91,466,0.9083,468,0.9066,470,0.9049,472,0.9032,474,0.9015,476,0.8997,478,0.898,480,0.8963,482,0.8946,484,0.8929,486,0.8912,488,0.8876,490,0.8846,492,0.8877,494,0.8904,496,0.893,498,0.8964,500,0.8964,502,0.895,504,0.8945,506,0.8922,508,0.8899,510,0.8876,512,0.8853,514,0.883,516,0.8807,518,0.8784,520,0.8761,522,0.8743,524,0.8728,526,0.8698,528,0.8669,530,0.8624,532,0.858,534,0.855,536,0.8506,538,0.8476,540,0.8432,542,0.8402,544,0.8358,546,0.8328,548,0.8284,550,0.8254,552,0.821,554,0.8166,556,0.8136,558,0.8092,560,0.8062,562,0.8023,564,0.7983,566,0.7944,568,0.7899,570,0.787,572,0.7825,574,0.7781,576,0.7751,578,0.7707,580,0.7663,582,0.7618,584,0.7559,586,0.75,588,0.7441,590,0.7396,592,0.7337,594,0.7278,596,0.7219,598,0.716,600,0.7101,602,0.7056,604,0.6997,606,0.695,608,0.6905,610,0.6852,612,0.6808,614,0.6763,616,0.6719,618,0.6675,620,0.663,622,0.6583,624,0.6553,626,0.6509,628,0.6464,630,0.642,632,0.6376,634,0.6317,636,0.6272,638,0.6213,640,0.6154,642,0.6109,644,0.6036,646,0.5962,648,0.5902,650,0.5843,652,0.5799,654,0.574,656,0.5695,658,0.5636,660,0.5592,662,0.5545,664,0.5504,666,0.5462,668,0.542,670,0.5378,672,0.5328,674,0.5286,676,0.5244,678,0.5203,680,0.5163,682,0.5133,684,0.5089,686,0.5044,688,0.4985,690,0.4926,692,0.4867,694,0.4793,696,0.4719,698,0.4645,700,0.4586,702,0.4541,704,0.4497,706,0.4453,708,0.4408,710,0.4364,712,0.432,714,0.4275,716,0.4216,718,0.4186,720,0.4142,722,0.4127,724,0.4103,726,0.4078,728,0.4053,730,0.4024,732,0.3979,734,0.3935,736,0.3891,738,0.3831,740,0.3802,742,0.3772,744,0.3743,746,0.3713,748,0.3669,750,0.3624,752,0.3595,754,0.3559,756,0.3526,758,0.3494,760,0.3462,762,0.3429,764,0.3397,766,0.3364,768,0.3332,770,0.33,772,0.3267,774,0.3235,776,0.3203,778,0.317,780,0.3138,782,0.3106,784,0.3073,786,0.3041,788,0.3009,790,0.2976,792,0.2937,794,0.2905,796,0.2873,798,0.284,800,0.2808,802,0.2776,804,0.2743,806,0.2731,808,0.2703,810,0.2674,812,0.2646,814,0.2618,816,0.2589,818,0.2561,820,0.2533,822,0.2504,824,0.2476,826,0.2456,828,0.2439,830,0.2433,832,0.2427,834,0.2421,836,0.2416,838,0.2411,840,0.2382,842,0.2322,844,0.2278,846,0.2219,848,0.2175,850,0.2114,852,0.2069,854,0.2023,856,0.1978,858,0.1932,860,0.1918,862,0.1911,864,0.1904,866,0.1897,868,0.189,870,0.1883,872,0.1879,874,0.1834,876,0.179,878,0.1731,880,0.1672,882,0.1612,884,0.1568,886,0.1524,888,0.1479,890,0.1464,892,0.1464,894,0.1464,896,0.1464,898,0.1481,900,0.1494,902,0.1494,904,0.1494,906,0.1464,908,0.1435,910,0.1391,912,0.1346,914,0.1302,916,0.1257,918,0.1228,920,0.1183,922,0.1139,924,0.1109,926,0.1093,928,0.1085,930,0.108,932,0.108,934,0.108,936,0.108,938,0.108,940,0.1058,942,0.1039,944,0.1021,946,0.0998,948,0.0958,950,0.0918,952,0.0888,954,0.0828,956,0.0769,958,0.074,960,0.0714,962,0.0695,964,0.0677,966,0.0658,968,0.0651,970,0.0636,972,0.0626,974,0.0616,976,0.0606,978,0.0596,980,0.0586,982,0.0576,984,0.0567,986,0.0557,988,0.0547,990,0.0537,992,0.0527,994,0.0517,996,0.0507"; // identical to the SPFC curve above — same physical sensor
P.executeOn( rgbView );
```

### MGC — with MARS reference database

**MARS is not available locally (see below). Task 7 cannot set
`P.useMARSDatabase = true` and run unattended until the database is
downloaded.** The settings to write, once the database exists:

```javascript
var P = new MultiscaleGradientCorrection;
P.useMARSDatabase = true;
P.marsDatabaseFiles = [
   [ true, "<absolute path to downloaded MARS database file(s)>" ]
];
P.grayMARSFilter  = "L";   // or the appropriate FITS FILTER value for a mono run
P.redMARSFilter   = "R";
P.greenMARSFilter = "G";
P.blueMARSFilter  = "B";
P.referenceImageId = "";   // leave empty to use the target view itself as MGC's working image
P.enforceFieldLimits = true;
P.executeOn( view );
```
`command` is an unexplained string parameter (empty by default in the
live dump); do not set it without further investigation — it is not
documented in any user-facing reference found so far and its purpose is
unconfirmed.

### GraXpert — background-extraction mode, configurable smoothing

```javascript
var P = new GraXpert;
P.backgroundExtraction = true;   // boolean — this is what turns extraction ON
P.correction = "Subtraction";    // string enum: "Subtraction" or "Division" — NOT what enables extraction
P.smoothing = <configurable 0.0-1.0 value, pipeline parameter>;
P.createBackground = false;      // true would output the background model instead of the corrected image
P.replaceImage = true;           // REQUIRED — corrects the working window in place
P.denoising = false;             // out of scope per design
P.deconvolution = false;         // out of scope per design
P.disableGPU = false;
P.showLogs = false;
P.executeOn( view );
```
`P.replaceImage` must be `true`. Its default is `false` (see Finding 13):
this pipeline corrects each broadband channel in place on its working
window and then registers and saves that same window downstream, so the
corrected pixels must land back in the target view rather than spawn a
separate output window.

### StarAlignment — register a view to a reference view

```javascript
var P = new StarAlignment;
P.referenceImage = "<reference view id string>";
P.referenceIsFile = false;              // THIS is the parameter that makes referenceImage a view id, not a path
P.targets = [ [ true, false, "<target view id string>" ] ];  // [enabled, isFile, image] — isFile=false means 'image' is a view id
P.mode = StarAlignment.RegisterMatch;
P.writeKeywords = true;
P.generateDrizzleData = false;          // set true only if drizzle is part of this pipeline
P.executeOn( /* no-op for view registration; when isFile=false + no output-file params matter, executeGlobal() is used instead */ );
```
StarAlignment with in-memory views and `isFile = false` is normally
invoked with `P.executeGlobal()`, not `P.executeOn(view)` — the process
reads `targets`/`referenceImage` itself rather than acting "on" a bound
view. Task 7 must call `P.executeGlobal()`.

### LinearFit — to a reference view

```javascript
var P = new LinearFit;
P.referenceViewId = "<reference view id string>";
P.rejectLow = 0.000000;   // default; adjust if the pipeline needs stricter rejection
P.rejectHigh = 0.920000;  // default
P.executeOn( targetView );
```

### ChannelCombination — RGB from three mono views

```javascript
var P = new ChannelCombination;
P.colorSpace = ChannelCombination.RGB;
P.channels = [
   [ true, "<red mono view id>" ],
   [ true, "<green mono view id>" ],
   [ true, "<blue mono view id>" ]
];
P.inheritAstrometricSolution = true;   // keep true so the combined RGB carries WCS from an already-plate-solved input, avoiding a redundant solve
P.executeGlobal();                     // ChannelCombination builds a new image; it is not "executed on" an existing view
```

### Camera QE curve — confirmed present

`grep`-verified in `/Applications/PixInsight/library/filters.xspd`, full
untruncated entry:
```
<Filter name="Sony IMX411/455/461/533/571" channel="Q" data="402,0.7219,404,0.7367,406,0.75,408,0.7618,410,0.7751,412,0.787,414,0.7944,416,0.8018,418,0.8112,420,0.8214,422,0.8343,424,0.8462,426,0.8536,428,0.8595,430,0.8639,432,0.8713,434,0.8757,436,0.8802,438,0.8861,440,0.8905,442,0.895,444,0.8994,446,0.9038,448,0.9068,450,0.9112,452,0.9142,454,0.9172,456,0.9168,458,0.9151,460,0.9134,462,0.9117,464,0.91,466,0.9083,468,0.9066,470,0.9049,472,0.9032,474,0.9015,476,0.8997,478,0.898,480,0.8963,482,0.8946,484,0.8929,486,0.8912,488,0.8876,490,0.8846,492,0.8877,494,0.8904,496,0.893,498,0.8964,500,0.8964,502,0.895,504,0.8945,506,0.8922,508,0.8899,510,0.8876,512,0.8853,514,0.883,516,0.8807,518,0.8784,520,0.8761,522,0.8743,524,0.8728,526,0.8698,528,0.8669,530,0.8624,532,0.858,534,0.855,536,0.8506,538,0.8476,540,0.8432,542,0.8402,544,0.8358,546,0.8328,548,0.8284,550,0.8254,552,0.821,554,0.8166,556,0.8136,558,0.8092,560,0.8062,562,0.8023,564,0.7983,566,0.7944,568,0.7899,570,0.787,572,0.7825,574,0.7781,576,0.7751,578,0.7707,580,0.7663,582,0.7618,584,0.7559,586,0.75,588,0.7441,590,0.7396,592,0.7337,594,0.7278,596,0.7219,598,0.716,600,0.7101,602,0.7056,604,0.6997,606,0.695,608,0.6905,610,0.6852,612,0.6808,614,0.6763,616,0.6719,618,0.6675,620,0.663,622,0.6583,624,0.6553,626,0.6509,628,0.6464,630,0.642,632,0.6376,634,0.6317,636,0.6272,638,0.6213,640,0.6154,642,0.6109,644,0.6036,646,0.5962,648,0.5902,650,0.5843,652,0.5799,654,0.574,656,0.5695,658,0.5636,660,0.5592,662,0.5545,664,0.5504,666,0.5462,668,0.542,670,0.5378,672,0.5328,674,0.5286,676,0.5244,678,0.5203,680,0.5163,682,0.5133,684,0.5089,686,0.5044,688,0.4985,690,0.4926,692,0.4867,694,0.4793,696,0.4719,698,0.4645,700,0.4586,702,0.4541,704,0.4497,706,0.4453,708,0.4408,710,0.4364,712,0.432,714,0.4275,716,0.4216,718,0.4186,720,0.4142,722,0.4127,724,0.4103,726,0.4078,728,0.4053,730,0.4024,732,0.3979,734,0.3935,736,0.3891,738,0.3831,740,0.3802,742,0.3772,744,0.3743,746,0.3713,748,0.3669,750,0.3624,752,0.3595,754,0.3559,756,0.3526,758,0.3494,760,0.3462,762,0.3429,764,0.3397,766,0.3364,768,0.3332,770,0.33,772,0.3267,774,0.3235,776,0.3203,778,0.317,780,0.3138,782,0.3106,784,0.3073,786,0.3041,788,0.3009,790,0.2976,792,0.2937,794,0.2905,796,0.2873,798,0.284,800,0.2808,802,0.2776,804,0.2743,806,0.2731,808,0.2703,810,0.2674,812,0.2646,814,0.2618,816,0.2589,818,0.2561,820,0.2533,822,0.2504,824,0.2476,826,0.2456,828,0.2439,830,0.2433,832,0.2427,834,0.2421,836,0.2416,838,0.2411,840,0.2382,842,0.2322,844,0.2278,846,0.2219,848,0.2175,850,0.2114,852,0.2069,854,0.2023,856,0.1978,858,0.1932,860,0.1918,862,0.1911,864,0.1904,866,0.1897,868,0.189,870,0.1883,872,0.1879,874,0.1834,876,0.179,878,0.1731,880,0.1672,882,0.1612,884,0.1568,886,0.1524,888,0.1479,890,0.1464,892,0.1464,894,0.1464,896,0.1464,898,0.1481,900,0.1494,902,0.1494,904,0.1494,906,0.1464,908,0.1435,910,0.1391,912,0.1346,914,0.1302,916,0.1257,918,0.1228,920,0.1183,922,0.1139,924,0.1109,926,0.1093,928,0.1085,930,0.108,932,0.108,934,0.108,936,0.108,938,0.108,940,0.1058,942,0.1039,944,0.1021,946,0.0998,948,0.0958,950,0.0918,952,0.0888,954,0.0828,956,0.0769,958,0.074,960,0.0714,962,0.0695,964,0.0677,966,0.0658,968,0.0651,970,0.0636,972,0.0626,974,0.0616,976,0.0606,978,0.0596,980,0.0586,982,0.0576,984,0.0567,986,0.0557,988,0.0547,990,0.0537,992,0.0527,994,0.0517,996,0.0507"/>
```
This is the correct (and only) ASI2600MM-applicable entry — `channel="Q"`
means it's a monochrome quantum-efficiency curve (no separate R/G/B
splits), consistent with the ASI2600MM being a mono camera. Both
`deviceQECurveName` ("Sony IMX411/455/461/533/571") and the matching
`deviceQECurve` data string (the `data="..."` attribute above, copied
verbatim) must be set together on SPFC/SPCC — see Finding 4.

## MGC MARS database — presence check

**Not present.** Evidence:

1. The live `MultiscaleGradientCorrection.toSource()` dump shows
   `P.useMARSDatabase = false;` and `P.marsDatabaseFiles = [ ];` (empty
   array) as the process's current/default state — i.e., no database is
   currently registered with the process.
2. A filesystem search (`find / -iname "*mars*"` restricted to
   PixInsight-relevant paths, and a full search for `*mars*database*`)
   found **no MARS-named files anywhere** on this machine — not under
   `/Applications/PixInsight`, not under
   `~/Library/Application Support`, nowhere.
3. No PixInsight-specific application-support directory containing
   downloaded reference data was found at all (only a crash-reporter
   plist under `~/Library/Application Support/CrashReporter`).

**Consequence for Task 7**: MGC cannot run with `useMARSDatabase = true`
until the MARS all-sky reference database is downloaded through
PixInsight's own database-download mechanism (this was not attempted here
— it requires a large download and is out of scope for a parameter-only
verification task). Task 7 must either (a) treat MGC as **not runnable**
in this environment and gate it behind a preflight check that inspects
`P.marsDatabaseFiles` / checks for the database file on disk before
attempting `executeOn`, aborting with a clear message if absent, or (b)
defer MGC entirely until the database is downloaded out-of-band. Silently
running MGC with `useMARSDatabase = false` would produce a different
(non-MARS-referenced) gradient correction than the design intends — that
would be exactly the "runs cleanly, silently wrong" failure mode this task
exists to prevent.
