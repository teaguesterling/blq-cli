"""Tests for command argument parameterization."""

import pytest

from blq.commands.core import (
    RegisteredCommand,
    expand_command,
    format_command_help,
    parse_placeholders,
)
from blq.commands.execution import _parse_command_args


class TestParsePlaceholders:
    """Tests for parse_placeholders function."""

    def test_no_placeholders(self):
        """Commands without placeholders return empty list."""
        result = parse_placeholders("echo hello")
        assert result == []

    def test_keyword_only_required(self):
        """Test {name} - keyword-only, required."""
        result = parse_placeholders("kubectl apply -f {file}")
        assert len(result) == 1
        assert result[0].name == "file"
        assert result[0].default is None
        assert result[0].positional is False

    def test_keyword_only_optional(self):
        """Test {name=default} - keyword-only, optional."""
        result = parse_placeholders("make -j{jobs=4}")
        assert len(result) == 1
        assert result[0].name == "jobs"
        assert result[0].default == "4"
        assert result[0].positional is False

    def test_positional_required(self):
        """Test {name:} - positional-able, required."""
        result = parse_placeholders("deploy {file:}")
        assert len(result) == 1
        assert result[0].name == "file"
        assert result[0].default is None
        assert result[0].positional is True

    def test_positional_optional(self):
        """Test {name:=default} - positional-able, optional."""
        result = parse_placeholders("pytest {path:=tests/}")
        assert len(result) == 1
        assert result[0].name == "path"
        assert result[0].default == "tests/"
        assert result[0].positional is True

    def test_empty_default(self):
        """Test empty default value."""
        result = parse_placeholders("cmd {arg=}")
        assert len(result) == 1
        assert result[0].default == ""

    def test_multiple_placeholders(self):
        """Test multiple placeholders in order."""
        result = parse_placeholders("kubectl apply -f {file:} -n {namespace:=default}")
        assert len(result) == 2
        assert result[0].name == "file"
        assert result[0].positional is True
        assert result[0].default is None
        assert result[1].name == "namespace"
        assert result[1].positional is True
        assert result[1].default == "default"

    def test_mixed_placeholder_types(self):
        """Test mix of keyword-only and positional placeholders."""
        result = parse_placeholders("make -j{jobs=4} {target:=all}")
        assert len(result) == 2
        assert result[0].name == "jobs"
        assert result[0].positional is False
        assert result[1].name == "target"
        assert result[1].positional is True


class TestExpandCommand:
    """Tests for expand_command function."""

    def test_no_placeholders(self):
        """Command without placeholders passes through."""
        result = expand_command("echo hello", {}, [])
        assert result == "echo hello"

    def test_keyword_only_with_named_arg(self):
        """Keyword-only placeholder filled by named arg."""
        result = expand_command("make -j{jobs=4}", {"jobs": "8"}, [])
        assert result == "make -j8"

    def test_keyword_only_with_default(self):
        """Keyword-only placeholder uses default when not provided."""
        result = expand_command("make -j{jobs=4}", {}, [])
        assert result == "make -j4"

    def test_positional_with_positional_arg(self):
        """Positional placeholder filled by positional arg."""
        result = expand_command("pytest {path:=tests/}", {}, ["unit/"])
        assert result == "pytest unit/"

    def test_positional_with_named_arg(self):
        """Positional placeholder can also be filled by named arg."""
        result = expand_command("pytest {path:=tests/}", {"path": "unit/"}, [])
        assert result == "pytest unit/"

    def test_positional_with_default(self):
        """Positional placeholder uses default when not provided."""
        result = expand_command("pytest {path:=tests/}", {}, [])
        assert result == "pytest tests/"

    def test_multiple_positional_args(self):
        """Multiple positional args fill placeholders in order."""
        result = expand_command(
            "kubectl apply -f {file:} -n {namespace:=default}",
            {},
            ["manifest.yaml", "prod"],
        )
        assert result == "kubectl apply -f manifest.yaml -n prod"

    def test_mixed_named_and_positional(self):
        """Named args take precedence, positional fills remaining."""
        result = expand_command(
            "kubectl apply -f {file:} -n {namespace:=default}",
            {"namespace": "staging"},
            ["manifest.yaml"],
        )
        assert result == "kubectl apply -f manifest.yaml -n staging"

    def test_extra_args_appended(self):
        """Extra args are appended to command."""
        result = expand_command("pytest {path:=tests/}", {}, ["unit/"], ["--verbose", "-x"])
        assert result == "pytest unit/ --verbose -x"

    def test_extra_positional_args_become_passthrough(self):
        """Positional args beyond placeholders become passthrough."""
        result = expand_command("pytest {path:=tests/}", {}, ["unit/", "--verbose", "-x"])
        assert result == "pytest unit/ --verbose -x"

    def test_required_missing_raises(self):
        """Missing required arg raises ValueError."""
        with pytest.raises(ValueError, match="Missing required argument 'file'"):
            expand_command("kubectl apply -f {file}", {}, [])

    def test_unknown_named_arg_raises(self):
        """Unknown named arg raises ValueError."""
        with pytest.raises(ValueError, match="Unknown argument 'unknown'"):
            expand_command("make -j{jobs=4}", {"unknown": "value"}, [])

    def test_keyword_only_not_filled_positionally(self):
        """Keyword-only placeholders are not filled by positional args."""
        result = expand_command("make -j{jobs=4} {target=all}", {}, ["clean"])
        # "clean" should be passthrough, not fill {jobs}
        assert result == "make -j4 all clean"


