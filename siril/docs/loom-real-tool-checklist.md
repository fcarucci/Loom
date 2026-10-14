# Real-tool checklist

This checklist is run by hand on one macOS machine and one Windows machine. Each machine needs:

- Siril 1.4.x installed and started at least once.
- The vendor tools Loom uses, installed and licensed: RC-Astro (`rc-astro`), SyQon Studio (`syqon-cli`), SyQon Starless,
  Prism and Parallax CLIs, StarNet2, and GraXpert-AI (the script, run from Siril).
- A way to open a PSB file (Adobe Photoshop) for item 13, and PixInsight for item 14.

Nothing from a licensed vendor tool (RC-Astro, SyQon, StarNet, GraXpert) is committed to the repository. Help text, JSON
schemas and logs captured here stay in your own notes. Only the result rows (OS, tool version, result, date) go into this
document when you share it. One exception: the narrowband normalisation reference pair in item 14, whose input is synthetic.

## How to fill the table

For each item, fill one row per machine:

| OS | Tool version | Result (pass, fail, or the observed value) | Date |
|----|--------------|--------------------------------------------|------|
|    |              |                                            |      |

Use `pass` or `fail` for yes/no items. For a numeric or text result, write the observed value. Write `not run` with the
reason when an item cannot be done on a machine.

## Automated part: `vendor` tests

Items 1, 2, 4 and 5 have automated versions in `tests/loom/vendor/test_real_tools.py`. They are marked `vendor` and
are skipped unless `LOOMSIRIL_VENDOR=1` is set. Run them from the repository root:

```
LOOMSIRIL_VENDOR=1 python -m pytest -q -m vendor tests/loom/vendor/test_real_tools.py
```

Set these variables when they apply (see the top of the test file):

- `LOOMSIRIL_VENDOR_TOOLS`: comma-separated tool ids that are installed, for example `rcastro,syqon-cli`.
- `LOOMSIRIL_VENDOR_LOCATE`: one executable, as the dialog's Locate... button would give it (item 1).
- `LOOMSIRIL_VENDOR_APP_BUNDLE`: the SyQon Studio `.app` bundle on macOS (item 1).
- `LOOMSIRIL_VENDOR_SYQON_MODEL`: a SyQon Studio model the account is entitled to (item 4; default `axiom`).
- `LOOMSIRIL_VENDOR_UNLICENSED=1`: set on a machine without RC-Astro activation (item 5).
- `LOOMSIRIL_VENDOR_UNENTITLED_MODEL`: a SyQon Studio model the account is not entitled to (item 5).

A skipped test names the missing input. A failing test prints the rejection reason from the resolver.

---

### 1. Discovery finds each installed tool

What to do:

1. On each machine, start Siril and open the tool dialog. Confirm that every installed tool is listed with its path.
2. Remove one tool's configured path and its environment variable, then confirm that Loom finds it at its default
   location: `/Applications/...` on macOS, `%ProgramFiles%` or `%LOCALAPPDATA%\Programs` on Windows.
3. On macOS, set the path to the SyQon Studio `.app` bundle. Loom must resolve it to the executable inside
   `Contents/MacOS`.
4. On Windows, confirm the tool is found through its App Paths registration (`HKCU`, then `HKLM`, under
   `App Paths`) when it is not at a default location.
5. Use Locate... for one tool and pick its executable by hand.

Expected: each installed tool is found by its route, and the dialog shows the source it came from. The `vendor` test
`test_item1_*` passes for the routes that apply.

| OS | Tool version | Result | Date |
|----|--------------|--------|------|
|    |              |        |      |
|    |              |        |      |

### 2. Each probe answers and returns

What to do: run each command from a terminal and note the exit code and the first lines of output.

- `rc-astro bxt --json`, `rc-astro nxt --json`, `rc-astro sxt --json`
- `syqon-cli --version` and `syqon-cli --list-models`
- `SyQonStarless --help`
- `prism_cli --help`
- `parallax_cli --help`
- `starnet2 --version`

