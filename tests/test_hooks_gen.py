"""Tests for hook script generation."""

import argparse
import re

import pytest


class TestComputeChecksum:
    """Tests for command checksum computation."""

    def test_checksum_deterministic(self):
        """Same command produces same checksum."""
        from blq.commands.core import RegisteredCommand
        from blq.commands.hooks_gen import compute_command_checksum

        cmd = RegisteredCommand(name="test", cmd="pytest tests/")
        checksum1 = compute_command_checksum(cmd)
        checksum2 = compute_command_checksum(cmd)
        assert checksum1 == checksum2

    def test_checksum_changes_with_cmd(self):
        """Different cmd produces different checksum."""
        from blq.commands.core import RegisteredCommand
        from blq.commands.hooks_gen import compute_command_checksum

        cmd1 = RegisteredCommand(name="test", cmd="pytest tests/")
        cmd2 = RegisteredCommand(name="test", cmd="pytest tests/ -v")
        assert compute_command_checksum(cmd1) != compute_command_checksum(cmd2)

    def test_checksum_changes_with_template(self):
        """Different template produces different checksum."""
        from blq.commands.core import RegisteredCommand
        from blq.commands.hooks_gen import compute_command_checksum

        cmd1 = RegisteredCommand(name="test", tpl="pytest {path}")
        cmd2 = RegisteredCommand(name="test", tpl="pytest {path} -v")
        assert compute_command_checksum(cmd1) != compute_command_checksum(cmd2)

    def test_checksum_changes_with_defaults(self):
        """Different defaults produces different checksum."""
        from blq.commands.core import RegisteredCommand
        from blq.commands.hooks_gen import compute_command_checksum

        cmd1 = RegisteredCommand(name="test", tpl="pytest {path}", defaults={"path": "tests/"})
        cmd2 = RegisteredCommand(name="test", tpl="pytest {path}", defaults={"path": "src/"})
        assert compute_command_checksum(cmd1) != compute_command_checksum(cmd2)

    def test_checksum_is_12_chars(self):
        """Checksum is truncated to 12 characters."""
        from blq.commands.core import RegisteredCommand
        from blq.commands.hooks_gen import compute_command_checksum

        cmd = RegisteredCommand(name="test", cmd="pytest")
        checksum = compute_command_checksum(cmd)
        assert len(checksum) == 12
        assert re.match(r"^[a-f0-9]+$", checksum)


class TestRenderStandaloneCmdTemplate:
    """Tests for template rendering to shell variables."""

    def test_simple_template(self):
        """Simple template with one parameter."""
        from blq.commands.hooks_gen import render_standalone_cmd_template

        result = render_standalone_cmd_template("pytest {path}")
        assert result == "pytest ${path}"

    def test_multiple_parameters(self):
        """Template with multiple parameters."""
        from blq.commands.hooks_gen import render_standalone_cmd_template

        result = render_standalone_cmd_template("pytest {path} -k {filter} -v")
        assert result == "pytest ${path} -k ${filter} -v"

    def test_no_parameters(self):
        """Template with no parameters passes through."""
        from blq.commands.hooks_gen import render_standalone_cmd_template

        result = render_standalone_cmd_template("pytest tests/ -v")
        assert result == "pytest tests/ -v"


