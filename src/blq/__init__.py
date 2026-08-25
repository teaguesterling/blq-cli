"""
blq - Log Query

Capture and query build/test logs with DuckDB.

Example usage:
    from blq import LogStore, LogQuery

    # Query stored events
    store = LogStore.open()
    errors = store.errors().filter(ref_file="%main%").df()

    # Query a log file directly
    events = LogQuery.from_file("build.log").filter(severity="error").df()
"""

from importlib.metadata import PackageNotFoundError, version as _dist_version

# Derived, never written by hand. The literal here read "1.0.1" against a
# distribution version of 1.2.1 — two releases stale, and immune to
# --force-reinstall because it was not derived from anything. Nothing consumes
# it today (the CLI reads importlib.metadata directly), which is exactly why it
# drifted unnoticed; a version string that lies makes a healthy install look
# broken, which is the most expensive direction to send a reader.
try:
    __version__ = _dist_version("blq-cli")
except PackageNotFoundError:  # running from a source tree with no install
    __version__ = "0.0.0.dev0"

from blq.query import LogQuery, LogQueryGrouped, LogStore

__all__ = ["LogQuery", "LogStore", "LogQueryGrouped", "__version__"]
