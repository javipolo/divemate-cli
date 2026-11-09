"""DiveMate query library.

Reusable core for querying a DiveMate .ddb (SQLite) logbook.
CLI and future web frontend both build on `DiveMate`.
"""

from .db import DiveMate, DiveFilter

__all__ = ["DiveMate", "DiveFilter"]
__version__ = "0.1.0"