class TestGenerateHookScript:
    """Tests for hook script generation."""

    def test_generates_valid_shell_script(self):
        """Generated script starts with shebang."""
        from blq.commands.core import RegisteredCommand
        from blq.commands.hooks_gen import generate_hook_script

        cmd = RegisteredCommand(name="lint", cmd="ruff check .")
        script = generate_hook_script(cmd)
        assert script.startswith("#!/bin/sh\n")

    def test_includes_command_name(self):
        """Script includes command name in header."""
        from blq.commands.core import RegisteredCommand
        from blq.commands.hooks_gen import generate_hook_script

        cmd = RegisteredCommand(name="lint", cmd="ruff check .")
        script = generate_hook_script(cmd)
        assert "# Command: lint" in script
        assert 'BLQ_COMMAND="lint"' in script

    def test_includes_checksum(self):
        """Script includes checksum for staleness detection."""
        from blq.commands.core import RegisteredCommand
        from blq.commands.hooks_gen import compute_command_checksum, generate_hook_script

        cmd = RegisteredCommand(name="lint", cmd="ruff check .")
        script = generate_hook_script(cmd)
        checksum = compute_command_checksum(cmd)
        assert f"# Checksum: {checksum}" in script
        assert f'BLQ_CHECKSUM="{checksum}"' in script

    def test_simple_command_script(self):
        """Script for simple (non-template) command."""
        from blq.commands.core import RegisteredCommand
        from blq.commands.hooks_gen import generate_hook_script

        cmd = RegisteredCommand(name="lint", cmd="ruff check .")
        script = generate_hook_script(cmd)
        assert 'STANDALONE_CMD="ruff check ."' in script
        assert 'BLQ_CMD="blq run lint"' in script

    def test_template_command_script(self):
        """Script for template command with defaults."""
        from blq.commands.core import RegisteredCommand
        from blq.commands.hooks_gen import generate_hook_script

        cmd = RegisteredCommand(
            name="test",
            tpl="pytest {path} -v",
            defaults={"path": "tests/"},
        )
        script = generate_hook_script(cmd)
        assert "# Template: pytest {path} -v" in script
        assert 'BLQ_DEFAULTS_path="tests/"' in script
        # Shell variable interpolation
        assert "${path}" in script

    def test_includes_help_option(self):
        """Script includes --help option."""
        from blq.commands.core import RegisteredCommand
        from blq.commands.hooks_gen import generate_hook_script

        cmd = RegisteredCommand(name="test", cmd="pytest")
        script = generate_hook_script(cmd)
        assert "--help|-h)" in script
        assert "Usage:" in script

    def test_includes_via_option(self):
        """Script handles --via option."""
        from blq.commands.core import RegisteredCommand
        from blq.commands.hooks_gen import generate_hook_script

        cmd = RegisteredCommand(name="test", cmd="pytest")
        script = generate_hook_script(cmd)
        assert "--via=*)" in script
        assert 'VIA="${BLQ_VIA:-auto}"' in script

    def test_includes_metadata_option(self):
        """Script handles --metadata option."""
        from blq.commands.core import RegisteredCommand
        from blq.commands.hooks_gen import generate_hook_script

        cmd = RegisteredCommand(name="test", cmd="pytest")
        script = generate_hook_script(cmd)
        assert "--metadata=*)" in script
        assert 'METADATA="${BLQ_METADATA:-auto}"' in script

    def test_auto_via_resolves_to_blq_or_standalone(self):
        """Script resolves auto via mode."""
        from blq.commands.core import RegisteredCommand
        from blq.commands.hooks_gen import generate_hook_script

        cmd = RegisteredCommand(name="test", cmd="pytest")
        script = generate_hook_script(cmd)
        assert 'if [ "$VIA" = "auto" ]' in script
        assert "command -v blq" in script

    def test_auto_metadata_depends_on_via(self):
        """Script resolves auto metadata based on via mode."""
        from blq.commands.core import RegisteredCommand
        from blq.commands.hooks_gen import generate_hook_script

        cmd = RegisteredCommand(name="test", cmd="pytest")
        script = generate_hook_script(cmd)
        assert 'if [ "$METADATA" = "auto" ]' in script
        assert 'METADATA="none"' in script  # when via=blq
        assert 'METADATA="footer"' in script  # when via=standalone


class TestGenerateGitHook:
    """Tests for git hook wrapper generation."""

    def test_generates_valid_shell_script(self):
        """Generated git hook starts with shebang."""
        from blq.commands.hooks_gen import generate_git_hook

        script = generate_git_hook(["lint", "test"])
        assert script.startswith("#!/bin/sh\n")

    def test_includes_marker(self):
        """Git hook includes blq marker for identification."""
        from blq.commands.hooks_gen import generate_git_hook

        script = generate_git_hook(["lint"])
        assert "# blq-managed-hook" in script

    def test_calls_hook_scripts(self):
        """Git hook calls .lq/hooks/*.sh scripts."""
        from blq.commands.hooks_gen import generate_git_hook

        script = generate_git_hook(["lint", "test"])
        assert ".lq/hooks/lint.sh" in script
        assert ".lq/hooks/test.sh" in script

    def test_chains_commands_with_and(self):
        """Multiple commands are chained with &&."""
        from blq.commands.hooks_gen import generate_git_hook

        script = generate_git_hook(["lint", "test", "format"])
        # Commands should be chained so failure stops execution
        assert ".lq/hooks/lint.sh" in script
        assert "&&" in script or script.count(".lq/hooks/") == 3

    def test_includes_hook_name(self):
        """Git hook includes hook name in header."""
        from blq.commands.hooks_gen import generate_git_hook

        script = generate_git_hook(["lint"], hook_name="pre-push")
        assert "# Hook: pre-push" in script

    def test_includes_uninstall_instructions(self):
        """Git hook includes uninstall instructions."""
        from blq.commands.hooks_gen import generate_git_hook

        script = generate_git_hook(["lint"])
        assert "blq hooks uninstall git" in script


