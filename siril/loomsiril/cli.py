"""The command line: ``python -m loomsiril`` / the ``loomsiril`` script (E6: loomsiril/cli.py).

Reads FITS or TIFF inputs (row-order convention: every plane is read top-down, so the WCS of a FITS file arrives
already converted), runs :func:`loomsiril.pipeline.run` and reports what it did. Exit 0 on success,
2 for a usage error, 1 for a failed render or input, with a one-line message in both cases.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np

from loomsiril import gaia, io_fits, io_tiff, presets, wcs as wcs_mod
from loomsiril.core import tan_unproject
from loomsiril.pipeline import FlyJob, clamp_to_image, run

__all__ = ["build_parser", "main"]

FITS_SUFFIXES: tuple[str, ...] = (".fits", ".fit", ".fts")
GAIA_SOURCES: tuple[str, ...] = ("cache", "online")


def _positive_float(text: str) -> float:
    """argparse type: a number above 0."""
    try:
        v = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}")
    if not v > 0:
        raise argparse.ArgumentTypeError(f"must be above 0, got {text}")
    return v


def _duration(text: str) -> float:
    """argparse type: the clip length in seconds, above 0 and at most pipeline.MAX_DURATION."""
    from loomsiril.pipeline import MAX_DURATION
    v = _positive_float(text)
    if v > MAX_DURATION:
        raise argparse.ArgumentTypeError(f"must be at most {MAX_DURATION:g} s, got {text}")
    return v


def _positive_int(text: str) -> int:
    """argparse type: a whole number of at least 1."""
    try:
        v = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}")
    if v < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {text}")
    return v


def _pair(text: str, what: str) -> tuple[float, float]:
    """Two comma-separated numbers ("x,y" or "ra,dec")."""
    parts = text.split(",")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"{what} must be two numbers separated by a comma, got {text!r}")
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        raise argparse.ArgumentTypeError(f"{what} must be two numbers separated by a comma, got {text!r}")


def _target(text: str) -> dict[str, float]:
    """argparse type for --target: "ra,dec" in degrees, RA in [0, 360), |Dec| <= 90."""
    ra, dec = _pair(text, "--target")
    if not (0 <= ra < 360) or not (-90 <= dec <= 90):
        raise argparse.ArgumentTypeError(f"--target needs RA in [0, 360) and Dec in [-90, 90], got {text!r}")
    return {"ra": ra, "dec": dec}


def _pixel(text: str) -> tuple[float, float]:
    """argparse type for --target-pixel: "x,y" in 0-based top-down pixel centres."""
    return _pair(text, "--target-pixel")


def _presets(text: str) -> list[str]:
    """argparse type for --preset: one or more preset ids, comma-separated."""
    ids = [p.strip() for p in text.split(",") if p.strip()]
    if not ids:
        raise argparse.ArgumentTypeError("--preset needs at least one preset id")
    for p in ids:
        if p not in presets.PRESETS:
            raise argparse.ArgumentTypeError(f"unknown preset {p!r} (one of {', '.join(presets.PRESET_ORDER)})")
    return ids


def _gaia(text: str) -> str:
    """argparse type for --gaia: "cache", "online" or "tsv:PATH" (the file must exist)."""
    if text in GAIA_SOURCES:
        return text
    if text.startswith("tsv:") and len(text) > 4:
        if not os.path.isfile(text[4:]):
            raise argparse.ArgumentTypeError(f"the Gaia TSV file is not there: {text[4:]}")
        return text
    raise argparse.ArgumentTypeError(f"--gaia takes cache, online or tsv:PATH, got {text!r}")


def build_parser() -> argparse.ArgumentParser:
    """The argument parser (the acceptance tests use these flags; the names and their defaults are the documented interface)."""
    p = argparse.ArgumentParser(prog="loomsiril", description="Render a Loom Fly-Through: a push-in video of a finished image.")
    p.add_argument("--starless", metavar="S", required=True, help="the starless image (FITS or TIFF)")
    p.add_argument("--stars", metavar="T", required=True, help="the stars layer (FITS or TIFF)")
    p.add_argument("--image", metavar="I", help="the image the two layers came from (FITS or TIFF): clamps the starless layer and gives the colour profile")
    wcs = p.add_mutually_exclusive_group(required=True)
    wcs.add_argument("--wcs", metavar="FITS", help="a FITS file whose header holds the TAN WCS keywords")
    wcs.add_argument("--solution", metavar="JSON", help="a JSON solution {crval1, crval2, crpix1, crpix2, cd: [4 numbers]}")
    tgt = p.add_mutually_exclusive_group()
    tgt.add_argument("--target", metavar="RA,DEC", type=_target, help="the target in degrees (sets the distance)")
    tgt.add_argument("--target-pixel", metavar="X,Y", type=_pixel, help="the target as a 0-based top-down pixel")
    p.add_argument("--type", choices=("nebula", "galaxy"), default="nebula", help="the target's type (default nebula)")
    p.add_argument("--distance", metavar="PC", type=_positive_float, help="the distance in parsecs (default: looked up)")
    p.add_argument("--gaia", metavar="SOURCE", type=_gaia, default="cache", help="cache (default), online, or tsv:PATH")
    p.add_argument("--preset", metavar="ID[,ID..]", type=_presets, default=["youtube_1080"], help="output preset(s) (default youtube_1080)")
    p.add_argument("--duration", metavar="S", type=_duration, default=8.0, help="clip length in seconds, at most 600 (default 8)")
    p.add_argument("--fps", type=_positive_int, default=30, help="frames per second (default 30)")
    p.add_argument("--out", metavar="DIR", required=True, help="the output folder")
    p.add_argument("--video", action="store_true", help="encode the video with ffmpeg (else print the command)")
    p.add_argument("--ffmpeg", metavar="PATH", help="the ffmpeg to use (default: the one on PATH)")
    p.add_argument("--quality", choices=("high", "medium"), default="high", help="encode quality (default high)")
    p.add_argument("--star-quality", choices=("highest", "high", "medium"), default="high", help="star drawing (default high)")
    p.add_argument("--dynamic", choices=("sdr", "hdr"), default="sdr", help="sdr (default) or hdr")
    p.add_argument("--loop", choices=("none", "pingpong", "crossfade"), default="none", help="how the clip loops (default none)")
    p.add_argument("--orientation", choices=("landscape", "portrait"), default="landscape", help="landscape (default) or portrait")
    return p


def _under_tests() -> bool:
    """Gaia online is refused under pytest (PYTEST_CURRENT_TEST, set by pytest and inherited) or with LOOMSIRIL_OFFLINE=1."""
    return bool(os.environ.get("PYTEST_CURRENT_TEST")) or os.environ.get("LOOMSIRIL_OFFLINE") == "1"


def _load(path: str) -> tuple[list[np.ndarray], Optional[bytes], Optional[dict[str, Any]]]:
    """Planes (top-down float32), the ICC profile (TIFF) and the header (FITS) of an input file."""
    if Path(path).suffix.lower() in FITS_SUFFIXES:
        planes, header = io_fits.read_fits(path)
        return [np.asarray(p, dtype=np.float32) for p in planes], None, header
    planes, icc = io_tiff.read_image(path)
    return [np.asarray(p, dtype=np.float32) for p in planes], icc, None


def _wcs(args: argparse.Namespace) -> tuple[dict, Optional[dict[str, Any]]]:
    """The WCS dict and the header it came from: --wcs (FITS, through read_fits and keyword_wcs) or --solution (JSON)."""
    if args.wcs:
        _, _, header = _load(args.wcs)
        w = wcs_mod.keyword_wcs(header or {})
        if w is None:
            raise ValueError(f"no TAN WCS keywords (CRVAL, CRPIX, CD) in {args.wcs}")
        return w, header
    import json
    with open(args.solution, encoding="utf-8") as fh:
        return wcs_mod.wcs_from_solution(json.load(fh)), None


def _median(plane: np.ndarray) -> float:
    """The element at index n >> 1 of the sorted values (Fly.median), without sorting the whole plane."""
    v = np.asarray(plane, dtype=np.float64).ravel()
    k = v.size >> 1
    return float(np.partition(v, k)[k])


def _progress(last: list[Optional[str]]) -> Any:
    """A progress callback that prints each new stage to stderr once."""

    def report(stage: str, done: int, total: int) -> None:
        if stage != last[0]:
            last[0] = stage
            print(f"{stage}...", file=sys.stderr)

    return report


def _read_layers(args: argparse.Namespace) -> tuple[list[np.ndarray], list[np.ndarray], Optional[bytes], Optional[dict[str, Any]]]:
    """The starless and stars layers (same channels and size), the starless file's ICC profile and its header (FITS)."""
    starless, s_icc, s_header = _load(args.starless)
    stars, _, _ = _load(args.stars)
    if len(starless) != len(stars) or starless[0].shape != stars[0].shape:
        raise ValueError("--starless and --stars differ in channels or size")
    return starless, stars, s_icc, s_header


