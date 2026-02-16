"""Tests for the blq MCP server.

Uses FastMCP's in-memory transport for efficient testing without
subprocess or network overhead.
"""

import pytest

# Skip all tests if fastmcp not installed
fastmcp = pytest.importorskip("fastmcp")

if fastmcp:
    from fastmcp import Client


def get_data(result):
    """Extract data from CallToolResult."""
    if hasattr(result, "data"):
        return result.data
    return result


@pytest.fixture
def mcp_server(initialized_project, sample_build_script):
    """Create MCP server with initialized project and sample data."""
    # Run a build to generate some data
    import subprocess

    # Import here to avoid errors if fastmcp not installed
    from blq.serve import mcp

    # Use exec for ad-hoc command execution (run is for registered commands only)
    subprocess.run(
        ["blq", "exec", "--quiet", str(sample_build_script)],
        capture_output=True,
    )

    return mcp


@pytest.fixture
def mcp_server_empty(initialized_project):
    """Create MCP server with initialized project but no data."""
    from blq.serve import mcp

    return mcp


# ============================================================================
# Tool Tests
# ============================================================================


class TestExecTool:
    """Tests for the exec tool (ad-hoc command execution)."""

    @pytest.mark.asyncio
    async def test_exec_command(self, mcp_server_empty, sample_build_script):
        """Execute an ad-hoc command and capture output."""
        async with Client(mcp_server_empty) as client:
            raw = await client.call_tool("exec", {"command": str(sample_build_script)})
            result = get_data(raw)

            assert "run_ref" in result
            assert "status" in result
            assert result["status"] in ["OK", "FAIL"]

    @pytest.mark.asyncio
    async def test_exec_with_args(self, mcp_server_empty):
        """Execute a command with arguments."""
        async with Client(mcp_server_empty) as client:
            raw = await client.call_tool("exec", {"command": "echo", "args": ["hello", "world"]})
            result = get_data(raw)

            assert result["status"] == "OK"
            assert result["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_exec_failing_command(self, mcp_server_empty):
        """Execute a command that fails."""
        async with Client(mcp_server_empty) as client:
            raw = await client.call_tool(
                "exec",
                {"command": "false"},  # Always exits with 1
            )
            result = get_data(raw)

            assert result["status"] == "FAIL"
            assert result["exit_code"] != 0


class TestRunTool:
    """Tests for the run tool (registered commands)."""

    @pytest.mark.asyncio
    async def test_run_unregistered_command_fails(self, mcp_server_empty):
        """Run should fail for unregistered commands."""
        async with Client(mcp_server_empty) as client:
            raw = await client.call_tool("run", {"command": "nonexistent"})
            result = get_data(raw)

            assert result["status"] == "FAIL"
            assert "not registered" in result.get("error", "")


class TestQueryTool:
    """Tests for the query tool."""

    @pytest.mark.asyncio
    async def test_query_simple(self, mcp_server):
        """Run a simple SQL query."""
        async with Client(mcp_server) as client:
            raw = await client.call_tool(
                "query", {"sql": "SELECT COUNT(*) as count FROM blq_load_events()"}
            )
            result = get_data(raw)

            assert "columns" in result
            assert "rows" in result
            assert result["row_count"] >= 0

    @pytest.mark.asyncio
    async def test_query_with_limit(self, mcp_server):
        """Query with limit parameter."""
        async with Client(mcp_server) as client:
            sql = "SELECT * FROM blq_load_events()"
            raw = await client.call_tool("query", {"sql": sql, "limit": 5})
            result = get_data(raw)

            assert len(result["rows"]) <= 5

    @pytest.mark.asyncio
    async def test_query_errors_only(self, mcp_server):
        """Query filtering to errors only."""
        async with Client(mcp_server) as client:
            raw = await client.call_tool(
                "query", {"sql": "SELECT * FROM blq_load_events() WHERE severity = 'error'"}
            )
            result = get_data(raw)

            # All returned rows should be errors
            if result["rows"]:
                severity_idx = result["columns"].index("severity")
                for row in result["rows"]:
                    assert row[severity_idx] == "error"

    @pytest.mark.asyncio
    async def test_query_with_filter(self, mcp_server):
        """Query with simple filter expressions."""
        async with Client(mcp_server) as client:
            # Filter for errors
            raw = await client.call_tool("query", {"filter": "severity=error"})
            result = get_data(raw)

            assert "columns" in result
            assert "rows" in result

            # All returned rows should be errors
            if result["rows"]:
                severity_idx = result["columns"].index("severity")
                for row in result["rows"]:
                    assert row[severity_idx] == "error"

    @pytest.mark.asyncio
    async def test_query_filter_multiple(self, mcp_server):
        """Query with multiple filter expressions."""
        async with Client(mcp_server) as client:
            # Multiple filters are AND'd
            raw = await client.call_tool("query", {"filter": "severity=error,warning", "limit": 10})
            result = get_data(raw)

            assert "columns" in result
            assert len(result["rows"]) <= 10

    @pytest.mark.asyncio
    async def test_query_filter_contains(self, mcp_server):
        """Query with contains (~) filter."""
        async with Client(mcp_server) as client:
            raw = await client.call_tool("query", {"filter": "ref_file~test"})
            result = get_data(raw)

            assert "columns" in result
            # Results should contain files with 'test' in the path
            if result["rows"]:
                file_idx = result["columns"].index("ref_file")
                for row in result["rows"]:
                    if row[file_idx]:
                        assert "test" in row[file_idx].lower()

    @pytest.mark.asyncio
    async def test_query_requires_sql_or_filter(self, mcp_server):
        """Query requires either sql or filter parameter."""
        async with Client(mcp_server) as client:
            raw = await client.call_tool("query", {})
            result = get_data(raw)

            assert "error" in result
            assert "sql or filter" in result["error"].lower()


class TestEventsTool:
    """Tests for the events tool (consolidated from errors/warnings)."""

    @pytest.mark.asyncio
    async def test_events_errors_default(self, mcp_server):
        """Get errors with default parameters using events(severity='error')."""
        async with Client(mcp_server) as client:
            raw = await client.call_tool("events", {"severity": "error"})
            result = get_data(raw)

            assert "events" in result
            assert "total_count" in result
            assert isinstance(result["events"], list)

    @pytest.mark.asyncio
    async def test_events_errors_with_limit(self, mcp_server):
        """Get errors with limit."""
        async with Client(mcp_server) as client:
            raw = await client.call_tool("events", {"severity": "error", "limit": 5})
            result = get_data(raw)

            assert len(result["events"]) <= 5

    @pytest.mark.asyncio
    async def test_events_errors_with_file_pattern(self, mcp_server):
        """Get errors filtered by file pattern."""
        async with Client(mcp_server) as client:
            raw = await client.call_tool("events", {"severity": "error", "file_pattern": "%main%"})
            result = get_data(raw)

            for event in result["events"]:
                if event.get("ref_file"):
                    assert "main" in event["ref_file"].lower()

    @pytest.mark.asyncio
    async def test_events_error_structure(self, mcp_server):
        """Verify error event structure."""
        async with Client(mcp_server) as client:
            raw = await client.call_tool("events", {"severity": "error", "limit": 1})
            result = get_data(raw)

            if result["events"]:
                event = result["events"][0]
                assert "ref" in event
                assert "message" in event
                # ref should be in format "run_id:event_id"
                assert ":" in event["ref"]

    @pytest.mark.asyncio
    async def test_events_warnings_default(self, mcp_server):
        """Get warnings with default parameters using events(severity='warning')."""
        async with Client(mcp_server) as client:
            raw = await client.call_tool("events", {"severity": "warning"})
            result = get_data(raw)

            assert "events" in result
            assert "total_count" in result


class TestInspectTool:
    """Tests for the inspect tool (consolidated from event/context)."""

    @pytest.mark.asyncio
    async def test_inspect_event_details(self, mcp_server):
        """Get event details by reference using inspect."""
        async with Client(mcp_server) as client:
            # First get an error to find a valid ref
            events_raw = await client.call_tool("events", {"severity": "error", "limit": 1})
            events = get_data(events_raw)

            if events["events"]:
                ref = events["events"][0]["ref"]

                raw = await client.call_tool(
                    "inspect",
                    {"ref": ref, "include_log_context": False, "include_source_context": False},
                )
                result = get_data(raw)

                assert result is not None
                assert result["ref"] == ref
                assert "message" in result
                assert "severity" in result

    @pytest.mark.asyncio
    async def test_inspect_event_not_found(self, mcp_server):
        """Event not found returns appropriate response."""
        async with Client(mcp_server) as client:
            raw = await client.call_tool("inspect", {"ref": "99999:99999"})
            result = get_data(raw)

            # Should return error
            assert "error" in result

    @pytest.mark.asyncio
    async def test_inspect_with_log_context(self, mcp_server):
        """Get event with log context."""
        async with Client(mcp_server) as client:
            events_raw = await client.call_tool("events", {"severity": "error", "limit": 1})
            events = get_data(events_raw)

            if events["events"]:
                ref = events["events"][0]["ref"]

                raw = await client.call_tool(
                    "inspect",
                    {"ref": ref, "include_log_context": True, "include_source_context": False},
                )
                result = get_data(raw)

                # Should have log_context field
                assert "log_context" in result or "error" in result

    @pytest.mark.asyncio
    async def test_inspect_custom_lines(self, mcp_server):
        """Get context with custom line count."""
        async with Client(mcp_server) as client:
            events_raw = await client.call_tool("events", {"severity": "error", "limit": 1})
            events = get_data(events_raw)

            if events["events"]:
                ref = events["events"][0]["ref"]

                raw = await client.call_tool("inspect", {"ref": ref, "lines": 10})
                result = get_data(raw)

                # Returns event with context
                assert "log_context" in result or "error" in result


class TestOutputTool:
    """Tests for the output tool."""

    @pytest.mark.asyncio
    async def test_output_basic(self, mcp_server):
        """Get raw output for a run."""
        async with Client(mcp_server) as client:
            # Get history to find a run
            history_raw = await client.call_tool("history", {"limit": 1})
            history = get_data(history_raw)

            if history["runs"]:
                run_id = history["runs"][0]["run_serial"]

                raw = await client.call_tool("output", {"ref": str(run_id)})
                result = get_data(raw)

                assert "run_id" in result
                assert result["run_id"] == run_id
                # May have content or error depending on storage
                assert "streams" in result

    @pytest.mark.asyncio
    async def test_output_with_tail(self, mcp_server):
        """Get last N lines of output."""
        async with Client(mcp_server) as client:
            history_raw = await client.call_tool("history", {"limit": 1})
            history = get_data(history_raw)

            if history["runs"]:
                run_id = history["runs"][0]["run_serial"]

                raw = await client.call_tool("output", {"ref": str(run_id), "tail": 5})
                result = get_data(raw)

                assert "run_id" in result
                if "content" in result:
                    # If we got content, returned_lines should be <= 5
                    assert result.get("returned_lines", 0) <= 5

    @pytest.mark.asyncio
    async def test_output_not_found(self, mcp_server_empty):
        """Output for non-existent run."""
        async with Client(mcp_server_empty) as client:
            raw = await client.call_tool("output", {"ref": "9999"})
            result = get_data(raw)

            assert "error" in result


class TestStatusTool:
    """Tests for the status tool."""

    @pytest.mark.asyncio
    async def test_status(self, mcp_server):
        """Get status summary."""
        async with Client(mcp_server) as client:
            raw = await client.call_tool("status", {})
            result = get_data(raw)

            assert "sources" in result
            assert isinstance(result["sources"], list)

    @pytest.mark.asyncio
    async def test_status_structure(self, mcp_server):
        """Verify status structure."""
        async with Client(mcp_server) as client:
            raw = await client.call_tool("status", {})
            result = get_data(raw)

            if result["sources"]:
                source = result["sources"][0]
                assert "name" in source
                assert "status" in source
                assert source["status"] in ["OK", "FAIL", "WARN"]


class TestHistoryTool:
    """Tests for the history tool."""

    @pytest.mark.asyncio
    async def test_history_default(self, mcp_server):
        """Get run history with defaults."""
        async with Client(mcp_server) as client:
            raw = await client.call_tool("history", {})
            result = get_data(raw)

            assert "runs" in result
            assert isinstance(result["runs"], list)

    @pytest.mark.asyncio
    async def test_history_with_limit(self, mcp_server):
        """Get history with limit."""
        async with Client(mcp_server) as client:
            raw = await client.call_tool("history", {"limit": 5})
            result = get_data(raw)

            assert len(result["runs"]) <= 5

    @pytest.mark.asyncio
    async def test_history_structure(self, mcp_server):
        """Verify history entry structure."""
        async with Client(mcp_server) as client:
            raw = await client.call_tool("history", {"limit": 1})
            result = get_data(raw)

            if result["runs"]:
                run = result["runs"][0]
                assert "run_serial" in run
                assert "run_ref" in run
                assert "status" in run

    @pytest.mark.asyncio
    async def test_history_with_status_filter(self, mcp_server):
        """Filter history by status."""
        async with Client(mcp_server) as client:
            # Test completed filter (all runs in fixture should be completed)
            raw = await client.call_tool("history", {"status": "completed"})
            result = get_data(raw)

            assert "runs" in result
            # All returned runs should be completed (OK, FAIL, or WARN)
            for run in result["runs"]:
                assert run["status"] in ("OK", "FAIL", "WARN")

            # Test running filter (no running commands expected in fixture)
            raw = await client.call_tool("history", {"status": "running"})
            result = get_data(raw)

            assert "runs" in result
            # Any running commands should have RUNNING status
            for run in result["runs"]:
                assert run["status"] == "RUNNING"


class TestDiffTool:
    """Tests for the diff tool."""

    @pytest.mark.asyncio
    async def test_diff_two_runs(self, mcp_server_empty, sample_build_script):
        """Compare two runs."""
        async with Client(mcp_server_empty) as client:
            # Create two runs using exec (ad-hoc execution)
            run1_raw = await client.call_tool("exec", {"command": str(sample_build_script)})
            run1 = get_data(run1_raw)

            run2_raw = await client.call_tool("exec", {"command": str(sample_build_script)})
            run2 = get_data(run2_raw)

            if run1.get("run_id") and run2.get("run_id"):
                raw = await client.call_tool(
                    "diff", {"run1": run1["run_id"], "run2": run2["run_id"]}
                )
                result = get_data(raw)

                assert "summary" in result
                assert "run1_errors" in result["summary"]
                assert "run2_errors" in result["summary"]


# ============================================================================
# Resource Tests
# ============================================================================


class TestResources:
    """Tests for MCP resources."""

    @pytest.mark.asyncio
    async def test_list_resources(self, mcp_server):
        """List available resources."""
        async with Client(mcp_server) as client:
            resources = await client.list_resources()

            resource_uris = [str(r.uri) for r in resources]
            assert any("status" in uri for uri in resource_uris)

    @pytest.mark.asyncio
    async def test_read_status_resource(self, mcp_server):
        """Read the status resource."""
        async with Client(mcp_server) as client:
            content = await client.read_resource("blq://status")

            assert content is not None

    @pytest.mark.asyncio
    async def test_read_commands_resource(self, mcp_server):
        """Read the commands resource."""
        async with Client(mcp_server) as client:
            content = await client.read_resource("blq://commands")

            assert content is not None

    @pytest.mark.asyncio
    async def test_read_guide_resource(self, mcp_server):
        """Read the guide resource."""
        async with Client(mcp_server) as client:
            content = await client.read_resource("blq://guide")

            assert content is not None
            # Should contain markdown content
            assert "blq" in str(content).lower()

    @pytest.mark.asyncio
    async def test_read_errors_resource(self, mcp_server):
        """Read the errors resource."""
        async with Client(mcp_server) as client:
            content = await client.read_resource("blq://errors")

            assert content is not None

    @pytest.mark.asyncio
    async def test_read_warnings_resource(self, mcp_server):
        """Read the warnings resource."""
        async with Client(mcp_server) as client:
            content = await client.read_resource("blq://warnings")

            assert content is not None

    @pytest.mark.asyncio
    async def test_read_context_resource(self, mcp_server):
        """Read the context resource for an existing event."""
        async with Client(mcp_server) as client:
            # First get an error to find a valid ref
            events_raw = await client.call_tool("events", {"severity": "error", "limit": 1})
            events = get_data(events_raw)

            if events["events"]:
                ref = events["events"][0]["ref"]

                # Skip if ref contains path separators (from exec'd scripts)
                # as those can't be used in resource URIs
                if "/" not in ref and "\\" not in ref:
                    content = await client.read_resource(f"blq://context/{ref}")
                    assert content is not None

    @pytest.mark.asyncio
    async def test_read_context_resource_registered_command(
        self, mcp_server_empty, sample_build_script
    ):
        """Read the context resource with a registered command (clean refs)."""
        async with Client(mcp_server_empty) as client:
            # Register a command to get clean refs
            await client.call_tool(
                "register_command",
                {"name": "build", "cmd": str(sample_build_script)},
            )

            # Run the command
            await client.call_tool("run", {"command": "build"})

            # Get errors using events with severity filter
            events_raw = await client.call_tool("events", {"severity": "error", "limit": 1})
            events = get_data(events_raw)

            if events["events"]:
                ref = events["events"][0]["ref"]
                # Ref should be clean like "build:1:1"
                assert "/" not in ref

                content = await client.read_resource(f"blq://context/{ref}")
                assert content is not None


# ============================================================================
# Register Command Tests
# ============================================================================


class TestRegisterCommandTool:
    """Tests for the register_command tool."""

    @pytest.mark.asyncio
    async def test_register_new_command(self, mcp_server_empty):
        """Register a new command."""
        async with Client(mcp_server_empty) as client:
            raw = await client.call_tool(
                "register_command",
                {"name": "hello", "cmd": "echo hello"},
            )
            result = get_data(raw)

            assert result["success"] is True
            assert result["existing"] is False
            assert result["command"]["name"] == "hello"

    @pytest.mark.asyncio
    async def test_register_idempotent_same_command(self, mcp_server_empty):
        """Registering identical command returns existing."""
        async with Client(mcp_server_empty) as client:
            # First registration
            await client.call_tool(
                "register_command",
                {"name": "hello", "cmd": "echo hello"},
            )

            # Second registration with same name and command
            raw = await client.call_tool(
                "register_command",
                {"name": "hello", "cmd": "echo hello"},
            )
            result = get_data(raw)

            assert result["success"] is True
            assert result["existing"] is True
            assert "identical" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_register_same_cmd_different_name_fails(self, mcp_server_empty):
        """Registering same command under different name fails without force."""
        async with Client(mcp_server_empty) as client:
            # First registration
            await client.call_tool(
                "register_command",
                {"name": "hello", "cmd": "echo hello"},
            )

            # Second registration with different name but same command
            raw = await client.call_tool(
                "register_command",
                {"name": "greet", "cmd": "echo hello"},
            )
            result = get_data(raw)

            assert result["success"] is False
            assert "already registered" in result["error"]
            assert result["existing_name"] == "hello"

    @pytest.mark.asyncio
    async def test_register_different_command_same_name_fails(self, mcp_server_empty):
        """Registering different command with same name fails without force."""
        async with Client(mcp_server_empty) as client:
            # First registration
            await client.call_tool(
                "register_command",
                {"name": "hello", "cmd": "echo hello"},
            )

            # Second registration with same name but different command
            raw = await client.call_tool(
                "register_command",
                {"name": "hello", "cmd": "echo goodbye"},
            )
            result = get_data(raw)

            assert result["success"] is False
            assert "different command" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_register_with_force_overwrites(self, mcp_server_empty):
        """Registering with force=True overwrites existing."""
        async with Client(mcp_server_empty) as client:
            # First registration
            await client.call_tool(
                "register_command",
                {"name": "hello", "cmd": "echo hello"},
            )

            # Overwrite with force
            raw = await client.call_tool(
                "register_command",
                {"name": "hello", "cmd": "echo goodbye", "force": True},
            )
            result = get_data(raw)

            assert result["success"] is True
            assert result["existing"] is False
            assert result["command"]["cmd"] == "echo goodbye"

    @pytest.mark.asyncio
    async def test_register_with_run_now(self, mcp_server_empty):
        """Register and run immediately with run_now=True."""
        async with Client(mcp_server_empty) as client:
            raw = await client.call_tool(
                "register_command",
                {"name": "hello", "cmd": "echo hello", "run_now": True},
            )
            result = get_data(raw)

            assert result["success"] is True
            assert "run" in result
            assert result["run"]["status"] == "OK"

    @pytest.mark.asyncio
    async def test_register_idempotent_with_run_now(self, mcp_server_empty):
        """Idempotent registration also runs with run_now=True."""
        async with Client(mcp_server_empty) as client:
            # First registration
            await client.call_tool(
                "register_command",
                {"name": "hello", "cmd": "echo hello"},
            )

            # Second registration with run_now
            raw = await client.call_tool(
                "register_command",
                {"name": "hello", "cmd": "echo hello", "run_now": True},
            )
            result = get_data(raw)

            assert result["success"] is True
            assert result["existing"] is True
            assert "run" in result
            assert result["run"]["status"] == "OK"


# ============================================================================
# Exec Tool Registered Command Detection Tests
# ============================================================================


class TestExecRegisteredCommandDetection:
    """Tests for exec tool detecting registered command prefixes."""

    @pytest.mark.asyncio
    async def test_exec_matches_registered_command(self, mcp_server_empty):
        """Exec with registered command prefix uses run() instead."""
        async with Client(mcp_server_empty) as client:
            # Register a base command
            await client.call_tool(
                "register_command",
                {"name": "greet", "cmd": "echo hello"},
            )

            # Exec with extra args - should match registered command
            raw = await client.call_tool(
                "exec",
                {"command": "echo hello world"},
            )
            result = get_data(raw)

            assert result["status"] == "OK"
            assert result.get("matched_command") == "greet"
            assert result.get("extra_args") == ["world"]

    @pytest.mark.asyncio
    async def test_exec_no_match_runs_adhoc(self, mcp_server_empty):
        """Exec without matching registered command runs ad-hoc."""
        async with Client(mcp_server_empty) as client:
            # Register a command
            await client.call_tool(
                "register_command",
                {"name": "greet", "cmd": "echo hello"},
            )

            # Exec a different command - should not match
            raw = await client.call_tool(
                "exec",
                {"command": "echo goodbye"},
            )
            result = get_data(raw)

            assert result["status"] == "OK"
            assert "matched_command" not in result

    @pytest.mark.asyncio
    async def test_exec_exact_match_no_extra(self, mcp_server_empty):
        """Exec with exact registered command match (no extra args)."""
        async with Client(mcp_server_empty) as client:
            # Register a command
            await client.call_tool(
                "register_command",
                {"name": "greet", "cmd": "echo hello"},
            )

            # Exec exact same command
            raw = await client.call_tool(
                "exec",
                {"command": "echo hello"},
            )
            result = get_data(raw)

            assert result["status"] == "OK"
            assert result.get("matched_command") == "greet"
            assert result.get("extra_args") is None or result.get("extra_args") == []


# ============================================================================
# Prompt Tests
# ============================================================================


class TestPrompts:
    """Tests for MCP prompts."""

    @pytest.mark.asyncio
    async def test_list_prompts(self, mcp_server):
        """List available prompts."""
        async with Client(mcp_server) as client:
            prompts = await client.list_prompts()

            prompt_names = [p.name for p in prompts]
            assert "fix-errors" in prompt_names
            assert "analyze-regression" in prompt_names
            assert "summarize-run" in prompt_names

    @pytest.mark.asyncio
    async def test_get_fix_errors_prompt(self, mcp_server):
        """Get the fix-errors prompt."""
        async with Client(mcp_server) as client:
            prompt = await client.get_prompt("fix-errors", {})

            assert prompt is not None
            assert len(prompt.messages) > 0

    @pytest.mark.asyncio
    async def test_get_summarize_run_prompt(self, mcp_server):
        """Get the summarize-run prompt."""
        async with Client(mcp_server) as client:
            prompt = await client.get_prompt("summarize-run", {})

            assert prompt is not None
            assert len(prompt.messages) > 0


# ============================================================================
# Integration Tests
# ============================================================================


class TestIntegration:
    """Integration tests for common workflows."""

    @pytest.mark.asyncio
    async def test_build_and_query_workflow(self, mcp_server_empty, sample_build_script):
        """Test typical build -> query -> drill-down workflow."""
        async with Client(mcp_server_empty) as client:
            # 1. Run build using exec (ad-hoc execution)
            run_raw = await client.call_tool("exec", {"command": str(sample_build_script)})
            run_result = get_data(run_raw)
            assert "run_ref" in run_result

            # 2. Get errors using events with severity filter
            events_raw = await client.call_tool("events", {"severity": "error"})
            events_result = get_data(events_raw)
            assert "events" in events_result

            # 3. If errors, drill down using inspect
            if events_result["events"]:
                ref = events_result["events"][0]["ref"]
                inspect_raw = await client.call_tool("inspect", {"ref": ref})
                inspect_result = get_data(inspect_raw)
                assert inspect_result is not None
                assert "error" not in inspect_result or "ref" in inspect_result

    @pytest.mark.asyncio
    async def test_status_check_workflow(self, mcp_server):
        """Test status check workflow."""
        async with Client(mcp_server) as client:
            # 1. Check status
            status_raw = await client.call_tool("status", {})
            status = get_data(status_raw)
            assert "sources" in status

            # 2. Get history
            history_raw = await client.call_tool("history", {"limit": 5})
            hist = get_data(history_raw)
            assert "runs" in hist

            # 3. Query specific run if available using events
            if hist["runs"]:
                run_serial = hist["runs"][0]["run_serial"]
                events_raw = await client.call_tool(
                    "events", {"severity": "error", "run_id": run_serial}
                )
                events = get_data(events_raw)
                assert "events" in events


class TestBatchModes:
    """Tests for batch mode parameters in consolidated tools."""

    @pytest.mark.asyncio
    async def test_events_batch_mode(self, mcp_server):
        """Get events from multiple runs using events with run_ids parameter."""
        async with Client(mcp_server) as client:
            # Get history to find run IDs
            history_raw = await client.call_tool("history", {"limit": 5})
            history = get_data(history_raw)

            if history["runs"]:
                run_ids = [r["run_serial"] for r in history["runs"][:2]]

                raw = await client.call_tool("events", {"run_ids": run_ids, "severity": "error"})
                result = get_data(raw)

                assert "runs" in result
                assert "total_events" in result
                assert result["run_count"] == len(run_ids)

    @pytest.mark.asyncio
    async def test_inspect_batch_mode(self, mcp_server):
        """Get multiple events at once using inspect with refs parameter."""
        async with Client(mcp_server) as client:
            # Get some error refs
            events_raw = await client.call_tool("events", {"severity": "error", "limit": 3})
            events = get_data(events_raw)

            if events["events"]:
                refs = [e["ref"] for e in events["events"][:2]]

                raw = await client.call_tool("inspect", {"ref": refs[0], "refs": refs})
                result = get_data(raw)

                assert "events" in result
                assert result["total"] == len(refs)

    @pytest.mark.asyncio
    async def test_run_batch_mode_empty(self, mcp_server_empty):
        """Batch run with no commands using run with commands parameter."""
        async with Client(mcp_server_empty) as client:
            raw = await client.call_tool("run", {"command": "dummy", "commands": []})
            result = get_data(raw)

            assert result["status"] == "OK"
            assert result["commands_run"] == 0


class TestCleanTool:
    """Tests for the clean tool."""

    @pytest.mark.asyncio
    async def test_clean_requires_confirm(self, mcp_server):
        """Clean requires confirm=true."""
        async with Client(mcp_server) as client:
            raw = await client.call_tool("clean", {"mode": "data"})
            result = get_data(raw)

            assert result["success"] is False
            assert "confirm" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_clean_invalid_mode(self, mcp_server):
        """Invalid clean mode returns error."""
        async with Client(mcp_server) as client:
            raw = await client.call_tool("clean", {"mode": "invalid", "confirm": True})
            result = get_data(raw)

            assert result["success"] is False
            assert "invalid" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_clean_data_clears_runs(self, mcp_server_empty, sample_build_script):
        """Clean data clears run data."""
        async with Client(mcp_server_empty) as client:
            # Create some data
            await client.call_tool("exec", {"command": str(sample_build_script)})

            # Verify data exists
            history_raw = await client.call_tool("history", {})
            history = get_data(history_raw)
            assert len(history["runs"]) > 0

            # Clean data
            clean_raw = await client.call_tool("clean", {"mode": "data", "confirm": True})
            clean = get_data(clean_raw)
            assert clean["success"] is True

            # Verify data is cleared
            history_raw = await client.call_tool("history", {})
            history = get_data(history_raw)
            assert len(history["runs"]) == 0


class TestDisabledTools:
    """Tests for tool disabling (--disabled-tools, --safe-mode)."""

    def test_init_disabled_tools_safe_mode(self):
        """Safe mode disables state-modifying tools."""
        from blq import serve

        # Reset state
        serve._disabled_tools = None

        # Initialize with safe mode
        serve._init_disabled_tools(safe_mode=True)

        disabled = serve._load_disabled_tools()
        assert "exec" in disabled
        assert "clean" in disabled
        assert "register_command" in disabled
        assert "unregister_command" in disabled

        # Reset for other tests
        serve._disabled_tools = None

    def test_init_disabled_tools_cli_arg(self):
        """CLI --disabled-tools argument works."""
        from blq import serve

        # Reset state
        serve._disabled_tools = None

        # Initialize with CLI arg
        serve._init_disabled_tools(cli_disabled="exec,clean")

        disabled = serve._load_disabled_tools()
        assert "exec" in disabled
        assert "clean" in disabled
        assert "register_command" not in disabled

        # Reset for other tests
        serve._disabled_tools = None

    def test_init_disabled_tools_combined(self):
        """Safe mode and CLI args combine."""
        from blq import serve

        # Reset state
        serve._disabled_tools = None

        # Initialize with both
        serve._init_disabled_tools(cli_disabled="custom_tool", safe_mode=True)

        disabled = serve._load_disabled_tools()
        assert "exec" in disabled  # From safe mode
        assert "custom_tool" in disabled  # From CLI

        # Reset for other tests
        serve._disabled_tools = None

    def test_check_tool_enabled_raises(self):
        """Disabled tool raises PermissionError."""
        from blq import serve

        # Reset state
        serve._disabled_tools = None
        serve._init_disabled_tools(cli_disabled="exec")

        with pytest.raises(PermissionError) as exc_info:
            serve._check_tool_enabled("exec")

        assert "exec" in str(exc_info.value)
        assert "disabled" in str(exc_info.value)

        # Reset for other tests
        serve._disabled_tools = None
