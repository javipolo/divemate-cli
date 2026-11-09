"""DiveMate query library.

Reusable core for querying a DiveMate .ddb (SQLite) logbook.
CLI and future web frontend both build on `DiveMate`.
"""

from .db import DiveFilter, DiveMate

__all__ = ["DiveFilter", "DiveMate"]
__version__ = "0.1.0"
