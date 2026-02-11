"""Tests for the record-invocation commands."""

import argparse
import json
import sys
from io import BytesIO, StringIO
from unittest.mock import patch

import pytest

from blq.bird import BirdStore
from blq.commands.record_cmd import (
    _extract_tag_from_command,
    cmd_record_attempt,
    cmd_record_outcome,
)


class TestExtractTagFromCommand:
    """Tests for tag extraction from command strings."""

    def test_simple_command(self):
        """Extract tag from simple command."""
        assert _extract_tag_from_command("pytest tests/") == "pytest"

    def test_command_with_path(self):
        """Extract tag from command with path."""
        assert _extract_tag_from_command("/usr/bin/pytest tests/") == "pytest"

    def test_command_with_args(self):
        """Extract tag from command with arguments."""
        assert _extract_tag_from_command("make -j8 build") == "make"

    def test_empty_command(self):
        """Handle empty command string."""
        assert _extract_tag_from_command("") == "unknown"

    def test_command_with_special_chars(self):
        """Sanitize special characters in command."""
        assert _extract_tag_from_command("npm@latest install") == "npm_latest"


class TestRecordAttempt:
    """Tests for cmd_record_attempt."""

    def test_record_attempt_basic(self, initialized_project):
        """Record a basic attempt."""
        args = argparse.Namespace(
            command="pytest tests/ -v",
            tag=None,
            format=None,
            cwd=None,
            json=True,
        )

        # Capture stdout
        captured_output = StringIO()
        with patch.object(sys, "stdout", captured_output):
            cmd_record_attempt(args)

        output = captured_output.getvalue()
        result = json.loads(output)

        assert "attempt_id" in result
        assert result["command"] == "pytest tests/ -v"
        assert result["tag"] == "pytest"
        assert "started_at" in result

    def test_record_attempt_with_custom_tag(self, initialized_project):
        """Record attempt with custom tag."""
        args = argparse.Namespace(
            command="make build",
            tag="my-build",
            format=None,
            cwd=None,
            json=True,
        )

        captured_output = StringIO()
        with patch.object(sys, "stdout", captured_output):
            cmd_record_attempt(args)

        result = json.loads(captured_output.getvalue())
        assert result["tag"] == "my-build"

    def test_record_attempt_with_format(self, initialized_project):
        """Record attempt with explicit format hint."""
        args = argparse.Namespace(
            command="python test.py",
            tag=None,
            format="pytest_text",
            cwd=None,
            json=True,
        )

        captured_output = StringIO()
        with patch.object(sys, "stdout", captured_output):
            cmd_record_attempt(args)

        result = json.loads(captured_output.getvalue())
        assert result["attempt_id"] is not None

        # Verify format was stored in database
        store = BirdStore.open(initialized_project / ".lq")
        attempt_info = store.connection.execute(
            "SELECT format_hint FROM attempts WHERE id = ?",
            [result["attempt_id"]],
        ).fetchone()
        store.close()

        assert attempt_info[0] == "pytest_text"

    def test_record_attempt_creates_session(self, initialized_project):
        """Recording an attempt creates a session."""
        args = argparse.Namespace(
            command="test command",
            tag="mytest",
            format=None,
            cwd=None,
            json=True,
        )

        captured_output = StringIO()
        with patch.object(sys, "stdout", captured_output):
            cmd_record_attempt(args)

        # Verify session exists
        store = BirdStore.open(initialized_project / ".lq")
        sessions = store.connection.execute(
            "SELECT session_id FROM sessions WHERE session_id = ?",
            ["record-mytest"],
        ).fetchall()
        store.close()

        assert len(sessions) == 1

    def test_record_attempt_pending_status(self, initialized_project):
        """Recorded attempt has pending status."""
        args = argparse.Namespace(
            command="long running command",
            tag=None,
            format=None,
            cwd=None,
            json=True,
        )

        captured_output = StringIO()
        with patch.object(sys, "stdout", captured_output):
            cmd_record_attempt(args)

        result = json.loads(captured_output.getvalue())

        store = BirdStore.open(initialized_project / ".lq")
        status = store.get_attempt_status(result["attempt_id"])
        store.close()

        assert status == "pending"

    def test_record_attempt_text_output(self, initialized_project):
        """Record attempt with text output (no --json)."""
        args = argparse.Namespace(
            command="make build",
            tag=None,
            format=None,
            cwd=None,
            json=False,
        )

        captured_output = StringIO()
        with patch.object(sys, "stdout", captured_output):
            cmd_record_attempt(args)

        output = captured_output.getvalue()
        assert output.startswith("Recorded attempt: ")