class TestParseCommandArgs:
    """Tests for _parse_command_args helper function."""

    def test_empty_args(self):
        """Empty args returns empty collections."""
        named, positional, extra = _parse_command_args([])
        assert named == {}
        assert positional == []
        assert extra == []

    def test_named_args(self):
        """Named args (key=value) are parsed correctly."""
        named, positional, extra = _parse_command_args(["jobs=8", "target=clean"])
        assert named == {"jobs": "8", "target": "clean"}
        assert positional == []
        assert extra == []

    def test_positional_args(self):
        """Positional args (no =) are collected."""
        named, positional, extra = _parse_command_args(["unit/", "integration/"])
        assert named == {}
        assert positional == ["unit/", "integration/"]
        assert extra == []

    def test_mixed_args(self):
        """Mixed named and positional args are separated."""
        named, positional, extra = _parse_command_args(["unit/", "jobs=8", "integration/"])
        assert named == {"jobs": "8"}
        assert positional == ["unit/", "integration/"]
        assert extra == []

    def test_separator_splits_extra(self):
        """:: separator splits extra args."""
        named, positional, extra = _parse_command_args(["unit/", "::", "--verbose", "-x"])
        assert named == {}
        assert positional == ["unit/"]
        assert extra == ["--verbose", "-x"]

    def test_separator_with_named_args(self):
        """:: works with named args too."""
        named, positional, extra = _parse_command_args(["jobs=8", "::", "--dry-run"])
        assert named == {"jobs": "8"}
        assert positional == []
        assert extra == ["--dry-run"]

    def test_positional_limit(self):
        """Positional limit restricts placeholder args."""
        named, positional, extra = _parse_command_args(
            ["unit/", "integration/", "--verbose"],
            positional_limit=1,
        )
        assert named == {}
        assert positional == ["unit/"]
        assert extra == ["integration/", "--verbose"]

    def test_positional_limit_zero(self):
        """Positional limit of 0 sends all to extra."""
        named, positional, extra = _parse_command_args(
            ["unit/", "--verbose"],
            positional_limit=0,
        )
        assert named == {}
        assert positional == []
        assert extra == ["unit/", "--verbose"]

    def test_flag_like_args_are_positional(self):
        """Args starting with - are treated as positional."""
        named, positional, extra = _parse_command_args(["--verbose", "-x"])
        assert named == {}
        assert positional == ["--verbose", "-x"]
        assert extra == []

    def test_value_with_equals(self):
        """Values containing = are handled correctly."""
        named, positional, extra = _parse_command_args(["filter=name=foo"])
        assert named == {"filter": "name=foo"}


class TestFormatCommandHelp:
    """Tests for format_command_help function."""

    def test_simple_command(self):
        """Simple command without placeholders."""
        cmd = RegisteredCommand(name="build", cmd="make", description="Build the project")
        result = format_command_help(cmd)
        assert "build: make" in result
        assert "Build the project" in result

    def test_command_with_placeholders(self):
        """Command with placeholders shows argument info."""
        cmd = RegisteredCommand(
            name="deploy",
            cmd="kubectl apply -f {file:} -n {namespace:=default}",
            description="Deploy to Kubernetes",
        )
        result = format_command_help(cmd)
        assert "deploy:" in result
        assert "file" in result
        assert "required" in result
        assert "namespace" in result
        assert "default: default" in result


