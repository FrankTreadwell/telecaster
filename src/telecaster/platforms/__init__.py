"""Picks the platform layer for this OS. See ``base.py`` for the interface."""

import sys

if sys.platform == "win32":
    from . import windows as current
elif sys.platform == "darwin":
    from . import macos as current
else:  # pragma: no cover
    raise ImportError(f"Telecaster's overlay doesn't support {sys.platform} yet")

__all__ = ["current"]
