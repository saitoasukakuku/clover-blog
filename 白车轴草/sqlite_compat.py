"""SQLite compatibility helpers for older Linux distributions."""

import sys


def patch_sqlite():
    """Use bundled pysqlite3 when the system SQLite is too old for Django."""
    try:
        import pysqlite3
    except ImportError:
        return

    sys.modules["sqlite3"] = pysqlite3
    if hasattr(pysqlite3, "dbapi2"):
        sys.modules["sqlite3.dbapi2"] = pysqlite3.dbapi2
