"""Tests for Phase 2: Command registry support."""

import json
from pathlib import Path

import pytest

from blq.commands.core import BlqConfig, RegisteredCommand


class TestRegisteredCommand:
    """Tests for RegisteredCommand dataclass."""

    def test_create_with_defaults(self):
        """Create command with default values."""
        cmd = RegisteredCommand(name="build", cmd="make -j8")

        assert cmd.name == "build"
        assert cmd.cmd == "make -j8"
        assert cmd.description == ""
        assert cmd.timeout == 300
        assert cmd.format == "auto"

    def test_create_with_all_fields(self):
        """Create command with all fields specified."""
        cmd = RegisteredCommand(
            name="test",
            cmd="pytest -v",
            description="Run tests",
            timeout=600,
            format="pytest_json",
        )

        assert cmd.name == "test"
        assert cmd.cmd == "pytest -v"
        assert cmd.description == "Run tests"
        assert cmd.timeout == 600
        assert cmd.format == "pytest_json"

    def test_to_dict(self):
        """Convert to dictionary for serialization."""
        cmd = RegisteredCommand(
            name="build",
            cmd="make -j8",
            description="Build project",
            timeout=300,
            format="auto",
        )

        d = cmd.to_dict()

        assert d["cmd"] == "make -j8"
        assert d["description"] == "Build project"
        assert d["timeout"] == 300
        assert d["format"] == "auto"
        # name is not included in to_dict (it's the key)
        assert "name" not in d


class TestLoadSaveCommands:
    """Tests for loading and saving commands via BlqConfig."""

    def test_load_empty_directory(self, lq_dir):
        """Load returns empty dict when no commands.yaml exists."""
        config = BlqConfig.load(lq_dir)
        assert config.commands == {}

    def test_save_and_load_roundtrip(self, lq_dir):
        """Save commands and load them back."""
        config = BlqConfig.load(lq_dir)
        config._commands = {
            "build": RegisteredCommand(
                name="build",
                cmd="make -j8",
                description="Build the project",
            ),
            "test": RegisteredCommand(
                name="test",
                cmd="pytest -v",
                description="Run tests",
                timeout=600,
            ),
        }
        config.save_commands()

        # Reload to verify persistence
        config2 = BlqConfig.load(lq_dir)
        loaded = config2.commands

        assert len(loaded) == 2
        assert loaded["build"].cmd == "make -j8"
        assert loaded["build"].description == "Build the project"
        assert loaded["test"].cmd == "pytest -v"
        assert loaded["test"].timeout == 600

    def test_save_creates_yaml_file(self, lq_dir):
        """Save creates commands.yaml file."""
        config = BlqConfig.load(lq_dir)
        config._commands = {
            "build": RegisteredCommand(name="build", cmd="make"),
        }
        config.save_commands()

        yaml_path = lq_dir / "commands.yaml"
        assert yaml_path.exists()
        content = yaml_path.read_text()
        assert "commands:" in content
        assert "build:" in content

    def test_load_preserves_format(self, lq_dir):
        """Load preserves custom format hints."""
        config = BlqConfig.load(lq_dir)
        config._commands = {
            "lint": RegisteredCommand(
                name="lint",
                cmd="eslint .",
                format="eslint_json",
            ),
        }
        config.save_commands()

        config2 = BlqConfig.load(lq_dir)
        assert config2.commands["lint"].format == "eslint_json"

    def test_save_empty_commands(self, lq_dir):
        """Save empty commands dict creates valid yaml."""
        config = BlqConfig.load(lq_dir)
        config._commands = {}
        config.save_commands()

        yaml_path = lq_dir / "commands.yaml"
        assert yaml_path.exists()

        config2 = BlqConfig.load(lq_dir)
        assert config2.commands == {}


