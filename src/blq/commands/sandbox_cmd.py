"""Sandbox specification commands."""

from __future__ import annotations

import json
import sys
from typing import Any

from blq.commands.core import BlqConfig
from blq_sandbox.spec import resolve_sandbox


def cmd_sandbox_list(args: Any) -> None:
    """List sandbox specs for all registered commands."""
    config = BlqConfig.ensure()
    commands = config.commands

    # Collect sandbox info
    rows = []
    for name, cmd in sorted(commands.items()):
        sandbox_raw = cmd._extra.get("sandbox")
        if sandbox_raw is None:
            rows.append((name, "none", "-", "-", "-"))
            continue

        try:
            spec = resolve_sandbox(sandbox_raw)
        except (ValueError, TypeError):
            rows.append((name, str(sandbox_raw), "?", "?", "?"))
            continue

        if spec is None:
            rows.append((name, "none", "-", "-", "-"))
            continue

        preset = spec.matching_preset()
        label = preset if preset else "custom"
        rows.append((name, label, spec.grade_w, str(spec.effects_ceiling), spec.network))

    if getattr(args, "json", False):
        data = []
        for name, label, grade_w, ceiling, network in rows:
            data.append(
                {
                    "command": name,
                    "sandbox": label,
                    "grade_w": grade_w,
                    "effects_ceiling": ceiling,
                    "network": network,
                }
            )
        print(json.dumps(data, indent=2))
        return

    # Table output
    print(f"{'Command':<20} {'Sandbox':<14} {'Grade W':<10} {'Ceiling':<10} {'Network':<14}")
    print("-" * 68)
    for name, label, grade_w, ceiling, network in rows:
        print(f"{name:<20} {label:<14} {grade_w:<10} {ceiling:<10} {network:<14}")


def cmd_sandbox_inspect(args: Any) -> None:
    """Show full sandbox spec and grade for a command."""
    config = BlqConfig.ensure()
    cmd_name = args.command

    if cmd_name not in config.commands:
        print(f"Error: Unknown command '{cmd_name}'", file=sys.stderr)
        sys.exit(1)

    cmd = config.commands[cmd_name]
    sandbox_raw = cmd._extra.get("sandbox")

    if sandbox_raw is None:
        print(f"Command '{cmd_name}' has no sandbox spec.")
        return

    try:
        spec = resolve_sandbox(sandbox_raw)
    except (ValueError, TypeError) as e:
        print(f"Error resolving sandbox spec: {e}", file=sys.stderr)
        sys.exit(1)

    if spec is None:
        print(f"Command '{cmd_name}' has no sandbox spec.")
        return

    if getattr(args, "json", False):
        data = {
            "command": cmd_name,
            "spec": spec.to_dict(),
            "grade_w": spec.grade_w,
            "effects_ceiling": spec.effects_ceiling,
            "preset": spec.matching_preset(),
            "active_dimensions": sorted(spec.active_dimensions()),
        }
        print(json.dumps(data, indent=2))
        return

    preset = spec.matching_preset()
    print(f"Command: {cmd_name}")
    print(f"Sandbox: {preset or 'custom'}")
    print(f"Grade W: {spec.grade_w}")
    print(f"Effects Ceiling: {spec.effects_ceiling}")
    print()
    print("Dimensions:")
    d = spec.to_dict()
    if not d:
        print("  (all unrestricted)")
    for key, val in d.items():
        print(f"  {key}: {val}")