class TestRecordOutcome:
    """Tests for cmd_record_outcome."""

    def test_record_outcome_with_prior_attempt(self, initialized_project):
        """Record outcome linking to prior attempt."""
        # First record an attempt
        attempt_args = argparse.Namespace(
            command="pytest tests/",
            tag="test",
            format=None,
            cwd=None,
            json=True,
        )

        captured_output = StringIO()
        with patch.object(sys, "stdout", captured_output):
            cmd_record_attempt(attempt_args)

        attempt_result = json.loads(captured_output.getvalue())
        attempt_id = attempt_result["attempt_id"]

        # Now record outcome
        outcome_args = argparse.Namespace(
            attempt=attempt_id,
            command=None,
            exit=0,
            parse=False,
            format=None,
            tag=None,
            output=None,
            json=True,
        )

        # Mock stdin as empty
        with (
            patch.object(sys, "stdin", StringIO("")),
            patch.object(sys.stdin, "isatty", return_value=True),
        ):
            captured_output = StringIO()
            with patch.object(sys, "stdout", captured_output):
                cmd_record_outcome(outcome_args)

        result = json.loads(captured_output.getvalue())

        assert result["recorded"] is True
        assert result["attempt_id"] == attempt_id
        assert result["exit_code"] == 0
        assert result["duration_ms"] >= 0

    def test_record_outcome_standalone(self, initialized_project):
        """Record outcome without prior attempt (standalone mode)."""
        args = argparse.Namespace(
            attempt=None,
            command="echo hello",
            exit=0,
            parse=False,
            format=None,
            tag=None,
            output=None,
            json=True,
        )

        # Mock stdin as empty
        with (
            patch.object(sys, "stdin", StringIO("")),
            patch.object(sys.stdin, "isatty", return_value=True),
        ):
            captured_output = StringIO()
            with patch.object(sys, "stdout", captured_output):
                cmd_record_outcome(args)

        result = json.loads(captured_output.getvalue())

        assert result["recorded"] is True
        assert result["attempt_id"] is not None
        assert result["duration_ms"] == 0  # Unknown in standalone mode

    def test_record_outcome_with_parse(self, initialized_project):
        """Record outcome with event parsing."""
        # First record attempt
        attempt_args = argparse.Namespace(
            command="mypy src/",
            tag="typecheck",
            format="mypy_text",
            cwd=None,
            json=True,
        )

        captured_output = StringIO()
        with patch.object(sys, "stdout", captured_output):
            cmd_record_attempt(attempt_args)

        attempt_result = json.loads(captured_output.getvalue())
        attempt_id = attempt_result["attempt_id"]

        # Now record outcome with parse
        outcome_args = argparse.Namespace(
            attempt=attempt_id,
            command=None,
            exit=1,
            parse=True,
            format=None,
            tag=None,
            output=None,
            json=True,
        )

        # Provide mypy-style error output via stdin
        test_output = b"src/main.py:10: error: Incompatible types\n"

        # Create a mock stdin.buffer that returns our test output
        mock_stdin = type(
            "MockStdin",
            (),
            {"isatty": lambda self: False, "buffer": BytesIO(test_output)},
        )()

        with patch.object(sys, "stdin", mock_stdin):
            captured_output = StringIO()
            with patch.object(sys, "stdout", captured_output):
                cmd_record_outcome(outcome_args)

        result = json.loads(captured_output.getvalue())

        assert result["recorded"] is True
        assert result["output_bytes"] == len(test_output)
        # Events may or may not be parsed depending on duck_hunt availability
        assert "events" in result

    def test_record_outcome_from_file(self, initialized_project, temp_dir):
        """Record outcome reading output from file."""
        # Create a test output file
        test_content = "Test output\nLine 2\n"
        output_file = temp_dir / "output.log"
        output_file.write_text(test_content)

        args = argparse.Namespace(
            attempt=None,
            command="test command",
            exit=0,
            parse=False,
            format=None,
            tag=None,
            output=str(output_file),
            json=True,
        )

        captured_output = StringIO()
        with patch.object(sys, "stdout", captured_output):
            cmd_record_outcome(args)

        result = json.loads(captured_output.getvalue())

        assert result["recorded"] is True
        assert result["output_bytes"] == len(test_content)

    def test_record_outcome_failure(self, initialized_project):
        """Record outcome for failed command."""
        args = argparse.Namespace(
            attempt=None,
            command="failing command",
            exit=1,
            parse=False,
            format=None,
            tag=None,
            output=None,
            json=True,
        )

        with (
            patch.object(sys, "stdin", StringIO("")),
            patch.object(sys.stdin, "isatty", return_value=True),
        ):
            captured_output = StringIO()
            with patch.object(sys, "stdout", captured_output):
                cmd_record_outcome(args)

        result = json.loads(captured_output.getvalue())

        assert result["exit_code"] == 1

    def test_record_outcome_nonexistent_attempt(self, initialized_project):
        """Error when attempt ID doesn't exist."""
        args = argparse.Namespace(
            attempt="nonexistent-id",
            command=None,
            exit=0,
            parse=False,
            format=None,
            tag=None,
            output=None,
            json=True,
        )

        with (
            patch.object(sys, "stdin", StringIO("")),
            patch.object(sys.stdin, "isatty", return_value=True),
        ):
            with pytest.raises(SystemExit) as exc_info:
                cmd_record_outcome(args)

        assert exc_info.value.code == 1

    def test_record_outcome_no_attempt_or_command(self, initialized_project):
        """Error when neither attempt nor command provided."""
        args = argparse.Namespace(
            attempt=None,
            command=None,
            exit=0,
            parse=False,
            format=None,
            tag=None,
            output=None,
            json=True,
        )

        with pytest.raises(SystemExit) as exc_info:
            cmd_record_outcome(args)

        assert exc_info.value.code == 1

    def test_record_outcome_text_output(self, initialized_project):
        """Record outcome with text output (no --json)."""
        args = argparse.Namespace(
            attempt=None,
            command="test command",
            exit=0,
            parse=False,
            format=None,
            tag=None,
            output=None,
            json=False,
        )

        with (
            patch.object(sys, "stdin", StringIO("")),
            patch.object(sys.stdin, "isatty", return_value=True),
        ):
            captured_output = StringIO()
            with patch.object(sys, "stdout", captured_output):
                cmd_record_outcome(args)

        output = captured_output.getvalue()
        assert "Recorded outcome: OK" in output