class TestExtractChecksumFromScript:
    """Tests for extracting checksum from existing scripts."""

    def test_extracts_from_header_comment(self):
        """Extracts checksum from header comment."""
        from blq.commands.hooks_gen import extract_checksum_from_script

        content = """#!/bin/sh
# Checksum: abc123def456
# other stuff
"""
        assert extract_checksum_from_script(content) == "abc123def456"

    def test_extracts_from_variable(self):
        """Extracts checksum from BLQ_CHECKSUM variable."""
        from blq.commands.hooks_gen import extract_checksum_from_script

        content = """#!/bin/sh
BLQ_CHECKSUM="abc123def456"
"""
        assert extract_checksum_from_script(content) == "abc123def456"

    def test_returns_none_if_not_found(self):
        """Returns None if no checksum found."""
        from blq.commands.hooks_gen import extract_checksum_from_script

        content = """#!/bin/sh
echo "no checksum here"
"""
        assert extract_checksum_from_script(content) is None


class TestWriteHookScript:
    """Tests for writing hook scripts to disk."""

    def test_creates_hooks_directory(self, initialized_project):
        """Creates .lq/hooks/ directory if needed."""
        from blq.commands.core import BlqConfig, RegisteredCommand
        from blq.commands.hooks_gen import write_hook_script

        config = BlqConfig.ensure()
        cmd = RegisteredCommand(name="test", cmd="pytest")

        path, written = write_hook_script(cmd, config.lq_dir)
        assert (config.lq_dir / "hooks").is_dir()

    def test_creates_executable_script(self, initialized_project):
        """Creates script with executable permissions."""
        from blq.commands.core import BlqConfig, RegisteredCommand
        from blq.commands.hooks_gen import write_hook_script

        config = BlqConfig.ensure()
        cmd = RegisteredCommand(name="test", cmd="pytest")

        path, written = write_hook_script(cmd, config.lq_dir)
        assert path.exists()
        assert path.stat().st_mode & 0o111  # Has execute bit

    def test_returns_written_true_for_new_script(self, initialized_project):
        """Returns written=True for new script."""
        from blq.commands.core import BlqConfig, RegisteredCommand
        from blq.commands.hooks_gen import write_hook_script

        config = BlqConfig.ensure()
        cmd = RegisteredCommand(name="test", cmd="pytest")

        path, written = write_hook_script(cmd, config.lq_dir)
        assert written is True

    def test_returns_written_false_for_unchanged(self, initialized_project):
        """Returns written=False if script unchanged."""
        from blq.commands.core import BlqConfig, RegisteredCommand
        from blq.commands.hooks_gen import write_hook_script

        config = BlqConfig.ensure()
        cmd = RegisteredCommand(name="test", cmd="pytest")

        # Write twice
        write_hook_script(cmd, config.lq_dir)
        path, written = write_hook_script(cmd, config.lq_dir)
        assert written is False

    def test_force_overwrites(self, initialized_project):
        """Force flag overwrites even if unchanged."""
        from blq.commands.core import BlqConfig, RegisteredCommand
        from blq.commands.hooks_gen import write_hook_script

        config = BlqConfig.ensure()
        cmd = RegisteredCommand(name="test", cmd="pytest")

        # Write twice with force
        write_hook_script(cmd, config.lq_dir)
        path, written = write_hook_script(cmd, config.lq_dir, force=True)
        assert written is True


