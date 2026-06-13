"""Tests for sandbox violation detection."""

from __future__ import annotations

from blq_sandbox.violations import SandboxViolation, detect_violations


class TestDetectViolations:
    def test_detects_readonly_filesystem(self) -> None:
        output = "touch: cannot touch '/etc/foo': Read-only file system"
        spec: dict[str, str] = {"network": "none", "filesystem": "readonly"}
        violations = detect_violations(output, spec)
        assert len(violations) >= 1
        assert violations[0].dimension == "filesystem"

    def test_detects_permission_denied(self) -> None:
        output = "bash: /etc/foo: Permission denied"
        spec = {"filesystem": "readonly"}
        violations = detect_violations(output, spec)
        assert len(violations) >= 1
        assert violations[0].dimension == "filesystem"

    def test_detects_network_unreachable(self) -> None:
        output = "connect: Network is unreachable"
        spec = {"network": "none"}
        violations = detect_violations(output, spec)
        assert len(violations) >= 1
        assert violations[0].dimension == "network"

    def test_detects_dns_blocked(self) -> None:
        output = "Could not resolve host: example.com"
        spec = {"network": "none"}
        violations = detect_violations(output, spec)
        assert len(violations) >= 1
        assert violations[0].dimension == "network"

    def test_detects_name_or_service_not_known(self) -> None:
        output = "curl: (6) Could not resolve host: Name or service not known"
        spec = {"network": "none"}
        violations = detect_violations(output, spec)
        assert len(violations) >= 1
        assert violations[0].dimension == "network"

    def test_ignores_unrestricted_dimensions(self) -> None:
        output = "Permission denied"
        spec = {"filesystem": "unrestricted"}  # not restricted
        violations = detect_violations(output, spec)
        assert len(violations) == 0

    def test_ignores_no_sandbox(self) -> None:
        output = "Permission denied"
        spec: dict[str, str] = {}
        violations = detect_violations(output, spec)
        assert len(violations) == 0

    def test_one_violation_per_dimension(self) -> None:
        output = "line1: Permission denied\nline2: Permission denied\nline3: Permission denied"
        spec = {"filesystem": "readonly"}
        violations = detect_violations(output, spec)
        assert len(violations) == 1  # Only first

    def test_multiple_dimensions(self) -> None:
        output = "Permission denied\nNetwork is unreachable"
        spec = {"filesystem": "readonly", "network": "none"}
        violations = detect_violations(output, spec)
        dims = {v.dimension for v in violations}
        assert "filesystem" in dims
        assert "network" in dims

    def test_includes_line_number(self) -> None:
        output = "ok\nok\nPermission denied"
        spec = {"filesystem": "readonly"}
        violations = detect_violations(output, spec)
        assert violations[0].line_number == 3

    def test_empty_output(self) -> None:
        violations = detect_violations("", {"filesystem": "readonly"})
        assert len(violations) == 0

    def test_violation_dataclass(self) -> None:
        v = SandboxViolation(
            dimension="filesystem",
            pattern="write blocked",
            line="touch: Permission denied",
            line_number=5,
        )
        assert v.dimension == "filesystem"
        assert v.line_number == 5

    def test_network_not_restricted_ignores_connection_refused(self) -> None:
        output = "Connection refused"
        spec = {"filesystem": "readonly"}  # network not restricted
        violations = detect_violations(output, spec)
        assert len(violations) == 0

    def test_processes_isolated_not_triggered_by_fs_errors(self) -> None:
        # processes dimension shouldn't flag filesystem errors
        output = "Permission denied"
        spec = {"processes": "isolated"}
        violations = detect_violations(output, spec)
        # Permission denied is a filesystem pattern, processes not in restricted_dims from fs
        assert len(violations) == 0

    def test_line_content_preserved(self) -> None:
        output = "  touch: cannot touch '/var/run/foo': Read-only file system  "
        spec = {"filesystem": "readonly"}
        violations = detect_violations(output, spec)
        assert len(violations) >= 1
        assert "Read-only file system" in violations[0].line
