# Loom Fly-Through for Siril (script)

`loom_fly_through.py` is a thin Siril 1.4 Python script. It takes the plate-solved image loaded in Siril and renders
the Fly-Through frames (and optionally the video) with the `loomsiril` package in this repository.

**The script has NOT been run inside Siril.** It is tested against a stand-in `sirilpy` only
(`tests/sirilpy_stub`, mirroring the signatures of the Siril 1.4.4 Python module). The dialog, the Siril calls and the
window layout are unverified until someone runs it in Siril 1.4.4.

## Install by hand

1. Siril 1.4 with Python scripting. Siril installs `numpy` and `tifffile` for the script on first run.
2. Clone this repository and add its `Loom` folder to Siril's script folders (Preferences, Scripts: add the path
   on a new line, refresh, Apply). Run the script from the Scripts menu. It imports `loomsiril` from the clone it
   sits in. To update, `git pull` in the clone.
   Other ways, used first when they apply: the environment variable `LOOMSIRIL_SRC` set to a checkout before
   starting Siril, or the package installed into Siril's Python environment (`pip install .`). A copy of the
   script outside a checkout needs one of them.
4. Optional, for "Run StarNet": install the StarNet2 command-line tool yourself (official download; see the StarNet2
   section of the top-level README) and set `LOOMSIRIL_STARNET` to its `starnet2` executable, or put it on `PATH`.
   StarNet2 is not redistributed with this project. The script shows its licence and asks you to accept it the first
   time you choose "Run StarNet".

## Use

Load a plate-solved image (the header needs the CRVAL, CRPIX and CD keywords). Run the script. Choose either the
starless and stars files (TIFF or FITS, top-down, same size and channel count as the image) or "Run StarNet". Check the
target, duration, fps, presets and output folder, then Render. The result box lists the videos, or the ffmpeg command
when ffmpeg is not found.

## Loom (loom_preprocess.py)

`loom_preprocess.py` is the Loom pipeline script: masters in, finished plates out. The top-level README has the full
description; this section covers the script itself.

### Install

1. Siril 1.4 with Python scripting.
2. Add the clone's `Loom` folder to Siril's script folders, as above. `loom_preprocess.py` finds `loomsiril` in that clone.

### Dependencies

`numpy`, `scipy` and `tifffile`. On first run the script installs the ones that are missing, with Siril's
`ensure_installed`, into Siril's Python environment; that step needs the network. Optional external tools are listed
in the top-level README (Loom section).

### Dialog

Run `loom_preprocess.py` from Siril's Scripts menu. The options dialog opens. Choose the masters (one file per filter), check
the options and the output folder, then run. The results are written to the output folder. The options are saved in
Siril's configuration folder (`loomsiril/loom.json`) whenever the dialog closes, the output folder included.

### Headless job

Under `siril-cli` the script reads a job file and opens no window:

```
printf 'requires 1.4.0\npyscript <path>/Loom/loom_preprocess.py --job job.json\n' | siril-cli -d <dir> -s -
```

The job file, its keys and the status file are described in [docs/loom-job.md](../docs/loom-job.md). Read the
status file for the outcome: `siril-cli` does not report Python errors in its exit status. A headless run starts
from Loom's defaults plus the job file, and never writes the options file.

## Limits

- Pixel data from Siril is bottom-up (FITS order) and is flipped once to the top-down order the pipeline uses; the
  WCS and the detected stars are converted to match. See `docs/parity/e7.md`.
- Siril's `starnet` command is legacy; the script calls the StarNet2 command-line tool directly (`starnet2 --input
  --output`, float32 FITS).
- Frames are written as 16-bit TIFF into the chosen folder; the options of the last run are kept in Siril's
  configuration folder as `loomsiril-fly.json`.
