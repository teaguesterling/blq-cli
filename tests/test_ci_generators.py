"""Tests for CI workflow generators."""

from __future__ import annotations

import argparse

import pytest


class TestGitHubActionsGenerator:
    """Tests for GitHub Actions workflow generation."""

    def test_generates_workflow_file(self, initialized_project):
        """Generate .github/workflows/blq.yml."""
        from blq.commands.hooks_cmd import cmd_hooks_install
        from blq.commands.registry import cmd_register

        # Register a command
        reg_args = argparse.Namespace(
            name="test",
            cmd=["pytest", "tests/"],
            description="Run tests",
            timeout=300,
            capture=True,
            format="",
            force=False,
            run=False,
            template=False,
            default=[],
        )
        cmd_register(reg_args)

        # Install to github
        args = argparse.Namespace(
            target="github",
            commands=["test"],
            force=False,
        )
        cmd_hooks_install(args)

        workflow_path = initialized_project / ".github" / "workflows" / "blq.yml"
        assert workflow_path.exists()

    def test_workflow_contains_job_for_each_command(self, initialized_project):
        """Each command becomes a job in the workflow."""
        from blq.commands.hooks_cmd import cmd_hooks_install
        from blq.commands.registry import cmd_register

        # Register multiple commands
        for name in ["lint", "test"]:
            reg_args = argparse.Namespace(
                name=name,
                cmd=["echo", name],
                description=f"Run {name}",
                timeout=300,
                capture=True,
                format="",
                force=False,
                run=False,
                template=False,
                default=[],
            )
            cmd_register(reg_args)

        args = argparse.Namespace(
            target="github",
            commands=["lint", "test"],
            force=False,
        )
        cmd_hooks_install(args)

        workflow_path = initialized_project / ".github" / "workflows" / "blq.yml"
        content = workflow_path.read_text()

        assert "lint:" in content
        assert "test:" in content

    def test_workflow_calls_hook_scripts(self, initialized_project):
        """Workflow jobs call .lq/hooks/*.sh scripts."""
        from blq.commands.hooks_cmd import cmd_hooks_install
        from blq.commands.registry import cmd_register

        reg_args = argparse.Namespace(
            name="lint",
            cmd=["ruff", "check", "."],
            description="Lint code",
            timeout=300,
            capture=True,
            format="",
            force=False,
            run=False,
            template=False,
            default=[],
        )
        cmd_register(reg_args)

        args = argparse.Namespace(
            target="github",
            commands=["lint"],
            force=False,
        )
        cmd_hooks_install(args)

        workflow_path = initialized_project / ".github" / "workflows" / "blq.yml"
        content = workflow_path.read_text()

        # Should call the hook script with standalone mode
        assert ".lq/hooks/lint.sh" in content
        assert "--via=standalone" in content

    def test_workflow_has_checkout_step(self, initialized_project):
        """Workflow includes checkout action."""
        from blq.commands.hooks_cmd import cmd_hooks_install
        from blq.commands.registry import cmd_register

        reg_args = argparse.Namespace(
            name="test",
            cmd=["echo", "test"],
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

        args = argparse.Namespace(
            target="github",
            commands=["test"],
            force=False,
        )
        cmd_hooks_install(args)

        workflow_path = initialized_project / ".github" / "workflows" / "blq.yml"
        content = workflow_path.read_text()

        assert "actions/checkout" in content

    def test_workflow_triggers_on_push_and_pr(self, initialized_project):
        """Workflow triggers on push and pull_request."""
        from blq.commands.hooks_cmd import cmd_hooks_install
        from blq.commands.registry import cmd_register

        reg_args = argparse.Namespace(
            name="test",
            cmd=["echo", "test"],
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

        args = argparse.Namespace(
            target="github",
            commands=["test"],
            force=False,
        )
        cmd_hooks_install(args)

        workflow_path = initialized_project / ".github" / "workflows" / "blq.yml"
        content = workflow_path.read_text()

        assert "push" in content
        assert "pull_request" in content

    def test_refuses_overwrite_without_force(self, initialized_project):
        """Don't overwrite existing workflow without --force."""
        from blq.commands.hooks_cmd import cmd_hooks_install
        from blq.commands.registry import cmd_register

        reg_args = argparse.Namespace(
            name="test",
            cmd=["echo", "test"],
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

        # Create existing workflow
        workflow_dir = initialized_project / ".github" / "workflows"
        workflow_dir.mkdir(parents=True)
        workflow_path = workflow_dir / "blq.yml"
        workflow_path.write_text("existing content")

        args = argparse.Namespace(
            target="github",
            commands=["test"],
            force=False,
        )

        with pytest.raises(SystemExit):
            cmd_hooks_install(args)

    def test_force_overwrites_existing(self, initialized_project):
        """--force overwrites existing workflow."""
        from blq.commands.hooks_cmd import cmd_hooks_install
        from blq.commands.registry import cmd_register

        reg_args = argparse.Namespace(
            name="test",
            cmd=["echo", "test"],
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

        # Create existing workflow
        workflow_dir = initialized_project / ".github" / "workflows"
        workflow_dir.mkdir(parents=True)
        workflow_path = workflow_dir / "blq.yml"
        workflow_path.write_text("existing content")

        args = argparse.Namespace(
            target="github",
            commands=["test"],
            force=True,
        )
        cmd_hooks_install(args)

        content = workflow_path.read_text()
        assert "existing content" not in content
        assert ".lq/hooks/test.sh" in content


class TestGitLabCIGenerator:
    """Tests for GitLab CI configuration generation."""

    def test_generates_gitlab_ci_file(self, initialized_project):
        """Generate .gitlab-ci.blq.yml."""
        from blq.commands.hooks_cmd import cmd_hooks_install
        from blq.commands.registry import cmd_register

        reg_args = argparse.Namespace(
            name="test",
            cmd=["pytest", "tests/"],
            description="Run tests",
            timeout=300,
            capture=True,
            format="",
            force=False,
            run=False,
            template=False,
            default=[],
        )
        cmd_register(reg_args)

        args = argparse.Namespace(
            target="gitlab",
            commands=["test"],
            force=False,
        )
        cmd_hooks_install(args)

        gitlab_path = initialized_project / ".gitlab-ci.blq.yml"
        assert gitlab_path.exists()

    def test_gitlab_ci_contains_job_for_each_command(self, initialized_project):
        """Each command becomes a job in GitLab CI."""
        from blq.commands.hooks_cmd import cmd_hooks_install
        from blq.commands.registry import cmd_register

        for name in ["lint", "test"]:
            reg_args = argparse.Namespace(
                name=name,
                cmd=["echo", name],
                description=f"Run {name}",
                timeout=300,
                capture=True,
                format="",
                force=False,
                run=False,
                template=False,
                default=[],
            )
            cmd_register(reg_args)

        args = argparse.Namespace(
            target="gitlab",
            commands=["lint", "test"],
            force=False,
        )
        cmd_hooks_install(args)

        gitlab_path = initialized_project / ".gitlab-ci.blq.yml"
        content = gitlab_path.read_text()

        # GitLab CI uses job names as top-level keys
        assert "blq-lint:" in content
        assert "blq-test:" in content

    def test_gitlab_ci_calls_hook_scripts(self, initialized_project):
        """GitLab CI jobs call .lq/hooks/*.sh scripts."""
        from blq.commands.hooks_cmd import cmd_hooks_install
        from blq.commands.registry import cmd_register

        reg_args = argparse.Namespace(
            name="lint",
            cmd=["ruff", "check", "."],
            description="Lint code",
            timeout=300,
            capture=True,
            format="",
            force=False,
            run=False,
            template=False,
            default=[],
        )
        cmd_register(reg_args)

        args = argparse.Namespace(
            target="gitlab",
            commands=["lint"],
            force=False,
        )
        cmd_hooks_install(args)

        gitlab_path = initialized_project / ".gitlab-ci.blq.yml"
        content = gitlab_path.read_text()

        assert ".lq/hooks/lint.sh" in content
        assert "--via=standalone" in content

    def test_gitlab_ci_has_include_comment(self, initialized_project):
        """GitLab CI file has comment about how to include it."""
        from blq.commands.hooks_cmd import cmd_hooks_install
        from blq.commands.registry import cmd_register

        reg_args = argparse.Namespace(
            name="test",
            cmd=["echo", "test"],
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

        args = argparse.Namespace(
            target="gitlab",
            commands=["test"],
            force=False,
        )
        cmd_hooks_install(args)

        gitlab_path = initialized_project / ".gitlab-ci.blq.yml"
        content = gitlab_path.read_text()

        assert "include:" in content.lower() or "Include" in content


class TestDroneCIGenerator:
    """Tests for Drone CI configuration generation."""

    def test_generates_drone_ci_file(self, initialized_project):
        """Generate .drone.blq.yml."""
        from blq.commands.hooks_cmd import cmd_hooks_install
        from blq.commands.registry import cmd_register

        reg_args = argparse.Namespace(
            name="test",
            cmd=["pytest", "tests/"],
            description="Run tests",
            timeout=300,
            capture=True,
            format="",
            force=False,
            run=False,
            template=False,
            default=[],
        )
        cmd_register(reg_args)

        args = argparse.Namespace(
            target="drone",
            commands=["test"],
            force=False,
        )
        cmd_hooks_install(args)

        drone_path = initialized_project / ".drone.blq.yml"
        assert drone_path.exists()

    def test_drone_ci_contains_step_for_each_command(self, initialized_project):
        """Each command becomes a step in Drone CI."""
        from blq.commands.hooks_cmd import cmd_hooks_install
        from blq.commands.registry import cmd_register

        for name in ["lint", "test"]:
            reg_args = argparse.Namespace(
                name=name,
                cmd=["echo", name],
                description=f"Run {name}",
                timeout=300,
                capture=True,
                format="",
                force=False,
                run=False,
                template=False,
                default=[],
            )
            cmd_register(reg_args)

        args = argparse.Namespace(
            target="drone",
            commands=["lint", "test"],
            force=False,
        )
        cmd_hooks_install(args)

        drone_path = initialized_project / ".drone.blq.yml"
        content = drone_path.read_text()

        # Drone uses steps with name field
        assert "name: lint" in content
        assert "name: test" in content

    def test_drone_ci_calls_hook_scripts(self, initialized_project):
        """Drone CI steps call .lq/hooks/*.sh scripts."""
        from blq.commands.hooks_cmd import cmd_hooks_install
        from blq.commands.registry import cmd_register

        reg_args = argparse.Namespace(
            name="lint",
            cmd=["ruff", "check", "."],
            description="Lint code",
            timeout=300,
            capture=True,
            format="",
            force=False,
            run=False,
            template=False,
            default=[],
        )
        cmd_register(reg_args)

        args = argparse.Namespace(
            target="drone",
            commands=["lint"],
            force=False,
        )
        cmd_hooks_install(args)

        drone_path = initialized_project / ".drone.blq.yml"
        content = drone_path.read_text()

        assert ".lq/hooks/lint.sh" in content
        assert "--via=standalone" in content

    def test_drone_ci_has_pipeline_type(self, initialized_project):
        """Drone CI file specifies pipeline type."""
        from blq.commands.hooks_cmd import cmd_hooks_install
        from blq.commands.registry import cmd_register

        reg_args = argparse.Namespace(
            name="test",
            cmd=["echo", "test"],
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

        args = argparse.Namespace(
            target="drone",
            commands=["test"],
            force=False,
        )
        cmd_hooks_install(args)

        drone_path = initialized_project / ".drone.blq.yml"
        content = drone_path.read_text()

        assert "kind: pipeline" in content
        assert "type: docker" in content


class TestCIGeneratorCommon:
    """Common tests for all CI generators."""

    @pytest.mark.parametrize("target", ["github", "gitlab", "drone"])
    def test_generates_hook_scripts_if_missing(self, initialized_project, target):
        """CI install auto-generates missing hook scripts."""
        from blq.commands.hooks_cmd import cmd_hooks_install
        from blq.commands.registry import cmd_register

        reg_args = argparse.Namespace(
            name="mytest",
            cmd=["echo", "test"],
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

        # Script doesn't exist yet
        script_path = initialized_project / ".lq" / "hooks" / "mytest.sh"
        assert not script_path.exists()

        args = argparse.Namespace(
            target=target,
            commands=["mytest"],
            force=False,
        )
        cmd_hooks_install(args)

        # Script should now exist
        assert script_path.exists()

    @pytest.mark.parametrize("target", ["github", "gitlab", "drone"])
    def test_errors_on_missing_command(self, initialized_project, target):
        """Error if command doesn't exist."""
        from blq.commands.hooks_cmd import cmd_hooks_install

        args = argparse.Namespace(
            target=target,
            commands=["nonexistent"],
            force=False,
        )

        with pytest.raises(SystemExit):
            cmd_hooks_install(args)


class TestCIUninstall:
    """Tests for CI configuration uninstall."""

    def test_uninstall_github(self, initialized_project):
        """Uninstall GitHub Actions workflow."""
        from blq.commands.hooks_cmd import cmd_hooks_install, cmd_hooks_uninstall
        from blq.commands.registry import cmd_register

        reg_args = argparse.Namespace(
            name="test",
            cmd=["echo", "test"],
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

        # Install
        install_args = argparse.Namespace(
            target="github",
            commands=["test"],
            force=False,
        )
        cmd_hooks_install(install_args)

        workflow_path = initialized_project / ".github" / "workflows" / "blq.yml"
        assert workflow_path.exists()

        # Uninstall
        uninstall_args = argparse.Namespace(target="github")
        cmd_hooks_uninstall(uninstall_args)

        assert not workflow_path.exists()

    def test_uninstall_gitlab(self, initialized_project):
        """Uninstall GitLab CI configuration."""
        from blq.commands.hooks_cmd import cmd_hooks_install, cmd_hooks_uninstall
        from blq.commands.registry import cmd_register

        reg_args = argparse.Namespace(
            name="test",
            cmd=["echo", "test"],
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

        # Install
        install_args = argparse.Namespace(
            target="gitlab",
            commands=["test"],
            force=False,
        )
        cmd_hooks_install(install_args)

        gitlab_path = initialized_project / ".gitlab-ci.blq.yml"
        assert gitlab_path.exists()

        # Uninstall
        uninstall_args = argparse.Namespace(target="gitlab")
        cmd_hooks_uninstall(uninstall_args)

        assert not gitlab_path.exists()

    def test_uninstall_drone(self, initialized_project):
        """Uninstall Drone CI configuration."""
        from blq.commands.hooks_cmd import cmd_hooks_install, cmd_hooks_uninstall
        from blq.commands.registry import cmd_register

        reg_args = argparse.Namespace(
            name="test",
            cmd=["echo", "test"],
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

        # Install
        install_args = argparse.Namespace(
            target="drone",
            commands=["test"],
            force=False,
        )
        cmd_hooks_install(install_args)

        drone_path = initialized_project / ".drone.blq.yml"
        assert drone_path.exists()

        # Uninstall
        uninstall_args = argparse.Namespace(target="drone")
        cmd_hooks_uninstall(uninstall_args)

        assert not drone_path.exists()

    def test_uninstall_nonexistent_is_noop(self, initialized_project, capsys):
        """Uninstall when not installed is a no-op."""
        from blq.commands.hooks_cmd import cmd_hooks_uninstall

        uninstall_args = argparse.Namespace(target="github")
        cmd_hooks_uninstall(uninstall_args)

        # Should not raise, just inform
        captured = capsys.readouterr()
        assert "not installed" in captured.out.lower() or "not found" in captured.out.lower()
