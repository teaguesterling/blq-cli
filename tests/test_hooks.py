"""Tests for git hooks integration."""

import argparse
import os
import subprocess

import pytest


class TestHookTemplate:
    """Tests for the hook script template."""

    def test_template_uses_subcommand_format(self):
        """Hook template uses 'blq hooks run' not 'blq hooks-run'."""
        from blq.commands.hooks_cmd import PRECOMMIT_HOOK_TEMPLATE

        assert "blq hooks run" in PRECOMMIT_HOOK_TEMPLATE
        assert "blq hooks-run" not in PRECOMMIT_HOOK_TEMPLATE

    def test_template_contains_marker(self):
        """Hook template contains the marker for identification."""
        from blq.commands.hooks_cmd import HOOK_MARKER, PRECOMMIT_HOOK_TEMPLATE

        assert HOOK_MARKER in PRECOMMIT_HOOK_TEMPLATE


@pytest.fixture
def git_repo(temp_dir):
    """Create a git repository in temp_dir."""
    subprocess.run(["git", "init"], cwd=temp_dir, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=temp_dir,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=temp_dir,
        capture_output=True,
    )
    return temp_dir


@pytest.fixture
def initialized_git_project(git_repo):
    """A git repo with blq initialized (using legacy parquet mode for compat)."""
    original = os.getcwd()
    os.chdir(git_repo)

    from blq.cli import cmd_init

    args = argparse.Namespace()
    args.mcp = False
    args.detect = False
    args.detect_mode = "none"
    args.yes = False
    args.force = False
    args.parquet = True  # Explicitly use parquet for backward compatibility
    args.namespace = None
    args.project = None
    cmd_init(args)

    yield git_repo
    os.chdir(original)


class TestHooksInstall:
    """Tests for hooks-install command."""

    def test_install_creates_hook(self, initialized_git_project):
        """Installing hooks creates pre-commit script."""
        from blq.commands.hooks_cmd import cmd_hooks_install

        args = argparse.Namespace(force=False)
        cmd_hooks_install(args)

        hook_path = initialized_git_project / ".git" / "hooks" / "pre-commit"
        assert hook_path.exists()
        assert hook_path.stat().st_mode & 0o111  # Is executable

    def test_install_contains_marker(self, initialized_git_project):
        """Installed hook contains blq marker."""
        from blq.commands.hooks_cmd import HOOK_MARKER, cmd_hooks_install

        args = argparse.Namespace(force=False)
        cmd_hooks_install(args)

        hook_path = initialized_git_project / ".git" / "hooks" / "pre-commit"
        content = hook_path.read_text()
        assert HOOK_MARKER in content

    def test_install_uses_correct_command_format(self, initialized_git_project):
        """Installed hook uses 'blq hooks run' (not 'blq hooks-run')."""
        from blq.commands.hooks_cmd import cmd_hooks_install

        args = argparse.Namespace(force=False)
        cmd_hooks_install(args)

        hook_path = initialized_git_project / ".git" / "hooks" / "pre-commit"
        content = hook_path.read_text()
        # Must use subcommand format, not hyphenated
        assert "blq hooks run" in content
        assert "blq hooks-run" not in content

    def test_install_idempotent(self, initialized_git_project, capsys):
        """Installing twice without force shows message."""
        from blq.commands.hooks_cmd import cmd_hooks_install

        args = argparse.Namespace(force=False)
        cmd_hooks_install(args)
        cmd_hooks_install(args)

        captured = capsys.readouterr()
        assert "already installed" in captured.out

    def test_install_force_overwrites(self, initialized_git_project):
        """Installing with force overwrites existing hook."""
        from blq.commands.hooks_cmd import cmd_hooks_install

        hook_path = initialized_git_project / ".git" / "hooks" / "pre-commit"

        # First install
        args = argparse.Namespace(force=False)
        cmd_hooks_install(args)
        original_content = hook_path.read_text()

        # Force reinstall
        args = argparse.Namespace(force=True)
        cmd_hooks_install(args)

        assert hook_path.read_text() == original_content  # Same content

    def test_install_refuses_foreign_hook(self, initialized_git_project, capsys):
        """Installing refuses to overwrite non-blq hook."""
        from blq.commands.hooks_cmd import cmd_hooks_install

        # Create a foreign hook
        hook_path = initialized_git_project / ".git" / "hooks" / "pre-commit"
        hook_path.parent.mkdir(parents=True, exist_ok=True)
        hook_path.write_text("#!/bin/sh\necho 'foreign hook'\n")

        args = argparse.Namespace(force=False)
        with pytest.raises(SystemExit):
            cmd_hooks_install(args)

        captured = capsys.readouterr()
        assert "not created by blq" in captured.err

    def test_install_force_overwrites_foreign(self, initialized_git_project, capsys):
        """Installing with force overwrites foreign hook."""
        from blq.commands.hooks_cmd import HOOK_MARKER, cmd_hooks_install

        # Create a foreign hook
        hook_path = initialized_git_project / ".git" / "hooks" / "pre-commit"
        hook_path.parent.mkdir(parents=True, exist_ok=True)
        hook_path.write_text("#!/bin/sh\necho 'foreign hook'\n")

        args = argparse.Namespace(force=True)
        cmd_hooks_install(args)

        content = hook_path.read_text()
        assert HOOK_MARKER in content
        assert "foreign hook" not in content