Expected: every command returns within a few seconds, exits, and does not open a window or wait for input. The
`vendor` test `test_item2_*` checks that the resolver accepts each probe.

| OS | Tool version | Result | Date |
|----|--------------|--------|------|
|    |              |        |      |
|    |              |        |      |

### 3. Unverified flags and exit codes

What to do:

1. Read the `rc-astro nxt --json` schema. Record the flag name of the detail parameter. Loom uses a flag-bearing
   parameter named exactly `detail`; if there is none, the name must contain `detail` and be the only candidate.
   Record which name the schema gives.
2. Run one SyQon Studio FITS round trip: write a 32-bit float FITS plane, run it through `syqon-cli`, and read the
   result back. Record the data type of the output and whether the values match within float32 precision.
3. Record the exit code of each legacy probe from item 2 (`SyQonStarless --help`, `prism_cli --help`,
   `parallax_cli --help`, `starnet2 --version`).
4. Save the `--help` and `--json` text in your own notes. Do not add it to the repository.

Expected: the detail flag name is recorded; the FITS output is float32 with values matching the input; each legacy
probe's exit code is recorded.

| OS | Tool version | Result (detail flag; FITS type; exit codes) | Date |
|----|--------------|----------------------------------------------|------|
|    |              |                                              |      |

### 4. One 512 x 512 synthetic image through every contract

What to do: run the `vendor` test `test_item4_*` on each machine. It writes one 512 x 512 synthetic plate with a
vertical ramp and a few stars, and runs it through RC-Astro (aberration, sharpen, denoise, star removal), StarNet2,
and SyQon Studio.

Expected: every output file exists, has the same number of planes and the same shape, contains only finite values,
and keeps the row order (the row-profile correlation with the input is at least 0.9).

The legacy CLIs are not covered by the vendor test. Run them by hand on the same plate, saved as a 32-bit float FITS:

- SyQonStarless: run the starless step from Loom's dialog (or `SyQonStarless` with the same input and output paths).
  Expected: the output exists, has the same shape, rows in the same order, and only finite values.
- prism_cli: run the Prism step from Loom's dialog with the default settings. Expected: the same four checks.
- parallax_cli: run the Parallax step from Loom's dialog with the default settings. Expected: the same four checks.

Record the three results in the row below.

| OS | Tool version | Result (per contract; SyQonStarless, prism_cli and parallax_cli by hand) | Date |
|----|--------------|--------------------------------------------------------------------------|------|
|    |              |                                                                          |      |

### 5. Refusals: rc-astro exit 77 and SyQon exit 4

What to do:

1. On a machine without RC-Astro activation, run the `vendor` test `test_item5_rcastro_unlicensed_exit_77_is_refused`
   with `LOOMSIRIL_VENDOR_UNLICENSED=1`.
2. On a machine whose SyQon Studio account is not entitled to one model, run
   `test_item5_syqon_unentitled_model_exit_4` with `LOOMSIRIL_VENDOR_UNENTITLED_MODEL` set to that model.

Expected: `rc-astro` exits 77 and Loom refuses the tool with the "not licensed" reason. `syqon-cli` exits 4 for the
model and Loom reports the model as refused, with no output file.

| OS | Tool version | Result (exit code; reason shown) | Date |
|----|--------------|----------------------------------|------|
|    |              |                                  |      |

### 6. macOS Keychain prompt on the first headless SyQon Studio run

What to do: on macOS, after a fresh install or a sign-out, start a headless SyQon Studio run from Siril. Watch for a
Keychain prompt.

Expected: if the tool shows nothing for about 10 seconds, Loom shows its sign-in notice. Record whether a Keychain
prompt appeared, whether it asked for the Mac password, and whether the run completed after you answered it.

| OS | Tool version | Result (prompt seen; password asked; run completed) | Date |
|----|--------------|-----------------------------------------------------|------|
|    |              |                                                     |      |