def _clamp_to_image_arg(args: argparse.Namespace, starless: list[np.ndarray],
                        icc: Optional[bytes]) -> tuple[list[np.ndarray], Optional[bytes]]:
    """With --image: the starless layer is clamped to the image, and the image's profile (else the starless file's) is the colour."""
    if not args.image:
        return starless, icc
    image, i_icc, _ = _load(args.image)
    if len(image) != len(starless) or image[0].shape != starless[0].shape:
        raise ValueError("--image differs from --starless in channels or size")
    return clamp_to_image(image, starless), i_icc or icc      # the stars layer is the --stars file as given (D2)


def _target_for(args: argparse.Namespace, w: dict, header: Optional[dict[str, Any]]) -> Optional[dict]:
    """The target: --target, else --target-pixel through the WCS, else the header's centre, else None (the image centre)."""
    if args.target is not None:
        return dict(args.target)
    if args.target_pixel is not None:
        x, y = args.target_pixel
        return tan_unproject(x, y, w)
    return presets.header_centre(header) if header else None


def _ffmpeg_for(args: argparse.Namespace, warnings: list[str]) -> tuple[Optional[str], bool]:
    """The ffmpeg to encode with (--ffmpeg, else the one on PATH) and whether the video is made; a warning when there is none."""
    if not args.video:
        return None, False
    ffmpeg = args.ffmpeg or shutil.which("ffmpeg")
    if ffmpeg is None:
        warnings.append("no ffmpeg on PATH: the frames are written and the command is printed instead")
    return ffmpeg, ffmpeg is not None