class TestHooksRemove:
    """Tests for hooks-remove command."""

    def test_remove_deletes_hook(self, initialized_git_project):
        """Removing deletes the hook file."""
        from blq.commands.hooks_cmd import cmd_hooks_install, cmd_hooks_remove

        # Install first
        args = argparse.Namespace(force=False)
        cmd_hooks_install(args)

        hook_path = initialized_git_project / ".git" / "hooks" / "pre-commit"
        assert hook_path.exists()

        # Remove
        cmd_hooks_remove(argparse.Namespace())
        assert not hook_path.exists()

    def test_remove_no_hook(self, initialized_git_project, capsys):
        """Removing when no hook installed shows message."""
        from blq.commands.hooks_cmd import cmd_hooks_remove

        cmd_hooks_remove(argparse.Namespace())

        captured = capsys.readouterr()
        assert "No pre-commit hook" in captured.out

    def test_remove_refuses_foreign_hook(self, initialized_git_project, capsys):
        """Removing refuses to delete non-blq hook."""
        from blq.commands.hooks_cmd import cmd_hooks_remove

        # Create a foreign hook
        hook_path = initialized_git_project / ".git" / "hooks" / "pre-commit"
        hook_path.parent.mkdir(parents=True, exist_ok=True)
        hook_path.write_text("#!/bin/sh\necho 'foreign hook'\n")

        with pytest.raises(SystemExit):
            cmd_hooks_remove(argparse.Namespace())

        # Hook should still exist
        assert hook_path.exists()


class TestHooksStatus:
    """Tests for hooks-status command."""

    def test_status_not_installed(self, initialized_git_project, capsys):
        """Status shows not installed when no hook."""
        from blq.commands.hooks_cmd import cmd_hooks_status

        cmd_hooks_status(argparse.Namespace())

        captured = capsys.readouterr()
        assert "not installed" in captured.out

    def test_status_installed(self, initialized_git_project, capsys):
        """Status shows installed when hook exists."""
        from blq.commands.hooks_cmd import cmd_hooks_install, cmd_hooks_status

        args = argparse.Namespace(force=False)
        cmd_hooks_install(args)
        capsys.readouterr()  # Clear install output

        cmd_hooks_status(argparse.Namespace())

        captured = capsys.readouterr()
        assert "[installed]" in captured.out
        assert "pre-commit" in captured.out

    def test_status_shows_ci_workflows(self, initialized_git_project, capsys):
        """Status shows CI workflow installation status."""
        from blq.commands.hooks_cmd import cmd_hooks_status

        cmd_hooks_status(argparse.Namespace())

        captured = capsys.readouterr()
        # Should show CI workflow section
        assert "CI Workflows:" in captured.out
        assert "github" in captured.out
        assert "gitlab" in captured.out
        assert "drone" in captured.out

    def test_status_shows_stale_scripts_section(self, initialized_git_project, capsys):
        """Status shows stale scripts summary when scripts are stale."""
        from blq.commands.core import BlqConfig
        from blq.commands.hooks_cmd import cmd_hooks_status
        from blq.commands.hooks_gen import write_hook_script
        from blq.commands.registry import cmd_register

        # Register and generate a script
        reg_args = argparse.Namespace(
            name="mytest",
            cmd=["echo", "original"],
            description="Test",
            timeout=300,
            capture=True,
            format="",
            force=False,
            run=False,
            template=False,
            default=[],
        )
        cmd_register(reg_args)

        config = BlqConfig.ensure()
        cmd = config.commands["mytest"]
        write_hook_script(cmd, config.lq_dir, force=True)

        # Modify the command to make the script stale
        reg_args.cmd = ["echo", "modified"]
        reg_args.force = True
        cmd_register(reg_args)

        capsys.readouterr()  # Clear output

        cmd_hooks_status(argparse.Namespace())

        captured = capsys.readouterr()
        # Should show stale indicator
        assert "[stale]" in captured.out


