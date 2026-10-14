"""The star-layer driver: a stars layer and its placed Gaia stars become moving sprites and a residual.

Port of ``Sky.sprites`` (Sky.js 683-731, Loom v0.4.1). The steps run in the JS order and
emit the JS stage texts. The invariant the pipeline relies on: the sprites' pixels plus
the residual add back up to the stars layer, plane by plane.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Optional, Sequence

import numpy as np

from loomsiril import assign, detect, starfit
from loomsiril import starmodel as sm
from loomsiril.types import Detection, Placed, Sprite, SpriteMatch

__all__ = ["StarLayer", "sprites"]


@dataclass
class StarLayer:
    """``Sky.sprites`` result: the moving stars, the stars layer left after them, and the counts.

    ``residual`` is one ``(h, w)`` float32 plane per channel; ``residual_mask`` a uint8
    ``(h, w)`` plane of ones (the JS ``residualMask``). ``blended``, ``unmatched`` and
    ``detected`` are the JS ``a.blended``, ``a.unmatched`` and ``dets.length``.
    """

    sprites: list[Sprite]
    residual: list[np.ndarray]
    residual_mask: np.ndarray
    blended: int
    unmatched: int
    detected: int


def _no_stage(text: str, done: int, total: int) -> None:
    """The JS default ``stage = function() {}``."""


class _Modeller:
    """Sky.sprites' halo callback: models a detection once and returns its support radius.

    The progress text "Modelling stars" is sent every 200 new models, as the JS does.
    """

    def __init__(self, env: starfit.StarEnv, dets: Sequence[Detection], stage: Callable[[str, int, int], None]) -> None:
        self.env = env
        self.dets = dets
        self.stage = stage
        self.modelled = 0

    def halo(self, d: Detection) -> float:
        """Models ``d`` if it has no model yet; returns the model's support radius."""
        if d.model is None:
            d.model = starfit.star_model(self.env, d)
            self.modelled += 1
            if self.modelled % 200 == 0:
                self.stage("Modelling stars", self.modelled, len(self.dets))
        return d.model.support


def _detections(stars: Sequence[np.ndarray], px: int, neighbours: Sequence[dict],
                detections: Optional[Sequence[Detection]]) -> list[Detection]:
    """Sky.detections, the Gaia stars the detector missed (Fly.addMissingBright), and the fragment filter; indexed."""
    I = sm.intensity(stars)
    if detections is not None:
        # the driver sets index, model and halo on the detections it uses: work on copies, so the
        # caller's objects are not changed (a reused detection would keep a stale model otherwise)
        found = [replace(d, model=None, halo=None) for d in detections]
    else:
        found = detect.detect_stars(stars, fwhm=px)
    dets = assign.add_missing_bright(found, neighbours,
                                     lambda x, y: detect.bright_measure(I, x, y))
    # a detection on a brighter star's core, slope or spike is part of it (Fly.isFragment);
    # every test sees the list before the filter, as the JS filter callback does
    dets = sm.fragment_filter(dets, sm.reader(I), px)
    for k, d in enumerate(dets):
        d.index = k
    return dets


def _missed_stars(env: starfit.StarEnv, matched: Sequence[SpriteMatch], sums: list[np.ndarray],
                  stage: Callable[[str, int, int], None]) -> None:
    """The second pass of crowded-field photometry: compact light the models do not explain gets a model
    of its own (added to ``sums``), and one inside a moving star's core is part of that star (Fly.attachToCores)."""
    stage("Looking for stars the detector missed", 0, 0)
    extra = starfit.unexplained_sources(env, matched, sums)
    if extra:
        starfit.add_to_sums(env, extra, sums)
    sm.attach_to_cores(matched, extra)


def _deblend_all(env: starfit.StarEnv, matched: Sequence[SpriteMatch], sums: list[np.ndarray],
                 stage: Callable[[str, int, int], None]) -> tuple[list[Sprite], list[np.ndarray]]:
    """Deblends every moving star out of a residual that starts as copies of the stars planes (float32, as the JS Float32Array)."""
    residual = [np.array(p, dtype=np.float32, copy=True) for p in env.T]
    out: list[Sprite] = []
    for k, s in enumerate(matched):
        if k % 50 == 0:
            stage("Deblending moving stars", k, len(matched))
        out.append(starfit.deblend_sprite(env, s, sums, residual))
    return out, residual


def sprites(stars: Sequence[np.ndarray], placed: Sequence[Placed], fwhm: Optional[float],
            neighbours: Sequence[dict], stage: Optional[Callable[[str, int, int], None]] = None,
            detections: Optional[Sequence[Detection]] = None) -> StarLayer:
    """Fly-Through's Sky.sprites: moving stars and residual from a stars layer and its placed Gaia stars.

    ``stars`` is one ``(h, w)`` plane per channel (the JS ``starsImage``); ``placed`` the
    :class:`Placed` records (``Fly.placeStars``); ``fwhm`` the stars' FWHM in px (None
    takes ``Sky.FWHM_DEFAULT``); ``neighbours`` the catalogue sources projected into the
    image as ``{source, x, y}``. ``stage(text, done, total)`` gets the JS progress texts.
    ``detections`` replaces the detector (the Siril bridge passes Siril's own star list);
    an empty list means "no detections", not "detect". The detections passed in are copied
    (their index, model and halo are not changed).

    ``neighbours[k]["source"]`` must be the SAME object as the ``Placed.source`` it belongs
    to: Fly.catalogueBlend compares sources by identity (``n.source !== placed.source``), so
    a copy (for example after a JSON round trip) makes every placed star look blended. The
    Siril bridge must build neighbours from the placed records' own source dicts.
    """
    stage = stage or _no_stage
    stage("Detecting stars", 0, 0)
    h, w = np.shape(stars[0])
    px = starfit.sprite_px(fwhm)  # Sky.js 688: Math.max(1, Math.round(fwhm || Sky.FWHM_DEFAULT))
    dets = _detections(stars, px, neighbours, detections)
    env = starfit.make_env(stars, dets, px)

    modeller = _Modeller(env, dets, stage)
    stage("Modelling stars", 0, 0)
    for d in dets:
        modeller.halo(d)
    sm.drop_borrowed_spikes(dets)
    a = assign.assign_sprites(dets, placed, w, h, px, neighbours, modeller.halo)
    matched = a["sprites"]

    sums = starfit.model_sums(env, dets)
    _missed_stars(env, matched, sums, stage)
    out, residual = _deblend_all(env, matched, sums, stage)

    residual_mask = np.ones((h, w), dtype=np.uint8)
    return StarLayer(sprites=out, residual=residual, residual_mask=residual_mask,
                     blended=a["blended"], unmatched=a["unmatched"], detected=len(dets))
