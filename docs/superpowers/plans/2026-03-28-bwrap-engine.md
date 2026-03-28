# Bwrap Sandbox Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a bubblewrap (bwrap) enforcement engine for the blq sandbox spec system, translating SandboxSpec dimensions into bwrap namespace isolation flags.

**Architecture:** A `BwrapEngine` class conforming to the existing `SandboxEngine` protocol. Given a `SandboxSpec`, it builds a bwrap command line that isolates the command's network, filesystem, PID namespace, and tmpfs. Follows patterns from Anthropic's sandbox-runtime: `--ro-bind / /` as base, selective `--bind` for writable paths, `--die-with-parent` and `--new-session` for safety.

**Tech Stack:** Python 3.12+, bubblewrap (bwrap) CLI, existing `SandboxEngine` protocol from `blq_sandbox.engines`, existing `SandboxSpec` from `blq_sandbox.spec`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/blq_sandbox_bwrap/__init__.py` | Create | `BwrapEngine` class: builds bwrap args from SandboxSpec, `BwrapCollector` placeholder |
| `src/blq_sandbox_bwrap/args.py` | Create | Pure function: `build_bwrap_args(spec, workspace, attempt_id) -> list[str]` |
| `tests/test_bwrap_args.py` | Create | Unit tests for arg building (no bwrap needed) |
| `tests/test_bwrap_engine.py` | Create | Integration tests (requires bwrap on system) |
| `pyproject.toml` | Modify | Add entry point `bwrap = "blq_sandbox_bwrap:BwrapEngine"` and package |

---

### Task 1: Bwrap Arg Builder — Core Dimensions

**Files:**
- Create: `src/blq_sandbox_bwrap/args.py`
- Create: `tests/test_bwrap_args.py`

This is the core logic: translate a `SandboxSpec` into a list of bwrap CLI arguments. Pure function, no side effects, fully testable without bwrap installed.

- [ ] **Step 1: Write failing tests for base args and network isolation**

```python
# tests/test_bwrap_args.py
"""Tests for bwrap argument building from SandboxSpec."""
from __future__ import annotations

from pathlib import Path

import pytest

from blq_sandbox.spec import SandboxSpec
from blq_sandbox_bwrap.args import build_bwrap_args


class TestBaseArgs:
    """Every bwrap invocation gets these safety flags."""

    def test_includes_die_with_parent(self):
        spec = SandboxSpec(network="none", filesystem="readonly")
        args = build_bwrap_args(spec, Path("/project"), "abc123")
        assert "--die-with-parent" in args

    def test_includes_new_session(self):
        spec = SandboxSpec(network="none", filesystem="readonly")
        args = build_bwrap_args(spec, Path("/project"), "abc123")
        assert "--new-session" in args

    def test_includes_dev_and_proc(self):
        spec = SandboxSpec(network="none", filesystem="readonly")
        args = build_bwrap_args(spec, Path("/project"), "abc123")
        assert "--dev" in args
        assert "--proc" in args

    def test_returns_list_of_strings(self):
        spec = SandboxSpec(network="none", filesystem="readonly")
        args = build_bwrap_args(spec, Path("/project"), "abc123")
        assert isinstance(args, list)
        assert all(isinstance(a, str) for a in args)


class TestNetworkIsolation:
    """Network dimension maps to --unshare-net."""

    def test_network_none_adds_unshare_net(self):
        spec = SandboxSpec(network="none", filesystem="unrestricted")
        args = build_bwrap_args(spec, Path("/project"), "abc123")
        assert "--unshare-net" in args

    def test_network_localhost_adds_unshare_net(self):
        spec = SandboxSpec(network="localhost", filesystem="unrestricted")
        args = build_bwrap_args(spec, Path("/project"), "abc123")
        # localhost still uses unshare-net (loopback available inside namespace)
        assert "--unshare-net" in args

    def test_network_unrestricted_no_unshare(self):
        spec = SandboxSpec(network="unrestricted", filesystem="unrestricted")
        args = build_bwrap_args(spec, Path("/project"), "abc123")
        assert "--unshare-net" not in args


