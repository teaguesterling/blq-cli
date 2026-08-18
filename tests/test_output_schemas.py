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
    "events":  {"events", "total_count"},
    "status":  {"sources"},
    "history": {"runs"},
    "query":   {"rows", "columns", "row_count"},
    "inspect": {"events", "found", "total"},
    "output":  {"streams", "ref"},
    "diff":    {"new", "fixed", "summary"},
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
    assert props, (
        f"{tool_name}'s outputSchema names no properties at all"
    )
