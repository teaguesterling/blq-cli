"""Tests for strace output parser."""
from __future__ import annotations

import pytest

from blq_sandbox.strace_parser import StraceProfile, parse_strace_output

SAMPLE_STRACE = """\
1234 execve("/usr/bin/echo", ["echo", "hello"], 0x7fff /* 65 vars */) = 0
1234 access("/etc/ld.so.preload", R_OK) = -1 ENOENT (No such file or directory)
1234 openat(AT_FDCWD, "/etc/ld.so.cache", O_RDONLY|O_CLOEXEC) = 3
1234 openat(AT_FDCWD, "/lib/x86_64-linux-gnu/libc.so.6", O_RDONLY|O_CLOEXEC) = 3
1234 openat(AT_FDCWD, "/tmp/output.txt", O_WRONLY|O_CREAT|O_TRUNC, 0666) = 4
1234 openat(AT_FDCWD, "/usr/lib/locale/locale-archive", O_RDONLY|O_CLOEXEC) = 3
1234 exit_group(0)                   = ?
1234 +++ exited with 0 +++
"""

SAMPLE_NETWORK = """\
5678 socket(AF_INET, SOCK_STREAM|SOCK_CLOEXEC, IPPROTO_TCP) = 3
5678 connect(3, {sa_family=AF_INET, sin_port=htons(443), sin_addr=inet_addr("93.184.216.34")}, 16) = 0
5678 socket(AF_INET6, SOCK_STREAM, IPPROTO_TCP) = 4
5678 connect(4, {sa_family=AF_INET6, sin6_port=htons(80), sin6_flowinfo=htonl(0), inet_pton(AF_INET6, "2606:4700::1"), sin6_scope_id=0}, 28) = 0
5678 socket(AF_UNIX, SOCK_STREAM|SOCK_CLOEXEC|SOCK_NONBLOCK, 0) = 5
5678 connect(5, {sa_family=AF_UNIX, sun_path="/var/run/nscd/socket"}, 110) = -1 ENOENT
"""

SAMPLE_PROCESS = """\
9999 clone3({flags=CLONE_VM|CLONE_FS|CLONE_FILES}, 88) = 10000
9999 clone(child_stack=NULL, flags=CLONE_CHILD_CLEARTID|SIGCHLD) = 10001
10000 execve("/usr/bin/python3", ["python3", "script.py"], 0x7fff /* 40 vars */) = 0
"""


class TestParseStraceOutput:
    def test_returns_strace_profile(self) -> None:
        result = parse_strace_output(SAMPLE_STRACE)
        assert isinstance(result, StraceProfile)

    def test_extracts_read_files(self) -> None:
        result = parse_strace_output(SAMPLE_STRACE)
        assert "/etc/ld.so.cache" in result.files_read
        assert "/lib/x86_64-linux-gnu/libc.so.6" in result.files_read
        assert "/usr/lib/locale/locale-archive" in result.files_read

    def test_extracts_write_files(self) -> None:
        result = parse_strace_output(SAMPLE_STRACE)
        assert "/tmp/output.txt" in result.files_written

    def test_write_files_excluded_from_reads(self) -> None:
        result = parse_strace_output(SAMPLE_STRACE)
        assert "/tmp/output.txt" not in result.files_read

    def test_access_check_failed_not_in_reads(self) -> None:
        # access("/etc/ld.so.preload", R_OK) = -1 should NOT be in files_read
        result = parse_strace_output(SAMPLE_STRACE)
        assert "/etc/ld.so.preload" not in result.files_read

    def test_executables(self) -> None:
        result = parse_strace_output(SAMPLE_STRACE)
        assert "/usr/bin/echo" in result.executables

    def test_ipv4_network_connections(self) -> None:
        result = parse_strace_output(SAMPLE_NETWORK)
        assert ("93.184.216.34", 443) in result.network_connections

    def test_ipv6_network_connections(self) -> None:
        result = parse_strace_output(SAMPLE_NETWORK)
        assert ("2606:4700::1", 80) in result.network_connections

    def test_ignores_unix_sockets(self) -> None:
        result = parse_strace_output(SAMPLE_NETWORK)
        # AF_UNIX connect should not appear in network_connections
        addrs = {addr for addr, _ in result.network_connections}
        assert "/var/run/nscd/socket" not in addrs
        assert len(result.network_connections) == 2

    def test_failed_unix_connect_ignored(self) -> None:
        # The failed AF_UNIX connect (-1 ENOENT) should not be counted
        result = parse_strace_output(SAMPLE_NETWORK)
        assert len(result.network_connections) == 2

    def test_process_spawns_counted(self) -> None:
        result = parse_strace_output(SAMPLE_PROCESS)
        assert result.process_spawns == 2

    def test_clone_variants_counted(self) -> None:
        # clone3 and clone both counted
        result = parse_strace_output(SAMPLE_PROCESS)
        assert result.process_spawns == 2

    def test_executables_from_multiple_pids(self) -> None:
        result = parse_strace_output(SAMPLE_PROCESS)
        assert "/usr/bin/python3" in result.executables

    def test_empty_input(self) -> None:
        result = parse_strace_output("")
        assert result.files_read == set()
        assert result.files_written == set()
        assert result.network_connections == set()
        assert result.executables == set()
        assert result.process_spawns == 0