class TestFilesystemIsolation:
    """Filesystem dimension controls bind mounts."""

    def test_readonly_uses_ro_bind(self):
        spec = SandboxSpec(network="none", filesystem="readonly")
        args = build_bwrap_args(spec, Path("/project"), "abc123")
        assert "--ro-bind" in args
        # Root is read-only
        idx = args.index("--ro-bind")
        assert args[idx + 1] == "/"
        assert args[idx + 2] == "/"

    def test_readonly_workspace_not_writable(self):
        spec = SandboxSpec(network="none", filesystem="readonly")
        args = build_bwrap_args(spec, Path("/project"), "abc123")
        # Should NOT have --bind /project /project (writable)
        bind_pairs = []
        for i, a in enumerate(args):
            if a == "--bind" and i + 2 < len(args):
                bind_pairs.append((args[i + 1], args[i + 2]))
        assert ("/project", "/project") not in bind_pairs

    def test_workspace_only_binds_workspace_rw(self):
        spec = SandboxSpec(network="none", filesystem="workspace_only")
        args = build_bwrap_args(spec, Path("/project"), "abc123")
        # Root read-only
        assert "--ro-bind" in args
        # Workspace writable
        bind_pairs = []
        for i, a in enumerate(args):
            if a == "--bind" and i + 2 < len(args):
                bind_pairs.append((args[i + 1], args[i + 2]))
        assert ("/project", "/project") in bind_pairs

    def test_unrestricted_filesystem_uses_bind(self):
        spec = SandboxSpec(network="none", filesystem="unrestricted")
        args = build_bwrap_args(spec, Path("/project"), "abc123")
        # Full writable bind
        idx = args.index("--bind")
        assert args[idx + 1] == "/"
        assert args[idx + 2] == "/"


class TestPidIsolation:
    """Processes dimension maps to --unshare-pid."""

    def test_isolated_adds_unshare_pid(self):
        spec = SandboxSpec(processes="isolated", filesystem="unrestricted")
        args = build_bwrap_args(spec, Path("/project"), "abc123")
        assert "--unshare-pid" in args

    def test_visible_no_unshare_pid(self):
        spec = SandboxSpec(processes="visible", filesystem="unrestricted")
        args = build_bwrap_args(spec, Path("/project"), "abc123")
        assert "--unshare-pid" not in args


class TestTmpfs:
    """Tmpfs dimension creates a writable /tmp."""

    def test_tmpfs_mounts_tmp(self):
        spec = SandboxSpec(filesystem="readonly", tmpfs=100 * 1024 * 1024)
        args = build_bwrap_args(spec, Path("/project"), "abc123")
        assert "--tmpfs" in args
        idx = args.index("--tmpfs")
        assert args[idx + 1] == "/tmp"

    def test_tmpfs_sets_size(self):
        spec = SandboxSpec(filesystem="readonly", tmpfs=100 * 1024 * 1024)
        args = build_bwrap_args(spec, Path("/project"), "abc123")
        size_idx = args.index("--size")
        assert args[size_idx + 1] == str(100 * 1024 * 1024)

    def test_no_tmpfs_when_not_specified(self):
        spec = SandboxSpec(filesystem="readonly")
        args = build_bwrap_args(spec, Path("/project"), "abc123")
        # No explicit tmpfs mount (bwrap default behavior)
        tmpfs_dests = [args[i + 1] for i, a in enumerate(args) if a == "--tmpfs" and i + 1 < len(args)]
        assert "/tmp" not in tmpfs_dests


class TestPathsHidden:
    """Hidden paths are replaced with empty tmpfs."""

    def test_hidden_paths_get_tmpfs(self):
        spec = SandboxSpec(
            filesystem="readonly",
            paths_hidden=["/home", "/root"],
        )
        args = build_bwrap_args(spec, Path("/project"), "abc123")
        tmpfs_dests = [args[i + 1] for i, a in enumerate(args) if a == "--tmpfs" and i + 1 < len(args)]
        assert "/home" in tmpfs_dests
        assert "/root" in tmpfs_dests


