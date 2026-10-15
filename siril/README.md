# Loom-Siril

Loom's tools for [Siril](https://siril.org), starting with **Fly-Through**:
a push-in video of a finished astrophoto whose stars move at their real Gaia
distances. It also carries the **Frame Selector**, which rates and culls light subframes (see "Frame Selector" below).

Part of the [Loom](https://github.com/fcarucci/Loom) repository, whose `pixinsight/` folder is the PixInsight
version. This folder is the Siril port: its code is Python and it is released together with the PixInsight scripts, under the same `vX.Y.Z` tag.

## Status

- Port of Loom v0.4.1 (`script/FlyThrough.js` and `script/lib/*.js`) to Python
  with numpy. The maths are pinned against recorded output of the JavaScript;
  [docs/PARITY.md](docs/PARITY.md) lists what is ported, vectorised, replaced or
  not ported.
- This script has not been run inside Siril. It targets the Siril 1.4 Python API
  (`sirilpy`) and is tested against a stand-in of that API. Its bridge (pixel data,
  header WCS, star list) was also checked against headless `siril-cli` 1.4.4 in the
  Docker image: star positions and the WCS agree with Siril's own `pix2radec`. The
  dialog and the full script have not been run in Siril's user interface.
- StarNet: the script runs the official StarNet2 2.6.2 command-line tool as a separate
  process (see "StarNet2" below). The `starnet` tests run the real binary in the Docker
  test image; they skip without it.
- The Loom pipeline (`Loom/loom_preprocess.py`, see the Loom section) is a port of Loom's JavaScript to numpy and scipy.
  [docs/PARITY-LOOM.md](docs/PARITY-LOOM.md) lists each ported function and the deviations from Loom.
- The Frame Selector (`Loom/loom_frame_selector.py`, see the Frame Selector section) is a port of the PixInsight Frame
  Selector, with its measurements taken from Siril. It has not been run inside Siril's user interface: it is tested
  against a stand-in for the Siril API and, for the Siril-dependent parts, headless `siril-cli` 1.4.4 in the Docker
  image. [docs/frame-selector-parity.md](docs/frame-selector-parity.md) lists what is ported and every deviation.
- Version 0.5.0 (tag `v0.5.0`, shared with the PixInsight scripts) is the first release and is experimental.

## What it does

Fly-Through takes a finished image (its starless layer and its stars) and a
target on the sky. It places the stars with their Gaia parallax, so nearer stars
drift past the camera faster than distant ones. It renders 16-bit TIFF frames
with an sRGB or HDR output transform and, when ffmpeg is on the PATH, encodes
them to a video.

A **Draft** shows the clip before the full render: 480 px on the long side, a
reduced frame rate (`presets.draft_plan`), always SDR, from the same scene and
frame pipeline as the render. It plays in its own window (play/pause, scrub,
loop) and is kept in `<Siril config dir>/loomsiril-fly-drafts`, so an unchanged
draw opens at once. The code is `loomsiril/draft.py`; the Fly-Through dialog's
Draft button calls `draft.show_draft`. Tested with fakes and, where Tk has a
display, with real Tk; it has not been run inside Siril.

**Dialog.** Four groups, and what does not apply is greyed at once as you change a radio button or tick box.
*Input*: either "Extract stars from the image with:" (a drop-down of the tools found, an optional **Image file
(TIFF/FITS)** with Browse..., empty meaning the image open in Siril) or "Starless and stars files" (a Browse... button
next to each box). Both layer pickers and the image picker open in the last folder used. The image file is read with
the same TIFF/FITS reader as the layers and may be **the same field at another pixel size** (an export smaller than the
open image, say): when its aspect ratio equals the open image's (within 0.5 %) it is resampled onto the open image's
pixel grid on the worker thread (Catmull-Rom when enlarging, an area filter when shrinking) before the stars are
extracted, and a grey line under the box says so ("Image file 2000 x 1222 is the same field as the open image
(11799 x 7211): it is resampled to that size"). A file of another shape, or with another channel count (mono onto
colour or colour onto mono: nothing is converted), is refused with a message that says what to do. The plate solution
and the stars' positions still come from the image open in Siril, and RA/Dec stay as typed (a file is not
plate-solved by this script). The progress window of a run that fails shows the error, "Stopped" and empty bars with
"Failed at: <stage>" (a cancelled run says "Cancelled"), not a bar left at its last percent. Nothing of this has been
run inside Siril. *Target*, *Clip* and *Output* (object name, output folder with a Browse... folder picker, ffmpeg video).
Greyed: the files boxes in extract mode and the tool/image row in files mode; the whole extract group when no tool is
found; "Encode video" when ffmpeg is not found; Loop when only the Exhibition preset is ticked (it always loops).
Render and Draft stay disabled, with a one-line grey reason, while an input is missing or unreadable, no output size
is ticked, or the output folder cannot be created or written to.

**Orientation** (Clip, after Frames per second): Horizontal or Vertical, as in the PixInsight dialog. Vertical turns the
YouTube and Exhibition presets portrait (1080x1920, 2160x3840); the Social ones keep their shape, and a vertical image
is turned for a horizontal preset, never cut. The output-size boxes show the frame each preset renders at in the chosen
orientation and change as you change it. It starts on the image's own orientation (Vertical for a taller-than-wide
image, else Horizontal; the Image file's size is used when one is set), a grey note says so ("follows the image:
portrait"), a choice you make stays until the image changes, and it is not remembered between runs. It reaches Render and
Draft as the job's `orientation` (`landscape` or `portrait`), and the Draft's cache key follows the frame, so a preset
whose frame does not change (the Social ones) keeps its draft. Tested with fakes; not run inside Siril.

