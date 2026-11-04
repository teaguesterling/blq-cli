"""Every workspace tool names its workspace, or is refused.

The resolution order is the `cwd=` argument, then runtime `active_root`, then
the server's own process cwd.  2.0.0 refused that last step, and blq-cli#59
put it back: 69 project `.mcp.json` files declare a blq server with no
BLQ_ACTIVE_ROOT, so a bare call raised and the server read as down.

The fallback does not reopen #55, because the mismatch was the fault, not
the fallback.  `run` and `exec` pass the resolved root to the child, so the
same directory executes the run and reads it back.  What a bare call loses
is only the guarantee that the caller named the workspace on purpose.

Three things have to agree on the answer, and before this change they did
not (blq-cli#55):

* the child `blq exec`/`blq run` process, which found `.bird/` from its own
  cwd and so wrote the log into the server's workspace;
* `_get_storage()`, which honoured `active_root` and so read a different one;
* `BlqConfig.find()`, which resolves from the process cwd and supplies both
  the registered-command registry and the `mcp.disabled_tools` policy.

So the decisive test here is a round trip, not a `pwd`: a run made through
one workspace has to be readable back through the same one.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from blq import runtime as rt
from blq.runtime import BlqRuntimeConfig, update_runtime

fastmcp = pytest.importorskip("fastmcp")
if fastmcp:
    from fastmcp import Client


def full_text(result):
    """Everything the call returned, content blocks included.

    `OutputResult` declares only streams/ref/run_id/error, so the captured log
    text arrives in the result's content blocks rather than its structured
    data. A round-trip assertion has to look at both.
    """
    parts = [str(getattr(result, "data", ""))]
    for block in getattr(result, "content", None) or []:
        parts.append(getattr(block, "text", "") or "")
    return "\n".join(parts)


def get_data(result):
    """Unwrap a CallToolResult into a dict.

    Tools whose return annotation is a TypedDict (the #47 outputSchema work)
    come back as a pydantic model rather than a dict, so both shapes have to
    be handled or an assertion fails on the wrapper instead of the value.
    """
    data = result.data if hasattr(result, "data") else result
    if isinstance(data, dict):
        return data
    if hasattr(data, "model_dump"):
        return data.model_dump()
    if hasattr(data, "__dict__"):
        return dict(vars(data))
    return {"_repr": str(data)}


@pytest.fixture(autouse=True)
def _no_production_seed(monkeypatch):
    """Opt out of conftest's production-style seed.

    conftest seeds the workspace from the process cwd so the rest of the suite
    can call without `cwd=`, the way a launched server can. This module tests
    the contract underneath that, so it needs the real resolution: the `cwd=`
    argument, then active_root, then nothing.
    """
    from blq import runtime as rt

    monkeypatch.setattr(rt, "resolve_storage_root", lambda: rt.get_runtime().active_root)


@pytest.fixture(autouse=True)
def _isolate_runtime():
    rt._runtime = BlqRuntimeConfig()
    rt._seed = BlqRuntimeConfig()
    yield
    rt._runtime = BlqRuntimeConfig()
    rt._seed = BlqRuntimeConfig()


@pytest.fixture(autouse=True)
def _reset_root_fallback_notice():
    """The announce-once flag is module state; clear it between tests.

    Without this, whichever test ran first consumes the notice and the test
    that asserts it sees nothing -- a pass or fail decided by test order.
    """
    from blq import serve

    serve._root_fallback_announced = False
    yield
    serve._root_fallback_announced = False


@pytest.fixture(autouse=True)
def _reset_disabled_tools():
    """The disabled-tools cache is module state; clear it between tests."""
    from blq import serve

    serve._disabled_tools = None
    yield
    serve._disabled_tools = None


def _init(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "blq", "init", "--no-mcp", "--yes", "--no-gitignore"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    return path


@pytest.fixture
def two_projects(tmp_path, monkeypatch):
    """B is the server's cwd and a valid workspace; X is the target.

    B being initialized is the point.  The failure guarded against is not
    "server outside a workspace", it is "server inside the wrong one", which
    is the case that answers without complaining.
    """
    server_repo = _init(tmp_path / "server_repo")
    target = _init(tmp_path / "target_repo")
    monkeypatch.chdir(server_repo)
    return server_repo, target


@pytest.fixture
def server():
    from blq.serve import mcp

    return mcp


class TestRunsGoToTheNamedWorkspace:
    """A run made through one workspace must be readable back through it.

    This is the assertion a `cwd` on exec alone does not satisfy. The child
    process walks up from its own cwd to find `.bird/`, so without a cwd the
    run is recorded in the server's workspace; and `_get_storage` resolved
    from `active_root`, so the read looked somewhere else again. Checking that
    `pwd` printed the right directory would pass while the two halves still
    disagreed, so the check here is the round trip plus the negative: the
    server's own workspace must not have gained a run.
    """

    @pytest.mark.asyncio
    async def test_exec_executes_in_the_named_directory(self, server, two_projects):
        _, target = two_projects
        (target / "only-in-target.txt").write_text("x\n")

        async with Client(server) as client:
            raw = await client.call_tool(
                "exec",
                {"command": "ls", "args": ["only-in-target.txt"], "cwd": str(target)},
            )
            result = get_data(raw)
        assert result["status"] == "OK", result

    @pytest.mark.asyncio
    async def test_round_trip_from_the_workspace_root(self, server, two_projects):
        server_repo, target = two_projects

        async with Client(server) as client:
            raw = await client.call_tool(
                "exec",
                {"command": "echo", "args": ["needle-in-target"], "cwd": str(target)},
            )
            assert get_data(raw)["status"] == "OK", get_data(raw)

            there = get_data(await client.call_tool("history", {"limit": 5, "cwd": str(target)}))
            here = get_data(
                await client.call_tool("history", {"limit": 5, "cwd": str(server_repo)})
            )

        assert len(there.get("runs", [])) == 1, f"target workspace: {there}"
        assert there["runs"][0]["source_name"] == "echo", there
        assert here.get("runs", []) == [], f"the server's own workspace gained a run: {here}"

    @pytest.mark.asyncio
    async def test_round_trip_from_a_subdirectory(self, server, two_projects):
        """`.bird/` is found by walking up, so both sides must walk the same way.

        Opening `<root>/.bird` directly would split them here: the child would
        record the run in the workspace above while the read found nothing.
        """
        server_repo, target = two_projects
        sub = target / "src" / "deep"
        sub.mkdir(parents=True)

        async with Client(server) as client:
            raw = await client.call_tool(
                "exec", {"command": "echo", "args": ["needle-from-sub"], "cwd": str(sub)}
            )
            assert get_data(raw)["status"] == "OK", get_data(raw)

            there = get_data(await client.call_tool("history", {"limit": 5, "cwd": str(sub)}))
            here = get_data(
                await client.call_tool("history", {"limit": 5, "cwd": str(server_repo)})
            )

        assert len(there.get("runs", [])) == 1, f"read from the subdirectory: {there}"
        assert here.get("runs", []) == [], f"the server's own workspace gained a run: {here}"


class TestNoRootFallsBackToTheProcessCwd:
    """A bare call answers from the server's own directory, and says so.

    The refusal this replaces was right about the risk and wrong about the
    deployment.  A directory that happens to be a workspace answers
    confidently about a project nobody asked about, which is why #56 removed
    the fallback; but almost every launched server has no BLQ_ACTIVE_ROOT, so
    removing it refused every call instead of the ambiguous ones.

    The fallback is therefore announced rather than silent, and `cwd=` remains
    the way a caller says which workspace it means.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "tool,args",
        [
            ("status", {}),
            ("history", {"limit": 2}),
            ("exec", {"command": "echo", "args": ["hi"]}),
            ("commands", {}),
        ],
    )
    async def test_bare_call_is_answered(self, server, two_projects, tool, args):
        """No tool raises for the want of a named workspace."""
        async with Client(server) as client:
            raw = await client.call_tool(tool, args)

        assert "no_root" not in full_text(raw), full_text(raw)

    @pytest.mark.asyncio
    async def test_a_bare_run_is_readable_through_a_bare_read(self, server, two_projects):
        """The #55 invariant has to survive the fallback.

        This is the test that would catch a fallback applied to the read but
        not to the child: the run would land in one workspace and the history
        would be taken from another.  Both halves resolve the same way here,
        so one bare call must be able to see the other's run.
        """
        server_repo, _ = two_projects
        async with Client(server) as client:
            await client.call_tool("exec", {"command": "echo", "args": ["fallback"]})
            raw = await client.call_tool("history", {"limit": 5})
            runs = get_data(raw).get("runs", [])

        assert len(runs) == 1, f"a bare read did not see the bare run: {runs}"
        assert (server_repo / ".bird").is_dir()

    @pytest.mark.asyncio
    async def test_the_fallback_is_announced(self, server, two_projects, capsys):
        """Silent is the one thing this must not be.

        The warning cannot travel in the return value: every tool declares a
        TypedDict return and fastmcp drops keys the schema does not name, which
        is the same reason #56 raised instead of returning an error dict.  So
        stderr is the only channel, and this pins that it is used.
        """
        async with Client(server) as client:
            await client.call_tool("status", {})

        assert "no_root_fallback" in capsys.readouterr().err

    @pytest.mark.asyncio
    async def test_active_root_satisfies_the_requirement(self, server, two_projects):
        _, target = two_projects
        update_runtime({"active_root": str(target)})
        async with Client(server) as client:
            raw = await client.call_tool("status", {})
            result = get_data(raw)
        assert "no_root" not in str(result), result

    @pytest.mark.asyncio
    async def test_a_root_that_is_not_a_directory_says_so(self, server, two_projects):
        """An unexpanded .mcp.json placeholder is the live case for this."""
        update_runtime({"active_root": "${CLAUDE_PROJECT_DIR}"})
        async with Client(server) as client:
            with pytest.raises(Exception) as excinfo:
                await client.call_tool("status", {})
        message = str(excinfo.value)
        assert "bad_root" in message, message
        assert "${CLAUDE_PROJECT_DIR}" in message, message