class TestStraceProfile:
    def test_has_network_true(self) -> None:
        profile = StraceProfile(network_connections={("1.2.3.4", 443)})
        assert profile.has_network is True

    def test_has_network_false(self) -> None:
        profile = StraceProfile()
        assert profile.has_network is False

    def test_has_writes_true(self) -> None:
        profile = StraceProfile(files_written={"/tmp/out.txt"})
        assert profile.has_writes is True

    def test_has_writes_false(self) -> None:
        profile = StraceProfile()
        assert profile.has_writes is False

    def test_has_spawns_true(self) -> None:
        profile = StraceProfile(process_spawns=1)
        assert profile.has_spawns is True

    def test_has_spawns_false(self) -> None:
        profile = StraceProfile()
        assert profile.has_spawns is False

    def test_read_directories(self) -> None:
        profile = StraceProfile(files_read={"/etc/ld.so.cache", "/lib/libc.so.6", "/etc/passwd"})
        dirs = profile.read_directories()
        assert "/etc" in dirs
        assert "/lib" in dirs

    def test_read_directories_deduped(self) -> None:
        profile = StraceProfile(files_read={"/etc/foo", "/etc/bar"})
        dirs = profile.read_directories()
        assert dirs == {"/etc"}

    def test_write_directories(self) -> None:
        profile = StraceProfile(files_written={"/tmp/output.txt", "/var/log/app.log"})
        dirs = profile.write_directories()
        assert "/tmp" in dirs
        assert "/var/log" in dirs

    def test_to_dict_keys(self) -> None:
        profile = StraceProfile(
            files_read={"/etc/foo"},
            files_written={"/tmp/bar"},
            network_connections={("1.2.3.4", 80)},
            executables={"/usr/bin/python3"},
            process_spawns=1,
        )
        d = profile.to_dict()
        assert "files_read" in d
        assert "files_written" in d
        assert "network_connections" in d
        assert "executables" in d
        assert "process_spawns" in d
        assert d["process_spawns"] == 1

    def test_to_dict_lists(self) -> None:
        profile = StraceProfile(files_read={"/etc/foo", "/etc/bar"})
        d = profile.to_dict()
        assert isinstance(d["files_read"], list)
        assert sorted(d["files_read"]) == ["/etc/bar", "/etc/foo"]

    def test_to_dict_network_connections(self) -> None:
        profile = StraceProfile(network_connections={("1.2.3.4", 443)})
        d = profile.to_dict()
        assert isinstance(d["network_connections"], list)
        assert ["1.2.3.4", 443] in d["network_connections"] or ("1.2.3.4", 443) in [
            tuple(x) for x in d["network_connections"]
        ]