**Music** (Output, under "Encode video"), as in the PixInsight dialog: a text box with a Browse... button (audio files:
mp3, m4a, aac, wav, flac, ogg, opus; it opens in the last folder used) and a **Fade in/out** box (on by default, as in
PixInsight; the music fades over 2 s, at most a quarter of a short clip). It is greyed, with a grey reason, unless
"Encode video" is ticked and ffmpeg is found. The file is checked (exists, readable) on the dialog's validation worker,
never on the Tk thread; a remembered file that has gone stays in its box, the reason is shown, and Render and Draft are
blocked until you fix or clear it (an empty box means no music). The path and the Fade box are remembered between
runs. The music reaches the job as `music_path` and `music_fade` and the ffmpeg arguments as `encode.ffmpeg_args(...,
audio=...)`: it is looped if it is shorter than the clip and cut to the clip's length (`-shortest`); a looping preset
(Back and forth, Crossfade, or the Exhibition) gets its music crossfaded end into start (`encode.loop_audio`) instead of
a fade. AAC in MP4 and MOV, Opus in WebM. The arguments are tested against the ones PixInsight's `Fly.ffmpegArgs` makes
for the same inputs (`tests/oracle/encode.json`); ffmpeg was not run with music in the tests, and nothing of this has
been run inside Siril. The command-line interface has no music option.

**Video details** (the first Advanced section), as the Video group of the PixInsight dialog (`FlyThrough.js` buildVideo):
an **ffmpeg** box with Browse... (empty: the ffmpeg found on this machine; a typed one is used for the encode and for the
probe, and the validation worker runs `ffmpeg -version` on it: a path that is not ffmpeg is reported and blocks Render
while Encode video is ticked, never silently replaced by another binary); a **Format** drop-down listing only the formats
that ffmpeg can encode among MP4 H.264, MP4 H.265/HEVC, MOV ProRes 422 HQ and WebM VP9 (`ffmpeg -encoders`, run once per
binary on a worker and cached by path, size and modification time; H.264 is not offered for HDR; the default is HEVC for
a 4K-wide video, else H.264; a format you chose or saved stays while it is offered); **Quality** High or Standard (the
CRF of each codec: `Fly.codecArgs`); a **Clear rendered frames...** button that, after counting and asking, deletes only
Loom's own `frame_NNNNN.tif` files, half-written frames and frame records in the output folder's preset folders (never a
video or any other file; a grey line says how many there are); and a grey **Estimated render: about N min** line from the
presets, duration, fps, HDR, orientation and loop (`FlyThrough.estimateMs`; HDR counts 1.3 times SDR). The estimate needs a
speed: PixInsight times one frame once the scene is built, Siril uses the speed measured on your last render (kept as
`ms_per_mp`), so the line stays empty until you have rendered once. Format and Quality are greyed, with the reason, unless
Encode video is ticked and an ffmpeg is found; picking a working ffmpeg when none was found ticks Encode video, as
PixInsight's Browse does. They reach the job as `format`, `quality` and `ffmpeg`; a format the ffmpeg cannot encode falls
back to the default with a note. They change only the encode, so frames are reused and the Draft cache key is unchanged.
Each row has a hover tip (adapted from the PixInsight tooltips). Tested with a fake ffmpeg script (it prints a version and
an encoder list) and fake widgets. Not seen or run: the hover tips and the section's look were never seen on a real display,
and no real ffmpeg encode ran with a chosen format, quality or ffmpeg path, nor did Clear rendered frames run on a real
render's folder; nothing ran inside Siril.

**Logo** (the second Advanced section), as the Logo box of the PixInsight dialog (`FlyThrough.js` 1399-1426): a **Logo file**
box with Browse..., **Placement** (Off, Center, Top left, Top middle, Top right, Bottom left, Bottom middle, Bottom right;
default Off), **Opacity** (a 0-100 % slider, default 100) and **Fade in after (s)** (0-600, default 0). The logo is drawn
as in `Render.logoLayer` / `addLogo` (`loomsiril/logo.py`): its long side is 20 % of the frame's short side, 4 % of the short
side from the edges (or centred), its own transparency kept and scaled by the opacity, area-averaged to its size, and added
to the frame's composite before the output transform (so it takes the same colour conversion as the picture, and in HDR it
also covers the star light above white); with a delay it is hidden until that second and then fades in over one second. A
Draft draws it at the draft's size with the same code. Choosing a file with Browse... while Placement is Off puts it at the
bottom right (`FlyThrough.useLogo`). Placement is greyed, with the reason, until a file is set, and Opacity and Fade until
it is also placed. The file is opened on the validation worker, never on the Tk thread; a remembered file that has gone keeps
its box, says why under it, and blocks Render and Draft while the placement is not Off. The file types are **PNG** (any
colour type and depth, with its transparency), **TIFF** and **FITS**, read without any extra package: PixInsight reads any
format it has a reader for, so a **JPEG (or other) logo is refused with a message** here; convert it to PNG. The logo's file
(path, modification time and size), placement, opacity and fade are in the frame signature and the Draft cache key, so
changing the logo renders again and an unchanged one reuses the frames (PixInsight keys the path only). The four options are
remembered (`logo_path`, `logo_place`, `logo_opacity`, `logo_delay`) and reach the job as `FlyJob.logo_*`. The maths (placement,
fade, the layer and the blend) are checked against PixInsight's JavaScript recordings (`tests/oracle/logo.json`); the readers
and the dialog are tested with small synthetic files and fake widgets (and real Tk where a display exists); the logo readers
were only tried on synthetic files, no real ffmpeg encode with a logo was run, the hover tips and the look were not seen on a real
display, and none of it was run inside Siril. With placement Off no logo value reaches the options, the frame signature or the Draft
key.