class TestConfigComesFromTheNamedWorkspace:
    """The registry and the policy both live in the workspace's config.toml."""

    @pytest.mark.asyncio
    async def test_registered_command_registry_is_the_targets(self, server, two_projects):
        """A prefix match must use the target's registry, not the server's.

        Otherwise `exec(command=..., cwd=X)` reroutes through whatever the
        server's own workspace happens to have registered under that prefix,
        and runs that definition instead.
        """
        _, target = two_projects
        async with Client(server) as client:
            reg = get_data(
                await client.call_tool(
                    "register_command",
                    {"name": "greet", "cmd": "echo hi", "cwd": str(target)},
                )
            )
            assert reg.get("success") is not False, reg

            result = get_data(
                await client.call_tool(
                    "exec", {"command": "echo", "args": ["hi"], "cwd": str(target)}
                )
            )
        # `matched_command` is not declared in the tool's output schema, so
        # fastmcp drops it. The run_ref carries the same evidence: the run was
        # recorded under the target's command name, which only happens if the
        # match came from the target's registry.
        assert str(result.get("run_ref", "")).startswith("greet:"), result

    @pytest.mark.asyncio
    async def test_disabled_tools_policy_is_the_targets(self, server, two_projects):
        """X's own mcp.disabled_tools has to apply to a call aimed at X.

        Without this, a per-call cwd runs commands in a workspace whose policy
        file was never read.
        """
        _, target = two_projects
        cfg = target / ".bird" / "config.toml"
        cfg.write_text(cfg.read_text() + '\n[mcp]\ndisabled_tools = ["exec"]\n')

        # The house contract for a disabled tool is PermissionError, which the
        # client surfaces as a tool error rather than a result.
        async with Client(server) as client:
            with pytest.raises(Exception) as excinfo:
                await client.call_tool(
                    "exec", {"command": "echo", "args": ["hi"], "cwd": str(target)}
                )
        assert "disabled" in str(excinfo.value).lower(), excinfo.value

    @pytest.mark.asyncio
    async def test_server_policy_still_applies_first(self, server, two_projects):
        """A server-level disable must not be escapable by naming a workspace.

        The cached server set is checked before the root is resolved, so a
        caller cannot point cwd= at a permissive project to get around it.
        """
        from blq import serve as serve_mod

        _, target = two_projects
        serve_mod._disabled_tools = {"exec"}
        async with Client(server) as client:
            with pytest.raises(Exception) as excinfo:
                await client.call_tool(
                    "exec", {"command": "echo", "args": ["hi"], "cwd": str(target)}
                )
        assert "disabled" in str(excinfo.value).lower(), excinfo.value
