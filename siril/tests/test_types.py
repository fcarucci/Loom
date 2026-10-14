"""The frozen record types construct, copy and cache as the JS objects do."""

from __future__ import annotations

import dataclasses
import math

import numpy as np

from loomsiril.core import Point, Rect, detection_rect
from loomsiril.types import Detection, Halo, Placed, Scene, SceneSprite, Spike, SpikeReach, Sprite, SpriteMatch, StarModel


def _sprite() -> Sprite:
    src = {"ra": 1.0, "dec": 2.0, "plx": 2.0, "pmra": 0.0, "pmdec": 0.0, "G": 9.0, "BP": math.nan, "RP": math.nan}
    placed = Placed(src, 500.0, 10.0, 12.0)
    model = StarModel(10.0, 12.0, 3.0, np.array([1.0, 0.5, 0.0]), 2.0,
                      [Spike(0.5, 3.0, np.array([0.1, 0.05]), 4.0)], [1.0], 5.0)
    det = Detection(0, 10.0, 12.0, 4.2, detection_rect(10.0, 12.0, 10.0), model=model,
                    halo=Halo(5.0, 2.0, 5.0, [SpikeReach(0.5, 4.0)]))
    rect = Rect(5, 7, 16, 18)
    planes = [np.zeros((rect.height, rect.width), np.float32)]
    return Sprite(src, placed.d, placed, det, rect, 5.0, Point(det.x, det.y), np.ones((11, 11), np.uint8),
                  planes, planes, planes, [0.5])


def test_sprite_copies_like_object_assign():
    s = _sprite()
    s.cache["split"] = 1
    t = dataclasses.replace(s, px_scale=0.5, mask=None)
    assert t.px_scale == 0.5 and t.mask is None and s.mask is not None
    assert t.det is s.det  # shallow, as Object.assign
    assert SpriteMatch(s.placed, s.det, s.rect).det.halo.spikes[0].reach == 4.0


def test_scene_holds_sprites_and_projector():
    s = _sprite()
    sc = Scene(20, 30, 1, [np.zeros((30, 20), np.float32)], [np.zeros((30, 20), np.float32)],
               [SceneSprite(s, Point(10.0, 12.0))], lambda ra, dec: Point(ra, dec), {"ra": 1.0, "dec": 2.0},
               Point(9.5, 14.5), 900.0)
    assert sc.project(1, 2) == Point(1, 2) and sc.sprites[0].p0.x == 10.0 and sc.cache == {}
    assert sc.S[0].shape == (sc.h, sc.w)