def cmd_sandbox_suggest(args: Any) -> None:
    """Suggest a sandbox spec from observed resource metrics."""
    config = BlqConfig.ensure()
    cmd_name = args.command

    if cmd_name not in config.commands:
        print(f"Error: Unknown command '{cmd_name}'", file=sys.stderr)
        sys.exit(1)

    # Query observed metrics from BIRD
    from blq.bird import BirdStore

    try:
        with BirdStore.open(config.lq_dir) as store:
            result = store._conn.execute(
                """
                SELECT
                    count(*) as run_count,
                    max(json_extract(extension_data,
                        '$.metrics.memory_peak_bytes')::BIGINT) as max_memory,
                    max(json_extract(extension_data,
                        '$.metrics.cpu_usage_usec')::BIGINT) as max_cpu_usec,
                    max(o.duration_ms) as max_duration_ms
                FROM invocations i
                LEFT JOIN outcomes o ON o.attempt_id = i.id
                WHERE i.source_name = ?
                  AND i.extension_data IS NOT NULL
            """,
                [cmd_name],
            ).fetchone()
    except Exception as e:
        print(f"Error querying metrics: {e}", file=sys.stderr)
        sys.exit(1)

    if not result or result[0] == 0:
        print(f"No runs found for '{cmd_name}'. Run it a few times first.")
        return

    run_count = result[0]
    max_memory = result[1]
    max_cpu_usec = result[2]
    max_duration_ms = result[3]

    print(f"Based on {run_count} run(s) of '{cmd_name}':")
    print()

    # Suggest with 2x headroom
    suggested: dict[str, Any] = {
        "network": "none",
        "filesystem": "readonly",
        "processes": "isolated",
    }

    if max_memory is not None:
        from blq_sandbox.spec import format_size

        suggested_mem = max_memory * 2
        print(f"  Observed peak memory: {format_size(max_memory)}")
        print(f"  Suggested memory:     {format_size(suggested_mem)} (2x headroom)")
        suggested["memory"] = format_size(suggested_mem)
    else:
        print("  No memory data (enable systemd engine for cgroup monitoring)")

    if max_cpu_usec is not None:
        from blq_sandbox.spec import format_duration

        suggested_cpu_s = int(max_cpu_usec / 1_000_000 * 2)
        print(f"  Observed peak CPU:    {format_duration(int(max_cpu_usec / 1_000_000))}")
        print(f"  Suggested CPU:        {format_duration(max(suggested_cpu_s, 1))} (2x headroom)")
        suggested["cpu"] = format_duration(max(suggested_cpu_s, 1))
    else:
        print("  No CPU data (enable systemd engine for cgroup monitoring)")

    if max_duration_ms is not None:
        from blq_sandbox.spec import format_duration

        suggested_timeout_s = int(max_duration_ms / 1000 * 3)
        print(f"  Observed max wall time: {format_duration(int(max_duration_ms / 1000))}")
        suggested_t = format_duration(max(suggested_timeout_s, 1))
        print(f"  Suggested timeout:      {suggested_t} (3x headroom)")
        suggested["timeout"] = format_duration(max(suggested_timeout_s, 1))

    print()
    print("Suggested TOML config:")
    print()
    print(f"[commands.{cmd_name}.sandbox]")
    for key, val in suggested.items():
        if isinstance(val, list):
            print(f"{key} = {json.dumps(val)}")
        else:
            print(f'{key} = "{val}"')


def cmd_sandbox_profile(args: Any) -> None:
    """Profile a command with strace to discover access patterns."""
    import shutil

    config = BlqConfig.ensure()
    cmd_name = args.command

    if not shutil.which("strace"):
        print("Error: strace is not installed. Install with:", file=sys.stderr)
        print("  sudo apt install strace", file=sys.stderr)
        sys.exit(1)

    if cmd_name not in config.commands:
        print(f"Error: Unknown command '{cmd_name}'", file=sys.stderr)
        sys.exit(1)

    reg_cmd = config.commands[cmd_name]
    command = reg_cmd.template

    print(f"Profiling '{cmd_name}': {command}")
    print("(This adds 2-10x overhead — one-time profiling step)")
    print()

    from blq_sandbox.profile import run_profile, suggest_spec_from_profile

    workspace = config.lq_dir.parent
    profile = run_profile(command, workspace=workspace, timeout=reg_cmd.timeout or 300)

    if profile is None:
        print("Error: Profiling failed", file=sys.stderr)
        sys.exit(1)

    if getattr(args, "json", False):
        print(json.dumps(profile.to_dict(), indent=2))
        return

    # Summary
    print(f"Files read:    {len(profile.files_read)}")
    print(f"Files written: {len(profile.files_written)}")
    print(f"Network:       {'yes' if profile.has_network else 'no'}")
    print(f"Subprocesses:  {profile.process_spawns}")
    print(f"Executables:   {', '.join(sorted(profile.executables))}")
    print()

    # Suggest spec
    suggested = suggest_spec_from_profile(profile, workspace=workspace)
    print("Suggested sandbox spec:")
    print()
    print(f"[commands.{cmd_name}.sandbox]")
    for key, val in suggested.items():
        if isinstance(val, list):
            print(f"{key} = {json.dumps(val)}")
        else:
            print(f'{key} = "{val}"')

    if profile.files_written:
        print()
        print("Write paths observed:")
        for f in sorted(profile.files_written)[:20]:
            print(f"  {f}")
        if len(profile.files_written) > 20:
            remaining = len(profile.files_written) - 20
            print(f"  ... and {remaining} more")

    if profile.network_connections:
        print()
        print("Network connections observed:")
        for addr, port in sorted(profile.network_connections):
            print(f"  {addr}:{port}")


