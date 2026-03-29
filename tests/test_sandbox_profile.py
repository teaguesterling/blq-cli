"""Tests for sandbox profile runner."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from blq_sandbox.profile import run_profile, suggest_spec_from_profile


@pytest.mark.skipif(not shutil.which("strace"), reason="strace not installed")
class TestRunProfile:
    def test_profiles_simple_command(self, tmp_path: Path):
        profile = run_profile("echo hello", workspace=tmp_path, timeout=10)
        assert profile is not None
        assert len(profile.files_read) > 0
        assert "/usr/bin/echo" in profile.executables

    def test_profiles_file_write(self, tmp_path: Path):
        target = tmp_path / "output.txt"
        profile = run_profile(f"touch {target}", workspace=tmp_path, timeout=10)
        assert str(target) in profile.files_written

    def test_profiles_subprocess(self, tmp_path: Path):
        profile = run_profile("bash -c 'echo inner'", workspace=tmp_path, timeout=10)
        assert "/usr/bin/bash" in profile.executables or "/bin/bash" in profile.executables


class TestRunProfileWithoutStrace:
    def test_returns_none(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda _: None)
        profile = run_profile("echo hello", workspace=tmp_path, timeout=10)
        assert profile is None


@pytest.mark.skipif(not shutil.which("strace"), reason="strace not installed")
class TestSuggestSpec:
    def test_suggests_readonly_for_echo(self, tmp_path: Path):
        profile = run_profile("echo hello", workspace=tmp_path, timeout=10)
        assert profile is not None
        spec = suggest_spec_from_profile(profile, workspace=tmp_path)
        assert spec["network"] == "none"
        assert spec["filesystem"] == "readonly"

    def test_suggests_workspace_only_for_touch(self, tmp_path: Path):
        target = tmp_path / "out.txt"
        profile = run_profile(f"touch {target}", workspace=tmp_path, timeout=10)
        assert profile is not None
        spec = suggest_spec_from_profile(profile, workspace=tmp_path)
        assert spec["filesystem"] == "workspace_only"

    def test_suggests_network_none_without_connections(self, tmp_path: Path):
        profile = run_profile("echo hello", workspace=tmp_path, timeout=10)
        assert profile is not None
        spec = suggest_spec_from_profile(profile, workspace=tmp_path)
        assert spec["network"] == "none"
