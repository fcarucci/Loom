# Loom

Loom takes integrated narrowband and broadband masters to finished plates, and turns a finished image into a push-in
video whose stars pass by at their real Gaia distances. It comes in two ports of the same pipeline:

| Folder | For | Install and use |
|---|---|---|
| [`pixinsight/`](pixinsight/README.md) | PixInsight (Loom, Frame Selector, Fly-Through) | the PixInsight update repository `https://fcarucci.github.io/Loom/`, or a git checkout (one of the two, not both); see below; releases are tagged `vX.Y.Z` |
| [`siril/`](siril/README.md) | Siril 1.4 (Loom pipeline and Fly-Through as Python scripts) | a checkout of this repository, scripts under `siril/Loom/`; released together with the PixInsight scripts, under one `vX.Y.Z` tag |

The two are meant to run comparable pipelines: the Siril port is a port of the PixInsight JavaScript to numpy and
scipy (see `siril/docs/PARITY-LOOM.md`), with Siril's own tools where PixInsight's processes have no equivalent.

## Loom for PixInsight

Three PixInsight scripts for the work between capture and making pictures:

- **Loom** takes integrated masters to finished plates: solving, flux calibration, gradient removal, registration,
  cropping, combination and colour calibration, optionally followed by sharpening, star extraction, stretch, noise
  reduction and a layered Photoshop file.
- **Frame Selector** measures a folder of subframes and removes the bad ones before you integrate. It can also import a
  night straight from an ASIAIR.
- **Fly-Through** turns a finished image into a push-in video whose stars pass by at their real Gaia distances.

Loom is for people who already know PixInsight. It needs PixInsight 1.9.4 or later, on macOS or Windows.

### Install

Install from the update repository or from git, not both: two copies register the scripts twice.

1. **From the PixInsight update repository.** In PixInsight, open Resources, Updates, Manage Repositories, add
   `https://fcarucci.github.io/Loom/` (with the trailing `/`), then run Resources, Updates, Check for Updates and apply
   the Loom update. PixInsight restarts to install it. The repository is signed by the Certified PixInsight Developer
   Francesco Carucci. The scripts appear under Script, Loom; if they do not, run Script, Feature Scripts, Regenerate.
   Later updates come through Check for Updates again.
2. **From git.** Clone `https://github.com/fcarucci/Loom` (`git@github.com:fcarucci/Loom.git`), then use Script,
   Feature Scripts, Add and choose the clone's `pixinsight` folder (not the clone itself, and not the `script`
   folder inside it). A git checkout can update itself.

Full documentation, every option and the details of each script: [pixinsight/README.md](pixinsight/README.md).

## Experimental: Loom for Siril

> **Experimental.** The Siril port is young. It has run the whole pipeline end to end on real masters (Sh2-132, H, S, O,
> L, R, G and B), but it has had far less use than the PixInsight scripts, and parts of it have not been exercised in
> Siril's own user interface. Expect rough edges, and check the plates against your data.

What it is: the Loom pipeline and Fly-Through as Siril 1.4 Python scripts (`siril/Loom/loom_preprocess.py` and
`siril/Loom/loom_fly_through.py`), written in numpy and scipy from the PixInsight JavaScript. Plate solving and
colour calibration (SPCC) run in Siril. Gradient removal, aberration correction, sharpening, star extraction and noise
reduction use external tools when they are installed: GraXpert, SyQon Studio, StarNet2, and the RC-Astro command-line
tools, which need their own licence.

How it differs from the PixInsight scripts:

- **Stretch.** There is no MultiscaleAdaptiveStretch in Siril. The histogram stretch aims at a sky level of 0.15 by
  default (`stretchTarget`, which is the level MultiscaleAdaptiveStretch gives); PixInsight's own histogram stretch uses
  0.25.
- **Narrowband normalisation.** PixInsight's NarrowbandNormalization is a closed process. The Siril port only brings the
  three channels to one sky level and keeps the line ratios of the nebula.
- **Tools.** PixInsight processes with no Siril equivalent are replaced by the tools above. When none is installed,
  that step is skipped or done by Siril itself.
- **Parity.** [siril/docs/PARITY-LOOM.md](siril/docs/PARITY-LOOM.md) lists every ported function and each deviation.

To try it, clone this repository, add `siril/Loom` to Siril's script folders (Preferences, Scripts), refresh, and run
`loom_preprocess.py` from Scripts, Loom. The details, the options and the headless job file are in
[siril/README.md](siril/README.md). It is not signed and not part of the PixInsight update repository. Please report
what breaks.

Licence: MIT, see [LICENSE](LICENSE).
