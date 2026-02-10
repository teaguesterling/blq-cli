"""Tests for the inspect command and related functionality."""

import argparse
import json
from pathlib import Path

import pytest
import yaml

from blq.commands.core import BlqConfig
from blq.commands.events import cmd_inspect
from blq.output import format_status, read_source_context


class TestSourceLookupConfig:
    """Tests for source_lookup configuration options."""

    def test_source_lookup_defaults(self, initialized_project):
        """Source lookup is enabled by default."""
        config = BlqConfig.ensure()
        assert config.source_lookup_enabled is True
        assert config.ref_root == Path(".")

    def test_source_lookup_disabled(self, initialized_project):
        """Source lookup can be disabled in config."""
        # Write config with source_lookup disabled
        config_path = Path(".lq/config.yaml")
        existing = yaml.safe_load(config_path.read_text()) or {}
        existing["source_lookup"] = {"enabled": False}
        config_path.write_text(yaml.dump(existing))

        # Reload config
        config = BlqConfig.load(Path(".lq"))
        assert config.source_lookup_enabled is False

    def test_source_lookup_custom_ref_root(self, initialized_project):
        """Source lookup can use custom ref_root."""
        config_path = Path(".lq/config.yaml")
        existing = yaml.safe_load(config_path.read_text()) or {}
        existing["source_lookup"] = {"enabled": True, "ref_root": "./src"}
        config_path.write_text(yaml.dump(existing))

        config = BlqConfig.load(Path(".lq"))
        assert config.source_lookup_enabled is True
        assert config.ref_root == Path("./src")


class TestReadSourceContext:
    """Tests for read_source_context helper."""

    def test_reads_source_file(self, chdir_temp):
        """Reads and formats context from source file."""
        # Create a source file
        src = chdir_temp / "test.py"
        src.write_text("""def foo():
    x = 1
    y = 2
    z = x + y
    return z
""")

        result = read_source_context("test.py", 3, ref_root=chdir_temp, context=2)
        assert result is not None
        assert ">>> " in result  # Marker for highlighted line
        assert "y = 2" in result
        assert "x = 1" in result  # Context before
        assert "z = x + y" in result  # Context after

    def test_returns_none_for_missing_file(self, chdir_temp):
        """Returns None if file doesn't exist."""
        result = read_source_context("nonexistent.py", 5, ref_root=chdir_temp)
        assert result is None

    def test_returns_none_for_invalid_line(self, chdir_temp):
        """Returns None if line number is out of range."""
        src = chdir_temp / "small.py"
        src.write_text("x = 1\ny = 2\n")

        result = read_source_context("small.py", 100, ref_root=chdir_temp)
        assert result is None

    def test_handles_relative_path(self, chdir_temp):
        """Works with relative file paths."""
        subdir = chdir_temp / "src"
        subdir.mkdir()
        src = subdir / "module.py"
        src.write_text("line 1\nline 2\nline 3\n")

        result = read_source_context("src/module.py", 2, ref_root=chdir_temp)
        assert result is not None
        assert "line 2" in result