class TestHooksAdd:
    """Tests for hooks-add command."""

    def test_add_command(self, initialized_git_project, capsys):
        """Adding a command updates config."""
        from blq.commands.core import BlqConfig
        from blq.commands.hooks_cmd import cmd_hooks_add

        args = argparse.Namespace(command="lint")
        cmd_hooks_add(args)

        # Reload config
        config = BlqConfig.find()
        assert "lint" in config.hooks_config.get("pre-commit", [])

    def test_add_duplicate(self, initialized_git_project, capsys):
        """Adding duplicate command shows message."""
        from blq.commands.hooks_cmd import cmd_hooks_add

        args = argparse.Namespace(command="lint")
        cmd_hooks_add(args)
        cmd_hooks_add(args)

        captured = capsys.readouterr()
        assert "already in" in captured.out


class TestHooksList:
    """Tests for hooks-list command."""

    def test_list_empty(self, initialized_git_project, capsys):
        """Listing with no commands produces empty output."""
        from blq.commands.hooks_cmd import cmd_hooks_list

        cmd_hooks_list(argparse.Namespace())

        captured = capsys.readouterr()
        assert captured.out.strip() == ""

    def test_list_commands(self, initialized_git_project, capsys):
        """Listing shows configured commands."""
        from blq.commands.hooks_cmd import cmd_hooks_add, cmd_hooks_list

        # Add some commands
        cmd_hooks_add(argparse.Namespace(command="lint"))
        cmd_hooks_add(argparse.Namespace(command="test"))

        cmd_hooks_list(argparse.Namespace())

        captured = capsys.readouterr()
        assert "lint" in captured.out
        assert "test" in captured.out


class TestHooksRun:
    """Tests for hooks-run command."""

    def test_run_no_commands(self, initialized_git_project, capsys):
        """Running with no commands does nothing."""
        from blq.commands.hooks_cmd import cmd_hooks_run

        cmd_hooks_run(argparse.Namespace())

        captured = capsys.readouterr()
        # Should be silent when no commands
        assert captured.out.strip() == ""


class TestNotInGitRepo:
    """Tests for behavior outside git repo."""

    def test_install_fails_not_initialized(self, temp_dir, capsys):
        """Install fails when blq not initialized."""
        original = os.getcwd()
        os.chdir(temp_dir)

        try:
            from blq.commands.hooks_cmd import cmd_hooks_install

            with pytest.raises(SystemExit):
                cmd_hooks_install(argparse.Namespace(force=False))

            captured = capsys.readouterr()
            # blq needs to be initialized first
            assert "not initialized" in captured.err
        finally:
            os.chdir(original)

    def test_install_fails_not_git(self, initialized_project, capsys):
        """Install fails when in blq project but not git repo."""
        from blq.commands.hooks_cmd import cmd_hooks_install

        with pytest.raises(SystemExit):
            cmd_hooks_install(argparse.Namespace(force=False))

        captured = capsys.readouterr()
        assert "Not in a git repository" in captured.err

    def test_status_not_git(self, temp_dir, capsys):
        """Status shows not initialized when not in blq project."""
        original = os.getcwd()
        os.chdir(temp_dir)

        try:
            from blq.commands.hooks_cmd import cmd_hooks_status

            cmd_hooks_status(argparse.Namespace())

            captured = capsys.readouterr()
            # New behavior: checks for blq initialization first
            assert "not initialized" in captured.out
        finally:
            os.chdir(original)
