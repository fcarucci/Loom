# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **Loom takes a single Hα filter on a colour camera**, as in the PixInsight version: a new colour-master kind, "Narrowband Hα" (slot `OSCHA`), detected from `FILTER` (Ha, H-alpha, Halpha, Hα, with an optional bandwidth such as `7nm`; dual-band names like L-eXtreme, ALP-T Hα/OIII, Duo, Dual or Quad never match) and chosen by hand in the kind control. `H` is the master's red plane (green and blue are not used); the run is like a mono Hα-only run, with no colour calibration and no sensor. One Hα master may be combined with one broadband colour master; an Hα master with a dual-band master is refused, since both supply `H`; colour with mono and SHO/HSO stay refused.
- **The OSC sensor is chosen automatically** from the `INSTRUME` keyword of the broadband colour master when you have not chosen one: it maps colour cameras (ASI2600MC, QHY268C, ASI6200MC, ASI533MC, ASI294MC, ASI585MC, ASI2400MC and others) to the sensor model of Siril's SPCC database. The dialog shows **Auto (model)** in the sensor box, the run log says which sensor was used and why, a sensor you choose always wins, and an unknown or mono camera falls back to asking you, with the reason.
- Parity: `tools/oracle/loom_osc.js` calls the PixInsight JavaScript for the new kind, slot and combination rule, and `tests/oracle/loom_osc.json` was recorded again (the recorded cameras are compared with the Siril sensor table in `tests/loom/test_osc_halpha.py`).

## [0.5.3] — 2026-10-16

### Added

- **Fly-Through: a local Gaia folder.** A new "Gaia data" choice, `folder`, with a "Gaia folder" box and Browse... button (remembered), and `--gaia folder:DIR` on the command line, read Loom's Gaia cone files (`.csv` or `.tsv`, the cache's own format; layout in the README) instead of the online query wherever they cover the field. A field they do not cover falls back to the cache and Gaia online, and the run says which it used. Gaia online stays the default and its behaviour is unchanged.
- Siril's local Gaia catalogue is detected and shown under the Gaia folder box, but not used: it has no parallax, which Fly-Through needs to place every star and find the distance. `--gaia siril` is refused with that reason. A HEALpix reader for its format is in `loomsiril/gaia_local.py`, tested.

## [0.5.2] — 2026-10-15

### Added

- **One-shot colour (OSC) cameras in `Loom/loom_preprocess.py`**, as in the PixInsight version: debayered colour masters, one broadband and/or one Hα + OIII dual-band master per run, split into channels when loaded, with the detected kind overridable and remembered, HOO only (SHO and HSO are refused), and no mixing of colour and mono masters. With a broadband colour master (`OSC`), the Siril SPCC call takes the OSC sensor plus the front and low-pass filters; a run with only a dual-band master (`OSCNB`) needs no sensor and sends no SPCC names.
- The Siril dialog lists colour masters with a kind control (remembered, with a "detected" reset), greys SHO and HSO with their reasons while a colour master is listed, greys the R, G and B filter boxes (hidden while a broadband master is listed), and shows the sensor colour filters (sensor model, front filter, low-pass filter) while a broadband colour master is listed. A colour run leaves SHO and HSO out and keeps them for the next mono run.
- Colour masters in `.fz` (compressed FITS) or XISF form are converted once through Siril and then split; a warm run does not convert again.
- Siril deviations from the PixInsight version: a colour group is registered with `rgbcomp` and a layer choice because Siril cannot mix one- and three-channel images in a sequence, every SPCC call passes all its spectral arguments because Siril keeps omitted ones, and the calibration choice is part of the cache keys. See `docs/PARITY-LOOM.md`.

### Changed

- Mono runs re-measure their clean white balance once after the upgrade: its cache key now includes the registration reference's key, as in PixInsight.

### Fixed

- Mono runs whose SPCC sensor (`spccSensor`) is set by hand run the composite colour calibration (`spccRGB`) once more after the upgrade: that setting was in no cache key, so changing it did not calibrate again.
- Install instructions no longer show a developer-specific folder; they explain how the script folder path is formed.

