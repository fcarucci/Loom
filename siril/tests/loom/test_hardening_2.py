"""Second hardening pass: JSON string escapes, Prism 2.0 without a stretch key, and white-balance recovery on planes
above the strided-sampling cap.

Reference values: the JSON cases are what JSON.stringify returns under Node 20 for the same JavaScript strings; the
Prism 2.0 case follows Loom's studioModelsFor (lib/StepsSyqon.js:1594-1637) with the ladder of lib/Steps.js:1825-1850.
"""

from __future__ import annotations

import numpy as np

from loomsiril.jsmath import json_string
from loomsiril.loom import imageops as io
from loomsiril.loom import levels as L

# ---- json_string on surrogates --------------------------------------------------------------------------------------


def test_astral_character_is_written_unescaped() -> None:
    # JSON.stringify("\u{1F600}") is the emoji itself
    assert json_string("\U0001F600") == '"\U0001F600"'


def test_well_formed_surrogate_pair_is_written_unescaped_as_the_same_characters() -> None:
    # two code units of one JS string form one character; JSON.stringify keeps it as it is.
    # The two units are built with chr() so they stay two code points (a literal "\ud83d\ude00" is merged by Python).
    pair = chr(0xD83D) + chr(0xDE00)
    assert len(pair) == 2
    assert json_string(pair) == '"\U0001F600"'
    assert json_string("x" + pair + "y") == '"x\U0001F600y"'


def test_reversed_pair_stays_escaped_in_lower_case_hex() -> None:
    # JSON.stringify("\udc00\ud800") is the twelve characters \udc00\ud800
    reversed_pair = chr(0xDC00) + chr(0xD800)
    assert json_string(reversed_pair) == '"\\udc00\\ud800"'
    assert len(json_string(reversed_pair)) == 2 + 12


def test_lone_high_surrogate_stays_escaped() -> None:
    assert json_string("\ud800") == '"\\ud800"'
    assert json_string("\ud83dA") == '"\\ud83dA"'


def test_lone_low_surrogate_stays_escaped() -> None:
    assert json_string("\udc00") == '"\\udc00"'
    assert json_string("A\udfffB") == '"A\\udfffB"'


# ---- studio_models_for: Prism 2.0 with the stretch key absent -------------------------------------------------------


def test_prism2_high_without_a_stretch_key_runs_the_stretched_max_model() -> None:
    # Loom reads config.stretch !== false, so an absent key is on: the stretched ladder step at High is Max.
    # The Linear pass is off (lib/Steps.js:1720), so High has no linear model; noiseLevelL is unset and falls back
    # to noiseLevel, so the same model is listed once. Expected value from lib/StepsSyqon.js:1594-1637.
    cfg = {"noiseTool": L.NR_TOOL_STUDIO2, "noiseLevel": "high"}
    assert "stretch" not in cfg
    assert L.studio_models_for(cfg, True) == ["prism-max"]


# ---- recover_wb_factors on planes above the 4 Mpx sampling cap -----------------------------------------------------


def test_recover_wb_factors_recovers_exact_gains_on_planes_above_the_sample_cap() -> None:
    shape = (2200, 2400)  # 5.28 Mpx: above io._WB_MAX_SAMPLES, so the strided sample is taken
    assert shape[0] * shape[1] > io._WB_MAX_SAMPLES
    rng = np.random.default_rng(20261013)
    gains = [0.42, 0.71, 1.0]
    before = [rng.uniform(0.05, 0.4, shape).astype(np.float32) for _ in gains]
    after = [(k * b + np.float32(0.05)).astype(np.float32) for k, b in zip(gains, before)]
    got = io.recover_wb_factors(before, after)
    assert len(got) == 3
    for g, k in zip(got, gains):
        assert abs(g - k) < 1e-6