class TestCommandRegistryCLI:
    """Integration tests for command registry CLI commands."""

    def test_register_command(self, initialized_project, capsys):
        """Register a new command."""
        import argparse

        from blq.cli import cmd_register

        args = argparse.Namespace(
            name="build",
            cmd=["make", "-j8"],
            description="Build the project",
            timeout=300,
            format="auto",
            no_capture=False,
            force=False,
        )

        cmd_register(args)

        captured = capsys.readouterr()
        assert "Registered command 'build'" in captured.out

        # Verify it was saved
        config = BlqConfig.load(Path(".lq"))
        assert "build" in config.commands
        assert config.commands["build"].cmd == "make -j8"

    def test_register_command_force_overwrite(self, initialized_project, capsys):
        """Force overwrite existing command."""
        import argparse

        from blq.cli import cmd_register

        # Register first time
        args = argparse.Namespace(
            name="build",
            cmd=["make"],
            description="v1",
            timeout=300,
            format="auto",
            no_capture=False,
            force=False,
        )
        cmd_register(args)

        # Register again with force
        args = argparse.Namespace(
            name="build",
            cmd=["make", "-j8"],
            description="v2",
            timeout=300,
            format="auto",
            no_capture=False,
            force=True,
        )
        cmd_register(args)

        config = BlqConfig.load(Path(".lq"))
        assert config.commands["build"].cmd == "make -j8"
        assert config.commands["build"].description == "v2"

    def test_register_command_no_force_fails(self, initialized_project, capsys):
        """Refuse to overwrite without force flag."""
        import argparse

        from blq.cli import cmd_register

        # Register first time
        args = argparse.Namespace(
            name="build",
            cmd=["make"],
            description="v1",
            timeout=300,
            format="auto",
            no_capture=False,
            force=False,
        )
        cmd_register(args)

        # Try to register again without force
        args = argparse.Namespace(
            name="build",
            cmd=["make", "-j8"],
            description="v2",
            timeout=300,
            format="auto",
            no_capture=False,
            force=False,
        )

        with pytest.raises(SystemExit) as exc_info:
            cmd_register(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "already exists" in captured.err

    def test_unregister_command(self, initialized_project, capsys):
        """Unregister an existing command."""
        import argparse

        from blq.cli import cmd_register, cmd_unregister

        # Register first
        args = argparse.Namespace(
            name="build",
            cmd=["make"],
            description="",
            timeout=300,
            format="auto",
            no_capture=False,
            force=False,
        )
        cmd_register(args)

        # Unregister
        args = argparse.Namespace(name="build")
        cmd_unregister(args)

        captured = capsys.readouterr()
        assert "Unregistered command 'build'" in captured.out

        config = BlqConfig.load(Path(".lq"))
        assert "build" not in config.commands

    def test_unregister_nonexistent_fails(self, initialized_project, capsys):
        """Unregister nonexistent command fails."""
        import argparse

        from blq.cli import cmd_unregister

        args = argparse.Namespace(name="nonexistent")

        with pytest.raises(SystemExit) as exc_info:
            cmd_unregister(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "not found" in captured.err

    def test_list_commands_empty(self, initialized_project, capsys):
        """List commands when none registered."""
        import argparse

        from blq.cli import cmd_commands

        args = argparse.Namespace(json=False)
        cmd_commands(args)

        captured = capsys.readouterr()
        assert "No commands registered" in captured.out

    def test_list_commands(self, initialized_project, capsys):
        """List registered commands."""
        import argparse

        from blq.cli import cmd_commands, cmd_register

        # Register some commands
        for name, cmd, desc in [
            ("build", ["make", "-j8"], "Build project"),
            ("test", ["pytest"], "Run tests"),
        ]:
            args = argparse.Namespace(
                name=name,
                cmd=cmd,
                description=desc,
                timeout=300,
                format="auto",
                no_capture=False,
                force=False,
            )
            cmd_register(args)
            capsys.readouterr()  # Clear output

        # List commands
        args = argparse.Namespace(json=False)
        cmd_commands(args)

        captured = capsys.readouterr()
        assert "build" in captured.out
        assert "make -j8" in captured.out
        assert "Build project" in captured.out
        assert "test" in captured.out
        assert "pytest" in captured.out

    def test_list_commands_json(self, initialized_project, capsys):
        """List commands in JSON format."""
        import argparse

        from blq.cli import cmd_commands, cmd_register

        args = argparse.Namespace(
            name="build",
            cmd=["make"],
            description="Build",
            timeout=300,
            format="auto",
            no_capture=False,
            force=False,
        )
        cmd_register(args)
        capsys.readouterr()

        args = argparse.Namespace(json=True)
        cmd_commands(args)

        captured = capsys.readouterr()
        data = json.loads(captured.out)

        assert "build" in data
        assert data["build"]["cmd"] == "make"
        assert data["build"]["description"] == "Build"


class TestRunRegisteredCommand:
    """Tests for running registered commands."""

    def test_run_by_name(self, initialized_project, sample_success_script, capsys):
        """Run a command by its registered name."""
        import argparse

        from blq.cli import cmd_register, cmd_run

        # Register the command
        args = argparse.Namespace(
            name="success",
            cmd=[str(sample_success_script)],
            description="Run success script",
            timeout=300,
            format="auto",
            no_capture=False,
            force=False,
        )
        cmd_register(args)
        capsys.readouterr()

        # Run by name
        args = argparse.Namespace(
            command=["success"],
            name=None,
            format="auto",
            keep_raw=False,
            json=True,
            markdown=False,
            quiet=False,
            include_warnings=False,
            error_limit=20,
            capture=None,
        )

        # Should not raise (exit code 0)
        try:
            cmd_run(args)
        except SystemExit as e:
            assert e.code == 0

        captured = capsys.readouterr()
        data = json.loads(captured.out)

        assert data["status"] == "OK"
        assert data["exit_code"] == 0

    def test_run_fails_for_unregistered_command(
        self, initialized_project, sample_success_script, capsys
    ):
        """Run should fail for unregistered commands (use exec for ad-hoc)."""
        import argparse

        from blq.cli import cmd_run

        args = argparse.Namespace(
            command=[str(sample_success_script)],
            name=None,
            format="auto",
            keep_raw=False,
            json=True,
            markdown=False,
            quiet=False,
            include_warnings=False,
            error_limit=20,
            capture=None,
            register=False,
        )

        # cmd_run should exit with error for unregistered commands
        with pytest.raises(SystemExit) as exc_info:
            cmd_run(args)

        assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "not a registered command" in captured.err

    def test_run_registered_uses_stored_format(self, initialized_project, capsys):
        """Running registered command uses its stored format hint."""
        import argparse

        from blq.cli import cmd_register

        # Register with specific format
        args = argparse.Namespace(
            name="lint",
            cmd=["echo", "test"],
            description="Run linter",
            timeout=300,
            format="eslint_json",
            no_capture=False,
            force=False,
        )
        cmd_register(args)

        config = BlqConfig.load(Path(".lq"))
        assert config.commands["lint"].format == "eslint_json"

    def test_run_with_extra_args_passes_them_through(self, initialized_project, capsys):
        """Extra args after registered command name are passed through."""
        import argparse

        from blq.cli import cmd_register, cmd_run

        # Register 'build' command
        args = argparse.Namespace(
            name="build",
            cmd=["echo build"],
            description="",
            timeout=300,
            format="auto",
            no_capture=False,
            force=False,
        )
        cmd_register(args)
        capsys.readouterr()

        # Run 'build extra args' - extra args are now passed through to the command
        args = argparse.Namespace(
            command=["build", "extra", "args"],
            name=None,
            format="auto",
            keep_raw=False,
            json=True,
            markdown=False,
            quiet=False,
            summary=False,
            verbose=False,
            include_warnings=False,
            error_limit=20,
            capture=None,
            register=False,
            positional_args=None,
        )

        # cmd_run should succeed - extra args become passthrough
        # The expanded command will be "echo build extra args"
        with pytest.raises(SystemExit) as exc_info:
            cmd_run(args)

        # Should exit with 0 (success)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        # Check that the command ran (JSON output contains run info)
        assert "run_id" in captured.out
        assert '"command": "echo build extra args"' in captured.out

    def test_run_unregistered_command_fails(self, initialized_project, capsys):
        """Unregistered command name fails with helpful error."""
        import argparse

        from blq.cli import cmd_run

        # Try to run 'notregistered' - should fail
        args = argparse.Namespace(
            command=["notregistered"],
            name=None,
            format="auto",
            keep_raw=False,
            json=True,
            markdown=False,
            quiet=False,
            summary=False,
            verbose=False,
            include_warnings=False,
            error_limit=20,
            capture=None,
            register=False,
            positional_args=None,
        )

        with pytest.raises(SystemExit) as exc_info:
            cmd_run(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "not a registered command" in captured.err
