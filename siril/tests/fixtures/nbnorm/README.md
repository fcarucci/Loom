# Narrowband normalisation reference pair

The regression in `tests/loom/test_nbnorm.py` reads three files from this directory:

- `input.fits`: a three-plane linear palette composite (R, G, B order, as the palette maps them).
- `output.fits`: the three planes produced by the reference PixInsight NarrowbandNormalization run on `input.fits`
  with the neutral settings listed in `loomsiril/loom/nbnorm.py`.
- `palette.txt`: one line with the palette name (`HOO`, `SHO` or `HSO`).

The pair is made from a synthetic input and is not yet present. While the files are absent the test is skipped with
the reason "reference pair not yet supplied". The tolerance is a maximum absolute difference of 0.02 per sample and is
to be tightened once the pair is in place.