class TestTemplateCommand:
    """Tests for parameterized commands with tpl + defaults."""

    def test_is_template_with_cmd(self):
        """Commands with cmd only are not templates."""
        cmd = RegisteredCommand(name="build", cmd="make -j8")
        assert not cmd.is_template

    def test_is_template_with_tpl(self):
        """Commands with tpl are templates."""
        cmd = RegisteredCommand(name="test", tpl="pytest {path} {flags}")
        assert cmd.is_template

    def test_is_template_with_both(self):
        """Commands with both cmd and tpl prefer tpl."""
        cmd = RegisteredCommand(name="test", cmd="pytest tests/", tpl="pytest {path}")
        assert cmd.is_template

    def test_render_simple_template(self):
        """Render template with all args provided."""
        cmd = RegisteredCommand(
            name="test",
            tpl="pytest {path} {flags}",
            defaults={"path": "tests/", "flags": "-v"},
        )
        result = cmd.render({})
        assert result == "pytest tests/ -v"

    def test_render_override_defaults(self):
        """Override default values."""
        cmd = RegisteredCommand(
            name="test",
            tpl="pytest {path} {flags}",
            defaults={"path": "tests/", "flags": "-v"},
        )
        result = cmd.render({"path": "tests/unit/"})
        assert result == "pytest tests/unit/ -v"

    def test_render_all_args_override(self):
        """Override all defaults."""
        cmd = RegisteredCommand(
            name="test",
            tpl="pytest {path} {flags}",
            defaults={"path": "tests/", "flags": "-v"},
        )
        result = cmd.render({"path": "tests/unit/", "flags": "-vvs -x"})
        assert result == "pytest tests/unit/ -vvs -x"

    def test_render_required_param(self):
        """Required parameter without default raises error."""
        cmd = RegisteredCommand(
            name="test-file",
            tpl="pytest {file} -v",
        )
        with pytest.raises(ValueError, match="Missing required params.*file"):
            cmd.render({})

    def test_render_required_param_provided(self):
        """Required parameter provided works."""
        cmd = RegisteredCommand(
            name="test-file",
            tpl="pytest {file} -v",
        )
        result = cmd.render({"file": "test_foo.py"})
        assert result == "pytest test_foo.py -v"

    def test_render_mixed_required_optional(self):
        """Mix of required and optional params."""
        cmd = RegisteredCommand(
            name="build",
            tpl="make -j{jobs} {target}",
            defaults={"jobs": "4"},
        )
        # Missing required 'target'
        with pytest.raises(ValueError, match="Missing required params.*target"):
            cmd.render({})

        # Provide required, use default for optional
        result = cmd.render({"target": "all"})
        assert result == "make -j4 all"

        # Override both
        result = cmd.render({"target": "clean", "jobs": "8"})
        assert result == "make -j8 clean"

    def test_render_extra_args(self):
        """Extra arguments are appended."""
        cmd = RegisteredCommand(
            name="test",
            tpl="pytest {path}",
            defaults={"path": "tests/"},
        )
        result = cmd.render({}, extra=["--verbose", "-x"])
        assert result == "pytest tests/ --verbose -x"

    def test_render_unknown_arg_raises(self):
        """Unknown argument raises error."""
        cmd = RegisteredCommand(
            name="test",
            tpl="pytest {path}",
            defaults={"path": "tests/"},
        )
        with pytest.raises(ValueError, match="Unknown argument.*unknown"):
            cmd.render({"unknown": "value"})

    def test_render_escaped_braces(self):
        """Escaped braces are preserved."""
        cmd = RegisteredCommand(
            name="echo",
            tpl="echo '{{not a param}}' && run {actual_param}",
            defaults={"actual_param": "value"},
        )
        result = cmd.render({})
        assert result == "echo '{not a param}' && run value"

    def test_required_params_property(self):
        """required_params returns params without defaults."""
        cmd = RegisteredCommand(
            name="build",
            tpl="make -j{jobs} {target}",
            defaults={"jobs": "4"},
        )
        assert cmd.required_params() == {"target"}

    def test_required_params_all_have_defaults(self):
        """No required params when all have defaults."""
        cmd = RegisteredCommand(
            name="test",
            tpl="pytest {path} {flags}",
            defaults={"path": "tests/", "flags": "-v"},
        )
        assert cmd.required_params() == set()

    def test_required_params_none_have_defaults(self):
        """All params required when no defaults."""
        cmd = RegisteredCommand(
            name="deploy",
            tpl="kubectl apply -f {file} -n {namespace}",
        )
        assert cmd.required_params() == {"file", "namespace"}

    def test_template_string_property(self):
        """template property returns tpl if present, else cmd."""
        cmd_only = RegisteredCommand(name="build", cmd="make -j8")
        assert cmd_only.template == "make -j8"

        tpl_only = RegisteredCommand(name="test", tpl="pytest {path}")
        assert tpl_only.template == "pytest {path}"

        both = RegisteredCommand(name="test", cmd="fallback", tpl="pytest {path}")
        assert both.template == "pytest {path}"

    def test_to_dict_simple_command(self):
        """to_dict for simple cmd command."""
        cmd = RegisteredCommand(name="build", cmd="make -j8", description="Build")
        d = cmd.to_dict()
        assert d["cmd"] == "make -j8"
        assert "tpl" not in d
        assert "defaults" not in d

    def test_to_dict_template_command(self):
        """to_dict for template command."""
        cmd = RegisteredCommand(
            name="test",
            tpl="pytest {path} {flags}",
            defaults={"path": "tests/", "flags": "-v"},
            description="Run tests",
        )
        d = cmd.to_dict()
        assert "cmd" not in d
        assert d["tpl"] == "pytest {path} {flags}"
        assert d["defaults"] == {"path": "tests/", "flags": "-v"}

    def test_to_dict_template_no_defaults(self):
        """to_dict for template without defaults."""
        cmd = RegisteredCommand(
            name="test-file",
            tpl="pytest {file} -v",
        )
        d = cmd.to_dict()
        assert d["tpl"] == "pytest {file} -v"
        assert "defaults" not in d  # Don't include empty defaults


