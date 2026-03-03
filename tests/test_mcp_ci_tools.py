"""Tests for MCP CI tools (report, ci_check, ci_generate).

Uses FastMCP's in-memory transport for efficient testing.
"""

import subprocess

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
def mcp_server_with_data(initialized_project, sample_build_script):
    """MCP server with initialized project and sample run data."""
    from blq.serve import mcp

    subprocess.run(
        ["blq", "exec", "--quiet", str(sample_build_script)],
        capture_output=True,
    )

    return mcp


@pytest.fixture
def mcp_server_with_commands(initialized_project, sample_build_script):
    """MCP server with registered commands and sample run data."""
    from blq.serve import mcp

    # Register a command
    subprocess.run(
        ["blq", "commands", "register", "build", str(sample_build_script)],
        capture_output=True,
    )

    # Run the command to generate data
    subprocess.run(
        ["blq", "run", "--quiet", "build"],
        capture_output=True,
    )

    return mcp


@pytest.fixture
def mcp_server_empty(initialized_project):
    """MCP server with initialized project but no data."""
    from blq.serve import mcp

    return mcp


# ============================================================================
# Report Tool Tests
# ============================================================================


class TestReportTool:
    """Tests for the report MCP tool."""

    @pytest.mark.asyncio
    async def test_report_latest_run(self, mcp_server_with_data):
        """Generate report for the latest run."""
        async with Client(mcp_server_with_data) as client:
            raw = await client.call_tool("report", {})
            result = get_data(raw)

            assert "report" in result
            assert "run_id" in result
            assert "total_errors" in result
            assert "total_warnings" in result
            assert isinstance(result["report"], str)
            assert "# Build Report" in result["report"]

    @pytest.mark.asyncio
    async def test_report_contains_errors(self, mcp_server_with_data):
        """Report should include error information."""
        async with Client(mcp_server_with_data) as client:
            raw = await client.call_tool("report", {})
            result = get_data(raw)

            assert result["total_errors"] > 0
            # Report should contain error details
            assert "Error" in result["report"]

    @pytest.mark.asyncio
    async def test_report_summary_only(self, mcp_server_with_data):
        """Summary-only report excludes individual error details."""
        async with Client(mcp_server_with_data) as client:
            raw = await client.call_tool("report", {"summary_only": True})
            result = get_data(raw)

            assert "report" in result
            # Should still have the summary section
            assert "## Summary" in result["report"]

    @pytest.mark.asyncio
    async def test_report_with_warnings(self, mcp_server_with_data):
        """Report with warnings included."""
        async with Client(mcp_server_with_data) as client:
            raw = await client.call_tool("report", {"warnings": True})
            result = get_data(raw)

            assert "report" in result
            assert result["total_warnings"] >= 0

    @pytest.mark.asyncio
    async def test_report_no_runs(self, mcp_server_empty):
        """Report with no runs returns error."""
        async with Client(mcp_server_empty) as client:
            raw = await client.call_tool("report", {})
            result = get_data(raw)

            assert "error" in result

    @pytest.mark.asyncio
    async def test_report_invalid_baseline(self, mcp_server_with_data):
        """Report with invalid baseline returns error."""
        async with Client(mcp_server_with_data) as client:
            raw = await client.call_tool("report", {"baseline": "nonexistent-branch"})
            result = get_data(raw)

            assert "error" in result

    @pytest.mark.asyncio
    async def test_report_with_limits(self, mcp_server_with_data):
        """Report respects error_limit and file_limit."""
        async with Client(mcp_server_with_data) as client:
            raw = await client.call_tool("report", {"error_limit": 1, "file_limit": 1})
            result = get_data(raw)

            assert "report" in result
            assert result["run_id"] is not None


# ============================================================================
# CI Check Tool Tests
# ============================================================================