**Scrolling and Advanced sections.** The form is in a scroll area (scrollbar, mouse wheel on macOS, Windows and Linux,
Tab scrolls the focused control into view) and the window is at most 85 % of the screen high, so it fits a laptop; the
reason line, the draft note and the Draft, Render and Cancel buttons stay at the bottom, always visible. After Output come
four collapsed sections, each a flat header button (`▸ Video details`, `▸ Logo`, `▸ Stars and motion`, `▸ HDR`) that
Space or Enter opens (`▾`). Which ones you left open is remembered between runs (the `sections` option). When every
control of a section is greyed, its header says why in grey (for example "needs Encode video"); a greyed parent still
greys its children. Tested with fake widgets (and real Tk where a display exists); not run inside Siril, so the wheel
over drop-downs and the window size on a real laptop screen are unchecked.

**Stars and motion and HDR sections.** The two sections hold every control PixInsight's dialog has for them, built from one
table (`loomsiril/fly_options.py`, each default and range cited to `FlyThrough.js`): Travel (pc; empty is automatic: 0.2 of
the distance, 200 pc for a galaxy, with a grey note when a typed one is capped at 0.9 of the distance), Easing (Smooth or
Linear), Growth (0.15), Nebula motion (0 to 100 %, 40), Brighten approaching stars, Twinkle (0 to 10 %, 3), Bloom (0 to
200 %, 100), Motion blur, Star colour (0 to 200 %, 100) and, in HDR, Peak (nits, 400 to 4000 in steps of 100, 1000), Stars
into HDR headroom and an HLG or PQ transfer for each output size. Hover a control for its tip. The HDR controls are greyed
(and the HDR header says why) while Dynamic range is SDR, a transfer is also greyed for an output size that is not ticked,
and Nebula motion is greyed for a galaxy (a fixed backdrop; PixInsight leaves it on). A number that does not parse or is out
of range is held and named when you click Render or Draft, as the other boxes do. Each section has **Reset to defaults**.
Every value is remembered (Travel is not: it belongs to the image), reaches the render as a `FlyJob.options` entry, and is
part of the frame signature, so changing one re-renders the frames and an equal value reuses them; in SDR the HDR-only
values are left out so they never re-render an SDR clip. Draft uses the same values (its cache key follows the ones that
change its SDR picture, not Peak, star HDR or the transfers). Differences from PixInsight: the per-preset transfer now
reaches the Siril renderer (it always used each preset's default before), Growth is limited to 0 to 1 (PixInsight takes
any number), and Travel is empty-for-automatic rather than filled in. Tested with fake widgets, the pipeline and the
frame signature (defaults against the engine's, and against `Fly.js` where its sources are checked out); nothing of this
has been run inside Siril.

**Finding the object by name (online).** The Siril port follows Loom 0.4.2/0.5.0. When the dialog opens, and
after you pick an Image file, a worker thread asks [CDS Sesame](https://cds.unistra.fr/cgi-bin/Sesame) (SIMBAD, NED,
VizieR) what the names in the Image file's name and folders, the open image's OBJECT keyword and the open image's own
file name might be (`loomsiril/skyname.py`: catalogue-looking words such as `Sh2-132` first, then two-word runs, then
single words, at most 6 requests, 8 s each). A result is accepted **only if it lies inside the plate-solved field**.
When one is found and **Object name** is empty, the box is filled (what you typed or saved is never overwritten), the
Target RA/Dec follow if you had not edited them (the distance is then looked up round that object, as in Loom; also
when the box already holds that same object's name, as the options remember it from an earlier run), and a
grey line under Target says where it came from, for example "Found: Sh2-132 (from the file name via CDS Sesame)".
You can also type a name in **Object name** and press Enter or **Find**: it is resolved the same way and refused,
with the reason, when it is outside the field. No network means no result and a grey "Name lookup needs the network";
answers are kept for the session (a failed request is not, so a retry works). No object name is written in the code.
**The distance, found for you.** Under the Distance box a grey line says what the render will use, found by a
worker thread whenever the target changes (the image centre on open, an object found by name, an edit of RA, Dec,
Target type or Gaia source): "Finding the distance...", then for example "Distance: ~1,950 pc (cluster of 37 stars,
Gaia)", or "No distance found: <reason>. Type one (pc)." The value is also the box's grey placeholder and is never
written into it: an empty box stays "auto", and a distance you type wins ("Distance: 2000 pc (typed)"). It is the
render's own lookup (`loomsiril/fly_distance.py`): a galaxy is a fixed backdrop; else the target's ionising cluster
from Gaia, else the bright star that lights it. With Gaia "cache" and a cache that has nothing for the field it asks
Gaia online once when the network is up ("Gaia: cache empty here, used online"), and says so when it is not (that
fallback is in the shared lookup, so the CLI, Draft and Render do it too). Draft and Render get the distance shown, so
they do not find another; when it is not valid at the click (the lookup was still running, or a box changed since) the
render's worker finds it first, with the same lookup and before the star tool runs, and shows "Finding the distance..."
in the progress window. The Siril console says "Loom Fly-Through: distance 4562 pc (illuminating star G 8.8, Gaia
online)" at render start, the progress and the final text name it, and when none can be found it stops at once:
"No distance found for this target (<reason>). Type a distance in pc in the Distance box and press Render again."
(the CLI keeps "give --distance PC"). When none is found the grey line says what was searched, for example "No distance
found: no star cluster and no bright illuminating star found: searched 1.35 deg round RA 334.8558, Dec +56.0278 (the
image field is 0.93 deg): 116,639 Gaia sources (from Gaia online), 36,967 with a usable parallax: no cluster of >= 30
members (...) and no star brighter than G 10 with a good parallax within 0.10 deg of the target (the nearest, G 8.0, is
0.106 deg away). Type one (pc)." The search is Loom's, number for number (`Fly.findCluster` in a cone of 3 x
min(radius, 0.45 deg) round the **target**, then `Fly.illuminatorDistance`: G < 10 within max(0.1 deg, diameter/120) of
the target; Gaia DR3 at VizieR, G < 17.6, no row limit in the way), so what decides it is the target's position: the
Sesame position of Sh2-132 (334.7875, 56.0791) has a G 8.8 star with a good parallax 0.072 deg away (about 4,562 pc),
the image centre (334.8558, 56.0278) has none within 0.1 deg, in Loom's JavaScript as in this port. Not run inside
Siril; Gaia was stubbed in the tests (`tests/test_distance_parity.py`, against `tests/oracle/distance.json` recorded
from Loom's JavaScript on a frozen copy of the live Gaia rows). Checked live once, by hand, outside Siril, with the
real header of the Sh2-132 image (11799 x 7211, field radius 0.927 deg): both targets above, the same answers.

The dialog's and the progress window's titles carry the image's file
name. Differences from PixInsight: Siril has no NGC/IC catalogue in this port (Sesame knows NGC and IC too) and no
`Image<number>` views, so `view_id_from_path` and `image_title` are ported and tested but nothing calls them. Tested
with a stubbed fetch only: the suite has no network, and nothing was run inside Siril's UI. One live Sesame request
(Sh2-132) answered and parsed correctly when made by hand.

The tool drop-down lists only the tools found on this machine, through Loom's resolver: StarNet2 (its licence is shown
and accepted first), SyQon Studio (Axiom, `syqon-cli`) and StarXTerminator (RC-Astro CLI; without a licence it is not
listed, and a refusal at run time is reported as such). The search (and the ffmpeg check) and the extraction run on
worker threads. If no tool is found the option is disabled with a one-line reason and the files mode stays. The last
tool chosen is saved with the other options, and so are the Image, Starless and Stars file paths, the mode (extract or files) and the folder the Browse... buttons last used (they are saved a moment after each change and when the window closes; an empty box saves no path; a remembered file that has gone stays in its box with the "does not exist or cannot be read" reason and Render and Draft stay greyed until it is fixed or cleared); a saved tool that has vanished is replaced by the
first found, and the dialog says so.