class TestRecordIntegration:
    """Integration tests for the full record flow."""

    def test_full_pre_post_hook_flow(self, initialized_project):
        """Simulate a full pre/post hook flow."""
        # PreToolUse: Record attempt
        attempt_args = argparse.Namespace(
            command="pytest tests/ -v",
            tag="test",
            format="pytest_text",
            cwd=None,
            json=True,
        )

        captured_output = StringIO()
        with patch.object(sys, "stdout", captured_output):
            cmd_record_attempt(attempt_args)

        attempt_result = json.loads(captured_output.getvalue())
        attempt_id = attempt_result["attempt_id"]

        # Verify attempt is pending
        store = BirdStore.open(initialized_project / ".lq")
        assert store.get_attempt_status(attempt_id) == "pending"
        store.close()

        # PostToolUse: Record outcome
        test_output = b"===== test session starts =====\ntest_foo.py::test_bar PASSED\n"

        outcome_args = argparse.Namespace(
            attempt=attempt_id,
            command=None,
            exit=0,
            parse=True,
            format=None,
            tag=None,
            output=None,
            json=True,
        )

        mock_stdin = type(
            "MockStdin",
            (),
            {"isatty": lambda self: False, "buffer": BytesIO(test_output)},
        )()

        with patch.object(sys, "stdin", mock_stdin):
            captured_output = StringIO()
            with patch.object(sys, "stdout", captured_output):
                cmd_record_outcome(outcome_args)

        outcome_result = json.loads(captured_output.getvalue())

        # Verify completion
        assert outcome_result["recorded"] is True
        assert outcome_result["exit_code"] == 0
        assert outcome_result["duration_ms"] >= 0
        assert outcome_result["output_bytes"] == len(test_output)

        # Verify attempt is now completed
        store = BirdStore.open(initialized_project / ".lq")
        assert store.get_attempt_status(attempt_id) == "completed"

        # Verify output was stored
        stored_output = store.read_output(attempt_id)
        assert stored_output == test_output

        store.close()

    def test_history_shows_recorded_run(self, initialized_project):
        """Recorded runs appear in history."""
        # Record a complete run
        args = argparse.Namespace(
            attempt=None,
            command="echo test",
            exit=0,
            parse=False,
            format=None,
            tag="echo-test",
            output=None,
            json=True,
        )

        with (
            patch.object(sys, "stdin", StringIO("")),
            patch.object(sys.stdin, "isatty", return_value=True),
        ):
            captured_output = StringIO()
            with patch.object(sys, "stdout", captured_output):
                cmd_record_outcome(args)

        # Check history
        store = BirdStore.open(initialized_project / ".lq")
        invocations = store.recent_invocations(limit=10)
        store.close()

        assert len(invocations) >= 1
        assert any(inv["source_name"] == "echo-test" for inv in invocations)

    def test_events_queryable_after_record(self, initialized_project):
        """Events from parsed output are queryable."""
        # Record with parseable output
        # Use a simple format that might get picked up
        test_output = b"ERROR: src/main.py:10:5: undefined variable 'x'\n"

        args = argparse.Namespace(
            attempt=None,
            command="mypy src/",
            exit=1,
            parse=True,
            format="auto",  # Let duck_hunt detect
            tag="typecheck",
            output=None,
            json=True,
        )

        mock_stdin = type(
            "MockStdin",
            (),
            {"isatty": lambda self: False, "buffer": BytesIO(test_output)},
        )()

        with patch.object(sys, "stdin", mock_stdin):
            captured_output = StringIO()
            with patch.object(sys, "stdout", captured_output):
                cmd_record_outcome(args)

        result = json.loads(captured_output.getvalue())
        attempt_id = result["attempt_id"]

        # Verify the invocation was recorded (events may be 0 if duck_hunt not available)
        store = BirdStore.open(initialized_project / ".lq")
        store.connection.execute(
            "SELECT COUNT(*) FROM events WHERE invocation_id = ?",
            [attempt_id],
        ).fetchone()[0]  # Just verify query succeeds
        store.close()
        assert result["recorded"] is True
