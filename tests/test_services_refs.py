"""Tests for blq.services.refs — canonical ref parser and resolver."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from blq.services.refs import ParsedRef, parse_ref, resolve_run_ref


class TestParseRef:
    """Tests for the parse_ref function."""

    def test_bare_serial(self):
        result = parse_ref("5")
        assert result == ParsedRef(run_serial=5)
        assert not result.is_relative
        assert result.run_ref == "5"

    def test_tag_serial(self):
        result = parse_ref("build:3")
        assert result == ParsedRef(tag="build", run_serial=3)
        assert result.run_ref == "build:3"

    def test_full_ref(self):
        result = parse_ref("test:5:2")
        assert result == ParsedRef(tag="test", run_serial=5, event_id=2)
        assert result.run_ref == "test:5"

    def test_serial_event(self):
        """When first part is numeric, it's serial:event not tag:serial."""
        result = parse_ref("5:2")
        assert result == ParsedRef(run_serial=5, event_id=2)
        assert result.run_ref == "5"

    def test_relative(self):
        result = parse_ref("~1")
        assert result == ParsedRef(relative=1)
        assert result.is_relative
        assert result.run_ref == "~1"

    def test_relative_with_tag(self):
        result = parse_ref("test:~2")
        assert result == ParsedRef(tag="test", relative=2)
        assert result.is_relative
        assert result.run_ref == "test:~2"

    def test_relative_with_event(self):
        result = parse_ref("test:~1:3")
        assert result == ParsedRef(tag="test", relative=1, event_id=3)
        assert result.is_relative

    def test_invalid_empty(self):
        with pytest.raises(ValueError, match="Empty ref"):
            parse_ref("")

    def test_invalid_whitespace(self):
        with pytest.raises(ValueError, match="Empty ref"):
            parse_ref("   ")

    def test_uuid(self):
        uuid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        result = parse_ref(uuid)
        assert result == ParsedRef(uuid=uuid)
        assert result.run_ref == uuid

    def test_bare_tag(self):
        """A non-numeric single part is treated as a bare tag."""
        result = parse_ref("build")
        assert result == ParsedRef(tag="build")
        assert result.run_ref == "build"

    def test_invalid_two_part(self):
        with pytest.raises(ValueError, match="Invalid ref"):
            parse_ref("build:notanumber")

    def test_strips_whitespace(self):
        result = parse_ref("  5  ")
        assert result == ParsedRef(run_serial=5)


class TestResolveRunRef:
    """Tests for resolve_run_ref against a real database."""

    def test_returns_none_for_nonexistent(self, initialized_project):
        from blq.storage import BlqStorage

        with BlqStorage.open() as storage:
            result = resolve_run_ref(storage, "999")
            assert result is None

    def test_returns_dict_for_valid_run(self, initialized_project):
        """Create a run via storage API then resolve it."""
        from blq.storage import BlqStorage

        now = datetime.now(timezone.utc).isoformat()
        with BlqStorage.open() as storage:
            storage.write_run(
                run_meta={
                    "command": "echo hello",
                    "source_name": "test-cmd",
                    "source_type": "exec",
                    "exit_code": 0,
                    "started_at": now,
                    "completed_at": now,
                },
                events=[],
                output=b"hello\n",
            )

            result = resolve_run_ref(storage, "1")
            assert result is not None
            assert isinstance(result, dict)
            assert "run_id" in result
            assert "source_name" in result

    def test_returns_none_for_invalid_ref(self, initialized_project):
        from blq.storage import BlqStorage

        with BlqStorage.open() as storage:
            result = resolve_run_ref(storage, "::::")
            assert result is None

    def test_relative_returns_none_when_no_data(self, initialized_project):
        from blq.storage import BlqStorage

        with BlqStorage.open() as storage:
            result = resolve_run_ref(storage, "~1")
            assert result is None
