"""Mirror of the sirilpy.models.PSFStar fields the bridge reads (names, defaults and meaning as in Siril 1.4)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class PSFStar:
    """A star from Siril's PSF fit (subset of the real record; the fields not listed are not used here)."""

    star_name: Optional[str] = None
    B: float = 0.0            #: average sky background value
    A: float = 0.0            #: amplitude
    x0: float = 0.0           #: x coordinate of the peak
    y0: float = 0.0           #: y coordinate of the peak
    fwhmx: float = 0.0        #: FWHM in x axis in pixels
    fwhmy: float = 0.0        #: FWHM in y axis in pixels
    rmse: float = 0.0         #: RMSE of the minimization
    xpos: float = 0.0         #: x position of the star in the image
    ypos: float = 0.0         #: y position of the star in the image
    layer: int = 0            #: image channel on which the star modelling was carried out
    mag: float = 0.0          #: magnitude of the star
    beta: float = 0.0        #: Moffat beta (0 for the Gaussian fit)
    angle: float = 0.0       #: rotation angle of the PSF in degrees
