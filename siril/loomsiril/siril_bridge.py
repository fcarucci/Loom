"""The Siril bridge: pure helpers between Siril's in-memory image and the loomsiril pipeline (E7).

Everything here is testable without Siril: the script (Loom/loom_fly_through.py) reads Siril's pixels, header,
star list and ICC profile through sirilpy and hands them to these functions. Nothing here imports sirilpy.

Row order convention: everything inside loomsiril is TOP-DOWN (array row 0 is the top of the
displayed image). Siril's pixel data is bottom-up (FITS order, confirmed on siril-cli 1.4.4), so
``planes_from_pixeldata`` flips the planes ONCE and ``top_down_wcs`` converts the WCS with ``wcs.flip_rows``. Siril's
PSF star positions are already measured from the top of its display (edge convention), so they need no flip, only the
half-pixel shift. Siril's header has no CD keys (CDELT + PC instead), converted here before anything reads the WCS.
StarNet and PixInsight-made TIFF layers are top-down already and are never flipped.

Ported pieces of Loom: Sky.splitStars (Sky.js 543-567) -> ``split_stars``; Sky.keywordWcs -> ``wcs.keyword_wcs``;
Fly.headerCentre -> ``presets.header_centre``. The StarNet step is new (Siril's ``starnet`` command is legacy).
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Optional, Sequence

import numpy as np

from loomsiril import core, io_fits, io_tiff, presets
from loomsiril import wcs as wcs_mod
from loomsiril.types import Detection

if TYPE_CHECKING:  # pragma: no cover - typing only; the pipeline is imported lazily (E6 may not be merged yet)
    from loomsiril.pipeline import FlyJob

__all__ = [
    "Bridged",
    "OPTIONS_NAME",
    "SIRIL_PSF_ORIGIN",
    "StarnetError",
    "bridge_image",
    "detections_from_psf",
    "find_starnet",
    "job_from_dialog",
    "load_options",
    "options_path",
    "planes_from_pixeldata",
    "read_layer",
    "run_starnet",
    "starnet_version",
    "save_options",
    "split_stars",
    "starnet_args",
    "target_from_header",
    "top_down_header",
    "top_down_wcs",
    "wcs_from_header",
]

#: Siril's star positions (PSFStar.xpos/ypos) use the pixel-edge convention: xpos = column + 0.5 and ypos = distance
#: from the TOP of the display + 0.5. Measured with siril-cli 1.4.4; no height flip is needed.
SIRIL_PSF_ORIGIN: float = 0.5

#: Environment variable that names the StarNet2 CLI (checked before the PATH names).
STARNET_ENV = "LOOMSIRIL_STARNET"
#: StarNet2 CLI names searched on PATH, in order.
STARNET_NAMES: tuple[str, ...] = ("starnet2", "starnet++")

#: One StarNet run may take at most one hour (3600 s). A run that goes longer is stopped and reported as an error.
#: A 512 x 512 frame takes seconds; the limit only stops a hung process, it is not a speed target.
STARNET_TIMEOUT_S: float = 3600.0

#: File name of the last dialog options, inside Siril's config directory.
OPTIONS_NAME = "loomsiril-fly.json"

#: Dialog keys that are kept between runs (never arrays, never files). ``starnet_licence`` is the licence revision the
#: user accepted (see loomsiril.starnet_licence); it is remembered so the acceptance is asked once per revision.
OPTION_KEYS: tuple[str, ...] = (
    "target_type", "distance", "presets", "duration", "fps", "loop", "star_quality", "dynamic",
    "orientation", "gaia", "video", "out_dir", "object_name", "starnet_licence",
)


class StarnetError(RuntimeError):
    """The StarNet CLI failed or wrote no output."""


@dataclass
class Bridged:
    """Siril's image after the bridge: everything top-down.

    ``header`` is Siril's FITS header with its TAN keywords converted to the top-down rows (CRPIX2, CD1_2, CD2_2),
    so ``wcs_from_header(header) == wcs``. ``detections`` come from Siril's star list (may be empty).
    """

    planes: list[np.ndarray]
    header: dict[str, Any]
    wcs: Optional[dict[str, Any]]
    target: Optional[dict[str, float]]
    detections: list[Detection]
    icc: Optional[bytes]
    height: int
    width: int


def planes_from_pixeldata(arr: Any) -> list[np.ndarray]:
    """Siril's pixel data -> one float32 (h, w) plane per channel, top-down, values in [0, 1].

    Accepts (c, h, w) for colour and (h, w) for mono, uint16 (divided by 65535), uint8 (by 255) or float (as is).
    The rows are flipped once: Siril's row 0 is the bottom of the image (FITS order).
    """
    a = np.asarray(arr)
    if a.ndim == 2:
        a = a[np.newaxis]
    elif a.ndim != 3:
        raise ValueError(f"pixel data must be (h, w) or (c, h, w), got shape {a.shape}")
    if a.dtype == np.uint16:
        scale: Optional[float] = 65535.0
    elif a.dtype == np.uint8:
        scale = 255.0
    elif a.dtype in (np.float32, np.float64):
        scale = None
    else:
        raise ValueError(f"unsupported pixel dtype {a.dtype}")
    planes = []
    for c in range(a.shape[0]):
        p = a[c].astype(np.float64) / scale if scale is not None else a[c]
        planes.append(np.ascontiguousarray(p[::-1], dtype=np.float32))   # bottom-up -> top-down
    return planes


_CD_KEYS = ("CD1_1", "CD1_2", "CD2_1", "CD2_2")
_PC_STYLE = ("PC1_1", "PC1_2", "PC2_1", "PC2_2", "CDELT1", "CDELT2", "CROTA2")


def _with_cd(header: Mapping[str, Any]) -> dict[str, Any]:
    """The header with explicit CD keywords. Siril writes CDELT + PC (and sometimes CROTA2) instead of CD.

    A header that already has CD keys is copied as it is; io_fits.materialise_cd (CDi_j = CDELTi * PCi_j, CROTA2
    fallback) is applied only when no CD key exists. The PC/CDELT/CROTA2 keys are dropped by it.
    """
    out = dict(header)
    if any(k in out for k in _CD_KEYS):
        return out
    if any(k in out for k in _PC_STYLE) or any(k.startswith("PC") for k in out):
        return io_fits.materialise_cd(out)
    return out


def wcs_from_header(header: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """The Fly.js wcs dict from a header's TAN keywords (CD, or CDELT + PC from Siril), or None when one is missing."""
    return wcs_mod.keyword_wcs(_with_cd(header))