class TestCheckScriptStaleness:
    """Tests for detecting stale scripts."""

    def test_not_stale_when_missing(self, initialized_project):
        """Script that doesn't exist is not stale."""
        from blq.commands.core import BlqConfig, RegisteredCommand
        from blq.commands.hooks_gen import check_script_staleness

        config = BlqConfig.ensure()
        cmd = RegisteredCommand(name="test", cmd="pytest")

        is_stale, checksum = check_script_staleness(cmd, config.lq_dir)
        assert is_stale is False
        assert checksum is None

    def test_not_stale_when_checksum_matches(self, initialized_project):
        """Script is not stale when checksum matches."""
        from blq.commands.core import BlqConfig, RegisteredCommand
        from blq.commands.hooks_gen import check_script_staleness, write_hook_script

        config = BlqConfig.ensure()
        cmd = RegisteredCommand(name="test", cmd="pytest")

        write_hook_script(cmd, config.lq_dir)
        is_stale, checksum = check_script_staleness(cmd, config.lq_dir)
        assert is_stale is False

    def test_stale_when_command_changed(self, initialized_project):
        """Script is stale when command definition changed."""
        from blq.commands.core import BlqConfig, RegisteredCommand
        from blq.commands.hooks_gen import check_script_staleness, write_hook_script

        config = BlqConfig.ensure()
        cmd_v1 = RegisteredCommand(name="test", cmd="pytest")
        cmd_v2 = RegisteredCommand(name="test", cmd="pytest -v")

        # Write with v1
        write_hook_script(cmd_v1, config.lq_dir)

        # Check with v2
        is_stale, old_checksum = check_script_staleness(cmd_v2, config.lq_dir)
        assert is_stale is True
        assert old_checksum is not None


