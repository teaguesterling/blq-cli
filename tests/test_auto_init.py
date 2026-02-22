"""Tests for auto-init behavior when registering commands."""

import argparse
from unittest.mock import patch

import pytest


class TestRegisterWithoutInit:
    """Tests for registering commands without initialization."""

    def test_register_fails_without_init_when_auto_init_false(self, chdir_temp):
        """Register exits with error when not initialized and auto_init is False."""
        from blq.commands.registry import cmd_register
        from blq.user_config import UserConfig

        # Mock user config with auto_init disabled
        with patch.object(UserConfig, "load", return_value=UserConfig(auto_init=False)):
            args = argparse.Namespace(
                name="test",
                cmd=["echo", "hello"],
                description="",
                timeout=300,
                format="auto",
                no_capture=False,
                force=False,
                run=False,
                template=False,
                default=[],
            )

            with pytest.raises(SystemExit) as exc_info:
                cmd_register(args)
            assert exc_info.value.code == 1

    def test_register_auto_inits_when_enabled(self, chdir_temp, capsys):
        """Register auto-initializes project when auto_init is True."""
        from blq.commands.core import BlqConfig
        from blq.commands.registry import cmd_register
        from blq.user_config import UserConfig

        # Verify not initialized
        assert not (chdir_temp / ".lq").exists()

        # Mock user config with auto_init enabled
        with patch.object(
            UserConfig, "load", return_value=UserConfig(auto_init=True, auto_mcp=False)
        ):
            args = argparse.Namespace(
                name="test",
                cmd=["echo", "hello"],
                description="",
                timeout=300,
                format="auto",
                no_capture=False,
                force=False,
                run=False,
                template=False,
                default=[],
            )

            cmd_register(args)

        # Verify initialized
        assert (chdir_temp / ".lq").exists()
        assert (chdir_temp / ".lq" / "commands.toml").exists()

        # Verify command was registered
        config = BlqConfig.find()
        assert config is not None
        assert "test" in config.commands

        # Verify notice was printed
        captured = capsys.readouterr()
        assert "Auto-initializing" in captured.err

    def test_auto_init_uses_user_config_defaults(self, chdir_temp):
        """Auto-init uses user config defaults for storage and gitignore."""
        from blq.commands.core import BlqConfig
        from blq.commands.registry import cmd_register
        from blq.user_config import UserConfig

        # Mock user config with specific defaults
        with patch.object(
            UserConfig,
            "load",
            return_value=UserConfig(
                auto_init=True,
                auto_mcp=False,
                auto_gitignore=False,
                default_storage="parquet",
            ),
        ):
            args = argparse.Namespace(
                name="build",
                cmd=["make"],
                description="",
                timeout=300,
                format="auto",
                no_capture=False,
                force=False,
                run=False,
                template=False,
                default=[],
            )

            cmd_register(args)

        # Verify initialized with parquet mode
        config = BlqConfig.find()
        assert config is not None
        assert config.storage_mode == "parquet"

        # Verify gitignore was not modified (auto_gitignore=False)
        gitignore = chdir_temp / ".gitignore"
        assert not gitignore.exists()