def top_down_wcs(header: Mapping[str, Any], height: int) -> Optional[dict[str, Any]]:
    """The header's WCS on top-down rows: ``wcs.flip_rows`` of the FITS (bottom-up) WCS, or None."""
    w = wcs_from_header(header)
    return None if w is None else wcs_mod.flip_rows(w, height)


def top_down_header(header: Mapping[str, Any], height: int) -> dict[str, Any]:
    """A copy of the header whose TAN keywords describe the top-down rows, as explicit CD keywords.

    CRPIX2 and the CD matrix come from :func:`top_down_wcs`; the PC/CDELT/CROTA2 keys are gone, so no stale
    keyword is left that could contradict the CD matrix.
    """
    out = {k: v for k, v in _with_cd(header).items() if k not in _PC_STYLE and not k.startswith("PC")}
    w = top_down_wcs(header, height)
    if w is not None:
        out["CRPIX2"] = w["crpix2"]
        for k, v in zip(_CD_KEYS, w["cd"]):
            out[k] = v
    return out


def target_from_header(header: Mapping[str, Any]) -> Optional[dict[str, float]]:
    """The header centre ``{ra, dec}`` (presets.header_centre, Fly.headerCentre), or None. Row order does not matter."""
    return presets.header_centre(header)


def detections_from_psf(stars: Optional[Sequence[Any]]) -> list[Detection]:
    """Siril's PSF stars -> loomsiril Detections in top-down array coordinates, brightest first.

    Each star: ``xpos`` = column + 0.5 and ``ypos`` = top-down row + 0.5 (both measured on siril-cli 1.4.4), so
    x = xpos - 0.5 and y = ypos - 0.5 with no height flip. ``A`` is the flux. The rect comes from core.detection_rect
    with area pi*(fwhm/2)^2, fwhm = mean of the two axes. Stars with a non-finite position or flux are skipped.
    """
    if not stars:
        return []
    rows = []
    for s in stars:
        x = float(s.xpos) - SIRIL_PSF_ORIGIN
        y = float(s.ypos) - SIRIL_PSF_ORIGIN
        flux = float(s.A)
        fwhm = 0.5 * (float(s.fwhmx) + float(s.fwhmy))
        if not all(math.isfinite(v) for v in (x, y, flux, fwhm)):
            continue
        rows.append((x, y, flux, fwhm))
    rows.sort(key=lambda r: -r[2])                             # brightest first; stable for equal fluxes
    out = []
    for i, (x, y, flux, fwhm) in enumerate(rows):
        rect = core.detection_rect(x, y, math.pi * (fwhm / 2.0) ** 2)
        out.append(Detection(index=i, x=x, y=y, flux=flux, rect=rect))
    return out