class TestChdir:
    """Sandbox should chdir into the workspace."""

    def test_chdir_to_workspace(self):
        spec = SandboxSpec(filesystem="workspace_only")
        args = build_bwrap_args(spec, Path("/my/project"), "abc123")
        idx = args.index("--chdir")
        assert args[idx + 1] == "/my/project"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_bwrap_args.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'blq_sandbox_bwrap'`

- [ ] **Step 3: Implement the arg builder**

```python
# src/blq_sandbox_bwrap/args.py
"""Build bwrap command-line arguments from a SandboxSpec.

Pure function — no side effects, no subprocess calls. The returned list
is the args to pass to bwrap before the `--` and the user command.
"""
from __future__ import annotations

from pathlib import Path

from blq_sandbox.spec import SandboxSpec


def build_bwrap_args(
    spec: SandboxSpec,
    workspace: Path,
    attempt_id: str,
) -> list[str]:
    """Translate a SandboxSpec into bwrap CLI arguments.

    Args:
        spec: The sandbox specification to enforce.
        workspace: Project root directory (for workspace_only writes).
        attempt_id: Attempt UUID (for unique scope naming).

    Returns:
        List of bwrap arguments (before -- and the command).
    """
    args: list[str] = []

    # Safety: always kill child if parent dies, isolate terminal session
    args.extend(["--die-with-parent", "--new-session"])

    # Filesystem: base mount strategy
    if spec.filesystem == "unrestricted":
        args.extend(["--bind", "/", "/"])
    else:
        # Start with everything read-only
        args.extend(["--ro-bind", "/", "/"])
        # Add writable workspace for workspace_only / scoped_write
        if spec.filesystem in ("workspace_only", "scoped_write"):
            ws = str(workspace)
            args.extend(["--bind", ws, ws])

    # Dev and proc (always needed for most commands)
    args.extend(["--dev", "/dev"])
    args.extend(["--proc", "/proc"])

    # Network isolation
    if spec.network in ("none", "localhost"):
        args.append("--unshare-net")

    # PID namespace isolation
    if spec.processes == "isolated":
        args.append("--unshare-pid")

    # Tmpfs: writable scratch space
    if spec.tmpfs is not None:
        args.extend(["--size", str(spec.tmpfs)])
        args.extend(["--tmpfs", "/tmp"])

    # Hidden paths: replace with empty tmpfs to hide contents
    for path in spec.paths_hidden:
        args.extend(["--tmpfs", path])

    # Working directory
    args.extend(["--chdir", str(workspace)])

    return args