def cmd_sandbox_tighten(args: Any) -> None:
    """Tighten sandbox spec from observed resource data."""
    config = BlqConfig.ensure()
    cmd_name = args.command

    if cmd_name not in config.commands:
        print(f"Error: Unknown command '{cmd_name}'", file=sys.stderr)
        sys.exit(1)

    reg_cmd = config.commands[cmd_name]
    sandbox_raw = reg_cmd._extra.get("sandbox")

    if sandbox_raw is None:
        print(f"Error: Command '{cmd_name}' has no sandbox spec.", file=sys.stderr)
        print("Use 'blq sandbox suggest' to generate one first.", file=sys.stderr)
        sys.exit(1)

    try:
        current = resolve_sandbox(sandbox_raw)
    except (ValueError, TypeError) as e:
        print(f"Error resolving sandbox spec: {e}", file=sys.stderr)
        sys.exit(1)

    if current is None:
        print(f"Error: Command '{cmd_name}' has no sandbox spec.", file=sys.stderr)
        sys.exit(1)

    # Query observed metrics from BIRD
    from blq.bird import BirdStore

    try:
        with BirdStore.open(config.lq_dir) as store:
            result = store._conn.execute(
                """
                SELECT
                    count(*) as run_count,
                    max(json_extract(extension_data,
                        '$.metrics.memory_peak_bytes')::BIGINT) as max_memory,
                    max(json_extract(extension_data,
                        '$.metrics.cpu_usage_usec')::BIGINT) as max_cpu_usec,
                    max(o.duration_ms) as max_duration_ms
                FROM invocations i
                LEFT JOIN outcomes o ON o.attempt_id = i.id
                WHERE i.source_name = ?
                  AND i.extension_data IS NOT NULL
            """,
                [cmd_name],
            ).fetchone()
    except Exception as e:
        print(f"Error querying metrics: {e}", file=sys.stderr)
        sys.exit(1)

    if not result or result[0] < 3:
        run_count = result[0] if result else 0
        print(
            f"Insufficient data: only {run_count} run(s) found for '{cmd_name}'. "
            "At least 3 runs are required for reliable tightening.",
            file=sys.stderr,
        )
        sys.exit(1)

    max_memory = result[1]
    max_cpu_usec = result[2]
    max_duration_ms = result[3]

    observed: dict[str, Any] = {}
    if max_memory is not None:
        observed["max_memory_bytes"] = max_memory
    if max_cpu_usec is not None:
        observed["max_cpu_usec"] = max_cpu_usec
    if max_duration_ms is not None:
        observed["max_duration_ms"] = max_duration_ms

    from blq_sandbox.tighten import compute_tighter_spec

    tighter = compute_tighter_spec(current, observed)

    if tighter == current:
        print("No changes: spec is already as tight as data allows.")
        return

    # Show diff
    from blq_sandbox.spec import format_duration, format_size

    print(f"Tightening sandbox spec for '{cmd_name}' (based on {run_count} runs):")
    print()

    def _fmt_memory(v: int | None) -> str:
        return format_size(v) if v is not None else "unlimited"

    def _fmt_duration(v: int | None) -> str:
        return format_duration(v) if v is not None else "unlimited"

    if tighter.memory != current.memory:
        print(f"  memory:  {_fmt_memory(current.memory)} → {_fmt_memory(tighter.memory)}")
    if tighter.cpu != current.cpu:
        print(f"  cpu:     {_fmt_duration(current.cpu)} → {_fmt_duration(tighter.cpu)}")
    if tighter.timeout != current.timeout:
        print(f"  timeout: {_fmt_duration(current.timeout)} → {_fmt_duration(tighter.timeout)}")

    if getattr(args, "dry_run", False):
        print()
        print("(dry-run: no changes written)")
        return

    # Write updated spec to commands.toml
    reg_cmd._extra["sandbox"] = tighter.to_dict()
    config.save_commands()
    print()
    print("Spec updated in commands.toml.")


def cmd_sandbox_help(args: Any) -> None:
    """Default handler for 'blq sandbox' without subcommand."""
    cmd_sandbox_list(args)