## [0.5.1] — 2026-10-15

Experimental, as 0.5.0. The Fly-Through dialog was reworked: see the entries below.

### Added

- Fly-Through dialog, Logo section (as the PixInsight Logo box): a logo file with Browse... (PNG with its transparency,
  TIFF or FITS; a JPEG is refused with a message), Placement (Off by default, Center, corners, top and bottom middles),
  Opacity (0-100 %, default 100) and Fade in after (s). The logo is sized to 20 % of the frame's short side, kept 4 % from
  the edges, and added to the composite before the output transform, in Draft and Render (`loomsiril/logo.py`, ported from
  `Render.logoLayer` / `addLogo` / `Fly.logoRect` / `logoFade`; the maths checked against PixInsight recordings). Its file
  (path, time, size), placement, opacity and fade key the frames and the Draft cache, so a changed logo renders again; the
  file is checked on the validation worker, a missing remembered one keeps its box and blocks Render; the options are
  remembered; hover tips on the Logo, Music and Fade rows. With placement Off nothing of the logo keys the frames or the Draft.
  Tested with synthetic files and fake widgets only: no real ffmpeg encode with a logo, the readers not tried on real logos, the
  tips and the look not seen on a real display; not run inside Siril.
- Fly-Through dialog, Video details section (as the PixInsight Video group): an ffmpeg path box with Browse... (checked on
  the validation worker by running `-version`), a Format drop-down listing only the formats the chosen ffmpeg can encode
  (`ffmpeg -encoders`, probed once per binary and cached; H.264 not for HDR), Quality High or Standard, Clear rendered
  frames... (confirms, deletes only Loom's own frames and records, shows the counts) and an "Estimated render: about N min"
  line from the last render's measured speed (`FlyThrough.estimateMs`). `FlyJob.format` now reaches the encode (it was
  always H.264, or HEVC for HDR); the new options are remembered and do not change the Draft cache key. Each row has a hover
  tip. Tested with a fake ffmpeg and fake widgets: the tips and the look were not seen on a real display, no real ffmpeg
  encode ran with a chosen format, quality or ffmpeg path, and nothing ran inside Siril.
- Fly-Through dialog: the form scrolls (scrollbar and mouse wheel) inside a window at most 85 % of the screen high, with
  the reason line and the Draft, Render and Cancel buttons fixed at the bottom; after Output, four collapsible sections
  (Video details, Logo, Stars and motion, HDR), collapsed by default, opened with Space or Enter, remembered between runs
  (`sections`), and showing in grey why when all their controls are greyed. Tested with fake widgets; not run inside Siril.
- Fly-Through dialog: a **Music** row in the Output group, under Encode video, as in PixInsight: a text box with
  Browse... (mp3, m4a, aac, wav, flac, ogg, opus) and a **Fade in/out** box (on by default). Greyed, with a grey reason,
  unless Encode video is ticked and ffmpeg is found; the file is checked on the validation worker and a missing
  remembered file keeps its box and blocks Render; the path and the fade are remembered. The music reaches the ffmpeg
  command: looped if shorter, cut to the clip, faded in and out, or crossfaded end into start for a looping preset;
  tested against PixInsight's `Fly.ffmpegArgs` recordings with fake widgets, ffmpeg not run with music, not run inside
  Siril.
- Fly-Through dialog: an Orientation drop-down (Horizontal, Vertical) in the Clip group after Frames per second. It starts
  on the image's own orientation (Vertical for a taller-than-wide image; the Image file's size is read on the worker
  thread), a grey note says it follows the image, a choice of yours stays until the image changes, and it is not
  remembered between runs. The output-size boxes show each preset's frame in the chosen orientation (YouTube 1080x1920,
  Exhibition 2160x3840 loop; the Social ones unchanged). It reaches Render and Draft as `orientation`, and the Draft
  cache key follows the frame, not the orientation word. Tested with fake widgets; not run inside Siril.