class TestGeneratedScriptExecution:
    """Tests for executing generated hook scripts."""

    def test_script_dry_run_blq_mode(self, initialized_project):
        """Dry run in blq mode shows blq run command."""
        import subprocess

        from blq.commands.core import BlqConfig, RegisteredCommand
        from blq.commands.hooks_gen import write_hook_script

        config = BlqConfig.ensure()
        cmd = RegisteredCommand(name="test", cmd="pytest tests/")
        write_hook_script(cmd, config.lq_dir)

        script_path = config.lq_dir / "hooks" / "test.sh"
        result = subprocess.run(
            [str(script_path), "--via=blq", "--dry-run"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "blq run test" in result.stdout

    def test_script_dry_run_standalone_mode(self, initialized_project):
        """Dry run in standalone mode shows direct command."""
        import subprocess

        from blq.commands.core import BlqConfig, RegisteredCommand
        from blq.commands.hooks_gen import write_hook_script

        config = BlqConfig.ensure()
        cmd = RegisteredCommand(name="test", cmd="pytest tests/")
        write_hook_script(cmd, config.lq_dir)

        script_path = config.lq_dir / "hooks" / "test.sh"
        result = subprocess.run(
            [str(script_path), "--via=standalone", "--dry-run"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "pytest tests/" in result.stdout

    def test_script_help_option(self, initialized_project):
        """Script --help shows usage information."""
        import subprocess

        from blq.commands.core import BlqConfig, RegisteredCommand
        from blq.commands.hooks_gen import write_hook_script

        config = BlqConfig.ensure()
        cmd = RegisteredCommand(name="test", cmd="pytest tests/")
        write_hook_script(cmd, config.lq_dir)

        script_path = config.lq_dir / "hooks" / "test.sh"
        result = subprocess.run(
            [str(script_path), "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Usage:" in result.stdout
        assert "--via" in result.stdout
        assert "--metadata" in result.stdout

    def test_script_template_with_params(self, initialized_project):
        """Script with template resolves parameters."""
        import subprocess

        from blq.commands.core import BlqConfig, RegisteredCommand
        from blq.commands.hooks_gen import write_hook_script

        config = BlqConfig.ensure()
        cmd = RegisteredCommand(
            name="test",
            tpl="pytest {path} -v",
            defaults={"path": "tests/"},
        )
        write_hook_script(cmd, config.lq_dir)

        script_path = config.lq_dir / "hooks" / "test.sh"
        result = subprocess.run(
            [str(script_path), "--via=standalone", "--dry-run"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        # Default path should be resolved
        assert "pytest" in result.stdout
        assert "-v" in result.stdout

    def test_script_template_param_override(self, initialized_project):
        """Script allows parameter override via key=value."""
        import subprocess

        from blq.commands.core import BlqConfig, RegisteredCommand
        from blq.commands.hooks_gen import write_hook_script

        config = BlqConfig.ensure()
        cmd = RegisteredCommand(
            name="test",
            tpl="pytest {path} -v",
            defaults={"path": "tests/"},
        )
        write_hook_script(cmd, config.lq_dir)

        script_path = config.lq_dir / "hooks" / "test.sh"
        result = subprocess.run(
            [str(script_path), "--via=standalone", "--dry-run", "path=src/"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "src/" in result.stdout

    def test_script_metadata_footer_standalone(self, initialized_project):
        """Script outputs metadata footer in standalone mode with auto metadata."""
        import subprocess

        from blq.commands.core import BlqConfig, RegisteredCommand
        from blq.commands.hooks_gen import write_hook_script

        config = BlqConfig.ensure()
        # Use a command that exits quickly and successfully
        cmd = RegisteredCommand(name="true-cmd", cmd="true")
        write_hook_script(cmd, config.lq_dir)

        script_path = config.lq_dir / "hooks" / "true-cmd.sh"
        result = subprocess.run(
            [str(script_path), "--via=standalone", "--metadata=footer"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "# blq:meta" in result.stdout
        assert '"command":"true-cmd"' in result.stdout
        assert '"exit_code":0' in result.stdout

    def test_script_metadata_none_no_output(self, initialized_project):
        """Script with metadata=none produces no metadata."""
        import subprocess

        from blq.commands.core import BlqConfig, RegisteredCommand
        from blq.commands.hooks_gen import write_hook_script

        config = BlqConfig.ensure()
        cmd = RegisteredCommand(name="true-cmd", cmd="true")
        write_hook_script(cmd, config.lq_dir)

        script_path = config.lq_dir / "hooks" / "true-cmd.sh"
        result = subprocess.run(
            [str(script_path), "--via=standalone", "--metadata=none"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "blq:meta" not in result.stdout


class TestGitHookInstallation:
    """Tests for git hook installation with new v2 system."""

    def test_install_git_creates_hook_file(self, initialized_project):
        """Installing to git creates .git/hooks/pre-commit."""
        import subprocess

        from blq.commands.hooks_cmd import cmd_hooks_install
        from blq.commands.registry import cmd_register

        # Initialize git repo
        subprocess.run(["git", "init"], capture_output=True)

        # Register a command
        reg_args = argparse.Namespace(
            name="mytest",
            cmd=["echo", "test"],
            description="",
            timeout=300,
            capture=True,
            format="",
            force=False,
            run=False,
        )
        cmd_register(reg_args)

        # Install to git
        args = argparse.Namespace(
            target="git",
            commands=["mytest"],
            hook="pre-commit",
            force=False,
        )
        cmd_hooks_install(args)

        hook_path = initialized_project / ".git" / "hooks" / "pre-commit"
        assert hook_path.exists()
        assert hook_path.stat().st_mode & 0o111  # Executable

    def test_install_git_hook_calls_scripts(self, initialized_project):
        """Git hook calls .lq/hooks/*.sh scripts."""
        import subprocess

        from blq.commands.hooks_cmd import cmd_hooks_install
        from blq.commands.registry import cmd_register

        subprocess.run(["git", "init"], capture_output=True)

        reg_args = argparse.Namespace(
            name="mytest",
            cmd=["echo", "test"],
            description="",
            timeout=300,
            capture=True,
            format="",
            force=False,
            run=False,
        )
        cmd_register(reg_args)

        args = argparse.Namespace(
            target="git",
            commands=["mytest"],
            hook="pre-commit",
            force=False,
        )
        cmd_hooks_install(args)

        hook_path = initialized_project / ".git" / "hooks" / "pre-commit"
        content = hook_path.read_text()
        assert ".lq/hooks/mytest.sh" in content
        assert "blq-managed-hook" in content

    def test_install_git_generates_scripts(self, initialized_project):
        """Installing to git auto-generates missing scripts."""
        import subprocess

        from blq.commands.core import BlqConfig
        from blq.commands.hooks_cmd import cmd_hooks_install
        from blq.commands.registry import cmd_register

        subprocess.run(["git", "init"], capture_output=True)

        reg_args = argparse.Namespace(
            name="mytest",
            cmd=["echo", "test"],
            description="",
            timeout=300,
            capture=True,
            format="",
            force=False,
            run=False,
        )
        cmd_register(reg_args)

        config = BlqConfig.ensure()
        # Script doesn't exist yet
        assert not (config.lq_dir / "hooks" / "mytest.sh").exists()

        args = argparse.Namespace(
            target="git",
            commands=["mytest"],
            hook="pre-commit",
            force=False,
        )
        cmd_hooks_install(args)

        # Script should now exist
        assert (config.lq_dir / "hooks" / "mytest.sh").exists()

    def test_install_git_pre_push(self, initialized_project):
        """Can install to pre-push hook."""
        import subprocess

        from blq.commands.hooks_cmd import cmd_hooks_install
        from blq.commands.registry import cmd_register

        subprocess.run(["git", "init"], capture_output=True)

        reg_args = argparse.Namespace(
            name="mytest",
            cmd=["echo", "test"],
            description="",
            timeout=300,
            capture=True,
            format="",
            force=False,
            run=False,
        )
        cmd_register(reg_args)

        args = argparse.Namespace(
            target="git",
            commands=["mytest"],
            hook="pre-push",
            force=False,
        )
        cmd_hooks_install(args)

        hook_path = initialized_project / ".git" / "hooks" / "pre-push"
        assert hook_path.exists()
        content = hook_path.read_text()
        assert "pre-push" in content


class TestCmdHooksGenerate:
    """Tests for blq hooks generate command."""

    def test_generates_script_for_command(self, initialized_project, capsys):
        """Generates script for specified command."""
        from blq.commands.core import BlqConfig
        from blq.commands.hooks_cmd import cmd_hooks_generate
        from blq.commands.registry import cmd_register

        # Register a command first
        reg_args = argparse.Namespace(
            name="mytest",
            cmd=["pytest", "tests/"],
            description="",
            timeout=300,
            capture=True,
            format="",
            force=False,
            run=False,
        )
        cmd_register(reg_args)

        # Generate hook
        args = argparse.Namespace(commands=["mytest"], force=False)
        cmd_hooks_generate(args)

        config = BlqConfig.ensure()
        script_path = config.lq_dir / "hooks" / "mytest.sh"
        assert script_path.exists()

        captured = capsys.readouterr()
        assert "Generated" in captured.out
        assert "mytest.sh" in captured.out

    def test_errors_on_missing_command(self, initialized_project, capsys):
        """Errors when command not registered."""
        from blq.commands.hooks_cmd import cmd_hooks_generate

        args = argparse.Namespace(commands=["nonexistent"], force=False)

        with pytest.raises(SystemExit) as exc_info:
            cmd_hooks_generate(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "not registered" in captured.err

    def test_errors_with_no_commands(self, initialized_project, capsys):
        """Errors when no commands specified."""
        from blq.commands.hooks_cmd import cmd_hooks_generate

        args = argparse.Namespace(commands=[], force=False)

        with pytest.raises(SystemExit) as exc_info:
            cmd_hooks_generate(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "No commands specified" in captured.err

    def test_generates_multiple_scripts(self, initialized_project, capsys):
        """Generates scripts for multiple commands."""
        from blq.commands.core import BlqConfig
        from blq.commands.hooks_cmd import cmd_hooks_generate
        from blq.commands.registry import cmd_register

        # Register commands
        for name in ["lint", "test"]:
            reg_args = argparse.Namespace(
                name=name,
                cmd=[f"{name}_command"],
                description="",
                timeout=300,
                capture=True,
                format="",
                force=False,
                run=False,
            )
            cmd_register(reg_args)

        # Generate hooks
        args = argparse.Namespace(commands=["lint", "test"], force=False)
        cmd_hooks_generate(args)

        config = BlqConfig.ensure()
        assert (config.lq_dir / "hooks" / "lint.sh").exists()
        assert (config.lq_dir / "hooks" / "test.sh").exists()
