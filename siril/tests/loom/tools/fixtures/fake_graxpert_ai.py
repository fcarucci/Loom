"""Stand-in for the GraXpert-AI Siril script, used only by the integration test (never a real model).

Run by Siril's pyscript command with the arguments Loom sends (``-bge -correction=subtraction -smoothing=<x>``).
It connects to Siril, fits a plane to every plane of the loaded image, subtracts it and sets the pixels back.

Environment variable ``FAKE_GRAXPERT_VARIANT=fail`` selects the failing variant: it prints the same kind of message
the real script prints when its model is missing, changes nothing and exits with status 1.
"""

import os
import sys

import numpy as np
import sirilpy as s


def fit_plane_subtract(plane):
    h, w = plane.shape
    yy, xx = np.mgrid[0:h, 0:w]
    design = np.column_stack([xx.ravel(), yy.ravel(), np.ones(h * w)])
    coef, *_ = np.linalg.lstsq(design, plane.astype(np.float64).ravel(), rcond=None)
    return (plane - (design @ coef).reshape(h, w)).astype(np.float32)


def main():
    if os.environ.get("FAKE_GRAXPERT_VARIANT") == "fail":
        print("ERROR: Models directory not found", flush=True)
        sys.exit(1)
    siril = s.SirilInterface()
    siril.connect()
    data = np.asarray(siril.get_image_pixeldata())
    if data.ndim == 2:
        out = fit_plane_subtract(data)
    else:
        out = np.stack([fit_plane_subtract(p) for p in data])
    with siril.image_lock():
        siril.set_image_pixeldata(out)
    print("fake GraXpert-AI: background subtracted", flush=True)


if __name__ == "__main__":
    main()