**Progress.** Render opens a progress window with two bars: the current stage (n of N with its percent, elapsed and
estimated time left, or a moving bar while a stage reports no count) and the whole run, a weighted percent that only goes
up (extraction 15, Gaia 5, scene 5, render 70, encode 10, narrowed to the phases that run) and reaches 100 only when the
run succeeds. It covers the star extraction, the Gaia lookup, the frames and the ffmpeg encode. Cancel stops between
frames or steps and says so; Close then shows what was written and where, or the error. The run is on a worker thread;
the window only reads a queue.

**Nothing slow on the Tk thread.** Looking for tools and ffmpeg, checking that the files exist and the output folder is
writable (250 ms after the last change, again when a box gets the focus, after Browse, and when Render or Draft is
clicked; Render and Draft say "Checking the files..." until it answers), creating the output folder, reading the StarNet
licence file, the draft's file stamp and cache key, the extraction, the Gaia lookup and the render all run on worker
threads. The Tk thread only parses the typed values and reads queues. The dialog, the progress window and the draft run
cancel their pending timer callbacks when they close, and a draft still running stops with the dialog.

Tested with fakes only: a fake resolver or a resolver over stand-in executables, stand-in tkinter widgets with an
`after` recorder, a fake master for the draft, a stand-in sirilpy; the enable/disable rules are pure
(`loomsiril/fly_model.py`, no file access) and tested without Tk. The script's draft closure is also run against the real
`loomsiril/draft.py` on a synthetic scene (cache key from the dialog's options, second call a cache hit). Some tests
(the dialog layout, parts of the draft player) use real Tk and run only where Tk has a display; there they open real
windows. No real StarNet2, SyQon or RC-Astro tool was run, the dialog and progress windows were not looked at by hand,
and none of this has been run inside Siril.

## Loom: masters to finished plates

`Loom/loom_preprocess.py` is a second Siril script in this repository. It takes calibrated, stacked masters, one file per
filter, and produces finished plates: an RGB composite, starless and stars layers, narrowband palettes (SHO, HSO,
HOO) and, when asked, a layered PSB export. The inputs must be R, G and B together, or none of them; L is optional;
and any of H, S or O alone is enough. Without any of these the run has nothing to do. It is a port of the
Loom pipeline for PixInsight. Plate solving and SPCC colour calibration run in Siril. The other steps use the
external tools below when they are installed, and Siril's own tools when they are not.

Requirements: Siril 1.4 with Python scripting. On its first run the script installs `numpy`, `scipy` and
`tifffile` into Siril's Python environment. Run it from a clone of this repository (see Install): it
finds the `loomsiril` package there.

### Running it

- **Dialog.** Run `Loom/loom_preprocess.py` from Siril's Scripts menu. The options dialog opens: choose the masters, check the
  options and run. The options, the masters and the output folder included, are saved in Siril's configuration
  folder (`loomsiril/loom.json`) whenever the dialog closes, by Run or Cancel, and the dialog reopens as it was left.
- **Headless job.** Under `siril-cli` the script reads a job file and opens no window. The job is a JSON file, described
  in [docs/loom-job.md](docs/loom-job.md). Run it with:

  ```
  printf 'requires 1.4.0\npyscript <path>/Loom/loom_preprocess.py --job job.json\n' | siril-cli -d <dir> -s -
  ```

  `siril-cli` does not report Python errors in its exit status, so read the status file: `job.json.status.json`
  beside the job, or the path given by the job's `statusFile` key. It holds `ok`, `exitCode` (0 finished, 1 run error,
  2 preflight problems, 130 cancelled), `problems`, `produced`, `outputs`, `log` and `error`. A headless run starts
  from Loom's defaults plus the job file only, and never writes the options file.

  A job that lacks these stops before the run, and the status file lists the problem:

  - `outputDir` is required. A job does not read the dialog's saved options, so it has no default.
  - An RGB run needs `filters` for R, G and B (Siril's SPCC filter names), and Siril's SPCC database folder must
    exist. Open Siril's graphical interface once to create it.
  - A TIFF or PSB export needs `exportDir`, and the stretch must be on.

  Output files are FITS, one per result, with the suffixes `_starless`, `_stars` and `_linear` where they apply.
  An existing file is never overwritten: the new one gets a number (`_1`, `_2`, ...).

### External tools

Each tool is optional. Loom looks for the tools it can use and skips any it cannot find. A tool's path can also be
given in the job file's `tools` key. Each tool has its own licence:

- **RC-Astro CLI.** The licence covers the command-line tool. The tool activates once online, and the licence
  allows three computers.
- **SyQon Studio CLI.** Sign in to SyQon Studio in the app first. The command-line tool then uses the token that
  the app keeps in the macOS Keychain or in Windows Credential Manager. On macOS the first headless run may show a
  Keychain prompt; allow it, or the run waits for it.
- **Legacy SyQon tools.** Older SyQon command-line tools (SyQonStarless, prism and parallax). Each is offered
  whenever it is found, alongside the current tools, and does not depend on SyQon Studio being installed.
- **StarNet2.** Its own licence, from StarNet Astro. Loom runs it only after you accept that licence (see
  [StarNet2](#starnet2)). A headless job gives the accepted licence revision in `starnetLicenceAccepted`.
- **GraXpert-AI.** Siril's own script, from Siril's scripts repository. Its models are CC BY-NC-SA 4.0 and are
  downloaded by GraXpert or by the script. Its first run installs its Python packages and needs the network.

### Options worth knowing

- **Siril background extraction only.** With the gradient tool set to none, each broadband channel (L, R, G and B)
  runs Siril's background extraction (`subsky`, polynomial degree 2) in place of the gradient correction. The
  narrowband channels (H, S and O) never get it.
- **GHS stretch.** The stretch method can be Siril's generalised hyperbolic stretch (GHS), used in place of the
  multiscale adaptive stretch. Its settings are fixed. Saved options that name the old stretch are migrated.
- **Gaia residual check.** It always runs, in dialog and headless runs, for every solved channel, including masters
  that arrive already solved: Loom measures the solution against the Gaia DR3 stars of the field. It reports the
  median deviation of the matched stars in pixels, with a verdict: `ok` up to 0.315 px (logged), `poor` above
  0.315 px and `bad` at 3.0 px or more (both logged as warnings; `bad` means the solution is wrong). There is no
  option to turn it off, and it does not change the solution. It reads the local cache first, in
  `$XDG_CACHE_HOME/loomsiril/gaia` (or `~/.cache/loomsiril/gaia` when `XDG_CACHE_HOME` is unset); the same path is
  used on macOS and Windows. It queries the online Gaia DR3 catalogue, through VizieR, only for a field that is not
  cached, and stores the answer in the cache. When the catalogue cannot be reached, the check logs
  one line and is skipped for the rest of the run; a failed check never fails the run.

### Outputs and the cache

Results are written as files to the output folder. At the end of a run that is not validate-only, the main result
is also loaded into Siril as the current image: RGB_starless and RGB when they exist, otherwise the first palette,
then L_starless and L, then H, S or O. The cache folder holds stage results, the white-balance
records and the run logs (`logs/loom-run-<stamp>.log`). Its default location is per user: `~/Library/Application Support/loomsiril/cache` on macOS, `%LOCALAPPDATA%\loomsiril\cache` on Windows, and
`$XDG_DATA_HOME/loomsiril/cache` (or `~/.local/share/loomsiril/cache`) elsewhere. Set `cacheDir` to use another
folder.

## Frame Selector

`Loom/loom_frame_selector.py` is a third Siril script in this repository. It is the Siril port of the Loom Frame
Selector: it measures every light subframe in a folder, groups the frames by `FILTER`, works out where the line falls
for each channel on that night, and deletes the frames below it or copies the rest to another folder. It can also read
a night straight off an ASIAIR card and turn its flats into master flats. The numbers come from Siril itself (see
"Differences from the PixInsight Frame Selector" below), not from PixInsight.

Requirements: Siril 1.4 with Python scripting (the script also installs `ttkthemes` on its first run), and the Install
steps below (the script sits next to the other Loom scripts and needs no extra setup). It has not been run inside
Siril's own user interface: the logic is tested with a stand-in for the Siril API and, for the parts that need Siril,
against headless `siril-cli` 1.4.4 in the Docker image.

### Using it

1. Run **Scripts, Loom, loom_frame_selector.py**. If an ASIAIR card is mounted the script offers it first (see below);
   cancel the night picker and it asks for a folder instead. Reading the folder can be stopped, and a read writes
   nothing but the cache. Run ignores a second click while the first is still working.
2. Frames are grouped by their raw `FILTER` string, one channel per filter. Frames with no readable filter are shown but
   never rejected automatically. A channel whose frames differ in exposure, binning, geometry or calibration state is
   flagged as not comparable; that is a warning, and Run still acts on it. Formats are those Siril reads: FITS
   (including compressed `.fz`) and XISF.
3. Each channel has a **preset**: Lenient, Balanced or Strict, which set `k`, the width of the cut in normalised MADs,
   to 3.0, 2.5 or 2.0. `k` can be edited, and each channel has an on/off switch. Below 10 valid measurements in a
   channel the cut does not run. All the measurements rank the frames (PSF SNR most heavily), but by default only FWHM,
   eccentricity and star count reject; PSF SNR can be turned on per channel.
4. Each criterion (`FWHM <=`, `ecc <=`, `stars >=`, `PSF SNR >=`) has a tick box and a limit. An untouched limit is
   automatic: greyed and in italics, it shows the cut `k` gives on this night. Type a number to replace it for that
   metric only (clear it to go back to automatic); it stays when the preset or `k` changes. Untick a criterion to switch
   it off. The dialog shows how many frames Run will keep.
5. Review: the **table** (one row per frame, named by what differs, the full path as its tooltip, a red cross on frames
   Run leaves out and the rejecting measurement in red), the **plot** of the channel's measurement in frame order with
   the keep band (click a point to select its row), the **filmstrip** of thumbnails (selected one highlighted, rejected
   ones crossed, a chooser for how many are shown, grey tiles not read yet, `!` for unreadable), and a **1:1 preview**
   (double click for the whole frame, drag, scroll bars or arrow keys, Shift for a page; the view stays put when the
   frame changes). **Override** a verdict with Space or the button under the preview: it survives a change of `k`, is
   counted separately and logged, and lasts for the session.
6. **Anomaly tags** mark frames that look wrong against the rest of their channel: A (altitude), F (focus), S (seeing),
   T (tracking), C (cloud), D (dropped). They follow the PixInsight rules and thresholds, are advisory, never change a
   verdict, and show on the preview, as letters on the filmstrip and in the summary line.
7. **Run** either deletes in place or copies:
   - Delete in place is the default and the only irreversible action. A confirmation gives the exact count per channel,
     and the list is written to a log before any file is removed; if the log cannot be written nothing is deleted. A
     frame that changed since it was measured (checked by content digest), vanished or became unreadable is skipped and
     reported. Kept frames are left exactly as they are.
   - Copy to another folder writes the approved frames there as FITS and leaves the originals alone. The destination
     folder is emptied first, without asking, hidden files and subfolders included (a symbolic link is removed, never
     followed); if anything in it cannot be removed, nothing is copied. Run refuses a destination that is or contains a
     source folder, its parent, the filesystem root or your home folder. A channel switched off is not copied.

### Importing from an ASIAIR

The script recognises an ASIAIR card by its `Plan/Light` or `Autorun/Light` folders, whatever the volume is called, and
offers it before the folder chooser. Cancel stops the card read. Every target is listed with its last three nights:
frames, filters, and flats filter by filter, a filter with no flats in red. A night is one target in one session, and a
session ends at a gap of more than four hours. Flats are matched to a night as a whole batch, by filter and binning, and
by camera and rotation where both state them.

Review the night as usual, then Run. Choose a destination: nothing is ever written to the ASIAIR, and a folder named
`Light` or `Flat` means its parent. Approved lights go to `<destination>/Light` and the night's flats to
`<destination>/Flat`, as FITS; two frames that would get the same name are refused rather than overwritten. Each written
file is reopened and checked (content digest, geometry and the required header keywords); one that fails is deleted and
reported. The review is then locked, so the night cannot be imported twice. Darks and bias frames are not imported.

**Master flats instead of raw flats.** Set a darks folder and tick **Combine flats to masters** (both are remembered)
and the night's flats become one master per filter instead of being kept. Darks on the card itself (`Plan/Dark` or
`Autorun/Dark`) are preferred when they suit, and then no darks folder is needed. A dark suits a flat when its exposure
is within 1 %, its temperature within 3 degC (a frame with no temperature never matches), and binning and gain agree
where both state them. A master dark is preferred; otherwise at least two raw darks are integrated and the result is
cached. The calibrated flats are integrated with multiplicative normalisation and rejection suited to the count, using
Siril's own calibration and stacking, and written to `<destination>/Flat/masterFlat_<filter>.fit` (`_bin1` or `_bin2` is
added when a filter was shot at two binnings; a master from an earlier import is never overwritten, the new one takes
`_2`). The master is checked before the raw flat copies are deleted, and only those copies are deleted. A filter with no
matching dark, fewer than two flats, or a failed master keeps its raw flats, and the closing message says which and why.
Cancel is checked between steps; if the master-flat step fails, the closing message says which masters were finished and
where the raw flats were kept. Cancelling the copy stops it after the file being written. Every import writes a log with
the copies and any failures and, for master flats, the flats read, the darks found and the plan for each filter; its
path is shown in the final message.

### What is remembered, and where

- Options are saved in one file in Siril's configuration folder (`loomsiril-frame-selector.json`): the source and
  destination folders, the darks folder, the Combine flats box, the preset, `k` (only when you typed it; otherwise the
  preset decides it next time), the score weights, and the criteria with any typed limits.
- Measurements and file digests are cached in a `frame-selector` folder inside the Loom cache folder (the same one the
  Loom pipeline uses; set `cacheDir` to move it), so reopening a folder is quick and a changed frame is measured again.
  Previews, the work folders of a measurement or a master flat, and cached master darks are kept there too.
- Deletion logs are written to `~/Siril/Loom-frame-selector/` before any file is removed. Import logs go to the system
  temporary folder, in `Loom-import-logs`.

### Differences from the PixInsight Frame Selector

[docs/frame-selector-parity.md](docs/frame-selector-parity.md) lists every PixInsight function with the Python function
that ports it and every deviation with its reason. In short:

- **Measurements come from Siril** (its star registration and image statistics, the numbers its Plot tab shows), not
  from SubframeSelector. PSF SNR becomes the median star amplitude over the background noise; eccentricity is
  derived from Siril's roundness; the SNR estimate is dropped; the star count is capped near 2000 by Siril.
- **Altitude comes from the header** (`AIRMASS`, `CENTALT`, `OBJCTALT`, else computed from the coordinates, time and
  site).
- **Copies and imports are FITS**, not XISF, and an import is verified by digest. Master flats are built by Siril and
  named `.fit`; Siril has no "equalize fluxes" normalisation, so rejection is not flux-equalised.
- **Delete in place does not convert the kept frames** to another format.
- **More options are remembered**, and the preset and a hand-typed `k` are remembered across runs.
- **The deletion log folder** is `~/Siril/Loom-frame-selector/`, and the script needs Siril 1.4 instead of a PixInsight
  core version.
- The star finder's settings are reset for each measurement and, apart from the focal length and pixel size, not
  restored afterwards. If Siril cannot report its focal length and pixel size, no reset is sent: the current settings
  are used and a warning is logged.
- During a master-flat run Siril's FITS compression is switched off and restored afterwards. The master's header is
  rewritten after stacking (`IMAGETYP` Master Flat, `EXPTIME` only when every flat shares one, `DATE-OBS` dropped).

## Install

### In Siril 1.4 (Python scripting)

1. Clone this repository:

   ```
   git clone https://github.com/fcarucci/Loom
   ```

2. In Siril, open Preferences, Scripts. Add the clone's `Loom` folder (for example `~/dev/Loom/siril/Loom`) on a
   new line of the script folders, click the refresh button, then Apply. Siril names the submenu after the folder:
   `loom_preprocess.py` (the pipeline), `loom_fly_through.py` (Fly-Through) and `loom_frame_selector.py` (Frame
   Selector) appear under Scripts, Loom.
3. Run one of them. The script finds the `loomsiril` package in the clone it sits in, so no environment variable
   is needed. On first run it installs `numpy`, `scipy` and `tifffile` (and `ttkthemes` for the Frame Selector) into Siril's
   Python environment; that step needs the network.

To update, run `git pull` in the clone and restart the script. Siril only syncs its own scripts repository; it does
not pull this one.

`LOOMSIRIL_SRC` still works: when it is set, the checkout it names comes first.

### Command line

```
pip install git+https://github.com/fcarucci/Loom-Siril
python -m loomsiril --help
```

Python 3.10 or newer. Dependencies: `numpy`, `scipy` and `tifffile`. `ffmpeg` is
optional: without it the program writes the frames and prints the exact
`ffmpeg` command to run.

## Command line

```
python -m loomsiril --starless S --stars T [--image I] (--wcs FITS | --solution a.json) \
    (--target "ra,dec" | --target-pixel x,y) [--type nebula|galaxy] [--distance pc] \
    [--gaia cache|online|tsv:PATH] [--preset id[,id..]] [--duration 8] [--fps 30] --out DIR \
    [--video] [--ffmpeg PATH] [--quality high|medium] [--star-quality highest|high|medium] \
    [--dynamic sdr|hdr] [--loop none|pingpong|crossfade] [--orientation landscape|portrait]
```

- `--starless` and `--stars` are the two layers of the image (FITS or TIFF).
- `--wcs FITS` reads the TAN keywords of a plate-solved header; `--solution` reads
  a solution JSON.
- `--target` is the sky position in degrees; `--target-pixel` is a pixel of the
  image. Without either, the header centre is used, else the image centre. The RA is
  taken modulo 360 for the Gaia query (the same sky point); a target with a NaN or
  infinite coordinate, or |dec| > 90, is refused with an error.
- `--gaia cache` uses the local cache, `online` queries VizieR (refused under
  pytest), `tsv:PATH` reads a Gaia TSV with the columns `ra dec plx pmra pmdec G BP RP`.
- `--video` encodes with `ffmpeg` once the frames are written.

Worked example, with the IC 1396 Gaia extract that the tests ship with:

```
python -m loomsiril --starless ic1396_starless.tif --stars ic1396_stars.tif \
    --wcs ic1396_solved.fits --target "324.745,57.514" --type nebula --distance 900 \
    --gaia tsv:tests/fixtures/ic1396-gaia-g16.tsv --duration 8 --fps 30 \
    --out out/ic1396 --video
```

Exit status 0 on success; non-zero with a one-line error otherwise.

## Tests and Docker

```
scripts/test.sh path/to/test_file.py     # venv run, pure Python
scripts/test.sh --docker                 # full suite in the Docker image
```

The full suite runs in `docker/Dockerfile`: Python 3.12 on
`python:3.12-slim-trixie`, ffmpeg, the Siril 1.4.4 AppImage (extracted, with
`siril-cli` linked to its AppRun) and the StarNet2 CLI when it is available.
`docker/run-tests.sh` builds the image and runs pytest in it; `DOCKER_HOST` is
honoured. Build arguments:

- `SIRIL_APPIMAGE_URL` and `SIRIL_APPIMAGE_SHA256`: the default is the Siril 1.4.4
  x86_64 AppImage from free-astro.org, pinned by sha256.
- `STARNET_URL` and `STARNET_SHA256`: the StarNet2 2.6.2 Linux CLI zip from the official
  download host, pinned by sha256; the build fails on a mismatch. They are the defaults for a local build. CI
  overrides both to empty (see the StarNet2 section).
  To build without StarNet, pass both empty (`--build-arg STARNET_URL= --build-arg STARNET_SHA256=`).
  Without a download, the image uses the gitignored `docker/vendor/starnet/` folder (see the
  README there), which holds the same files.

Test markers: `siril` (needs `siril-cli`), `starnet` (needs the StarNet2 CLI),
`ffmpeg` (needs `ffmpeg` on the PATH) and `perf` (timing budget, enabled by
`LOOMSIRIL_PERF=1` in the image). A test whose tool is missing skips with the
tool's name as the reason. Images built here are for local tests only and are
never pushed.

## StarNet2

Star removal in "Run StarNet" mode uses StarNet2, a separate program by StarNet Astro.
Loom never bundles, downloads for users or redistributes it.

- Minimum version: **2.6.2**. Older builds cannot read FITS, which Loom and the Fly-Through use to hand StarNet2 its
  image. An older (or unreadable) `starnet2 --version` is refused with a warning, shown in the Tools section and in
  the Fly-Through tool line, and StarNet2 is not offered until it is updated.

- Official download: https://download.starnetastro.com/starnet2_linux_2.6.2-0242_ORT_x64_cli.zip
  (sha256 `066a7683f0321ae8400d3bc1023a48747ac228e1d14e76b5d39c4e48cbce4920`). Documentation:
  https://starnetastro.com/documentation/starnet/command-line-tool/
- Install: unpack the zip and keep `starnet2`, `StarNet2_weights.onnx`, `lib/`, `LICENSE.txt` together, or
  install as the zip's manual layout describes. Then set `LOOMSIRIL_STARNET` to the `starnet2` executable,
  or put it on `PATH`.
- Licence (revision 2026-09-07, full text in `LICENSE.txt` next to the binary): integrations may run the
  CLI as a separate process if they name StarNet, show its licence and get the user's acceptance; downloads
  only from official sources; no redistribution, bundling or mirroring; no use of the software or its outputs
  to develop, test or validate other software (integration excepted); no training.
- Acceptance: the Siril script shows the licence and needs a tick box before StarNet mode runs. The accepted
  revision is kept in the saved options, so it is asked again only when the licence changes.
- Loom calls `starnet2 --input <in.fits> --output <starless.fits>` with float32 FITS. It checks
  `starnet2 --version` first. The stars layer comes from Loom's own unscreen (`Sky.splitStars`), not from
  StarNet's `--unscreen`.
- Docker test image: the build downloads the pinned zip and installs it under `/opt/starnet`. The image is for
  local tests only and is never pushed or published.
- Time limit: one StarNet run may take at most one hour (3600 s). A run that goes longer is stopped and reported as
  an error. The timeout is `siril_bridge.STARNET_TIMEOUT_S`.
- Temporary files: the FITS files for a run are written to a private temporary folder that is removed after the run,
  whether it succeeds, fails or the render is cancelled.
- GitHub Actions: the `docker` job builds the image with both `STARNET_URL` and `STARNET_SHA256` empty, so CI never
  downloads StarNet2 and its `starnet` tests skip. Running them needs a local build with the defaults (or the vendor
  folder), by a person who has read and accepted the licence.

## Limits (v1)

- No NGC/IC object lookup: give the target with `--target` (or `--target-pixel`)
  and the type with `--type`.
- No logo overlay.
- No plate solving: Siril's `platesolve` or another solver gives the WCS, read
  with `--wcs` or `--solution`.
- No linear-image stretch: the CLI warns on linear input; in Siril, run the
  autostretch first.
- Gaia comes online from VizieR (with a local cache) or from a TSV file. The
  PixInsight Gaia process has no equivalent in Siril's Python API.
- Star detection is a numpy replacement for PixInsight's StarDetector. On a crowded,
  noisy one-channel stars layer it misses some faint stars (its noise estimate is
  biased high by star light), and a three-channel frame with an exactly-zero border of
  about a quarter of the width or more loses some stars near 5 sigma. See
  [docs/PARITY.md](docs/PARITY.md).

## Performance

Measured in the Docker image on the test host (2026-10-10, the `perf`-marked tests):

- One 640x360 frame of the synthetic test scene (30 moving stars, star quality
  Highest) renders in about 0.03 s (`tests/test_frame.py::test_perf_640x360`, budget 2 s).
- One 1920x1080 three-channel frame (20 stars) at t = 0.5 renders in about 0.4 s
  (`test_perf_1080p_qa_scene`, budget 6 s).
- Building the scene (star detection, star models, deblending) is a one-off cost per
  image: about 2.4 s for a noisy one-channel 1920x1080 stars layer
  (`tests/test_detect.py::test_noisy_mono_1080p_sprites_build_fast`) and about 8 s for
  a whole one-channel 1080p pipeline run with a noiseless layer
  (`test_pipeline_build_1080p_mono_zero_noise_completes_fast`).

## Test data

Fixtures in this repository must be synthetic or public catalogue data. Never
commit a real frame.

## Licence

This repository is MIT, see [LICENSE](LICENSE).

`sirilpy` is part of Siril and is GPL-3.0-or-later. It is imported only at run
time inside Siril and is not redistributed here.

StarNet2 is distributed by its author under a licence that forbids redistribution.
It is not in this repository and not in any published image. Users fetch it
themselves; the Docker build downloads it only for local tests.

### Data attribution

This work has made use of data from the European Space Agency (ESA) mission Gaia
(https://www.cosmos.esa.int/gaia), processed by the Gaia Data Processing and
Analysis Consortium (DPAC, https://www.cosmos.esa.int/web/gaia/dpac/consortium).
Funding for the DPAC has been provided by national institutions, in particular the
institutions participating in the Gaia Multilateral Agreement. Gaia DR3: Gaia
Collaboration, Vallenari et al. (2023), A&A 674, A1.

This research has made use of the VizieR catalogue access tool, CDS, Strasbourg,
France.
