"""
Clean command for blq CLI.

Provides database cleanup and maintenance operations.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from blq.commands.core import BlqConfig


def cmd_clean(args: argparse.Namespace) -> None:
    """Handle clean subcommands."""
    mode = getattr(args, "clean_command", None)

    if mode is None:
        print("Usage: blq clean <data|prune|schema|full>", file=sys.stderr)
        print("", file=sys.stderr)
        print("Modes:", file=sys.stderr)
        print("  data    Clear run data, keep config and commands", file=sys.stderr)
        print("  prune   Remove data older than N days", file=sys.stderr)
        print("  schema  Recreate database schema", file=sys.stderr)
        print("  full    Delete and recreate .lq directory", file=sys.stderr)
        sys.exit(1)

    config = BlqConfig.ensure()
    lq_dir = config.lq_dir
    confirm = getattr(args, "confirm", False)

    if mode == "data":
        _clean_data(lq_dir, confirm)
    elif mode == "prune":
        days = args.days
        dry_run = getattr(args, "dry_run", False)
        _clean_prune(lq_dir, days, confirm, dry_run)
    elif mode == "schema":
        _clean_schema(lq_dir, confirm)
    elif mode == "full":
        _clean_full(lq_dir, confirm)
    else:
        print(f"Unknown mode: {mode}", file=sys.stderr)
        sys.exit(1)


def _clean_data(lq_dir: Path, confirm: bool) -> None:
    """Clear run data but keep config and commands."""
    if not confirm:
        print("This will delete all run data (invocations, events, outputs).", file=sys.stderr)
        print("Config and registered commands will be preserved.", file=sys.stderr)
        print("", file=sys.stderr)
        print("Run with --confirm to proceed.", file=sys.stderr)
        sys.exit(1)

    import duckdb

    db_path = lq_dir / "blq.duckdb"
    if db_path.exists():
        conn = duckdb.connect(str(db_path))
        conn.execute("DELETE FROM events")
        conn.execute("DELETE FROM outputs")
        conn.execute("DELETE FROM invocations")
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM blob_registry")
        conn.close()

    # Clear blobs
    blobs_dir = lq_dir / "blobs"
    if blobs_dir.exists():
        shutil.rmtree(blobs_dir)
        blobs_dir.mkdir()
        (blobs_dir / "content").mkdir()

    print("Cleared all run data. Config and commands preserved.")


def _clean_prune(lq_dir: Path, days: int, confirm: bool, dry_run: bool) -> None:
    """Remove data older than N days."""
    import duckdb

    cutoff = datetime.now() - timedelta(days=days)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    db_path = lq_dir / "blq.duckdb"
    if not db_path.exists():
        print("No database found.", file=sys.stderr)
        sys.exit(1)

    conn = duckdb.connect(str(db_path))

    # Count what would be removed
    result = conn.execute(
        "SELECT COUNT(*) FROM invocations WHERE timestamp < ?", [cutoff_str]
    ).fetchone()
    invocation_count = result[0] if result else 0

    result = conn.execute("""
        SELECT COUNT(*) FROM events e
        JOIN invocations i ON e.invocation_id = i.id
        WHERE i.timestamp < ?
    """, [cutoff_str]).fetchone()
    event_count = result[0] if result else 0

    if invocation_count == 0:
        print(f"No data older than {days} days found.")
        conn.close()
        return

    print(f"Found {invocation_count} invocations and {event_count} events older than {days} days.")

    if dry_run:
        print("Dry run - no changes made.")
        conn.close()
        return

    if not confirm:
        print("", file=sys.stderr)
        print("Run with --confirm to proceed.", file=sys.stderr)
        conn.close()
        sys.exit(1)

    # Get invocation IDs to delete (for output cleanup)
    old_ids = conn.execute(
        "SELECT id FROM invocations WHERE timestamp < ?", [cutoff_str]
    ).fetchall()
    old_id_list = [row[0] for row in old_ids]

    # Delete events first (foreign key constraint)
    conn.execute("""
        DELETE FROM events WHERE invocation_id IN (
            SELECT id FROM invocations WHERE timestamp < ?
        )
    """, [cutoff_str])

    # Delete outputs
    conn.execute("""
        DELETE FROM outputs WHERE invocation_id IN (
            SELECT id FROM invocations WHERE timestamp < ?
        )
    """, [cutoff_str])

    # Delete invocations
    conn.execute("DELETE FROM invocations WHERE timestamp < ?", [cutoff_str])

    # Clean up orphaned sessions
    conn.execute("""
        DELETE FROM sessions WHERE id NOT IN (
            SELECT DISTINCT session_id FROM invocations WHERE session_id IS NOT NULL
        )
    """)

    conn.close()

    # Clean up orphaned blobs
    from blq.bird import BirdStore
    store = BirdStore.open(lq_dir)
    blobs_deleted, bytes_freed = store.cleanup_orphaned_blobs()
    store.close()

    msg = f"Removed {invocation_count} invocations and {event_count} events."
    if blobs_deleted > 0:
        mb_freed = bytes_freed / (1024 * 1024)
        msg += f" Freed {blobs_deleted} blobs ({mb_freed:.1f} MB)."
    print(msg)


def _clean_schema(lq_dir: Path, confirm: bool) -> None:
    """Recreate database schema."""
    if not confirm:
        print("This will recreate the database schema.", file=sys.stderr)
        print("All run data will be lost. Config files will be preserved.", file=sys.stderr)
        print("", file=sys.stderr)
        print("Run with --confirm to proceed.", file=sys.stderr)
        sys.exit(1)

    db_path = lq_dir / "blq.duckdb"
    if db_path.exists():
        db_path.unlink()

    # Clear blobs
    blobs_dir = lq_dir / "blobs"
    if blobs_dir.exists():
        shutil.rmtree(blobs_dir)
        blobs_dir.mkdir()
        (blobs_dir / "content").mkdir()

    # Recreate database with schema
    from blq.bird import BirdStore
    store = BirdStore.open(lq_dir)
    store.close()

    print("Recreated database schema. Config files preserved.")


def _clean_full(lq_dir: Path, confirm: bool) -> None:
    """Delete and recreate .lq directory."""
    if not confirm:
        print("This will delete the entire .lq directory and reinitialize.", file=sys.stderr)
        print("ALL data including config and commands will be lost.", file=sys.stderr)
        print("", file=sys.stderr)
        print("Run with --confirm to proceed.", file=sys.stderr)
        sys.exit(1)

    shutil.rmtree(lq_dir)

    # Run init
    result = subprocess.run(
        ["blq", "init"],
        capture_output=True,
        text=True,
        cwd=lq_dir.parent,
    )

    if result.returncode == 0:
        print("Fully reinitialized .lq directory.")
    else:
        print(f"Init failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)
