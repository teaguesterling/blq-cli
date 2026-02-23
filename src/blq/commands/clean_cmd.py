"""
Clean command for blq CLI.

Provides database cleanup and maintenance operations.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from blq.commands.core import BlqConfig


def cmd_clean(args: argparse.Namespace) -> None:
    """Handle clean subcommands."""
    mode = getattr(args, "clean_command", None)

    if mode is None:
        print("Usage: blq clean <data|prune|orphans|schema|full>", file=sys.stderr)
        print("", file=sys.stderr)
        print("Modes:", file=sys.stderr)
        print("  data    Clear run data, keep config and commands", file=sys.stderr)
        print("  prune   Remove data older than N days", file=sys.stderr)
        print("  orphans Mark stale pending runs as orphaned", file=sys.stderr)
        print("  schema  Recreate database schema", file=sys.stderr)
        print("  full    Delete and recreate .lq directory", file=sys.stderr)
        sys.exit(1)

    config = BlqConfig.ensure()
    lq_dir = config.lq_dir
    confirm = getattr(args, "confirm", False)

    if mode == "data":
        _clean_data(lq_dir, confirm)
    elif mode == "prune":
        days = getattr(args, "days", None)
        max_runs = getattr(args, "max_runs", None)
        max_size = getattr(args, "max_size", None)
        dry_run = getattr(args, "dry_run", False)
        if days is None and max_runs is None and max_size is None:
            print(
                "Error: At least one of --days, --max-runs, or --max-size is required.",
                file=sys.stderr,
            )
            sys.exit(1)
        _clean_prune(lq_dir, days, max_runs, max_size, confirm, dry_run)
    elif mode == "orphans":
        dry_run = getattr(args, "dry_run", False)
        min_age = getattr(args, "min_age", 60)
        _clean_orphans(lq_dir, min_age, dry_run)
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


def _clean_prune(
    lq_dir: Path,
    days: int | None,
    max_runs: int | None,
    max_size_mb: int | None,
    confirm: bool,
    dry_run: bool,
) -> None:
    """Remove data by age, max runs per source, or total size."""
    from blq.storage import BlqStorage

    db_path = lq_dir / "blq.duckdb"
    if not db_path.exists():
        print("No database found.", file=sys.stderr)
        sys.exit(1)

    # Describe what will happen
    descriptions: list[str] = []
    if days is not None:
        descriptions.append(f"older than {days} days")
    if max_runs is not None:
        descriptions.append(f"exceeding {max_runs} runs per source")
    if max_size_mb is not None:
        descriptions.append(f"exceeding {max_size_mb} MB total output")

    desc_str = " and ".join(descriptions)
    print(f"Pruning data {desc_str}.")

    if dry_run:
        print("Dry run - no changes made.")
        return

    if not confirm:
        print("", file=sys.stderr)
        print("Run with --confirm to proceed.", file=sys.stderr)
        sys.exit(1)

    total_pruned = 0
    with BlqStorage.open(lq_dir) as storage:
        if days is not None:
            pruned = storage.prune(days=days)
            total_pruned += pruned
            if pruned > 0:
                print(f"  Pruned {pruned} invocations older than {days} days.")

        if max_runs is not None:
            pruned = storage.prune_by_max_runs(max_runs)
            total_pruned += pruned
            if pruned > 0:
                print(f"  Pruned {pruned} invocations exceeding {max_runs} per source.")

        if max_size_mb is not None:
            pruned = storage.prune_by_size(max_size_mb)
            total_pruned += pruned
            if pruned > 0:
                print(f"  Pruned {pruned} invocations to fit under {max_size_mb} MB.")

        # Clean up orphaned blobs
        if total_pruned > 0:
            blobs_deleted, bytes_freed = storage.cleanup_blobs()
            if blobs_deleted > 0:
                mb_freed = bytes_freed / (1024 * 1024)
                print(f"  Freed {blobs_deleted} blobs ({mb_freed:.1f} MB).")

    if total_pruned == 0:
        print("No data matched prune criteria.")
    else:
        print(f"Total: removed {total_pruned} invocations.")


def _clean_orphans(lq_dir: Path, min_age: int, dry_run: bool) -> None:
    """Mark stale pending runs as orphaned.

    A pending run is considered stale if:
    1. It has no outcome record (status=pending)
    2. Its process (PID) is no longer running
    3. It started more than min_age seconds ago
    """
    from blq.bird import BirdStore

    store = BirdStore.open(lq_dir)

    # Find stale pending attempts
    stale = store.get_stale_pending_attempts(min_age_seconds=float(min_age))

    if not stale:
        print("No stale pending runs found.")
        store.close()
        return

    print(f"Found {len(stale)} stale pending run(s):")
    for attempt in stale:
        age_mins = (attempt["age_seconds"] or 0) / 60
        attempt_id = str(attempt["id"])
        print(
            f"  {attempt_id[:8]}... ({attempt['source_name']}) "
            f"- started {age_mins:.0f}m ago, PID {attempt['pid']}"
        )

    if dry_run:
        print("\nDry run - no changes made.")
        store.close()
        return

    # Mark them as orphaned
    orphaned = store.mark_stale_as_orphaned(min_age_seconds=float(min_age))
    store.close()

    print(f"\nMarked {len(orphaned)} run(s) as orphaned.")


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
