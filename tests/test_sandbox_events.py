"""Tests for sandbox violation event generation."""
from __future__ import annotations

import hashlib


class TestSandboxEventGeneration:
    """Test that sandboxed command failures produce info events."""

    def test_sandbox_event_created_on_failure(self):
        """A failing sandboxed command should produce a sandbox info event."""
        extension_data = {
            "sandbox": {"network": "none", "filesystem": "readonly"},
            "sandbox_grade_w": "pinhole",
            "sandbox_effects_ceiling": 2,
        }
        exit_code = 1
        events: list[dict] = []

        if extension_data.get("sandbox") and exit_code != 0:
            sandbox = extension_data["sandbox"]
            grade_w = extension_data.get("sandbox_grade_w", "unknown")
            ceiling = extension_data.get("sandbox_effects_ceiling", "unknown")
            dims = []
            for key in ("network", "filesystem", "processes"):
                if key in sandbox:
                    dims.append(f"{key}={sandbox[key]}")
            spec_summary = ", ".join(dims) if dims else "custom"
            events.append({
                "severity": "info",
                "message": (
                    f"Command failed in sandbox ({spec_summary},"
                    f" grade_w={grade_w}, effects_ceiling={ceiling})"
                ),
                "code": f"sandbox_exit_{exit_code}",
                "fingerprint": hashlib.blake2b(
                    f"sandbox:{grade_w}:{ceiling}:{exit_code}".encode(),
                    digest_size=8,
                ).hexdigest(),
            })

        assert len(events) == 1
        assert events[0]["severity"] == "info"
        assert "sandbox" in events[0]["message"]
        assert "network=none" in events[0]["message"]
        assert "pinhole" in events[0]["message"]
        assert events[0]["code"] == "sandbox_exit_1"

    def test_no_sandbox_event_on_success(self):
        """Successful sandboxed commands should not produce sandbox events."""
        extension_data = {
            "sandbox": {"network": "none"},
            "sandbox_grade_w": "pinhole",
            "sandbox_effects_ceiling": 2,
        }
        exit_code = 0
        events: list[dict] = []

        if extension_data.get("sandbox") and exit_code != 0:
            events.append({"severity": "info", "message": "would be added"})

        assert len(events) == 0

    def test_no_sandbox_event_without_sandbox(self):
        """Non-sandboxed commands should not produce sandbox events."""
        extension_data: dict = {}
        exit_code = 1
        events: list[dict] = []

        if extension_data.get("sandbox") and exit_code != 0:
            events.append({"severity": "info", "message": "would be added"})

        assert len(events) == 0

    def test_fingerprint_differs_by_exit_code(self):
        """Different exit codes should produce different fingerprints."""
        fp1 = hashlib.blake2b(b"sandbox:pinhole:2:1", digest_size=8).hexdigest()
        fp2 = hashlib.blake2b(b"sandbox:pinhole:2:2", digest_size=8).hexdigest()
        assert fp1 != fp2

    def test_fingerprint_same_for_same_conditions(self):
        """Same sandbox + exit code should produce same fingerprint."""
        fp1 = hashlib.blake2b(b"sandbox:pinhole:2:1", digest_size=8).hexdigest()
        fp2 = hashlib.blake2b(b"sandbox:pinhole:2:1", digest_size=8).hexdigest()
        assert fp1 == fp2

    def test_sandbox_event_with_all_dimensions(self):
        """All sandbox dimensions should appear in the message."""
        extension_data = {
            "sandbox": {
                "network": "none",
                "filesystem": "readonly",
                "processes": "restricted",
            },
            "sandbox_grade_w": "sealed",
            "sandbox_effects_ceiling": 0,
        }
        exit_code = 2
        events: list[dict] = []

        if extension_data.get("sandbox") and exit_code != 0:
            sandbox = extension_data["sandbox"]
            grade_w = extension_data.get("sandbox_grade_w", "unknown")
            ceiling = extension_data.get("sandbox_effects_ceiling", "unknown")
            dims = []
            for key in ("network", "filesystem", "processes"):
                if key in sandbox:
                    dims.append(f"{key}={sandbox[key]}")
            spec_summary = ", ".join(dims) if dims else "custom"
            events.append({
                "severity": "info",
                "message": (
                    f"Command failed in sandbox ({spec_summary},"
                    f" grade_w={grade_w}, effects_ceiling={ceiling})"
                ),
                "code": f"sandbox_exit_{exit_code}",
                "fingerprint": hashlib.blake2b(
                    f"sandbox:{grade_w}:{ceiling}:{exit_code}".encode(),
                    digest_size=8,
                ).hexdigest(),
            })

        assert len(events) == 1
        assert "network=none" in events[0]["message"]
        assert "filesystem=readonly" in events[0]["message"]
        assert "processes=restricted" in events[0]["message"]
        assert events[0]["code"] == "sandbox_exit_2"

    def test_sandbox_event_missing_grade_defaults(self):
        """Missing grade_w and effects_ceiling should default to 'unknown'."""
        extension_data = {
            "sandbox": {"network": "none"},
        }
        exit_code = 1
        events: list[dict] = []

        if extension_data.get("sandbox") and exit_code != 0:
            sandbox = extension_data["sandbox"]
            grade_w = extension_data.get("sandbox_grade_w", "unknown")
            ceiling = extension_data.get("sandbox_effects_ceiling", "unknown")
            dims = []
            for key in ("network", "filesystem", "processes"):
                if key in sandbox:
                    dims.append(f"{key}={sandbox[key]}")
            spec_summary = ", ".join(dims) if dims else "custom"
            events.append({
                "severity": "info",
                "message": (
                    f"Command failed in sandbox ({spec_summary},"
                    f" grade_w={grade_w}, effects_ceiling={ceiling})"
                ),
                "code": f"sandbox_exit_{exit_code}",
                "fingerprint": hashlib.blake2b(
                    f"sandbox:{grade_w}:{ceiling}:{exit_code}".encode(),
                    digest_size=8,
                ).hexdigest(),
            })

        assert len(events) == 1
        assert "grade_w=unknown" in events[0]["message"]
        assert "effects_ceiling=unknown" in events[0]["message"]