class TestCiCheckTool:
    """Tests for the ci_check MCP tool."""

    @pytest.mark.asyncio
    async def test_ci_check_no_baseline(self, mcp_server_with_data):
        """Check without baseline available."""
        async with Client(mcp_server_with_data) as client:
            raw = await client.call_tool("ci_check", {})
            result = get_data(raw)

            assert "status" in result
            assert result["status"] in ("OK", "FAIL")
            assert "current_run_id" in result
            assert "current_errors" in result

    @pytest.mark.asyncio
    async def test_ci_check_fail_on_any(self, mcp_server_with_data):
        """Check with fail_on_any mode (build has errors)."""
        async with Client(mcp_server_with_data) as client:
            raw = await client.call_tool("ci_check", {"fail_on_any": True})
            result = get_data(raw)

            assert result["status"] == "FAIL"
            assert result["has_errors"] is True
            assert result["current_errors"] > 0
            assert result["mode"] == "fail_on_any"

    @pytest.mark.asyncio
    async def test_ci_check_fail_on_any_success(self, mcp_server_empty, sample_success_script):
        """Check with fail_on_any mode when no errors."""
        async with Client(mcp_server_empty) as client:
            # Run a successful command first
            await client.call_tool("exec", {"command": str(sample_success_script)})

            raw = await client.call_tool("ci_check", {"fail_on_any": True})
            result = get_data(raw)

            assert result["status"] == "OK"
            assert result["has_errors"] is False

    @pytest.mark.asyncio
    async def test_ci_check_no_runs(self, mcp_server_empty):
        """Check with no runs returns error."""
        async with Client(mcp_server_empty) as client:
            raw = await client.call_tool("ci_check", {})
            result = get_data(raw)

            assert result["status"] == "ERROR"
            assert "error" in result

    @pytest.mark.asyncio
    async def test_ci_check_with_specific_run_id(self, mcp_server_with_data):
        """Check with a specific run_id."""
        async with Client(mcp_server_with_data) as client:
            # Get the latest run info first
            info_raw = await client.call_tool("info", {})
            info = get_data(info_raw)

            if "run_serial" in info:
                raw = await client.call_tool(
                    "ci_check",
                    {"run_id": info["run_serial"], "fail_on_any": True},
                )
                result = get_data(raw)

                assert "status" in result
                assert result["current_run_id"] == info["run_serial"]

    @pytest.mark.asyncio
    async def test_ci_check_baseline_comparison(self, mcp_server_empty, sample_build_script):
        """Check with two runs and baseline comparison by run ID."""
        async with Client(mcp_server_empty) as client:
            # Create two runs
            await client.call_tool("exec", {"command": str(sample_build_script)})
            await client.call_tool("exec", {"command": str(sample_build_script)})

            # Compare run 2 against run 1
            raw = await client.call_tool("ci_check", {"baseline": "1", "run_id": 2})
            result = get_data(raw)

            assert result["status"] in ("OK", "FAIL")
            assert result["mode"] == "baseline_comparison"
            assert "baseline_run_id" in result


# ============================================================================
# CI Generate Tool Tests
# ============================================================================


class TestCiGenerateTool:
    """Tests for the ci_generate MCP tool."""

    @pytest.mark.asyncio
    async def test_ci_generate_all_commands(self, mcp_server_with_commands):
        """Generate scripts for all registered commands."""
        async with Client(mcp_server_with_commands) as client:
            raw = await client.call_tool("ci_generate", {})
            result = get_data(raw)

            assert "scripts" in result
            assert result["count"] > 0
            assert result["shell"] == "bash"

            # Check script content
            script = result["scripts"][0]
            assert "name" in script
            assert "content" in script
            assert script["name"].endswith(".sh")
            assert "#!/usr/bin/env bash" in script["content"]

    @pytest.mark.asyncio
    async def test_ci_generate_specific_command(self, mcp_server_with_commands):
        """Generate script for a specific command."""
        async with Client(mcp_server_with_commands) as client:
            raw = await client.call_tool("ci_generate", {"commands": ["build"]})
            result = get_data(raw)

            assert result["count"] == 1
            assert result["scripts"][0]["command_name"] == "build"

    @pytest.mark.asyncio
    async def test_ci_generate_unknown_command(self, mcp_server_with_commands):
        """Generate script for unknown command returns error."""
        async with Client(mcp_server_with_commands) as client:
            raw = await client.call_tool("ci_generate", {"commands": ["nonexistent"]})
            result = get_data(raw)

            assert "error" in result
            assert "available" in result

    @pytest.mark.asyncio
    async def test_ci_generate_different_shell(self, mcp_server_with_commands):
        """Generate script with different shell."""
        async with Client(mcp_server_with_commands) as client:
            raw = await client.call_tool("ci_generate", {"shell": "sh"})
            result = get_data(raw)

            assert result["shell"] == "sh"
            assert "#!/usr/bin/env sh" in result["scripts"][0]["content"]

    @pytest.mark.asyncio
    async def test_ci_generate_invalid_shell(self, mcp_server_with_commands):
        """Generate script with invalid shell returns error."""
        async with Client(mcp_server_with_commands) as client:
            raw = await client.call_tool("ci_generate", {"shell": "fish"})
            result = get_data(raw)

            assert "error" in result

    @pytest.mark.asyncio
    async def test_ci_generate_no_commands(self, mcp_server_empty):
        """Generate with no registered commands returns error."""
        async with Client(mcp_server_empty) as client:
            raw = await client.call_tool("ci_generate", {})
            result = get_data(raw)

            assert "error" in result

    @pytest.mark.asyncio
    async def test_ci_generate_script_has_blq_fallback(self, mcp_server_with_commands):
        """Generated script includes blq fallback logic."""
        async with Client(mcp_server_with_commands) as client:
            raw = await client.call_tool("ci_generate", {})
            result = get_data(raw)

            content = result["scripts"][0]["content"]
            # Should have blq detection logic
            assert "blq" in content
            # Should have fallback
            assert "else" in content