- Fly-Through dialog: a grey line under the Image file box says when the file is the same field at another size
  ("Image file 2000 x 1222 is the same field as the open image (11799 x 7211): it is resampled to that size") or why
  it is refused; its header is read on the dialog's validation worker, never on the Tk thread, and the line follows
  the grey-out rules (the hint returns when the Image file is not in use).
- Fly-Through dialog: a Browse... button next to the Starless and Stars file boxes (TIFF/FITS; both pickers start in
  the last folder used).
- Fly-Through: "Extract stars from the image with:" lists the star-extraction tools found on the machine (StarNet2,
  SyQon Studio Axiom, StarXTerminator) through Loom's tool resolver and runs the chosen one with Loom's own star
  removal. Discovery and extraction run on worker threads; the choice is remembered; no tool found disables the option.
- Fly-Through dialog regrouped (Input, Target, Clip, Output) with live greying (`loomsiril/fly_model.py`): an
  optional Image file to extract from (empty: the open image), a folder Browse... for the output folder (checked on
  Render), Render and Draft disabled with a grey reason while inputs are missing, video off when ffmpeg is missing.
- Fly-Through progress window for Render: the stage (n of N, percent, elapsed and time left) and a second bar for the
  whole run, a weighted percent that only goes up (extract 15, Gaia 5, scene 5, render 70, encode 10, narrowed to the
  phases that run) and reaches 100 only on success; Cancel stops between frames or steps; the final text names the
  output files and the folder.
- Fly-Through dialog checks: the file and folder checks (exists, readable, writable), the output folder creation, the
  StarNet licence text read and the draft's image stamp and cache key all run on worker threads, debounced 250 ms
  after the last change; while a result is pending Render and Draft say "Checking the files...". Every pending `after`
  callback of the dialog, the progress window and the draft run is cancelled when it closes.
- Loom's Fly-Through Draft (`loomsiril/draft.py`): a 480 px, reduced-rate, SDR preview of the clip from the same scene and
  frame pipeline as the render, an on-disk cache keyed by every picture-changing option (under the Siril config
  directory), a Tk player (play/pause, scrub, loop) and `show_draft`, which renders on a worker thread with a
  progress bar and Cancel.
- Fly-Through finds an object outside NGC/IC by name, online (port of Loom 0.4.2, `loomsiril/skyname.py`): the names in
  the Image file's name and folders, the open image's OBJECT keyword and file name are asked of CDS Sesame on a worker
  thread (`parse_sesame`, `name_queries`, `resolve_name`, `named_in_field`, `find_named`); only a hit inside the solved
  field is taken. It fills an empty Object name (never a typed one) and the target, with a grey "Found: ... (from the
  file name via CDS Sesame)" under Target; the Object name box also resolves a typed name on Enter or **Find**, and
  refuses one outside the field with the reason. Offline it is silent ("Name lookup needs the network"). The dialog and
  progress window titles carry the image's file name. No object name is hard-coded. Not run inside Siril, and tested
  with a stubbed fetch only (the suite has no network); one live Sesame request was made by hand and parsed.
- Tests use fakes only: stand-in sirilpy, tkinter widgets and `after` recorder, a fake master for the draft, no real
  StarNet2, SyQon or RC-Astro tool and no network. The script's draft closure is tested against the real `draft.py`
  (key, cache hit) with a synthetic scene. None of this has been run inside Siril, and the dialog and progress
  windows were not looked at on a real display.
