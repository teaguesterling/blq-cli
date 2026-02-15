"""
User-level configuration for blq.

Manages global user preferences stored in ~/.config/blq/config.toml
(or $XDG_CONFIG_HOME/blq/config.toml).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from blq.config_format import load_toml


@dataclass
class UserConfig:
    """User-level configuration from ~/.config/blq/config.toml.

    This stores global preferences that apply to all blq projects:

    - Init defaults (auto_mcp, auto_gitignore, default_storage, auto_detect)
    - Register behavior (auto_init)
    - Output preferences (default_format, default_limit)
    - Run preferences (show_summary, keep_raw)
    - MCP preferences (safe_mode)
    - Storage preferences (auto_prune, prune_days)
    - Additional environment variables to capture

    Example config file:
        [init]
        auto_mcp = true           # Create .mcp.json on init
        auto_gitignore = true     # Add .lq/ to .gitignore
        default_storage = "bird"  # Default storage mode
        auto_detect = false       # Auto-detect commands on init

        [register]
        auto_init = true          # Auto-init on register if not initialized

        [output]
        default_format = "table"  # table, json, markdown
        default_limit = 20        # Default limit for history, errors, etc.

        [run]
        show_summary = false      # Always show summary after runs
        keep_raw = false          # Always keep raw output

        [mcp]
        safe_mode = false         # Default to safe mode

        [storage]
        auto_prune = false        # Enable automatic pruning
        prune_days = 30           # Auto-prune logs older than N days

        [hooks]
        auto_claude_code = false  # Auto-install Claude Code hooks with mcp install

        [defaults]
        extra_capture_env = ["MY_CUSTOM_VAR"]
    """

    # Init defaults
    auto_mcp: bool = False  # Create .mcp.json on init (default: True if fastmcp installed)
    auto_gitignore: bool = True  # Add .lq/ to .gitignore
    default_storage: str = "bird"  # Default storage mode
    auto_detect: bool = False  # Auto-detect commands on init

    # Register behavior
    auto_init: bool = False  # Auto-init when registering

    # Output preferences
    default_format: str = "table"  # table, json, markdown
    default_limit: int = 20  # Default limit for history, errors, etc.

    # Run preferences
    show_summary: bool = False  # Always show summary after runs
    keep_raw: bool = False  # Always keep raw output

    # MCP preferences
    mcp_safe_mode: bool = False  # Default to safe mode

    # Storage preferences
    auto_prune: bool = False  # Enable automatic pruning
    prune_days: int = 30  # Auto-prune logs older than N days

    # Hooks preferences
    hooks_auto_claude_code: bool = False  # Auto-install Claude Code hooks with mcp install
    hooks_record_commands: bool = False  # Enable record-invocation hooks for command tracking
    hooks_record_format: str = "auto"  # Default format hint for parsing in record hooks
    hooks_record_hooks: list[str] = field(
        default_factory=lambda: ["pre", "post"]
    )  # Which record hooks to install

    # Default capture_env additions
    extra_capture_env: list[str] = field(default_factory=list)

    # Track if this was loaded from a file (vs defaults)
    _loaded_from_file: bool = field(default=False, repr=False)

    @classmethod
    def config_path(cls) -> Path:
        """Get config path, respecting XDG_CONFIG_HOME.

        Returns:
            Path to ~/.config/blq/config.toml (or XDG equivalent)
        """
        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        if xdg_config:
            config_home = Path(xdg_config)
        else:
            config_home = Path.home() / ".config"
        return config_home / "blq" / "config.toml"

    @classmethod
    def mcp_available(cls) -> bool:
        """Check if fastmcp is installed.

        Uses importlib.util.find_spec for fast lookup without importing.

        Returns:
            True if fastmcp is installed
        """
        import importlib.util

        return importlib.util.find_spec("fastmcp") is not None

    @classmethod
    def load(cls) -> UserConfig:
        """Load from ~/.config/blq/config.toml or return defaults.

        If the config file doesn't exist, returns a UserConfig with
        sensible defaults (auto_mcp is True if fastmcp is installed).

        Returns:
            UserConfig instance
        """
        config_path = cls.config_path()

        # Start with defaults
        auto_mcp = cls.mcp_available()  # Auto-enable if fastmcp is installed
        auto_gitignore = True
        default_storage = "bird"
        auto_detect = False
        auto_init = False
        default_format = "table"
        default_limit = 20
        show_summary = False
        keep_raw = False
        mcp_safe_mode = False
        auto_prune = False
        prune_days = 30
        hooks_auto_claude_code = False
        hooks_record_commands = False
        hooks_record_format = "auto"
        hooks_record_hooks: list[str] = ["pre", "post"]
        extra_capture_env: list[str] = []
        loaded_from_file = False

        if config_path.exists():
            try:
                data = load_toml(config_path)
                loaded_from_file = True

                # Parse [init] section
                init_section = data.get("init", {})
                if isinstance(init_section, dict):
                    if "auto_mcp" in init_section:
                        auto_mcp = bool(init_section["auto_mcp"])
                    if "auto_gitignore" in init_section:
                        auto_gitignore = bool(init_section["auto_gitignore"])
                    if "default_storage" in init_section:
                        default_storage = str(init_section["default_storage"])
                    if "auto_detect" in init_section:
                        auto_detect = bool(init_section["auto_detect"])

                # Parse [register] section
                register_section = data.get("register", {})
                if isinstance(register_section, dict):
                    if "auto_init" in register_section:
                        auto_init = bool(register_section["auto_init"])

                # Parse [output] section
                output_section = data.get("output", {})
                if isinstance(output_section, dict):
                    if "default_format" in output_section:
                        default_format = str(output_section["default_format"])
                    if "default_limit" in output_section:
                        default_limit = int(output_section["default_limit"])

                # Parse [run] section
                run_section = data.get("run", {})
                if isinstance(run_section, dict):
                    if "show_summary" in run_section:
                        show_summary = bool(run_section["show_summary"])
                    if "keep_raw" in run_section:
                        keep_raw = bool(run_section["keep_raw"])

                # Parse [mcp] section
                mcp_section = data.get("mcp", {})
                if isinstance(mcp_section, dict):
                    if "safe_mode" in mcp_section:
                        mcp_safe_mode = bool(mcp_section["safe_mode"])

                # Parse [storage] section
                storage_section = data.get("storage", {})
                if isinstance(storage_section, dict):
                    if "auto_prune" in storage_section:
                        auto_prune = bool(storage_section["auto_prune"])
                    if "prune_days" in storage_section:
                        prune_days = int(storage_section["prune_days"])

                # Parse [hooks] section
                hooks_section = data.get("hooks", {})
                if isinstance(hooks_section, dict):
                    if "auto_claude_code" in hooks_section:
                        hooks_auto_claude_code = bool(hooks_section["auto_claude_code"])
                    if "record_commands" in hooks_section:
                        hooks_record_commands = bool(hooks_section["record_commands"])
                    if "record_format" in hooks_section:
                        hooks_record_format = str(hooks_section["record_format"])
                    if "record_hooks" in hooks_section:
                        rh = hooks_section["record_hooks"]
                        if isinstance(rh, list):
                            hooks_record_hooks = [str(h) for h in rh]

                # Parse [defaults] section
                defaults_section = data.get("defaults", {})
                if isinstance(defaults_section, dict):
                    env_list = defaults_section.get("extra_capture_env", [])
                    if isinstance(env_list, list):
                        extra_capture_env = [str(v) for v in env_list]

            except Exception:
                # If we can't parse the config, use defaults
                pass

        return cls(
            auto_mcp=auto_mcp,
            auto_gitignore=auto_gitignore,
            default_storage=default_storage,
            auto_detect=auto_detect,
            auto_init=auto_init,
            default_format=default_format,
            default_limit=default_limit,
            show_summary=show_summary,
            keep_raw=keep_raw,
            mcp_safe_mode=mcp_safe_mode,
            auto_prune=auto_prune,
            prune_days=prune_days,
            hooks_auto_claude_code=hooks_auto_claude_code,
            hooks_record_commands=hooks_record_commands,
            hooks_record_format=hooks_record_format,
            hooks_record_hooks=hooks_record_hooks,
            extra_capture_env=extra_capture_env,
            _loaded_from_file=loaded_from_file,
        )

    def save(self) -> None:
        """Save config to ~/.config/blq/config.toml.

        Creates the parent directories if they don't exist.
        Only saves non-default values to keep the file minimal.
        """
        from blq.config_format import save_toml

        config_path = self.config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)

        data: dict[str, Any] = {}

        # [init] section
        init_section: dict[str, Any] = {}
        if self.auto_mcp != self.mcp_available():  # Only save if different from default
            init_section["auto_mcp"] = self.auto_mcp
        if not self.auto_gitignore:  # Only save if False (True is default)
            init_section["auto_gitignore"] = self.auto_gitignore
        if self.default_storage != "bird":  # Only save if different from default
            init_section["default_storage"] = self.default_storage
        if self.auto_detect:  # Only save if True (False is default)
            init_section["auto_detect"] = self.auto_detect
        if init_section:
            data["init"] = init_section

        # [register] section
        register_section: dict[str, Any] = {}
        if self.auto_init:  # Only save if True (False is default)
            register_section["auto_init"] = self.auto_init
        if register_section:
            data["register"] = register_section

        # [output] section
        output_section: dict[str, Any] = {}
        if self.default_format != "table":
            output_section["default_format"] = self.default_format
        if self.default_limit != 20:
            output_section["default_limit"] = self.default_limit
        if output_section:
            data["output"] = output_section

        # [run] section
        run_section: dict[str, Any] = {}
        if self.show_summary:
            run_section["show_summary"] = self.show_summary
        if self.keep_raw:
            run_section["keep_raw"] = self.keep_raw
        if run_section:
            data["run"] = run_section

        # [mcp] section
        mcp_section: dict[str, Any] = {}
        if self.mcp_safe_mode:
            mcp_section["safe_mode"] = self.mcp_safe_mode
        if mcp_section:
            data["mcp"] = mcp_section

        # [storage] section
        storage_section: dict[str, Any] = {}
        if self.auto_prune:
            storage_section["auto_prune"] = self.auto_prune
        if self.prune_days != 30:
            storage_section["prune_days"] = self.prune_days
        if storage_section:
            data["storage"] = storage_section

        # [hooks] section
        hooks_section: dict[str, Any] = {}
        if self.hooks_auto_claude_code:  # Only save if True (False is default)
            hooks_section["auto_claude_code"] = self.hooks_auto_claude_code
        if self.hooks_record_commands:  # Only save if True (False is default)
            hooks_section["record_commands"] = self.hooks_record_commands
        if self.hooks_record_format != "auto":
            hooks_section["record_format"] = self.hooks_record_format
        if self.hooks_record_hooks != ["pre", "post"]:
            hooks_section["record_hooks"] = self.hooks_record_hooks
        if hooks_section:
            data["hooks"] = hooks_section

        # [defaults] section
        defaults_section: dict[str, Any] = {}
        if self.extra_capture_env:
            defaults_section["extra_capture_env"] = self.extra_capture_env
        if defaults_section:
            data["defaults"] = defaults_section

        save_toml(config_path, data)