class TestLoadSaveTemplateCommands:
    """Tests for loading/saving template commands via BlqConfig."""

    def test_save_and_load_template_command(self, lq_dir):
        """Save and load template command roundtrip."""
        from blq.commands.core import BlqConfig

        config = BlqConfig.load(lq_dir)
        config._commands = {
            "test": RegisteredCommand(
                name="test",
                tpl="pytest {path} {flags}",
                defaults={"path": "tests/", "flags": "-v"},
                description="Run tests",
            ),
        }
        config.save_commands()

        # Reload to verify persistence
        config2 = BlqConfig.load(lq_dir)
        loaded = config2.commands

        assert len(loaded) == 1
        assert loaded["test"].is_template
        assert loaded["test"].tpl == "pytest {path} {flags}"
        assert loaded["test"].defaults == {"path": "tests/", "flags": "-v"}
        assert loaded["test"].description == "Run tests"

    def test_save_and_load_mixed_commands(self, lq_dir):
        """Save and load mix of cmd and tpl commands."""
        from blq.commands.core import BlqConfig

        config = BlqConfig.load(lq_dir)
        config._commands = {
            "lint": RegisteredCommand(
                name="lint",
                cmd="ruff check .",
                description="Run linter",
            ),
            "test": RegisteredCommand(
                name="test",
                tpl="pytest {path} {flags}",
                defaults={"path": "tests/", "flags": "-v"},
                description="Run tests",
            ),
            "test-file": RegisteredCommand(
                name="test-file",
                tpl="pytest {file} -v --tb=short",
                description="Test single file",
            ),
        }
        config.save_commands()

        config2 = BlqConfig.load(lq_dir)
        loaded = config2.commands

        assert len(loaded) == 3

        # Simple command
        assert not loaded["lint"].is_template
        assert loaded["lint"].cmd == "ruff check ."

        # Template with defaults
        assert loaded["test"].is_template
        assert loaded["test"].tpl == "pytest {path} {flags}"
        assert loaded["test"].defaults == {"path": "tests/", "flags": "-v"}

        # Template without defaults (required params)
        assert loaded["test-file"].is_template
        assert loaded["test-file"].tpl == "pytest {file} -v --tb=short"
        assert loaded["test-file"].defaults == {}

    def test_toml_format_output(self, lq_dir):
        """Verify TOML output format matches design spec."""
        from blq.commands.core import BlqConfig

        config = BlqConfig.load(lq_dir)
        config._commands = {
            "test": RegisteredCommand(
                name="test",
                tpl="pytest {path} {flags}",
                defaults={"path": "tests/", "flags": "-v"},
                description="Run tests",
            ),
        }
        config.save_commands()

        toml_path = lq_dir / "commands.toml"
        content = toml_path.read_text()

        # Should use tpl instead of cmd
        assert 'tpl = "pytest {path} {flags}"' in content
        # Defaults can be inline table or nested section - both are valid TOML
        assert "defaults" in content
        assert 'path = "tests/"' in content
        assert 'flags = "-v"' in content
        # cmd should not appear at top level (description can contain "cmd" as text)
        assert "[commands.test]" in content
        assert "cmd =" not in content


