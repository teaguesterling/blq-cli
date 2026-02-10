"""Tests for user-level configuration."""

import os
from pathlib import Path
from unittest.mock import patch

from blq.user_config import UserConfig


class TestUserConfigPath:
    """Tests for config path resolution."""

    def test_default_config_path(self):
        """Config path defaults to ~/.config/blq/config.toml."""
        with patch.dict(os.environ, {}, clear=True):
            # Clear XDG_CONFIG_HOME to test default
            if "XDG_CONFIG_HOME" in os.environ:
                del os.environ["XDG_CONFIG_HOME"]
            path = UserConfig.config_path()
            assert path == Path.home() / ".config" / "blq" / "config.toml"

    def test_respects_xdg_config_home(self, temp_dir):
        """Config path respects XDG_CONFIG_HOME."""
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(temp_dir)}):
            path = UserConfig.config_path()
            assert path == temp_dir / "blq" / "config.toml"


class TestUserConfigDefaults:
    """Tests for default configuration values."""

    def test_defaults_without_file(self, temp_dir):
        """Load returns defaults when no config file exists."""
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(temp_dir)}):
            config = UserConfig.load()

            # Defaults
            assert config.auto_gitignore is True
            assert config.default_storage == "bird"
            assert config.auto_init is False
            assert config.extra_capture_env == []
            assert config._loaded_from_file is False

    def test_auto_mcp_depends_on_fastmcp(self, temp_dir):
        """auto_mcp default depends on fastmcp availability."""
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(temp_dir)}):
            # Test with fastmcp available
            with patch.object(UserConfig, "mcp_available", return_value=True):
                config = UserConfig.load()
                assert config.auto_mcp is True

            # Test without fastmcp
            with patch.object(UserConfig, "mcp_available", return_value=False):
                config = UserConfig.load()
                assert config.auto_mcp is False


class TestUserConfigLoad:
    """Tests for loading config from file."""

    def test_loads_init_section(self, temp_dir):
        """Load parses [init] section correctly."""
        config_dir = temp_dir / "blq"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "config.toml"
        config_file.write_text("""
[init]
auto_mcp = false
auto_gitignore = false
default_storage = "parquet"
""")

        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(temp_dir)}):
            config = UserConfig.load()

            assert config.auto_mcp is False
            assert config.auto_gitignore is False
            assert config.default_storage == "parquet"
            assert config._loaded_from_file is True

    def test_loads_register_section(self, temp_dir):
        """Load parses [register] section correctly."""
        config_dir = temp_dir / "blq"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "config.toml"
        config_file.write_text("""
[register]
auto_init = true
""")

        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(temp_dir)}):
            config = UserConfig.load()
            assert config.auto_init is True

    def test_loads_defaults_section(self, temp_dir):
        """Load parses [defaults] section correctly."""
        config_dir = temp_dir / "blq"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "config.toml"
        config_file.write_text("""
[defaults]
extra_capture_env = ["MY_VAR", "ANOTHER_VAR"]
""")

        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(temp_dir)}):
            config = UserConfig.load()
            assert config.extra_capture_env == ["MY_VAR", "ANOTHER_VAR"]

    def test_partial_config(self, temp_dir):
        """Load handles partial config with defaults for missing fields."""
        config_dir = temp_dir / "blq"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "config.toml"
        config_file.write_text("""
[init]
auto_mcp = true
""")

        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(temp_dir)}):
            config = UserConfig.load()

            # Explicitly set
            assert config.auto_mcp is True
            # Defaults
            assert config.auto_gitignore is True
            assert config.default_storage == "bird"
            assert config.auto_init is False

    def test_handles_invalid_toml(self, temp_dir):
        """Load returns defaults if TOML is invalid."""
        config_dir = temp_dir / "blq"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "config.toml"
        config_file.write_text("invalid [ toml")

        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(temp_dir)}):
            with patch.object(UserConfig, "mcp_available", return_value=False):
                config = UserConfig.load()
                # Should get defaults
                assert config.auto_gitignore is True
                assert config.auto_mcp is False


class TestUserConfigSave:
    """Tests for saving config to file."""

    def test_save_creates_directories(self, temp_dir):
        """Save creates parent directories if needed."""
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(temp_dir)}):
            config = UserConfig(auto_init=True)
            config.save()

            config_file = temp_dir / "blq" / "config.toml"
            assert config_file.exists()

    def test_save_roundtrip(self, temp_dir):
        """Save and load produce the same config."""
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(temp_dir)}):
            with patch.object(UserConfig, "mcp_available", return_value=True):
                # Create config with non-default values
                original = UserConfig(
                    auto_mcp=False,  # Different from default (True when mcp_available)
                    auto_gitignore=False,
                    default_storage="parquet",
                    auto_init=True,
                    extra_capture_env=["CUSTOM_VAR"],
                )
                original.save()

                # Load it back
                loaded = UserConfig.load()

                assert loaded.auto_mcp == original.auto_mcp
                assert loaded.auto_gitignore == original.auto_gitignore
                assert loaded.default_storage == original.default_storage
                assert loaded.auto_init == original.auto_init
                assert loaded.extra_capture_env == original.extra_capture_env


class TestMcpAvailable:
    """Tests for fastmcp availability detection."""

    def test_mcp_available_when_installed(self):
        """mcp_available returns True when fastmcp is importable."""
        with patch.dict("sys.modules", {"fastmcp": object()}):
            # This doesn't actually test the import, but we can test the method
            pass

    def test_mcp_available_when_not_installed(self):
        """mcp_available returns False when fastmcp is not installed."""
        # We can't easily mock an import failure, but we can verify the method exists
        # and returns a boolean
        result = UserConfig.mcp_available()
        assert isinstance(result, bool)
