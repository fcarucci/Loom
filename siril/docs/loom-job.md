# Loom job file (headless mode)

`Loom/loom_preprocess.py` runs in two modes. In Siril's GUI it opens the options dialog. Under `siril-cli` (headless mode) it
reads a job file and opens no window. The job file is a JSON object. Its keys are the options of the dialog, named as
Loom names them, plus a few keys the script reads itself.

Run it with siril-cli:

```
printf 'requires 1.4.0\npyscript /path/to/clone/siril/Loom/loom_preprocess.py --job /path/to/job.json\n' \
  | siril-cli -d /path/to/workdir -s -
```

The script needs `numpy`, `scipy` and `tifffile` (it installs them into Siril's Python environment when they are
missing) and the `loomsiril` package: run the script from a checkout of this repository
(it finds the package there), set `LOOMSIRIL_SRC` to a checkout, or install the package.

## Keys

| Key | Meaning |
| --- | --- |
| `outputDir` | Required. The output folder; it is created when missing. A job never writes the options file. |
| `paths` | Channel to file path: `L`, `R`, `G`, `B`, `H`, `S`, `O`. Empty text or a missing key means no file. |
| `filters` | SPCC filter names for `R`, `G` and `B` (names in Siril's SPCC database). |
| `tools` | Optional. Tool id to the configured executable path, tried before the automatic search. |
| `starnetLicenceAccepted` | Optional. The StarNet2 licence revision you accept, exactly as the script's current revision (the value of `STARNET_LICENCE_REV` in `loomsiril/starnet_licence.py`). Any other value, or none, refuses StarNet2 (a preflight problem). In the dialog the acceptance is kept in the tools store instead; a job never reads it. |
| `statusFile` | Optional. Where the status file is written (default: the job path plus `.status.json`). |
| every other key | An option, with Loom's name and value type (see `loomsiril/loom/config.py`). Unknown keys are ignored with a log line. |

A job is self-contained. Its options start from Loom's defaults, not from the options file the dialog saved: a key
the job does not give takes its default (the `paths` and `filters` of the saved file are not inherited). Job values go
through the same type checks and migrations as a saved file: an old tool name is migrated, and a value of the wrong
type is replaced by the default with a log line.

A headless run never writes the options file, so it leaves the dialog's saved selection alone. The tools store (the
locations of tools already found) may still be updated in Siril's configuration folder, as the dialog does.

Run flags (`validateOnly`, `ignoreCache`, `keepWindowsOnError`) are true or false.

`validateOnly: true` runs the checks and reports the file selection, then stops with exit code 0. Nothing is executed.
Run flags are read from the job only; a job that omits them gets false.

Example (an RGB run with the gradient step off and the stretch on):

```json
{
  "paths": {"L": "", "R": "/data/R.fits", "G": "/data/G.fits", "B": "/data/B.fits", "H": "", "S": "", "O": ""},
  "filters": {"R": "Baader R", "G": "Baader G", "B": "Baader B"},
  "gradientTool": "none",
  "stretch": true,
  "outputDir": "/data/out",
  "cacheDir": "/data/cache"
}
```

## Status file

Every headless run writes the status file, whatever happened (atomic write). siril-cli's own exit status does not
reflect Python errors, so read the status file for the outcome.

```json
{
  "ok": true,
  "exitCode": 0,
  "problems": [],
  "produced": ["RGB"],
  "outputs": {"RGB": "/data/out/RGB.fits"},
  "log": "/data/cache/logs/loom-run-<stamp>.log",
  "error": null
}
```

| `exitCode` | Meaning |
| --- | --- |
| 0 | The run finished (or the validate-only check passed). `ok` is true. |
| 1 | A run error, or the job file could not be read. `error` holds the text. |
| 2 | Preflight found problems. Each one is in `problems`, and the run did not start. |
| 130 | The run was cancelled, or the job was interrupted. |

The status file is written whenever the job path is known. A failure before the run starts (the Siril command, the
package install, the imports) is exit code 1 with the error text. When `--job` is missing, no status file can be
written: the script logs one line and stops. When the job file cannot be read or is not a JSON object, the status
file is written beside it as `<job path>.status.json`, with exit code 1, because the job cannot name another path.

`log` is the path of the run's log file only for exit code 0. For every other exit code it is an empty string: the
reasons are in `problems` (exit code 2) or in `error` (exit codes 1 and 130).

Log lines are kept short (300 characters at most); the full run log is the file named in `log`.