- Fly-Through dialog finds the distance by itself and shows it (`loomsiril/fly_distance.py`, `fly_model.distance_line`).
  Whenever the target changes (the image centre on open, an object found by name, an edit of RA, Dec, Target type
  or Gaia source, after 400 ms) a worker thread runs the render's own lookup (a galaxy is a fixed backdrop; a typed
  distance wins; else the target's ionising cluster from Gaia, else the bright star that lights it) and a grey line
  under the Distance box says "Finding the distance...", then for example "Distance: ~1,950 pc (cluster of 37 stars,
  Gaia)", "Distance: 2000 pc (typed)", "Distance: galaxy (fixed backdrop)", or "No distance found: <reason>. Type one
  (pc)." (no target position, a bad typed value, nothing found, Gaia not reachable). The value is also the box's grey
  placeholder ("~1,950 pc (auto)"); it is never written into the box, so an empty box stays "auto". An answer for a
  target that has changed since is dropped, and the running lookup is told to stop. Gaia "cache" with a cache that
  has nothing for the field now asks Gaia online once when the network is up and says "Gaia: cache empty here, used
  online"; offline it says so. Draft and Render are given the distance the dialog showed (`found_distance`, source
  and label in the options; not saved with them), so the render does not look up another one unless you typed one,
  and the progress text and the final text name it ("Distance: ~1,950 pc (cluster of 37 stars, Gaia)").
  Tested with a stubbed Gaia and recording tk stand-ins; nothing was run inside Siril. One live, read-only Gaia
  request was made by hand for Sh2-132 (RA 334.7875, Dec 56.0791).
- Fly-Through dialog: the **Stars and motion** and **HDR** sections are filled, one row per PixInsight control and built from
  one table (`loomsiril/fly_options.py`, ranges and defaults cited to `FlyThrough.js`): Travel, Easing, Growth, Nebula
  motion, Brighten approaching stars, Twinkle, Bloom, Motion blur, Star colour, Peak (nits), Stars into HDR headroom and an
  HLG or PQ transfer per output size, each with a hover tip, remembered between runs (not Travel), carried into
  `FlyJob.options` and the frame signature (a changed value re-renders, an equal one reuses), used by Draft, greyed with a
  reason where it does not apply (the HDR ones in SDR, a transfer for an unticked size, Nebula motion for a galaxy), with an
  invalid number held and named and a Reset to defaults button per section. The per-preset HDR transfer now reaches the
  renderer (`opts.hdrTransfer`, as in `FlyThrough.presetOptions`). Tested with fake widgets, the pipeline and the signature;
  not run inside Siril.

### Fixed

- Fly-Through said "No distance found ... Type one (pc)" for the Sh2-132 image (target = the image centre, 334.8558
  +56.0278) while PixInsight's Loom finds one. Loom's JavaScript, run on the same Gaia rows, finds nothing at the image
  centre too (the nearest star brighter than G 10 with a good parallax is 0.106 deg away, the search reaches 0.1 deg)
  and 4,562 pc at the Sesame position of Sh2-132 (a G 8.8 star 0.072 deg away): the Gaia query, the cluster search and
  the lighting-star search were already the same, the target position was not. The dialog's name lookup left the target
  at the image centre when the Object name box already held the object's name (the options remember it): now the found
  object becomes the target whenever the box is empty or names that object (a different name you typed keeps its own
  target). The grey line now says what was searched when nothing is found (radius, Gaia source, number of sources, how
  many have a usable parallax, cluster members needed, the G limit and the nearest candidate), and the Render and
  Draft messages carry it. New `tests/test_distance_parity.py` replays Loom's JavaScript (`tools/oracle/distance.js`,
  `tests/oracle/distance.json`) on a frozen live Gaia field (`tests/fixtures/sh2-132-gaia.json.gz`). Checked live once
  by hand outside Siril; nothing was run inside Siril.

