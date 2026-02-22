"""Tests for .mcp.json merging and CLAUDE.md injection."""

import json

import pytest

from blq.commands.mcp_cmd import (
    BLQ_MCP_CONFIG,
    CLAUDE_MD_END_MARKER,
    CLAUDE_MD_INSTRUCTIONS,
    CLAUDE_MD_START_MARKER,
    MCP_SERVER_KEY,
    ensure_claude_md,
    ensure_mcp_config,
)


class TestEnsureMcpConfig:
    """Tests for ensure_mcp_config() merge behavior."""

    def test_creates_new_file(self, tmp_path):
        """Creates .mcp.json with blq_mcp key when file doesn't exist."""
        mcp_file = tmp_path / ".mcp.json"
        assert ensure_mcp_config(mcp_file) is True

        config = json.loads(mcp_file.read_text())
        assert "mcpServers" in config
        assert MCP_SERVER_KEY in config["mcpServers"]
        assert config["mcpServers"][MCP_SERVER_KEY] == BLQ_MCP_CONFIG

    def test_preserves_existing_servers(self, tmp_path):
        """Merges blq_mcp into file with other servers."""
        mcp_file = tmp_path / ".mcp.json"
        existing = {
            "mcpServers": {
                "other_tool": {"command": "other", "args": ["serve"]},
            }
        }
        mcp_file.write_text(json.dumps(existing))

        assert ensure_mcp_config(mcp_file) is True

        config = json.loads(mcp_file.read_text())
        assert "other_tool" in config["mcpServers"]
        assert config["mcpServers"]["other_tool"]["command"] == "other"
        assert MCP_SERVER_KEY in config["mcpServers"]
        assert config["mcpServers"][MCP_SERVER_KEY] == BLQ_MCP_CONFIG

    def test_idempotent_when_already_correct(self, tmp_path):
        """Returns False when blq_mcp already has correct config."""
        mcp_file = tmp_path / ".mcp.json"
        config = {"mcpServers": {MCP_SERVER_KEY: BLQ_MCP_CONFIG}}
        mcp_file.write_text(json.dumps(config))

        assert ensure_mcp_config(mcp_file) is False

    def test_skips_different_config_without_force(self, tmp_path, capsys):
        """Warns and skips when existing config differs and force=False."""
        mcp_file = tmp_path / ".mcp.json"
        different = {"command": "blq", "args": ["mcp", "serve", "--safe-mode"]}
        config = {"mcpServers": {MCP_SERVER_KEY: different}}
        mcp_file.write_text(json.dumps(config))

        assert ensure_mcp_config(mcp_file, force=False) is False

        captured = capsys.readouterr()
        assert "Use --force to overwrite" in captured.out

        # Verify file unchanged
        result = json.loads(mcp_file.read_text())
        assert result["mcpServers"][MCP_SERVER_KEY] == different

    def test_overwrites_with_force(self, tmp_path):
        """Overwrites existing config when force=True."""
        mcp_file = tmp_path / ".mcp.json"
        different = {"command": "blq", "args": ["mcp", "serve", "--safe-mode"]}
        config = {"mcpServers": {MCP_SERVER_KEY: different}}
        mcp_file.write_text(json.dumps(config))

        assert ensure_mcp_config(mcp_file, force=True) is True

        result = json.loads(mcp_file.read_text())
        assert result["mcpServers"][MCP_SERVER_KEY] == BLQ_MCP_CONFIG

    def test_adds_mcpServers_key_if_missing(self, tmp_path):
        """Adds mcpServers key to file that has other top-level keys."""
        mcp_file = tmp_path / ".mcp.json"
        mcp_file.write_text(json.dumps({"other": "data"}))

        assert ensure_mcp_config(mcp_file) is True

        config = json.loads(mcp_file.read_text())
        assert config["other"] == "data"
        assert MCP_SERVER_KEY in config["mcpServers"]

    def test_invalid_json_exits(self, tmp_path):
        """Exits with error on invalid JSON."""
        mcp_file = tmp_path / ".mcp.json"
        mcp_file.write_text("not json{")

        with pytest.raises(SystemExit) as exc_info:
            ensure_mcp_config(mcp_file)
        assert exc_info.value.code == 1

    def test_uses_blq_mcp_key_not_blq(self, tmp_path):
        """Verifies the key is 'blq_mcp', not 'blq'."""
        mcp_file = tmp_path / ".mcp.json"
        ensure_mcp_config(mcp_file)

        config = json.loads(mcp_file.read_text())
        assert "blq_mcp" in config["mcpServers"]
        assert "blq" not in config["mcpServers"]