def find_starnet(environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """The StarNet2 CLI: $LOOMSIRIL_STARNET if it is an executable file, else starnet2 then starnet++ on PATH."""
    env = os.environ if environ is None else environ
    cand = env.get(STARNET_ENV)
    if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
        return cand
    for name in STARNET_NAMES:
        found = shutil.which(name, path=env.get("PATH"))
        if found:
            return found
    return None


def starnet_args(binary: Any, inp: Any, out: Any) -> list[str]:
    """The StarNet2 CLI command line: ``<binary> --input <in> --output <starless>`` (no shell, no other flags).

    StarNet's own ``--unscreen`` is not requested: the stars layer is made by :func:`split_stars` (Sky.splitStars), the
    same maths the rest of Loom uses, so ``screen(starless, stars)`` gives the image back. StarNet's unscreen output is
    a dimensionless fraction with its own clamps, which would not match that invariant.
    """
    return [str(binary), "--input", str(inp), "--output", str(out)]


def starnet_version(binary: Any) -> str:
    """The first line of ``<binary> --version``. Raises StarnetError when the binary cannot be run or fails."""
    try:
        proc = subprocess.run([os.fspath(binary), "--version"], capture_output=True, text=True, check=False,
                              timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise StarnetError(f"cannot run StarNet at {binary}: {exc}") from exc
    text = (proc.stdout or "").strip()
    if proc.returncode != 0 or not text:
        detail = (proc.stderr or proc.stdout or "").strip()[-400:]
        raise StarnetError(f"StarNet at {binary} failed its --version check (exit {proc.returncode}): {detail}")
    return text.splitlines()[0]


def run_starnet(binary: Any, image_planes: Sequence[np.ndarray], tmp_dir: Any = None,
                timeout: float = STARNET_TIMEOUT_S) -> list[np.ndarray]:
    """Starless planes from the StarNet2 CLI: float32 FITS in, float32 FITS out, top-down planes back.

    The input is the image's planes (values in [0, 1], clipped here: the FITS input must be in that range); the output
    keeps the input's shape and channel count, as the CLI does for 2-D grey and 3-channel RGB inputs. A grey output
    for a colour input is replicated to three planes and a colour output for a grey input is averaged, so a stand-in
    binary with other channel counts still gives a usable result; the real CLI never needs either.

    With ``tmp_dir`` None a private temporary folder is used and removed on return or error. With a folder given, the
    two FITS files written there are removed before returning or raising. ``timeout`` (seconds) stops a hung run.
    """
    if tmp_dir is None:
        with tempfile.TemporaryDirectory(prefix="loomsiril-starnet-") as tmp:
            return run_starnet(binary, image_planes, tmp, timeout)
    starnet_version(binary)                                     # a clear error first, before any work is done
    tmp = Path(os.fspath(tmp_dir)).resolve()
    tmp.mkdir(parents=True, exist_ok=True)
    inp, out = tmp / "starnet_in.fits", tmp / "starnet_out.fits"
    try:
        out.unlink(missing_ok=True)
        io_fits.write_fits(inp, [np.clip(np.asarray(p, dtype=np.float32), 0.0, 1.0) for p in image_planes], bitpix=-32)
        # The CLI loads its weights from its own folder by default; the paths are absolute, so the working directory
        # affects nothing else.
        bin_dir = os.path.dirname(os.path.abspath(os.fspath(binary)))
        try:
            proc = subprocess.run(starnet_args(binary, inp, out), capture_output=True, text=True, check=False,
                                  cwd=bin_dir, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise StarnetError(f"StarNet did not finish within {timeout:g} s and was stopped") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise StarnetError(f"StarNet could not run: {exc}") from exc
        if proc.returncode != 0 or not out.exists():
            detail = (proc.stderr or proc.stdout or "").strip()[-400:]
            raise StarnetError(f"StarNet exited with {proc.returncode} and no output: {detail}")
        got, _ = io_fits.read_fits(out)
    finally:
        inp.unlink(missing_ok=True)
        out.unlink(missing_ok=True)
    n = len(image_planes)
    if len(got) == 1 and n == 3:
        got = [got[0], got[0], got[0]]
    elif len(got) == 3 and n == 1:
        got = [np.mean(np.stack(got), axis=0)]
    if len(got) != n or any(g.shape != image_planes[0].shape for g in got):
        raise StarnetError(f"StarNet output has {len(got)} channel(s) of shape {got[0].shape}; expected {n}")
    return [np.asarray(g, dtype=np.float32) for g in got]


def split_stars(image_planes: Sequence[np.ndarray], starless_planes: Sequence[np.ndarray]
                ) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Sky.splitStars: ``(starless, stars)`` from the image and its starless version.

    The starless layer is clamped to the image first (PixelMath ``min($T, image)``): where a tool brightened a pixel,
    unscreen would clip the stars at 0 and screen could no longer give the image back. The stars are then
    core.unscreen(image, starless), so core.screen(starless, stars) is the image.
    """
    starless = [np.minimum(np.asarray(s, dtype=np.float32), np.asarray(i, dtype=np.float32))
                for i, s in zip(image_planes, starless_planes)]
    stars = [core.unscreen(i, s) for i, s in zip(image_planes, starless)]
    return starless, stars


def read_layer(path: Any) -> list[np.ndarray]:
    """A starless or stars layer from a file: TIFF (top-down) or FITS (flipped to top-down by io_fits)."""
    p = Path(os.fspath(path))
    suffix = p.suffix.lower()
    if suffix in (".tif", ".tiff"):
        planes, _ = io_tiff.read_image(p)
        return planes
    if suffix in (".fit", ".fits", ".fts"):
        planes, _ = io_fits.read_fits(p)
        return planes
    raise ValueError(f"{p.name}: a layer must be a .tif, .tiff, .fit, .fits or .fts file")


def bridge_image(pixeldata: Any, header: Mapping[str, Any], psf_stars: Optional[Sequence[Any]],
                 icc: Optional[bytes]) -> Bridged:
    """Siril's image in, a top-down :class:`Bridged` out (the one place the three conversions meet)."""
    planes = planes_from_pixeldata(pixeldata)
    height, width = planes[0].shape
    return Bridged(
        planes=planes,
        header=top_down_header(header, height),
        wcs=top_down_wcs(header, height),
        target=target_from_header(header),
        detections=detections_from_psf(psf_stars),
        icc=icc,
        height=height,
        width=width,
    )


def job_from_dialog(values: Mapping[str, Any], planes: Sequence[np.ndarray], header: Mapping[str, Any],
                    icc: Optional[bytes]) -> "FlyJob":
    """The pipeline's FlyJob from the dialog values.

    ``values`` carries ``starless`` (required; a list of top-down planes), optionally ``stars`` (else derived with
    split_stars from ``planes``), ``detections``, and the dialog keys (target, target_type, distance, gaia, presets,
    duration, fps, loop, orientation, star_quality, quality, dynamic, video, object_name, fwhm, options, out_dir).
    ``planes`` is the top-down image; ``header`` is the top-down header (see :func:`top_down_header`).
    """
    from loomsiril.pipeline import FlyJob  # lazy: the pipeline is merged separately (E6)

    starless = values.get("starless")
    if starless is None:
        raise ValueError("no starless layer: choose the starless and stars files or run StarNet")
    stars = values.get("stars")
    if stars is None:
        starless, stars = split_stars(planes, starless)
    wcs = wcs_from_header(header)
    if wcs is None:
        raise ValueError("the image has no plate solution (CRVAL, CRPIX and CD keywords): run plate solving first")
    return FlyJob(
        starless=list(starless),
        stars=list(stars),
        wcs=wcs,
        target=values.get("target") or target_from_header(header),
        target_type=values.get("target_type", "nebula"),
        distance=values.get("distance"),
        gaia=values.get("gaia", "cache"),
        presets=list(values.get("presets") or ["youtube_1080"]),
        duration=float(values.get("duration", 8.0)),
        fps=int(values.get("fps", 30)),
        loop=values.get("loop", "none"),
        orientation=values.get("orientation", "landscape"),
        star_quality=values.get("star_quality", "high"),
        quality=values.get("quality", "high"),
        dynamic=values.get("dynamic", "sdr"),
        video=bool(values.get("video", False)),
        icc=icc,
        detections=values.get("detections"),
        object_name=values.get("object_name", ""),
        fwhm=values.get("fwhm"),
        options=dict(values.get("options") or {}),
        out_dir=values.get("out_dir", "."),
    )


def options_path(config_dir: Any) -> Path:
    """Where the last dialog options live: ``<Siril config dir>/loomsiril-fly.json``."""
    return Path(os.fspath(config_dir)) / OPTIONS_NAME


def save_options(values: Mapping[str, Any], config_dir: Any) -> Path:
    """Write the non-array dialog values as JSON (atomic: a .part file renamed into place). Returns the path."""
    import json

    path = options_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {k: values[k] for k in OPTION_KEYS if k in values}
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(part, path)
    return path


def load_options(config_dir: Any) -> dict[str, Any]:
    """The last saved dialog options; an empty dict when there are none or the file is unreadable."""
    import json

    try:
        data = json.loads(options_path(config_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: data[k] for k in OPTION_KEYS if k in data}
