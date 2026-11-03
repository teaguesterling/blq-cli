"""MCP tools must publish the NAMES of the keys they return.

fastmcp derives `outputSchema` from a tool's return annotation. A bare
`dict[str, Any]` becomes:

    {"type": "object", "additionalProperties": true}

which tells a caller it will receive *an object* and nothing more. A
programmatic consumer then has to guess the keys, and a wrong guess produces
a program that validates, runs, and answers incorrectly.

Measured by a session driving four local models against this suite through
lackpy, which generates one restricted-Python program per intent:

    0/24 correct while 17/24 called the correct tool

`len()` on `events`' returned dict gives 2 — its key count — where the answer
was 1. Annotating the return types is the whole fix: the wire format already
carries the property names, nothing else changes, and it helps every
programmatic caller rather than one harness.

See #47.
"""

import pytest

fastmcp = pytest.importorskip("fastmcp")


async def _schema_for(tool_name):
    from fastmcp import Client

    from blq.serve import mcp

    async with Client(mcp) as client:
        for tool in await client.list_tools():
            if tool.name == tool_name:
                return tool.outputSchema
    raise AssertionError(f"tool {tool_name!r} not published")


def _properties(schema):
    """The named properties a caller can rely on.

    fastmcp wraps a non-object return in {"result": ...}; for a TypedDict it
    emits the properties directly. Accept either, and look through the wrapper
    so the assertion is about the tool's shape, not fastmcp's packaging.
    """
    assert schema is not None, "tool publishes no outputSchema at all"
    props = schema.get("properties") or {}
    if set(props) == {"result"}:
        inner = props["result"]
        props = inner.get("properties") or {}
    return props


# Keys taken from the impl's own `return {...}` statements, not from docs.
EXPECTED = {
    "events": {"events", "total_count"},
    "status": {"sources"},
    "history": {"runs"},
    "query": {"rows", "columns", "row_count"},
    "inspect": {"events", "found", "total"},
    "output": {"streams", "ref"},
    "diff": {"new", "fixed", "summary"},
    "commands": {"commands"},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name,expected", sorted(EXPECTED.items()))
async def test_tool_publishes_named_properties(tool_name, expected):
    schema = await _schema_for(tool_name)
    props = _properties(schema)
    missing = expected - set(props)
    assert not missing, (
        f"{tool_name} publishes no property names for {sorted(missing)} — "
        f"got {sorted(props) or 'nothing'}. A caller cannot write code "
        f"against this."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", sorted(EXPECTED))
async def test_schema_is_not_an_open_object(tool_name):
    """`additionalProperties: true` with no properties is the defect."""
    schema = await _schema_for(tool_name)
    props = _properties(schema)
    assert props, f"{tool_name}'s outputSchema names no properties at all"


# ---------------------------------------------------------------------------
# The other direction, which the tests above cannot see.
#
# They assert that a declared key is published. They do not assert that a
# RETURNED key is declared -- and fastmcp coerces a tool's value to its
# declared schema, so a key the impl returns but the TypedDict omits is
# DELETED before the caller sees it. An incomplete TypedDict is silent data
# loss, not a loose type.
#
# Measured 2026-11-03, against the schemas as #47 left them:
#
#   inspect("1:1")        returned 13 keys; the caller received none of them
#   register_command()    lost `existing`, `command`, `format_detected`
#   exec()                lost `cmd`, `duration_sec`, `output_stats`, `preview`
#   ci_check()            lost `has_new_errors` -- declared as `has_errors`,
#                         a near-miss name, which is the same loss and harder
#                         to see
#   output()              did not declare `content` at all, so the tool whose
#                         job is returning captured text returned none
#
# Eleven tests in this suite were failing on exactly that, and two more were
# found while fixing blq-cli#55. These tests pin the keys that were lost.


@pytest.fixture
def project_with_a_run(initialized_project):
    """An initialized project holding one failed run with one real error."""
    import subprocess
    import sys

    script = initialized_project / "build.sh"
    script.write_text('#!/bin/bash\necho "src/main.c:15:5: error: undefined variable"\nexit 1\n')
    script.chmod(0o755)
    subprocess.run(
        [sys.executable, "-m", "blq", "exec", "--quiet", str(script)],
        cwd=initialized_project,
        capture_output=True,
    )
    return initialized_project


async def _call(tool_name, args):
    from fastmcp import Client

    from blq.serve import mcp

    async with Client(mcp) as client:
        raw = await client.call_tool(tool_name, args)
    data = raw.data if hasattr(raw, "data") else raw
    if isinstance(data, dict):
        return data
    if hasattr(data, "model_dump"):
        return data.model_dump()
    return dict(vars(data))


@pytest.mark.asyncio
async def test_exec_keeps_its_run_metadata(project_with_a_run):
    result = await _call("exec", {"command": "echo", "args": ["hi"]})
    for key in ("cmd", "duration_sec", "output_stats"):
        assert key in result, f"exec dropped {key!r}: got {sorted(result)}"


@pytest.mark.asyncio
async def test_inspect_keeps_the_event_it_was_asked_for(project_with_a_run):
    result = await _call("inspect", {"ref": "1:1"})
    if result.get("error"):
        pytest.skip(f"no event to inspect: {result['error']}")
    for key in ("message", "severity", "ref_file", "ref_line"):
        assert key in result, f"inspect dropped {key!r}: got {sorted(result)}"


@pytest.mark.asyncio
async def test_register_command_keeps_whether_it_already_existed(initialized_project):
    result = await _call("register_command", {"name": "greet", "cmd": "echo hi"})
    assert "existing" in result, f"register_command dropped 'existing': {sorted(result)}"
    assert "command" in result, f"register_command dropped 'command': {sorted(result)}"


@pytest.mark.asyncio
async def test_ci_check_keeps_the_key_it_actually_returns(project_with_a_run):
    result = await _call("ci_check", {})
    assert "has_new_errors" in result, (
        f"ci_check dropped 'has_new_errors': got {sorted(result)}. The schema "
        f"declared 'has_errors', which the impl does not return."
    )