class TestEnsureClaudeMd:
    """Tests for ensure_claude_md() marker-based injection."""

    def test_creates_new_file(self, tmp_path):
        """Creates CLAUDE.md with instructions when file doesn't exist."""
        assert ensure_claude_md(tmp_path) is True

        claude_md = tmp_path / "CLAUDE.md"
        assert claude_md.exists()
        content = claude_md.read_text()
        assert CLAUDE_MD_START_MARKER in content
        assert CLAUDE_MD_END_MARKER in content
        assert "mcp__blq_mcp__run" in content

    def test_appends_to_existing_file(self, tmp_path):
        """Appends instructions to existing CLAUDE.md."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# My Project\n\nExisting content.\n")

        assert ensure_claude_md(tmp_path) is True

        content = claude_md.read_text()
        assert content.startswith("# My Project")
        assert "Existing content." in content
        assert CLAUDE_MD_START_MARKER in content

    def test_idempotent_when_already_present(self, tmp_path):
        """Returns False when instructions already present and correct."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(CLAUDE_MD_INSTRUCTIONS + "\n")

        assert ensure_claude_md(tmp_path) is False

    def test_replaces_existing_section(self, tmp_path):
        """Replaces content between markers on re-run."""
        claude_md = tmp_path / "CLAUDE.md"
        old_section = (
            "<!-- blq:agent-instructions -->\n"
            "Old instructions here\n"
            "<!-- /blq:agent-instructions -->"
        )
        claude_md.write_text(f"# My Project\n\n{old_section}\n\n# Other Section\n")

        assert ensure_claude_md(tmp_path) is True

        content = claude_md.read_text()
        assert "Old instructions here" not in content
        assert "mcp__blq_mcp__run" in content
        assert "# My Project" in content
        assert "# Other Section" in content
        # Should have exactly one start marker
        assert content.count(CLAUDE_MD_START_MARKER) == 1

    def test_appends_newline_to_content_without_trailing_newline(self, tmp_path):
        """Handles files without trailing newline."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# No trailing newline")

        assert ensure_claude_md(tmp_path) is True

        content = claude_md.read_text()
        assert content.startswith("# No trailing newline\n")
        assert CLAUDE_MD_START_MARKER in content

    def test_instructions_contain_expected_tools(self, tmp_path):
        """Verifies instructions reference the correct MCP tool names."""
        ensure_claude_md(tmp_path)
        content = (tmp_path / "CLAUDE.md").read_text()

        expected_tools = [
            "mcp__blq_mcp__commands",
            "mcp__blq_mcp__run",
            "mcp__blq_mcp__register_command",
            "mcp__blq_mcp__status",
            "mcp__blq_mcp__errors",
            "mcp__blq_mcp__info",
        ]
        for tool in expected_tools:
            assert tool in content, f"Missing tool reference: {tool}"


class TestMcpInstallIntegration:
    """Integration tests for mcp install with merging and CLAUDE.md."""

    def test_mcp_install_creates_both_files(self, chdir_temp):
        """blq mcp install creates .mcp.json and CLAUDE.md."""
        import argparse

        from blq.commands.mcp_cmd import cmd_mcp_install

        args = argparse.Namespace(force=False, hooks=False)
        cmd_mcp_install(args)

        assert (chdir_temp / ".mcp.json").exists()
        assert (chdir_temp / "CLAUDE.md").exists()

        config = json.loads((chdir_temp / ".mcp.json").read_text())
        assert MCP_SERVER_KEY in config["mcpServers"]

    def test_mcp_install_preserves_other_servers(self, chdir_temp):
        """blq mcp install preserves other MCP server configs."""
        import argparse

        from blq.commands.mcp_cmd import cmd_mcp_install

        existing = {"mcpServers": {"my_server": {"command": "my-cmd"}}}
        (chdir_temp / ".mcp.json").write_text(json.dumps(existing))

        args = argparse.Namespace(force=False, hooks=False)
        cmd_mcp_install(args)

        config = json.loads((chdir_temp / ".mcp.json").read_text())
        assert "my_server" in config["mcpServers"]
        assert MCP_SERVER_KEY in config["mcpServers"]

    def test_mcp_install_idempotent(self, chdir_temp, capsys):
        """Running mcp install twice reports already configured."""
        import argparse

        from blq.commands.mcp_cmd import cmd_mcp_install

        args = argparse.Namespace(force=False, hooks=False)
        cmd_mcp_install(args)
        capsys.readouterr()  # Clear first run output

        cmd_mcp_install(args)
        captured = capsys.readouterr()
        assert "already configured" in captured.out


class TestInitMcpMerge:
    """Tests for init --mcp using merge behavior."""

    def test_init_mcp_creates_merged_config(self, chdir_temp):
        """blq init --mcp creates .mcp.json with blq_mcp key."""
        import argparse
        from unittest.mock import patch

        from blq.commands.init_cmd import cmd_init
        from blq.user_config import UserConfig

        with patch.object(
            UserConfig, "load", return_value=UserConfig(auto_mcp=False, auto_gitignore=False)
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

        config = json.loads((chdir_temp / ".mcp.json").read_text())
        assert MCP_SERVER_KEY in config["mcpServers"]
        assert "blq" not in config["mcpServers"]

    def test_init_mcp_preserves_existing(self, chdir_temp):
        """blq init --mcp preserves existing .mcp.json servers."""
        import argparse
        from unittest.mock import patch

        from blq.commands.init_cmd import cmd_init
        from blq.user_config import UserConfig

        existing = {"mcpServers": {"other": {"command": "other-cmd"}}}
        (chdir_temp / ".mcp.json").write_text(json.dumps(existing))

        with patch.object(
            UserConfig, "load", return_value=UserConfig(auto_mcp=False, auto_gitignore=False)
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

        config = json.loads((chdir_temp / ".mcp.json").read_text())
        assert "other" in config["mcpServers"]
        assert MCP_SERVER_KEY in config["mcpServers"]

    def test_init_mcp_creates_claude_md(self, chdir_temp):
        """blq init --mcp also creates CLAUDE.md."""
        import argparse
        from unittest.mock import patch

        from blq.commands.init_cmd import cmd_init
        from blq.user_config import UserConfig

        with patch.object(
            UserConfig, "load", return_value=UserConfig(auto_mcp=False, auto_gitignore=False)
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

        claude_md = chdir_temp / "CLAUDE.md"
        assert claude_md.exists()
        assert CLAUDE_MD_START_MARKER in claude_md.read_text()