class TestInitWithAutoMcp:
    """Tests for init with auto MCP configuration."""

    def test_init_creates_mcp_when_auto_mcp_true(self, chdir_temp):
        """Init creates .mcp.json when auto_mcp is True."""
        from blq.commands.init_cmd import cmd_init
        from blq.user_config import UserConfig

        with patch.object(
            UserConfig, "load", return_value=UserConfig(auto_mcp=True, auto_gitignore=True)
        ):
            args = argparse.Namespace(
                mcp=False,  # Not explicitly set
                no_mcp=False,
                detect=False,
                detect_mode="none",
                yes=False,
                force=False,
                parquet=False,
                namespace=None,
                project=None,
                gitignore=None,
            )

            cmd_init(args)

        assert (chdir_temp / ".mcp.json").exists()

    def test_init_respects_no_mcp_flag(self, chdir_temp):
        """Init respects --no-mcp flag even if auto_mcp is True."""
        from blq.commands.init_cmd import cmd_init
        from blq.user_config import UserConfig

        with patch.object(
            UserConfig, "load", return_value=UserConfig(auto_mcp=True, auto_gitignore=True)
        ):
            args = argparse.Namespace(
                mcp=False,
                no_mcp=True,  # Explicitly disabled
                detect=False,
                detect_mode="none",
                yes=False,
                force=False,
                parquet=False,
                namespace=None,
                project=None,
                gitignore=None,
            )

            cmd_init(args)

        assert not (chdir_temp / ".mcp.json").exists()

    def test_init_explicit_mcp_overrides_config(self, chdir_temp):
        """Init with --mcp creates .mcp.json even if auto_mcp is False."""
        from blq.commands.init_cmd import cmd_init
        from blq.user_config import UserConfig

        with patch.object(
            UserConfig, "load", return_value=UserConfig(auto_mcp=False, auto_gitignore=True)
        ):
            args = argparse.Namespace(
                mcp=True,  # Explicitly enabled
                no_mcp=False,
                detect=False,
                detect_mode="none",
                yes=False,
                force=False,
                parquet=False,
                namespace=None,
                project=None,
                gitignore=None,
            )

            cmd_init(args)

        assert (chdir_temp / ".mcp.json").exists()

    def test_mcp_config_has_correct_command(self, chdir_temp):
        """Init creates .mcp.json with correct 'blq mcp serve' command."""
        import json

        from blq.commands.init_cmd import cmd_init
        from blq.user_config import UserConfig

        with patch.object(
            UserConfig, "load", return_value=UserConfig(auto_mcp=True, auto_gitignore=False)
        ):
            args = argparse.Namespace(
                mcp=True,
                no_mcp=False,
                detect=False,
                detect_mode="none",
                yes=False,
                force=False,
                parquet=False,
                namespace=None,
                project=None,
                gitignore=None,
            )

            cmd_init(args)

        mcp_path = chdir_temp / ".mcp.json"
        assert mcp_path.exists()

        config = json.loads(mcp_path.read_text())
        assert "mcpServers" in config
        assert "blq_mcp" in config["mcpServers"]

        blq_config = config["mcpServers"]["blq_mcp"]
        assert blq_config["command"] == "blq"
        # Must be ["mcp", "serve"], not just ["serve"]
        assert blq_config["args"] == ["mcp", "serve"]


class TestInitGitignoreConfig:
    """Tests for gitignore handling with user config."""

    def test_init_respects_auto_gitignore_true(self, chdir_temp):
        """Init adds .gitignore when auto_gitignore is True."""
        from blq.commands.init_cmd import cmd_init
        from blq.user_config import UserConfig

        with patch.object(
            UserConfig, "load", return_value=UserConfig(auto_mcp=False, auto_gitignore=True)
        ):
            args = argparse.Namespace(
                mcp=False,
                no_mcp=False,
                detect=False,
                detect_mode="none",
                yes=False,
                force=False,
                parquet=False,
                namespace=None,
                project=None,
                gitignore=None,  # Not explicitly set
            )

            cmd_init(args)

        assert (chdir_temp / ".gitignore").exists()

    def test_init_respects_auto_gitignore_false(self, chdir_temp):
        """Init skips .gitignore when auto_gitignore is False."""
        from blq.commands.init_cmd import cmd_init
        from blq.user_config import UserConfig

        with patch.object(
            UserConfig, "load", return_value=UserConfig(auto_mcp=False, auto_gitignore=False)
        ):
            args = argparse.Namespace(
                mcp=False,
                no_mcp=False,
                detect=False,
                detect_mode="none",
                yes=False,
                force=False,
                parquet=False,
                namespace=None,
                project=None,
                gitignore=None,  # Not explicitly set
            )

            cmd_init(args)

        assert not (chdir_temp / ".gitignore").exists()

    def test_explicit_gitignore_overrides_config(self, chdir_temp):
        """Explicit --gitignore flag overrides user config."""
        from blq.commands.init_cmd import cmd_init
        from blq.user_config import UserConfig

        with patch.object(
            UserConfig, "load", return_value=UserConfig(auto_mcp=False, auto_gitignore=False)
        ):
            args = argparse.Namespace(
                mcp=False,
                no_mcp=False,
                detect=False,
                detect_mode="none",
                yes=False,
                force=False,
                parquet=False,
                namespace=None,
                project=None,
                gitignore=True,  # Explicitly enabled
            )

            cmd_init(args)

        assert (chdir_temp / ".gitignore").exists()