```

Also create the package init:

```python
# src/blq_sandbox_bwrap/__init__.py
"""blq-sandbox-bwrap: bubblewrap enforcement engine for sandbox specs."""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_bwrap_args.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/blq_sandbox_bwrap/ tests/test_bwrap_args.py
git commit -m "feat: add bwrap arg builder from SandboxSpec"
```

---

### Task 2: BwrapEngine Class

**Files:**
- Modify: `src/blq_sandbox_bwrap/__init__.py`
- Modify: `pyproject.toml`
- Create: `tests/test_bwrap_engine.py`

The engine class conforms to `SandboxEngine` protocol and plugs into the existing engine discovery system.

- [ ] **Step 1: Write failing tests for the engine**

```python
# tests/test_bwrap_engine.py
"""Tests for the BwrapEngine class."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from blq_sandbox.spec import SandboxSpec
from blq_sandbox_bwrap import BwrapEngine


@pytest.fixture
def engine():
    return BwrapEngine()


class TestBwrapEngineProtocol:
    """BwrapEngine satisfies the SandboxEngine protocol."""

    def test_has_name(self, engine):
        assert engine.name == "bwrap"

    def test_has_capabilities(self, engine):
        assert "network" in engine.capabilities
        assert "filesystem" in engine.capabilities
        assert "processes" in engine.capabilities
        assert "tmpfs" in engine.capabilities

    def test_wrap_returns_string(self, engine):
        spec = SandboxSpec(network="none", filesystem="readonly")
        result = engine.wrap("echo hello", spec, Path("/project"), "abc-123")
        assert isinstance(result, str)

    def test_wrap_starts_with_bwrap(self, engine):
        spec = SandboxSpec(network="none", filesystem="readonly")
        result = engine.wrap("echo hello", spec, Path("/project"), "abc-123")
        assert result.startswith("bwrap ")

    def test_wrap_ends_with_original_command(self, engine):
        spec = SandboxSpec(network="none", filesystem="readonly")
        result = engine.wrap("echo hello", spec, Path("/project"), "abc-123")
        assert result.endswith("-- echo hello")

    def test_wrap_includes_die_with_parent(self, engine):
        spec = SandboxSpec(network="none", filesystem="readonly")
        result = engine.wrap("echo hello", spec, Path("/project"), "abc-123")
        assert "--die-with-parent" in result

    def test_collector_returns_none(self, engine):
        """Bwrap engine has no collector (cgroup collection is systemd engine's job)."""
        spec = SandboxSpec(network="none", filesystem="readonly")
        result = engine.collector(spec, "abc-123")
        assert result is None


@pytest.mark.skipif(not shutil.which("bwrap"), reason="bwrap not installed")
class TestBwrapEngineIntegration:
    """Integration tests that actually run bwrap."""

    def test_simple_command_in_sandbox(self, engine):
        import subprocess

        spec = SandboxSpec(network="none", filesystem="readonly", processes="isolated")
        wrapped = engine.wrap("echo sandbox-works", spec, Path("/tmp"), "test-int")
        result = subprocess.run(
            wrapped, shell=True, capture_output=True, text=True, timeout=10
        )
        assert result.returncode == 0
        assert "sandbox-works" in result.stdout

    def test_network_blocked(self, engine):
        """With network=none, network access should fail."""
        import subprocess

        spec = SandboxSpec(network="none", filesystem="readonly")
        # Try to reach localhost — should fail in network namespace
        wrapped = engine.wrap(
            "python3 -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:1')\"",
            spec, Path("/tmp"), "test-net",
        )
        result = subprocess.run(
            wrapped, shell=True, capture_output=True, text=True, timeout=10
        )
        assert result.returncode != 0

    def test_readonly_blocks_write(self, engine, tmp_path):
        """With filesystem=readonly, writes outside sandbox should fail."""
        import subprocess

        target = tmp_path / "canary.txt"
        spec = SandboxSpec(network="none", filesystem="readonly")
        wrapped = engine.wrap(
            f"touch {target}", spec, Path("/tmp"), "test-ro",
        )
        result = subprocess.run(
            wrapped, shell=True, capture_output=True, text=True, timeout=10
        )
        assert not target.exists()

    def test_workspace_only_allows_workspace_write(self, engine, tmp_path):
        """With filesystem=workspace_only, writes to workspace should succeed."""
        import subprocess

        target = tmp_path / "output.txt"
        spec = SandboxSpec(network="none", filesystem="workspace_only")
        wrapped = engine.wrap(
            f"touch {target}", spec, tmp_path, "test-ws",
        )
        result = subprocess.run(
            wrapped, shell=True, capture_output=True, text=True, timeout=10
        )
        assert result.returncode == 0
        assert target.exists()

    def test_preset_test(self, engine):
        """The 'test' preset should work end-to-end."""
        import subprocess

        spec = SandboxSpec.from_preset("test")
        wrapped = engine.wrap("echo preset-ok", spec, Path("/tmp"), "test-preset")
        result = subprocess.run(
            wrapped, shell=True, capture_output=True, text=True, timeout=10
        )
        assert result.returncode == 0
        assert "preset-ok" in result.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_bwrap_engine.py -v`
Expected: FAIL — `ImportError: cannot import name 'BwrapEngine' from 'blq_sandbox_bwrap'`

- [ ] **Step 3: Implement BwrapEngine**

```python
# src/blq_sandbox_bwrap/__init__.py
"""blq-sandbox-bwrap: bubblewrap enforcement engine for sandbox specs."""
from __future__ import annotations

from pathlib import Path

from blq.ext import Collector
from blq_sandbox.spec import SandboxSpec
from blq_sandbox_bwrap.args import build_bwrap_args


class BwrapEngine:
    """Bubblewrap (bwrap) sandbox enforcement engine.

    Translates SandboxSpec dimensions into bwrap namespace isolation:
    - network: --unshare-net
    - filesystem: --ro-bind / --bind mount strategy
    - processes: --unshare-pid
    - tmpfs: --tmpfs with --size
    - paths_hidden: --tmpfs overlays

    Does NOT handle: memory, cpu (those are cgroup limits — use systemd engine).
    """

    name = "bwrap"
    capabilities = {"network", "filesystem", "processes", "tmpfs", "paths_hidden", "paths_readable"}

    def wrap(
        self, command: str, spec: SandboxSpec, workspace: Path, attempt_id: str
    ) -> str:
        args = build_bwrap_args(spec, workspace, attempt_id)
        parts = ["bwrap"] + args + ["--", command]
        return " ".join(parts)

    def collector(self, spec: SandboxSpec, attempt_id: str) -> Collector | None:
        return None
```

- [ ] **Step 4: Register entry point in pyproject.toml**

Add to `[project.entry-points."blq.sandbox.engines"]`:
```toml
bwrap = "blq_sandbox_bwrap:BwrapEngine"
```

Add to `[tool.hatch.build.targets.wheel]` packages list:
```toml
packages = ["src/blq", "src/blq_sandbox", "src/blq_sandbox_systemd", "src/blq_sandbox_bwrap"]
```

- [ ] **Step 5: Reinstall the package**

Run: `.venv/bin/pip install -e ".[dev]" -q`

This is needed so the entry point is discoverable.

- [ ] **Step 6: Run tests**

Run: `.venv/bin/pytest tests/test_bwrap_engine.py tests/test_bwrap_args.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add src/blq_sandbox_bwrap/ tests/test_bwrap_engine.py pyproject.toml
git commit -m "feat: add bwrap sandbox enforcement engine"
```

---

### Task 3: Engine Discovery Integration Test

**Files:**
- Modify: `tests/test_bwrap_engine.py`

Verify the bwrap engine is discovered by the existing engine loading system.

- [ ] **Step 1: Add discovery test**

Append to `tests/test_bwrap_engine.py`:

```python
class TestBwrapEngineDiscovery:
    """Verify bwrap engine is found by the extension system."""

    def test_load_engines_finds_bwrap(self):
        from blq_sandbox.engines import load_engines

        engines = load_engines()
        assert "bwrap" in engines
        assert engines["bwrap"].name == "bwrap"

    def test_select_engines_picks_bwrap_for_network(self):
        from blq_sandbox.engines import load_engines, select_engines

        spec = SandboxSpec(network="none", filesystem="readonly", processes="isolated")
        engines = load_engines()
        selected = select_engines(spec, engines)
        engine_names = [e.name for e in selected]
        assert "bwrap" in engine_names

    def test_bwrap_covers_more_than_systemd(self):
        from blq_sandbox.engines import load_engines

        engines = load_engines()
        if "bwrap" in engines and "systemd" in engines:
            bwrap_caps = engines["bwrap"].capabilities
            systemd_caps = engines["systemd"].capabilities
            # bwrap covers network/filesystem/processes which systemd doesn't
            assert "network" in bwrap_caps
            assert "network" not in systemd_caps
```

- [ ] **Step 2: Run test**

Run: `.venv/bin/pytest tests/test_bwrap_engine.py::TestBwrapEngineDiscovery -v`
Expected: All PASS

- [ ] **Step 3: Run full test suite**

Run: `.venv/bin/pytest tests/ -q --tb=short`
Expected: All pass (1079+)

- [ ] **Step 4: Commit**

```bash
git add tests/test_bwrap_engine.py
git commit -m "test: verify bwrap engine discovery and selection"
```

---

### Task 4: Documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/design/design-sandbox-specs.md` (update Phase 2 status)

- [ ] **Step 1: Update CLAUDE.md completed list**

Add to the Completed section:
```
- **Bwrap sandbox engine** for namespace isolation (network, filesystem, PID, tmpfs)
```

- [ ] **Step 2: Add a note in the design doc Phase 2 section**

At the end of the Phase 2 section in `docs/design/design-sandbox-specs.md`, add:

```
**Status**: bwrap engine implemented (`blq_sandbox_bwrap`). Covers network
(`--unshare-net`), filesystem (`--ro-bind`/`--bind`), PID (`--unshare-pid`),
tmpfs (`--tmpfs`/`--size`), and hidden paths. Memory and CPU enforcement
remain with the systemd engine (cgroup limits). nsjail engine is future work
for seccomp support.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md docs/design/design-sandbox-specs.md
git commit -m "docs: document bwrap sandbox engine implementation"
```
