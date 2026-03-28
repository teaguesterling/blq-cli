"""Tests for the lock field on RegisteredCommand."""
from blq.commands.core import RegisteredCommand


class TestRegisteredCommandLockField:
    def test_default_lock_is_none(self):
        cmd = RegisteredCommand(name="build", cmd="make")
        assert cmd.lock is None

    def test_lock_field_set(self):
        cmd = RegisteredCommand(name="build", cmd="make", lock="build")
        assert cmd.lock == "build"

    def test_lock_in_to_dict(self):
        cmd = RegisteredCommand(name="build", cmd="make", lock="build")
        d = cmd.to_dict()
        assert d["lock"] == "build"

    def test_lock_omitted_from_to_dict_when_none(self):
        cmd = RegisteredCommand(name="build", cmd="make")
        d = cmd.to_dict()
        assert "lock" not in d

    def test_lock_shared_across_commands(self):
        build = RegisteredCommand(name="build", cmd="make", lock="compile")
        test = RegisteredCommand(name="test", cmd="pytest", lock="compile")
        assert build.lock == test.lock == "compile"
