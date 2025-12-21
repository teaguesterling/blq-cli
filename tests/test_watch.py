"""Tests for watch mode."""

import argparse
import time
from unittest.mock import MagicMock, patch

import pytest

from blq.commands.core import BlqConfig
from blq.commands.watch_cmd import (
    DebounceHandler,
    WatchController,
    WatchSession,
    _matches_pattern,
    cmd_watch,
)


class TestWatchSession:
    """Tests for WatchSession dataclass."""

    def test_create_generates_session_id(self):
        """Creating a session generates a unique session ID."""
        session = WatchSession.create(
            commands=["build", "test"],
            include_patterns=["src/**/*"],
            exclude_patterns=["**/__pycache__/**"],
        )
        assert session.session_id
        assert len(session.session_id) == 8  # Short UUID
        assert session.commands == ["build", "test"]
        assert session.run_count == 0

    def test_create_with_custom_debounce(self):
        """Creating a session with custom debounce."""
        session = WatchSession.create(
            commands=["build"],
            include_patterns=[],
            exclude_patterns=[],
            debounce_ms=1000,
        )
        assert session.debounce_ms == 1000


class TestMatchesPattern:
    """Tests for pattern matching helper."""

    def test_matches_glob_pattern(self):
        """Matches glob patterns."""
        assert _matches_pattern("src/main.py", ["src/**/*.py"])
        assert _matches_pattern("src/utils/helper.py", ["src/**/*.py"])
        assert not _matches_pattern("tests/test_main.py", ["src/**/*.py"])

    def test_matches_filename_pattern(self):
        """Matches by filename alone."""
        assert _matches_pattern("/path/to/file.py", ["*.py"])
        assert not _matches_pattern("/path/to/file.js", ["*.py"])

    def test_matches_any_pattern(self):
        """Matches if any pattern matches."""
        patterns = ["*.py", "*.js"]
        assert _matches_pattern("file.py", patterns)
        assert _matches_pattern("file.js", patterns)
        assert not _matches_pattern("file.rs", patterns)


class TestDebounceHandler:
    """Tests for DebounceHandler."""

    def test_filters_by_include_pattern(self):
        """Only includes files matching include patterns."""
        callback = MagicMock()
        handler = DebounceHandler(
            callback=callback,
            debounce_ms=10,
            include_patterns=["*.py"],
            exclude_patterns=[],
        )

        # Mock event for .py file
        py_event = MagicMock()
        py_event.is_directory = False
        py_event.event_type = "modified"
        py_event.src_path = "/path/to/file.py"

        # Mock event for .js file
        js_event = MagicMock()
        js_event.is_directory = False
        js_event.event_type = "modified"
        js_event.src_path = "/path/to/file.js"

        handler.on_any_event(py_event)
        handler.on_any_event(js_event)

        # Wait for debounce
        time.sleep(0.05)

        # Only .py file should trigger callback
        assert callback.called
        called_files = callback.call_args[0][0]
        assert "/path/to/file.py" in called_files
        assert "/path/to/file.js" not in called_files

    def test_excludes_by_pattern(self):
        """Excludes files matching exclude patterns."""
        callback = MagicMock()
        handler = DebounceHandler(
            callback=callback,
            debounce_ms=10,
            include_patterns=["*.py"],
            exclude_patterns=["**/test_*.py"],
        )

        # Main file
        main_event = MagicMock()
        main_event.is_directory = False
        main_event.event_type = "modified"
        main_event.src_path = "/path/to/main.py"

        # Test file (should be excluded)
        test_event = MagicMock()
        test_event.is_directory = False
        test_event.event_type = "modified"
        test_event.src_path = "/path/to/test_main.py"

        handler.on_any_event(main_event)
        handler.on_any_event(test_event)

        time.sleep(0.05)

        called_files = callback.call_args[0][0]
        assert "/path/to/main.py" in called_files
        assert "/path/to/test_main.py" not in called_files

    def test_debounce_batches_events(self):
        """Multiple rapid events are batched into one callback."""
        callback = MagicMock()
        handler = DebounceHandler(
            callback=callback,
            debounce_ms=50,
            include_patterns=["*.py"],
            exclude_patterns=[],
        )

        # Fire multiple events rapidly
        for i in range(5):
            event = MagicMock()
            event.is_directory = False
            event.event_type = "modified"
            event.src_path = f"/path/to/file{i}.py"
            handler.on_any_event(event)
            time.sleep(0.01)

        # Wait for debounce
        time.sleep(0.1)

        # Should be called once with all files
        assert callback.call_count == 1
        called_files = callback.call_args[0][0]
        assert len(called_files) == 5

    def test_ignores_directory_events(self):
        """Directory events are ignored."""
        callback = MagicMock()
        handler = DebounceHandler(
            callback=callback,
            debounce_ms=10,
            include_patterns=[],
            exclude_patterns=[],
        )

        dir_event = MagicMock()
        dir_event.is_directory = True
        dir_event.event_type = "modified"
        dir_event.src_path = "/path/to/dir"

        handler.on_any_event(dir_event)
        time.sleep(0.05)

        assert not callback.called


