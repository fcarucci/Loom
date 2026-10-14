# Loom-Siril

Loom's tools for [Siril](https://siril.org), starting with **Fly-Through**:
a push-in video of a finished astrophoto whose stars move at their real Gaia
distances.

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
- Version 0.5.0 (tag `v0.5.0`, shared with the PixInsight scripts) is the first release and is experimental.

## What it does

Fly-Through takes a finished image (its starless layer and its stars) and a
target on the sky. It places the stars with their Gaia parallax, so nearer stars
drift past the camera faster than distant ones. It renders 16-bit TIFF frames
with an sRGB or HDR output transform and, when ffmpeg is on the PATH, encodes
them to a video.

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

## Install

### In Siril 1.4 (Python scripting)

1. Clone this repository:

   ```
   git clone https://github.com/fcarucci/Loom
   ```

2. In Siril, open Preferences, Scripts. Add the clone's `Loom` folder (for example `~/dev/Loom/siril/Loom`) on a
   new line of the script folders, click the refresh button, then Apply. Siril names the submenu after the folder:
   `loom_preprocess.py` (the pipeline) and `loom_fly_through.py` (Fly-Through) appear under Scripts, Loom.
3. Run one of them. The script finds the `loomsiril` package in the clone it sits in, so no environment variable
   is needed. On first run it installs `numpy`, `scipy` and `tifffile` into Siril's Python environment; that step
   needs the network.

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
