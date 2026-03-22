"""Tests for extension discovery and ordering."""
from __future__ import annotations

from typing import Any
from unittest.mock import patch, MagicMock

from blq.ext import CommandSpec, ExecutionResult
from blq.ext.discovery import load_extensions, order_extensions


class FakeExtension:
    def __init__(self, name: str, config_key: str):
        self.name = name
        self.config_key = config_key

    def prepare(self, spec: CommandSpec) -> CommandSpec:
        return spec

    def validate(self, config: dict[str, Any]) -> list[str]:
        return []

    def store(self, spec: CommandSpec, result: ExecutionResult, store: Any) -> None:
        pass


class TestLoadExtensions:
    def test_empty_when_no_extensions(self) -> None:
        with patch("blq.ext.discovery.entry_points", return_value=[]):
            result = load_extensions()
            assert result == {}

    def test_loads_extension_by_config_key(self) -> None:
        ep = MagicMock()
        ep.load.return_value = lambda: FakeExtension("sandbox", "sandbox")
        with patch("blq.ext.discovery.entry_points", return_value=[ep]):
            result = load_extensions()
            assert "sandbox" in result
            assert result["sandbox"].name == "sandbox"


class TestOrderExtensions:
    def test_default_order(self) -> None:
        exts = {
            "sandbox": FakeExtension("sandbox", "sandbox"),
            "env": FakeExtension("env", "env"),
        }
        ordered = order_extensions(exts)
        keys = [e.config_key for e in ordered]
        assert keys.index("env") < keys.index("sandbox")

    def test_custom_order(self) -> None:
        exts = {
            "sandbox": FakeExtension("sandbox", "sandbox"),
            "env": FakeExtension("env", "env"),
        }
        ordered = order_extensions(exts, order=["sandbox", "env"])
        keys = [e.config_key for e in ordered]
        assert keys.index("sandbox") < keys.index("env")

    def test_unlisted_extensions_go_last(self) -> None:
        exts = {
            "sandbox": FakeExtension("sandbox", "sandbox"),
            "custom": FakeExtension("custom", "custom"),
        }
        ordered = order_extensions(exts, order=["sandbox"])
        keys = [e.config_key for e in ordered]
        assert keys == ["sandbox", "custom"]