class TestRunTemplateCommand:
    """Tests for running template commands via cmd_run."""

    def test_run_template_command_with_defaults(self, lq_dir, monkeypatch):
        """Running a template command should expand template with defaults."""
        import argparse
        from unittest.mock import MagicMock, patch

        from blq.commands.core import BlqConfig

        # Set up a template command
        config = BlqConfig.load(lq_dir)
        config._commands = {
            "greet": RegisteredCommand(
                name="greet",
                tpl="echo Hello {name}",
                defaults={"name": "World"},
                description="Greet someone",
            ),
        }
        config.save_commands()

        monkeypatch.chdir(lq_dir.parent)

        # Mock _execute_command to capture the command that would be run
        with patch("blq.commands.execution._execute_command") as mock_exec:
            mock_exec.return_value = MagicMock(
                exit_code=0,
                output="",
                started_at=None,
                completed_at=None,
                timed_out=False,
            )

            from blq.commands.execution import cmd_run

            args = argparse.Namespace(
                command=["greet"],
                name=None,
                json=False,
                markdown=False,
                csv=False,
                quiet=True,
                summary=False,
                verbose=False,
                include_warnings=False,
                error_limit=None,
                keep_raw=False,
                format=None,
                timeout=None,
                capture=None,
                register=False,
                positional_args=None,
            )

            with pytest.raises(SystemExit) as exc_info:
                cmd_run(args)

            assert exc_info.value.code == 0

            # Verify _execute_command was called with the expanded command
            mock_exec.assert_called_once()
            call_kwargs = mock_exec.call_args[1]
            assert call_kwargs["command"] == "echo Hello World"

    def test_run_template_command_with_args(self, lq_dir, monkeypatch):
        """Running a template command with args should override defaults."""
        import argparse
        from unittest.mock import MagicMock, patch

        from blq.commands.core import BlqConfig

        # Set up a template command
        config = BlqConfig.load(lq_dir)
        config._commands = {
            "greet": RegisteredCommand(
                name="greet",
                tpl="echo Hello {name}",
                defaults={"name": "World"},
                description="Greet someone",
            ),
        }
        config.save_commands()

        monkeypatch.chdir(lq_dir.parent)

        # Mock _execute_command to capture the command that would be run
        with patch("blq.commands.execution._execute_command") as mock_exec:
            mock_exec.return_value = MagicMock(
                exit_code=0,
                output="",
                started_at=None,
                completed_at=None,
                timed_out=False,
            )

            from blq.commands.execution import cmd_run

            args = argparse.Namespace(
                command=["greet", "name=Claude"],
                name=None,
                json=False,
                markdown=False,
                csv=False,
                quiet=True,
                summary=False,
                verbose=False,
                include_warnings=False,
                error_limit=None,
                keep_raw=False,
                format=None,
                timeout=None,
                capture=None,
                register=False,
                positional_args=None,
            )

            with pytest.raises(SystemExit) as exc_info:
                cmd_run(args)

            assert exc_info.value.code == 0

            # Verify _execute_command was called with the expanded command
            mock_exec.assert_called_once()
            call_kwargs = mock_exec.call_args[1]
            assert call_kwargs["command"] == "echo Hello Claude"