def _prepare(args: argparse.Namespace) -> tuple[FlyJob, list[str]]:
    """Read the inputs and build the job; returns the job and the warnings to print."""
    warnings: list[str] = []
    starless, stars, s_icc, s_header = _read_layers(args)
    starless, icc = _clamp_to_image_arg(args, starless, s_icc)
    w, wcs_header = _wcs(args)
    header = wcs_header or s_header
    if presets.looks_linear(_median(starless[0])):
        warnings.append("the input looks linear (its sky is near black): the frames will be dark; stretch it first")
    target = _target_for(args, w, header)
    object_name = str(header.get("OBJECT", "")).strip() if header else ""
    ffmpeg, video = _ffmpeg_for(args, warnings)
    job = FlyJob(
        starless=starless, stars=stars, wcs=w, target=target, target_type=args.type,
        distance=args.distance, gaia=args.gaia, presets=args.preset, duration=args.duration,
        fps=args.fps, loop=args.loop, orientation=args.orientation, star_quality=args.star_quality,
        quality=args.quality, dynamic=args.dynamic, video=video, ffmpeg=ffmpeg, icc=icc,
        object_name=object_name, out_dir=args.out,
    )
    return job, warnings


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the command line; returns the exit code (the console script exits with it)."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.gaia == "online" and _under_tests():
        print("loomsiril: error: Gaia online queries are disabled under tests (PYTEST_CURRENT_TEST or "
              "LOOMSIRIL_OFFLINE=1 is set): use --gaia cache or --gaia tsv:PATH", file=sys.stderr)
        return 2
    try:
        job, warnings = _prepare(args)
        for text in warnings:
            print(f"loomsiril: warning: {text}", file=sys.stderr)
        res = run(job, progress=_progress([None]))
    except (ValueError, OSError, KeyError, RuntimeError, gaia.GaiaError) as exc:
        print(f"loomsiril: error: {exc}", file=sys.stderr)
        return 1
    for note in res.notes:
        print(f"note: {note}", file=sys.stderr)
    print(f"frames: {res.written} written, {res.reused} reused, in {args.out}")
    for video in res.videos:
        print(f"video: {video}")
    for command in res.commands:
        print(f"ffmpeg command: {command}")
    for failure in res.failed:
        print(f"loomsiril: error: ffmpeg failed for {failure['video']}: {failure['command']}", file=sys.stderr)
    if res.failed:
        return 1
    return 0