### 7. Windows: paths, console windows and quoted output arguments

What to do, on Windows only:

1. Use an input folder whose path contains spaces, and run a SyQon Studio or RC-Astro step. Expected: it runs.
2. Run a step while watching the taskbar. Expected: no console window flashes up for any tool.
3. Use a path longer than 260 characters (long paths enabled in Windows). Expected: the step runs and writes its output.
4. Run `link` and `rgbcomp` in Siril with an output folder whose path contains a backslash right before the closing
   quote of the `"-out=<path>"` argument. Use a folder such as `C:\work\out\`. Expected: the sequence is written to that
   folder, and no folder named with a quote character is created.

| OS | Tool version | Result (spaces; no flash; long path; trailing backslash) | Date |
|----|--------------|----------------------------------------------------------|------|
| Windows |  |                                                         |      |

### 8. Windows: cancelling a tool that starts workers

What to do: start a run with `syqon-cli` or `rc-astro` that spawns worker processes. Cancel it from Siril while it is
working. Then open Task Manager and look for the tool's processes and their children.

Expected: no worker process remains after the cancel. Loom first runs `taskkill /PID <pid> /T /F` on the tool's
process, which ends the whole process tree.

| OS | Tool version | Result (workers left after cancel) | Date |
|----|--------------|------------------------------------|------|
| Windows |  |                                    |      |

### 9. Dialog and worker thread in Siril's GUI

What to do: in Siril's GUI, start a long tool run from the Loom dialog. The run executes on a worker thread that sends
Siril commands while the GUI stays live. Then check, in order:

1. Progress text updates during the run (not only at the start and the end).
2. Press Cancel during the tool step. The run stops and Cancel returns within a few seconds.
3. Start a second run straight after the cancelled one. It starts, with no "busy" state and no leftover progress text.
4. Let one run finish. Its result loads into Siril as a new image.
5. Move, resize and click the Siril window during each run.

Expected: no deadlock, no crash, no frozen window at any step. Progress updates, Cancel returns in a few seconds, the
result loads, and a second run starts after a cancel.

| OS | Tool version | Result (progress; cancel within seconds; second run starts; result loads; no freeze) | Date |
|----|--------------|--------------------------------------------------------------------------------------|------|
|    |              |                                                                                      |      |

### 10. GraXpert-AI from the GUI

What to do:

1. Record the folder where Siril's scripts repository copy holds `GraXpert-AI.py`, on macOS and on Windows. Loom looks
   in the data folder's `siril-scripts/processing` folder, then in Siril's own `siril/siril-scripts/processing`
   folder.
2. Run the GraXpert-AI script once from Siril's GUI. Its first run installs packages and the model. Let it finish.
3. Run a Loom background-extraction step. Loom calls the script in the session as
   `pyscript ... -bge -correction=subtraction -smoothing=0.50`.

Expected: the script runs without opening its own window (its `is_cli()` check is True inside the session), and the
GUI stays responsive while it runs.

| OS | Tool version | Result (script folder; first run; in-session call without window; no freeze) | Date |
|----|--------------|-------------------------------------------------------------------------------|------|
|    |              |                                                                               |      |

### 11. Siril's SPCC database location

What to do: find the SPCC database folder that Siril uses on each machine. Record the full path. On Linux the default
is `$XDG_DATA_HOME/siril-spcc-database` or `~/.local/share/siril-spcc-database`. On macOS and Windows it is not yet
verified, so this item is what settles it. Loom's preflight checks this folder.

Expected: a folder that contains the sensor and filter entries Loom asks for. Record the path exactly as shown.

| OS | Tool version | Result (database path) | Date |
|----|--------------|------------------------|------|
|    |              |                        |      |

### 12. Narrowband SPCC after a broadband SPCC in one session

What to do: on a real colour field with narrowband filters, in one Siril session:

1. Run a broadband SPCC with the sensor and filter names (`-monosensor`, `-rfilter`, `-gfilter`, `-bfilter`).
2. Run the narrowband SPCC with the same four names.
3. Record the result. Then, in a new session, run the narrowband SPCC alone with the same names and record that result.

Expected: the two results are equal. Without the four names the narrowband step fails with "Argument error" after a
broadband run, and that is the failure this item checks for.

| OS | Tool version | Result (same session equals fresh session) | Date |
|----|--------------|--------------------------------------------|------|
|    |              |                                            |      |

### 13. Full GUI run: LRGB and SHO, star extraction, stretch, TIFF and PSB

What to do: in Siril's GUI, run Loom on a real LRGB set and on a real SHO set. Use star extraction and stretch. Write
both a TIFF and a PSB output. Open each PSB in Photoshop.

Expected: both runs finish. The TIFF and PSB files open. In Photoshop, the PSB has its layers in the expected order,
the blend modes are set, and the colour profile is attached.

| OS | Tool version | Result (LRGB; SHO; TIFF; PSB layers, blend modes, profile) | Date |
|----|--------------|------------------------------------------------------------|------|
|    |              |                                                            |      |

### 14. PixInsight NarrowbandNormalization reference pair

Only PixInsight is needed for this item. The input is synthetic; the output is the PixInsight result for it.

What to do:

1. Make a three-plane linear palette composite (R, G, B order, as the palette maps them). Use a synthetic input, not a
   real field. Save it as `input.fits`.
2. Run PixInsight's NarrowbandNormalization on `input.fits` with the neutral settings listed in
   `loomsiril/loom/nbnorm.py`. Save the result as `output.fits`.
3. Write the palette name (`HOO`, `SHO` or `HSO`) on one line in `palette.txt`.
4. Copy the three files into `tests/fixtures/nbnorm/` and commit them there.
5. Run `python -m pytest -q tests/loom/test_nbnorm.py`. The reference test must run (not skip) and pass at the tolerance
   of 0.02 per sample.
6. Record the maximum absolute difference. If it is far below 0.02, lower `REFERENCE_TOLERANCE` in
   `tests/loom/test_nbnorm.py` to a value just above it, and commit that change.

Expected: the reference test runs and passes; the maximum difference is recorded.

| OS | Tool version | Result (test ran, not skipped; passed; max difference) | Date |
|----|--------------|--------------------------------------------------------|------|
|    |              |                                                        |      |

### 15. StarNet licence dialog

What to do: in a Siril session with StarNet2 found, and with the StarNet licence not yet accepted on this machine:

1. Start a star-removal step. Expected: the licence dialog opens with the licence text and a link to the StarNet
   documentation. Choose Decline. Expected: nothing runs, and the step reports that the licence was not accepted.
2. Start the step again and choose Accept and continue. Expected: the step runs to completion.
3. Restart Siril and start the step again. Expected: no dialog appears; the acceptance is remembered.
4. On a build whose licence revision is newer than the one accepted, start the step.
   Expected: the dialog appears again.

| OS | Tool version | Result (dialog shown; decline runs nothing; accept runs; remembered after restart; asked again on a new revision) | Date |
|----|--------------|----------------------------------------------------------------------------------------------------------------------|------|
|    |              |                                                                                                                      |      |

### 16. Plate-solve residual with SIP order 3

What to do: on a real field, solve it with Siril's plate solve using the default SIP order 3. Network access is needed
for the Gaia catalogue. Then run Loom's residual check on the solve.

1. Record the median residual in pixels that Loom reports. Expected: the verdict is `ok`; the residual is well below the
   poor threshold of 0.315 px.
2. Record the same field with SIP not applied. Expected: the verdict reads `poor` or `bad`.

| OS | Tool version | Result (SIP 3 residual px; verdict ok; no-SIP residual px and verdict) | Date |
|----|--------------|-----------------------------------------------------------------------|------|
|    |              |                                                                       |      |
