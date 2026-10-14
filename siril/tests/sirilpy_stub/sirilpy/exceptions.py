"""Mirror of sirilpy.exceptions: the Siril error hierarchy (names and bases as in Siril 1.4)."""

from __future__ import annotations


class SirilError(Exception):
    """Base exception class for all Siril-related errors."""


class SirilConnectionError(SirilError):
    """The connection to Siril failed or was lost."""


class CommandError(SirilError):
    """A Siril command returned an error status."""


class DataError(SirilError):
    """No response, or an incorrect response, was received from Siril."""


class NoImageError(SirilError):
    """No image is currently loaded in Siril."""