- Fly-Through Render could stop with "no distance found for the target (no cluster and no illuminating star): give
  --distance PC" (after the star tool had run) although the dialog had shown "Distance: ~4,562 pc (illuminating star G
  8.8, Gaia)". The dialog's answer is passed on only while it is valid for the boxes as they stand, and it was dropped
  while a new lookup ran and by any write of the same text into a watched box (Tk fires its trace for those too); the
  render then did its own lookup, which lacked the dialog's cache-empty -> Gaia online fallback. Now: the fallback is in
  the shared lookup (`pipeline._find_distance`), so the CLI, Draft, Render and the dialog preview all do it; the worker
  settles the distance first, before any star extraction (the dialog's value as it is; else the dialog's own resolver
  for the current boxes, waiting for a lookup still running and reading what it cached; a typed value wins); writing
  the same text into a box keeps the answer; the Siril console says "Loom Fly-Through: distance 4562 pc (illuminating
  star G 8.8, Gaia online)" at render start and the progress window names it; and when none can be found it stops at once
  with "No distance found for this target (<reason>). Type a distance in pc in the Distance box and press Render
  again." (Draft: "press Draft again."). The CLI keeps "give --distance PC". A cache-miss lookup is now labelled "Gaia
  online" in the render too, as in the dialog. Tests use stubs only; not run inside Siril. I could not reproduce the
  exact click that dropped the value (it depends on the timing of the user's edits), and a live Gaia request for the
  same target and field gave 4562 pc through both the dialog's and the render's lookup.
- Fly-Through dialog: the Image, Starless and Stars file paths, the mode (extract or files) and the Browse... folder
  are remembered with the other options (autosaved 500 ms after a change on a worker thread, and saved when the window
  closes), so the image is no longer lost between runs. A remembered file that has gone stays in its box with the
  "does not exist or cannot be read" reason and Render/Draft stay greyed; an empty box saves no path.
- Fly-Through: an Image file that is the same field as the open image but at another pixel size (for example an
  export of 2000 x 1222 beside an open 11799 x 7211 image) was refused ("does not match the image open in Siril").
  It is now resampled onto the open image's grid on the worker thread before the stars are extracted
  (`loomsiril/fly_grid.py`, using the port's resampling kernels); the plate solution and the stars' positions still
  come from the open image. A file of another aspect ratio (beyond 0.5 %) or another channel count is refused with a
  message that says what to do (export at the same aspect ratio, or open the file in Siril and solve it), also in
  the dialog's reason line and the error window. Tested with fakes and tiny synthetic images; not run inside Siril.
- The progress window (Render and Draft) of a failed run looked stuck ("Overall 19%" under the error). A failed run
  now shows the error, "Stopped" for the overall bar, empty bars and "Failed at: <stage>"; a cancelled run shows
  "Cancelled" and empty bars; Close stays.
- StarNet2 older than 2.6.2 cannot read FITS (`Unable to read input image`, exit 255) and made star removal fail
  mid-run. Loom now needs StarNet2 2.6.2 or newer (`MIN_STARNET_VERSION` in `loomsiril/starnet_version.py`): an older
  or unreadable `--version` is rejected by the tool resolver with a warning ("StarNet2 2.5.2 is too old: Loom needs
  2.6.2 or newer (older builds cannot read FITS)") shown in the Tools section, the headless preflight and the
  Fly-Through tool status, and the tool is not offered anywhere (a saved StarNet2 choice falls back to none with the
  usual note). A remembered StarNet2 is probed again each time.
- The real-binary StarNet2 tests skip, with the reason, when the installed StarNet2 is older than 2.6.2 (they still run
  in the Docker image, which pins 2.6.2).
- The SyQon Starless "model missing" test no longer depends on a SyQon install on the machine.
- Fly-Through Draft button did nothing when the draft failed (for example an Image file whose size or channels do not
  match the image open in Siril): the run closed its progress window on any error, so the message was never seen.
  A failed draft now keeps its progress window with the error and a Close button; a player that cannot open says
  why; an exception in a dialog callback is shown in a box (and a click on a greyed Draft or Render, or on a button
  while the previous click is still being prepared, names the reason) instead of reaching a stderr nobody reads. The
  draft and its player windows are raised in front, and Siril's console logs each phase (`Draft: starting`,
  `extracting stars...`, `rendering n/N`, `done, opening the player`, `failed: ...`).

## [0.5.0] — 2026-10-14

First release, and an experimental one: the Loom pipeline has been run in Siril's dialog and headless, but
expect rough edges. The Siril port now lives in the Loom repository, under `siril/` (PixInsight's Loom is in
`pixinsight/`); it is released together with the PixInsight scripts, under the same `vX.Y.Z` tag.

### Added

- Loom pipeline for Siril (`Loom/loom_preprocess.py`, package `loomsiril.loom`): masters in, finished plates out (RGB,
  starless and stars layers, SHO, HSO and HOO palettes, optional PSB export). A numpy and scipy port of Loom's
  JavaScript, with a dialog mode in Siril and a headless job mode (`--job`) that writes a status file. The
  optional external tools (RC-Astro CLI, SyQon Studio CLI, legacy SyQon tools, StarNet2, GraXpert-AI) are found
  when installed. Parity and deviations: `docs/PARITY-LOOM.md`; job format: `docs/loom-job.md`.
- StarNet2 2.6.2 integration (E7c): the Siril script runs `starnet2 --input/--output` on float32 FITS, with a
  `--version` check first. Its licence is shown and must be accepted before StarNet mode runs. The Docker test
  image downloads the pinned official zip (URL and sha256 are build defaults).
- Python port of Loom's Fly-Through (v0.4.1) on numpy: geometry, parallax, colour,
  presets, rendering and the frame pipeline, pinned against the JavaScript output.
- Command-line interface (`python -m loomsiril`) with `--video` via ffmpeg.
- Siril 1.4 script `Loom/loom_fly_through.py`. It has not been run inside Siril yet.
- Docker test image (`docker/Dockerfile`, `docker/run-tests.sh`): Python 3.12,
  ffmpeg, the pinned Siril 1.4.4 AppImage and an optional StarNet2 CLI.
- GitHub Actions workflow `.github/workflows/ci.yml`: unit tests on Linux and macOS,
  and a Docker build (best effort).
- Row order: FITS files and Siril's pixel data are turned top-down on the way in,
  with their WCS converted, so frames are never mirrored.
- `docs/PARITY.md` (generated by `tools/make_parity.py`): every JavaScript name of
  Fly-Through mapped to its Python port, or listed as not ported with the reason.
- Acceptance tests (`tests/acceptance/`) for the product criteria.
- The master list is refilled from the saved paths, with each master's star count.
- A final "Loom is done" line in the log names the saved PSB.

### Fixed

- Saved SPCC filters are kept when Siril's SPCC database cannot be read.

- A one-channel image no longer takes minutes to build: the star detector no longer
  mistakes the background (a noiseless layer, or noise clipped at zero by the star
  removal) for tens of thousands of stars.

### Changed

- The script `Loom/loom.py` is renamed `Loom/loom_preprocess.py`.
- In the dialog, the preflight, the master scan, the tool rescan and locate, and the cache size run in
  background threads, so the dialog stays responsive; the options are saved automatically.
- Palette SPCC borrows the registered plates' astrometric solution.
- Narrowband normalisation now only matches sky levels; it no longer scales noise or blends Ha, which greyed
  the palette.
- The `stretchTarget` option defaults to 0.15 (PixInsight's Loom keeps 0.25).
- Gaia queries take the target's RA normalised into [0, 360) (the same sky point); a
  target with a NaN or infinite coordinate, or |dec| > 90, is refused with a clear error.
- Three rounds of internal refactoring for maintainability (shared JavaScript-semantics
  helpers, smaller functions, parameter objects, no import cycles); frames are
  bit-identical to before, checked by recorded frame hashes.

### Known limits

- The Siril script has not been run inside Siril (only its bridge was checked against
  headless `siril-cli` 1.4.4).
- The StarNet2 call is tested against the real StarNet2 2.6.2 CLI in the Docker test image
  (`starnet` tests) and against stand-in programs in the unit tests.
- The star detector floors its noise at one 16-bit step (1/65535): a noiseless stars
  layer loses stars fainter than about 7.6e-5 above the sky (see `docs/PARITY.md`).
- On a crowded, noisy one-channel stars layer the detector's noise estimate stays biased
  high by star light, so some faint stars are not found; a three-channel frame with an
  exactly-zero border of about a quarter of the width or more is affected the same way
  for stars near 5 sigma.