class TestWatchController:
    """Tests for WatchController."""

    def test_state_transitions(self, initialized_project):
        """Controller transitions through states correctly."""
        config = BlqConfig.find()
        session = WatchSession.create(
            commands=[],
            include_patterns=[],
            exclude_patterns=[],
        )
        controller = WatchController(session, config, quiet=True)

        assert controller._state == "idle"

        # Trigger change - should go to running
        # (but since no commands, will quickly return to idle)
        controller.on_files_changed({"file.py"})
        time.sleep(0.1)
        assert controller._state == "idle"


class TestWatchConfig:
    """Tests for WatchConfig in BlqConfig."""

    def test_default_watch_config(self, initialized_project):
        """BlqConfig has default watch config."""
        config = BlqConfig.find()
        watch_config = config.watch_config

        assert watch_config.debounce_ms == 500
        assert "src/**/*" in watch_config.include
        assert "**/__pycache__/**" in watch_config.exclude
        assert watch_config.clear_screen is False
        assert watch_config.quiet is False

    def test_watch_config_from_yaml(self, initialized_project):
        """WatchConfig is loaded from config.yaml."""
        config = BlqConfig.find()

        # Write custom watch config
        config_path = config.config_path
        import yaml

        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

        data["watch"] = {
            "debounce_ms": 1000,
            "include": ["custom/**/*"],
            "exclude": ["vendor/**/*"],
            "clear_screen": True,
            "quiet": True,
        }

        with open(config_path, "w") as f:
            yaml.dump(data, f)

        # Reload config
        config._watch_config = None
        watch_config = config.watch_config

        assert watch_config.debounce_ms == 1000
        assert watch_config.include == ["custom/**/*"]
        assert watch_config.exclude == ["vendor/**/*"]
        assert watch_config.clear_screen is True
        assert watch_config.quiet is True


class TestCmdWatch:
    """Tests for cmd_watch function."""

    def test_watch_no_commands_error(self, initialized_project, capsys):
        """Error when no commands registered."""
        # Ensure no commands are registered
        config = BlqConfig.find()
        config._commands = {}
        config.save_commands()

        args = argparse.Namespace(
            commands=[],
            include=[],
            exclude=[],
            debounce=None,
            quiet=False,
            clear=False,
            once=False,
        )

        with pytest.raises(SystemExit):
            cmd_watch(args)

        captured = capsys.readouterr()
        assert "No commands to run" in captured.err

    def test_watch_warns_missing_commands(self, initialized_project, capsys):
        """Warning when specified commands aren't registered."""
        args = argparse.Namespace(
            commands=["nonexistent"],
            include=[],
            exclude=[],
            debounce=None,
            quiet=False,
            clear=False,
            once=True,  # Exit immediately
        )

        # Don't actually run - just check the warning
        with patch("blq.commands.watch_cmd.Observer"):
            with patch("blq.commands.watch_cmd.WatchController"):
                try:
                    cmd_watch(args)
                except (SystemExit, StopIteration):
                    pass

        captured = capsys.readouterr()
        assert "not registered" in captured.err


class TestSessionId:
    """Tests for session_id in parquet schema."""

    def test_session_id_in_schema(self):
        """session_id is in the parquet schema."""
        from blq.commands.core import PARQUET_SCHEMA_COLUMNS

        assert "session_id" in PARQUET_SCHEMA_COLUMNS
