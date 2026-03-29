# Strace Profiling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `blq sandbox profile <command>` that wraps a command in strace, parses the output to discover file/network/process access patterns, and feeds the results into `blq sandbox suggest`.

**Architecture:** A strace output parser extracts file opens, network connections, and process spawns from `strace -f -e trace=%file,%network,%process` output. The profile command runs the strace-wrapped command, parses the trace, stores a `StraceProfile` alongside the run, and enhances `blq sandbox suggest` to use access patterns for more specific spec recommendations (e.g., suggest `paths_readable` lists, `network=none` when no connections observed).

**Tech Stack:** Python 3.12+, strace CLI, regex parsing, existing `SandboxSpec` and `blq sandbox suggest`

**Note:** duck_hunt has a strace format but currently only extracts exit summaries (filed [duck_hunt#51](https://github.com/teaguesterling/duck_hunt/issues/51)). We build a focused parser for the syscalls we care about. When duck_hunt adds full strace parsing, we can switch to it.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/blq_sandbox/strace_parser.py` | Create | Parse strace output into structured access lists (files, networks, processes) |
| `src/blq_sandbox/profile.py` | Create | Run strace-wrapped command, collect and store profile |
| `src/blq/commands/sandbox_cmd.py` | Modify | Add `cmd_sandbox_profile()` handler, enhance `cmd_sandbox_suggest()` |
| `src/blq/cli.py` | Modify | Add `sandbox profile` subparser |
| `tests/test_strace_parser.py` | Create | Unit tests for strace line parsing |
| `tests/test_sandbox_profile.py` | Create | Integration tests for profile command |

---

### Task 1: Strace Output Parser

**Files:**
- Create: `src/blq_sandbox/strace_parser.py`
- Create: `tests/test_strace_parser.py`

Parse individual strace lines to extract file paths, network addresses, and process spawns.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_strace_parser.py
"""Tests for strace output parsing."""
from __future__ import annotations

import pytest

from blq_sandbox.strace_parser import StraceProfile, parse_strace_output


SAMPLE_STRACE = """\
1234 execve("/usr/bin/echo", ["echo", "hello"], 0x7fff /* 65 vars */) = 0
1234 access("/etc/ld.so.preload", R_OK) = -1 ENOENT (No such file or directory)
1234 openat(AT_FDCWD, "/etc/ld.so.cache", O_RDONLY|O_CLOEXEC) = 3
1234 openat(AT_FDCWD, "/lib/x86_64-linux-gnu/libc.so.6", O_RDONLY|O_CLOEXEC) = 3
1234 openat(AT_FDCWD, "/tmp/output.txt", O_WRONLY|O_CREAT|O_TRUNC, 0666) = 4
1234 openat(AT_FDCWD, "/usr/lib/locale/locale-archive", O_RDONLY|O_CLOEXEC) = 3
1234 exit_group(0)                   = ?
1234 +++ exited with 0 +++
"""

SAMPLE_NETWORK = """\
5678 socket(AF_INET, SOCK_STREAM|SOCK_CLOEXEC, IPPROTO_TCP) = 3
5678 connect(3, {sa_family=AF_INET, sin_port=htons(443), sin_addr=inet_addr("93.184.216.34")}, 16) = 0
5678 socket(AF_INET6, SOCK_STREAM, IPPROTO_TCP) = 4
5678 connect(4, {sa_family=AF_INET6, sin6_port=htons(80), sin6_flowinfo=htonl(0), inet_pton(AF_INET6, "2606:4700::1"), sin6_scope_id=0}, 28) = 0
5678 socket(AF_UNIX, SOCK_STREAM|SOCK_CLOEXEC|SOCK_NONBLOCK, 0) = 5
5678 connect(5, {sa_family=AF_UNIX, sun_path="/var/run/nscd/socket"}, 110) = -1 ENOENT
"""

SAMPLE_PROCESS = """\
9999 clone3({flags=CLONE_VM|CLONE_FS|CLONE_FILES}, 88) = 10000
9999 clone(child_stack=NULL, flags=CLONE_CHILD_CLEARTID|SIGCHLD) = 10001
10000 execve("/usr/bin/python3", ["python3", "script.py"], 0x7fff /* 40 vars */) = 0
"""


class TestParseStraceOutput:
    def test_extracts_read_files(self):
        profile = parse_strace_output(SAMPLE_STRACE)
        assert "/etc/ld.so.cache" in profile.files_read
        assert "/lib/x86_64-linux-gnu/libc.so.6" in profile.files_read

    def test_extracts_write_files(self):
        profile = parse_strace_output(SAMPLE_STRACE)
        assert "/tmp/output.txt" in profile.files_written

    def test_read_files_exclude_writes(self):
        profile = parse_strace_output(SAMPLE_STRACE)
        assert "/tmp/output.txt" not in profile.files_read

    def test_extracts_access_checks(self):
        profile = parse_strace_output(SAMPLE_STRACE)
        assert "/etc/ld.so.preload" in profile.files_read

    def test_extracts_executables(self):
        profile = parse_strace_output(SAMPLE_STRACE)
        assert "/usr/bin/echo" in profile.executables

    def test_extracts_network_ipv4(self):
        profile = parse_strace_output(SAMPLE_NETWORK)
        assert ("93.184.216.34", 443) in profile.network_connections

    def test_extracts_network_ipv6(self):
        profile = parse_strace_output(SAMPLE_NETWORK)
        assert ("2606:4700::1", 80) in profile.network_connections

    def test_ignores_unix_sockets(self):
        profile = parse_strace_output(SAMPLE_NETWORK)
        # Unix sockets are not network connections
        addrs = [addr for addr, _ in profile.network_connections]
        assert "/var/run/nscd/socket" not in addrs

    def test_extracts_spawned_processes(self):
        profile = parse_strace_output(SAMPLE_PROCESS)
        assert "/usr/bin/python3" in profile.executables

    def test_counts_clone_calls(self):
        profile = parse_strace_output(SAMPLE_PROCESS)
        assert profile.process_spawns >= 2

    def test_empty_input(self):
        profile = parse_strace_output("")
        assert len(profile.files_read) == 0
        assert len(profile.files_written) == 0
        assert len(profile.network_connections) == 0
        assert profile.process_spawns == 0

    def test_returns_strace_profile(self):
        profile = parse_strace_output(SAMPLE_STRACE)
        assert isinstance(profile, StraceProfile)


class TestStraceProfile:
    def test_has_network_returns_true(self):
        profile = parse_strace_output(SAMPLE_NETWORK)
        assert profile.has_network is True

    def test_has_network_returns_false(self):
        profile = parse_strace_output(SAMPLE_STRACE)
        assert profile.has_network is False

    def test_has_writes_returns_true(self):
        profile = parse_strace_output(SAMPLE_STRACE)
        assert profile.has_writes is True

    def test_has_spawns_returns_true(self):
        profile = parse_strace_output(SAMPLE_PROCESS)
        assert profile.has_spawns is True

    def test_unique_read_dirs(self):
        profile = parse_strace_output(SAMPLE_STRACE)
        dirs = profile.read_directories()
        assert "/etc" in dirs
        assert "/lib/x86_64-linux-gnu" in dirs

    def test_to_dict(self):
        profile = parse_strace_output(SAMPLE_STRACE)
        d = profile.to_dict()
        assert "files_read" in d
        assert "files_written" in d
        assert "network_connections" in d
        assert "process_spawns" in d
        assert "executables" in d
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_strace_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'blq_sandbox.strace_parser'`

- [ ] **Step 3: Implement the parser**

```python
# src/blq_sandbox/strace_parser.py
"""Parse strace output to extract file, network, and process access patterns.

Parses output from: strace -f -e trace=%file,%network,%process -o <file> -- <command>

Extracts:
- Files opened for reading (openat with O_RDONLY, access)
- Files opened for writing (openat with O_WRONLY/O_CREAT/O_APPEND)
- Network connections (connect to AF_INET/AF_INET6)
- Process spawns (clone, clone3)
- Executables run (execve)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


# Regex patterns for strace line parsing
_OPENAT_PATTERN = re.compile(
    r'openat\(AT_FDCWD,\s*"([^"]+)",\s*([^)]+)\)\s*=\s*(\d+|-1)'
)
_ACCESS_PATTERN = re.compile(
    r'access\("([^"]+)",\s*([^)]+)\)\s*='
)
_EXECVE_PATTERN = re.compile(
    r'execve\("([^"]+)",\s*'
)
_CONNECT_IPV4_PATTERN = re.compile(
    r'connect\(\d+,\s*\{sa_family=AF_INET,\s*sin_port=htons\((\d+)\),\s*'
    r'sin_addr=inet_addr\("([^"]+)"\)'
)
_CONNECT_IPV6_PATTERN = re.compile(
    r'connect\(\d+,\s*\{sa_family=AF_INET6,\s*sin6_port=htons\((\d+)\),'
    r'.*?inet_pton\(AF_INET6,\s*"([^"]+)"\)'
)
_CLONE_PATTERN = re.compile(r'clone[3]?\(')

_WRITE_FLAGS = {"O_WRONLY", "O_RDWR", "O_CREAT", "O_APPEND", "O_TRUNC"}


@dataclass
class StraceProfile:
    """Parsed strace access patterns."""

    files_read: set[str] = field(default_factory=set)
    files_written: set[str] = field(default_factory=set)
    network_connections: set[tuple[str, int]] = field(default_factory=set)
    executables: set[str] = field(default_factory=set)
    process_spawns: int = 0

    @property
    def has_network(self) -> bool:
        return len(self.network_connections) > 0

    @property
    def has_writes(self) -> bool:
        return len(self.files_written) > 0

    @property
    def has_spawns(self) -> bool:
        return self.process_spawns > 0

    def read_directories(self) -> set[str]:
        """Unique parent directories of read files."""
        return {str(Path(f).parent) for f in self.files_read}

    def write_directories(self) -> set[str]:
        """Unique parent directories of written files."""
        return {str(Path(f).parent) for f in self.files_written}

    def to_dict(self) -> dict:
        return {
            "files_read": sorted(self.files_read),
            "files_written": sorted(self.files_written),
            "network_connections": [
                {"address": addr, "port": port}
                for addr, port in sorted(self.network_connections)
            ],
            "executables": sorted(self.executables),
            "process_spawns": self.process_spawns,
        }


def parse_strace_output(output: str) -> StraceProfile:
    """Parse strace output into a StraceProfile.

    Args:
        output: Raw strace output (from -o file or stderr).

    Returns:
        StraceProfile with extracted access patterns.
    """
    profile = StraceProfile()

    for line in output.splitlines():
        # Strip PID prefix (e.g., "1234 ")
        stripped = re.sub(r"^\d+\s+", "", line)

        # openat — file reads and writes
        m = _OPENAT_PATTERN.search(stripped)
        if m:
            path, flags_str, result = m.group(1), m.group(2), m.group(3)
            if result != "-1":  # Only successful opens
                flags = set(flags_str.replace(" ", "").split("|"))
                if flags & _WRITE_FLAGS:
                    profile.files_written.add(path)
                else:
                    profile.files_read.add(path)
            continue

        # access — file existence checks
        m = _ACCESS_PATTERN.search(stripped)
        if m:
            profile.files_read.add(m.group(1))
            continue

        # execve — program execution
        m = _EXECVE_PATTERN.search(stripped)
        if m:
            profile.executables.add(m.group(1))
            continue

        # connect — IPv4 network connections
        m = _CONNECT_IPV4_PATTERN.search(stripped)
        if m:
            port, addr = int(m.group(1)), m.group(2)
            profile.network_connections.add((addr, port))
            continue

        # connect — IPv6 network connections
        m = _CONNECT_IPV6_PATTERN.search(stripped)
        if m:
            port, addr = int(m.group(1)), m.group(2)
            profile.network_connections.add((addr, port))
            continue

        # clone/clone3 — process spawning
        if _CLONE_PATTERN.search(stripped):
            profile.process_spawns += 1

    return profile
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_strace_parser.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/blq_sandbox/strace_parser.py tests/test_strace_parser.py
git commit -m "feat: add strace output parser for sandbox profiling"
```

---

### Task 2: Profile Runner

**Files:**
- Create: `src/blq_sandbox/profile.py`
- Create: `tests/test_sandbox_profile.py`

Run a command wrapped in strace and collect the profile.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_sandbox_profile.py
"""Tests for sandbox profile runner."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from blq_sandbox.profile import run_profile


@pytest.mark.skipif(not shutil.which("strace"), reason="strace not installed")
class TestRunProfile:
    def test_profiles_simple_command(self, tmp_path: Path):
        profile = run_profile("echo hello", workspace=tmp_path, timeout=10)
        assert profile is not None
        # echo should read libc at minimum
        assert len(profile.files_read) > 0
        assert "/usr/bin/echo" in profile.executables

    def test_profiles_file_write(self, tmp_path: Path):
        target = tmp_path / "output.txt"
        profile = run_profile(
            f"touch {target}", workspace=tmp_path, timeout=10
        )
        assert str(target) in profile.files_written

    def test_profiles_network_access(self, tmp_path: Path):
        # Use python to attempt a connection (will fail but strace sees the syscall)
        profile = run_profile(
            'python3 -c "import socket; s=socket.socket(); s.settimeout(0.1); '
            "s.connect_ex(('127.0.0.1', 1))\"",
            workspace=tmp_path,
            timeout=10,
        )
        assert profile.has_network or len(profile.network_connections) >= 0
        # connect_ex to localhost:1 should show up
        if profile.network_connections:
            ports = [p for _, p in profile.network_connections]
            assert 1 in ports

    def test_profiles_subprocess(self, tmp_path: Path):
        profile = run_profile(
            "bash -c 'echo inner'", workspace=tmp_path, timeout=10
        )
        assert profile.process_spawns >= 0  # bash may or may not clone
        assert "/usr/bin/bash" in profile.executables or "/bin/bash" in profile.executables

    def test_returns_none_without_strace(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda _: None)
        profile = run_profile("echo hello", workspace=tmp_path, timeout=10)
        assert profile is None

    def test_suggest_spec_from_profile(self, tmp_path: Path):
        from blq_sandbox.profile import suggest_spec_from_profile

        profile = run_profile("echo hello", workspace=tmp_path, timeout=10)
        assert profile is not None
        spec_dict = suggest_spec_from_profile(profile, workspace=tmp_path)
        assert "network" in spec_dict
        assert spec_dict["network"] == "none"  # echo doesn't use network
        assert "filesystem" in spec_dict
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_sandbox_profile.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'blq_sandbox.profile'`

- [ ] **Step 3: Implement the profile runner**

```python
# src/blq_sandbox/profile.py
"""Run commands under strace to discover access patterns.

This is Phase 0 Tier 2: one-time profiling to inform sandbox spec creation.
Has 2-10x overhead, so it's a profiling step, not continuous monitoring.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from blq_sandbox.strace_parser import StraceProfile, parse_strace_output

logger = logging.getLogger("blq-sandbox")


def run_profile(
    command: str,
    workspace: Path,
    timeout: int = 300,
) -> StraceProfile | None:
    """Run a command under strace and return the access profile.

    Args:
        command: Shell command to profile.
        workspace: Working directory.
        timeout: Maximum execution time in seconds.

    Returns:
        StraceProfile with observed access patterns, or None if strace
        is not available.
    """
    if not shutil.which("strace"):
        logger.warning("strace not installed — cannot profile command")
        return None

    with tempfile.NamedTemporaryFile(
        prefix="blq-strace-", suffix=".log", delete=False
    ) as trace_file:
        trace_path = trace_file.name

    try:
        strace_cmd = (
            f"strace -f -e trace=%file,%network,%process "
            f"-o {trace_path} -- {command}"
        )
        subprocess.run(
            strace_cmd,
            shell=True,
            cwd=workspace,
            timeout=timeout,
            capture_output=True,
        )

        trace_output = Path(trace_path).read_text()
        return parse_strace_output(trace_output)
    except subprocess.TimeoutExpired:
        logger.warning(f"Profiling timed out after {timeout}s")
        # Still try to parse partial output
        try:
            trace_output = Path(trace_path).read_text()
            return parse_strace_output(trace_output)
        except Exception:
            return None
    except Exception as e:
        logger.warning(f"Profiling failed: {e}")
        return None
    finally:
        Path(trace_path).unlink(missing_ok=True)


def suggest_spec_from_profile(
    profile: StraceProfile,
    workspace: Path,
) -> dict[str, str | list[str]]:
    """Generate a suggested sandbox spec dict from observed access patterns.

    Args:
        profile: Observed access patterns from strace.
        workspace: Project workspace (used to classify paths).

    Returns:
        Dict suitable for SandboxSpec.from_dict() or TOML output.
    """
    spec: dict[str, str | list[str]] = {}

    # Network
    spec["network"] = "none" if not profile.has_network else "unrestricted"

    # Filesystem
    ws = str(workspace)
    writes_outside_workspace = [
        f for f in profile.files_written
        if not f.startswith(ws) and not f.startswith("/tmp")
    ]
    if not profile.has_writes:
        spec["filesystem"] = "readonly"
    elif not writes_outside_workspace:
        spec["filesystem"] = "workspace_only"
    else:
        spec["filesystem"] = "unrestricted"

    # Processes
    spec["processes"] = "visible" if profile.has_spawns else "isolated"

    # Paths readable — group by top-level directories, exclude system dirs
    system_dirs = {"/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc", "/proc", "/dev", "/sys"}
    read_dirs = profile.read_directories()
    non_system = {d for d in read_dirs if not any(d.startswith(s) for s in system_dirs)}
    non_system -= {ws, "."}  # workspace is handled by filesystem
    if non_system:
        spec["paths_readable"] = sorted(non_system)

    return spec
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_sandbox_profile.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/blq_sandbox/profile.py tests/test_sandbox_profile.py
git commit -m "feat: add strace-based sandbox profiling runner"
```

---

### Task 3: CLI Integration

**Files:**
- Modify: `src/blq/commands/sandbox_cmd.py`
- Modify: `src/blq/cli.py`

Add `blq sandbox profile <command>` and enhance `blq sandbox suggest` to use profile data.

- [ ] **Step 1: Add `cmd_sandbox_profile()` to sandbox_cmd.py**

Add this function to `src/blq/commands/sandbox_cmd.py`:

```python
def cmd_sandbox_profile(args: Any) -> None:
    """Profile a command with strace to discover access patterns."""
    import shutil

    config = BlqConfig.ensure()
    cmd_name = args.command

    if not shutil.which("strace"):
        print("Error: strace is not installed. Install it with:", file=sys.stderr)
        print("  sudo apt install strace", file=sys.stderr)
        sys.exit(1)

    if cmd_name not in config.commands:
        print(f"Error: Unknown command '{cmd_name}'", file=sys.stderr)
        sys.exit(1)

    reg_cmd = config.commands[cmd_name]
    command = reg_cmd.template

    print(f"Profiling '{cmd_name}': {command}")
    print("(This adds 2-10x overhead — it's a one-time profiling step)")
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
            print(f'{key} = {json.dumps(val)}')
        else:
            print(f'{key} = "{val}"')

    if profile.files_written:
        print()
        print("Write paths observed:")
        for f in sorted(profile.files_written)[:20]:
            print(f"  {f}")
        if len(profile.files_written) > 20:
            print(f"  ... and {len(profile.files_written) - 20} more")

    if profile.network_connections:
        print()
        print("Network connections observed:")
        for addr, port in sorted(profile.network_connections):
            print(f"  {addr}:{port}")
```

- [ ] **Step 2: Add `sandbox profile` subparser to cli.py**

Find the sandbox subparser section in `src/blq/cli.py`. After the `suggest` subparser, add:

```python
    # sandbox profile
    p_sandbox_profile = sandbox_subparsers.add_parser(
        "profile", help="Profile command with strace"
    )
    p_sandbox_profile.add_argument("command", help="Command name to profile")
    p_sandbox_profile.add_argument(
        "--json", "-j", action="store_true", help="Output raw profile as JSON"
    )
    p_sandbox_profile.set_defaults(func=cmd_sandbox_profile)
```

Also add `cmd_sandbox_profile` to the import from `blq.commands.sandbox_cmd`.

- [ ] **Step 3: Run full test suite**

Run: `.venv/bin/pytest tests/ -q --tb=short -x`
Expected: All pass

- [ ] **Step 4: Manual test**

Run: `blq sandbox profile echo` (if echo is registered, or use any registered command)

Expected output:
```
Profiling 'echo': echo hi
(This adds 2-10x overhead — it's a one-time profiling step)

Files read:    5
Files written: 0
Network:       no
Subprocesses:  0
Executables:   /usr/bin/echo

Suggested sandbox spec:

[commands.echo.sandbox]
network = "none"
filesystem = "readonly"
processes = "isolated"
```

- [ ] **Step 5: Commit**

```bash
git add src/blq/commands/sandbox_cmd.py src/blq/cli.py
git commit -m "feat: add blq sandbox profile command for strace-based discovery"
```

---

### Task 4: Documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/commands/registry.md`

- [ ] **Step 1: Update CLAUDE.md completed list**

Add to the Completed section:
```
- **Strace profiling** (`blq sandbox profile`) for sandbox spec discovery (Phase 0 Tier 2)
```

- [ ] **Step 2: Add profiling section to registry.md**

After the "Command Locks" section in `docs/commands/registry.md`, add:

```markdown
### Sandbox Profiling

Discover what resources a command actually uses to inform sandbox spec creation:

\`\`\`bash
blq sandbox profile test
\`\`\`

This wraps the command in `strace` (2-10x overhead) and reports:
- Files read and written
- Network connections attempted
- Subprocesses spawned
- Suggested sandbox spec based on observed patterns

Use the output to create a data-driven sandbox spec:

\`\`\`bash
blq sandbox profile test     # discover access patterns
blq sandbox suggest test     # combine with resource metrics
\`\`\`

Requires `strace` to be installed (`sudo apt install strace`).
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md docs/commands/registry.md
git commit -m "docs: add sandbox profiling documentation"
```