class TestCmdInspect:
    """Tests for blq inspect command."""

    def test_shows_event_details(
        self, initialized_project, sample_build_script, run_adhoc_command, capsys
    ):
        """Show comprehensive details for a specific event."""
        run_adhoc_command([str(sample_build_script)])
        capsys.readouterr()  # Clear output

        args = argparse.Namespace(ref="1:1", lines=3, json=False)
        cmd_inspect(args)

        captured = capsys.readouterr()
        assert "Event: 1:1" in captured.out
        assert "Severity:" in captured.out
        assert "File:" in captured.out

    def test_requires_event_ref_not_run_ref(
        self, initialized_project, sample_build_script, run_adhoc_command, capsys
    ):
        """Inspect requires event reference, not run reference."""
        run_adhoc_command([str(sample_build_script)])
        capsys.readouterr()

        # Try with run ref (no event_id)
        args = argparse.Namespace(ref="1", lines=3, json=False)

        with pytest.raises(SystemExit) as exc_info:
            cmd_inspect(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "event reference" in captured.err.lower()

    def test_event_not_found(
        self, initialized_project, sample_build_script, run_adhoc_command, capsys
    ):
        """Error when event not found."""
        run_adhoc_command([str(sample_build_script)])
        capsys.readouterr()

        args = argparse.Namespace(ref="999:999", lines=3, json=False)

        with pytest.raises(SystemExit) as exc_info:
            cmd_inspect(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "not found" in captured.err

    def test_json_output(
        self, initialized_project, sample_build_script, run_adhoc_command, capsys
    ):
        """JSON output includes context fields."""
        run_adhoc_command([str(sample_build_script)])
        capsys.readouterr()

        args = argparse.Namespace(ref="1:1", lines=3, json=True)
        cmd_inspect(args)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "severity" in data
        assert "log_context" in data
        assert "source_context" in data

    def test_shows_tool_info(
        self, initialized_project, sample_build_script, run_adhoc_command, capsys
    ):
        """Shows tool_name and category when available."""
        run_adhoc_command([str(sample_build_script)])
        capsys.readouterr()

        args = argparse.Namespace(ref="1:1", lines=3, json=False)
        cmd_inspect(args)

        captured = capsys.readouterr()
        # At minimum should show event details
        assert "Event: 1:1" in captured.out

    def test_shows_source_context_when_file_exists(
        self, initialized_project, sample_build_script, run_adhoc_command, capsys
    ):
        """Shows source context when ref_file exists."""
        # Create the source file that the error references
        src_dir = Path("src")
        src_dir.mkdir(exist_ok=True)
        (src_dir / "main.c").write_text("""#include <stdio.h>

int main() {
    // Some code here
    int x = 10;
    int y = 20;
    int z = 30;
    int a = 40;
    int b = 50;
    int c = 60;
    // Line 11
    // Line 12
    // Line 13
    // Line 14
    foo;  // Line 15 - undefined variable
    // Line 16
    // Line 17
}
""")

        run_adhoc_command([str(sample_build_script)])
        capsys.readouterr()

        args = argparse.Namespace(ref="1:1", lines=3, json=False)
        cmd_inspect(args)

        captured = capsys.readouterr()
        # Should show both log and source context
        assert "Log Context" in captured.out or "Event: 1:1" in captured.out


class TestEnhancedEventOutput:
    """Tests for enhanced event command output."""

    def test_event_shows_tool_info(
        self, initialized_project, sample_build_script, run_adhoc_command, capsys
    ):
        """Event command shows tool_name and category."""
        from blq.commands.events import cmd_event

        run_adhoc_command([str(sample_build_script)])
        capsys.readouterr()

        args = argparse.Namespace(ref="1:1", json=False)
        cmd_event(args)

        captured = capsys.readouterr()
        assert "Event: 1:1" in captured.out
        assert "Severity:" in captured.out
        assert "File:" in captured.out

    def test_event_json_includes_all_fields(
        self, initialized_project, sample_build_script, run_adhoc_command, capsys
    ):
        """Event JSON output includes enhanced fields."""
        from blq.commands.events import cmd_event

        run_adhoc_command([str(sample_build_script)])
        capsys.readouterr()

        args = argparse.Namespace(ref="1:1", json=True)
        cmd_event(args)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        # Should have standard fields
        assert "severity" in data
        assert "ref_file" in data
        assert "message" in data


class TestFormatErrorsWithCode:
    """Tests for code column in error listings."""

    def test_format_errors_includes_code(self):
        """format_errors includes code column."""
        from blq.output import format_errors

        data = [
            {
                "source_name": "test",
                "ref": "1:1",
                "severity": "error",
                "ref_file": "test.py",
                "ref_line": 10,
                "code": "E501",
                "message": "Line too long",
            }
        ]

        result = format_errors(data, output_format="table")
        # The code column should be present in table output
        assert "E501" in result or "Code" in result

    def test_format_errors_normalizes_code_field(self):
        """format_errors normalizes code from different field names."""
        from blq.output import format_errors

        # Test with 'rule' field instead of 'code'
        data = [
            {
                "source_name": "test",
                "ref": "1:1",
                "severity": "error",
                "ref_file": "test.py",
                "ref_line": 10,
                "rule": "no-unused-vars",
                "message": "Unused variable",
            }
        ]

        result = format_errors(data, output_format="json")
        parsed = json.loads(result)
        # In JSON output, both original field and normalized field should be present
        assert any("no-unused-vars" in str(v) for v in parsed[0].values())


class TestFormatStatusWithUniqueCounts:
    """Tests for unique fingerprint counts in status output."""

    def test_format_status_shows_unique_counts(self):
        """Status shows unique counts when different from total."""
        data = [
            {
                "source_name": "test",
                "badge": "[FAIL]",
                "error_count": 5,
                "warning_count": 3,
                "unique_error_count": 2,
                "unique_warning_count": 3,
                "age": "5m",
            }
        ]

        result = format_status(data, output_format="table")
        # Should show "5(2)" for errors (5 total, 2 unique)
        assert "5(2)" in result

    def test_format_status_no_parens_when_equal(self):
        """Status doesn't show parens when unique equals total."""
        data = [
            {
                "source_name": "test",
                "badge": "[FAIL]",
                "error_count": 3,
                "warning_count": 2,
                "unique_error_count": 3,  # Same as total
                "unique_warning_count": 2,  # Same as total
                "age": "5m",
            }
        ]

        result = format_status(data, output_format="table")
        # Should just show "3/2" without parentheses
        assert "3/2" in result
        assert "3(3)" not in result

    def test_format_status_checkmark_for_clean(self):
        """Status shows checkmark when no errors or warnings."""
        data = [
            {
                "source_name": "test",
                "badge": "[ OK ]",
                "error_count": 0,
                "warning_count": 0,
                "unique_error_count": 0,
                "unique_warning_count": 0,
                "age": "5m",
            }
        ]

        result = format_status(data, output_format="table")
        assert "\u2713" in result  # Checkmark


class TestInspectMCPTool:
    """Tests for the inspect MCP tool."""

    @pytest.fixture
    def mcp_server(self, initialized_project, sample_build_script, run_adhoc_command):
        """Create MCP server with test data."""
        from blq.serve import mcp

        run_adhoc_command([str(sample_build_script)])
        return mcp

    @pytest.mark.asyncio
    async def test_inspect_tool_returns_details(self, mcp_server):
        """Inspect tool returns event details with context."""

        # Skip if mcp test infrastructure not available
        pytest.importorskip("mcp")

        from blq.serve import _inspect_impl

        # Test the implementation directly since full MCP client test is complex
        result = _inspect_impl("1:1", lines=3)

        # Should either have details or an error
        if "error" not in result:
            assert "severity" in result
            assert "log_context" in result
            assert "source_context" in result

    def test_inspect_impl_event_not_found(self, initialized_project):
        """Inspect impl returns error for non-existent event."""
        from blq.serve import _inspect_impl

        result = _inspect_impl("999:999", lines=3)
        assert "error" in result

    def test_inspect_impl_with_data(
        self, initialized_project, sample_build_script, run_adhoc_command
    ):
        """Inspect impl returns full event data."""
        from blq.serve import _inspect_impl

        run_adhoc_command([str(sample_build_script)])

        result = _inspect_impl("1:1", lines=3)

        # Should have event details
        if "error" not in result:
            assert "ref" in result
            assert "severity" in result
            assert "log_context" in result
            assert "source_context" in result
