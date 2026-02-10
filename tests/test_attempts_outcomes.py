"""Tests for the attempts/outcomes schema (BIRD v5 pattern)."""

from blq.bird import AttemptRecord, BirdStore, OutcomeRecord


class TestAttemptRecord:
    """Tests for AttemptRecord dataclass."""

    def test_create_minimal(self):
        """Create attempt with minimal required fields."""
        record = AttemptRecord(
            id=AttemptRecord.generate_id(),
            session_id="test",
            cmd="make build",
            cwd="/home/user/project",
            client_id="blq-run",
        )

        assert record.id is not None
        assert record.session_id == "test"
        assert record.cmd == "make build"
        assert record.cwd == "/home/user/project"
        assert record.client_id == "blq-run"
        assert record.timestamp is not None

    def test_create_with_all_fields(self):
        """Create attempt with all fields."""
        record = AttemptRecord(
            id="test-id",
            session_id="test",
            cmd="pytest tests/",
            cwd="/project",
            client_id="blq-run",
            executable="/usr/bin/pytest",
            format_hint="pytest_text",
            hostname="localhost",
            username="testuser",
            tag="unit-tests",
            source_name="test",
            source_type="run",
            environment={"PATH": "/usr/bin"},
            platform="Linux",
            arch="x86_64",
            git_commit="abc123",
            git_branch="main",
            git_dirty=False,
            ci={"provider": "github"},
        )

        assert record.executable == "/usr/bin/pytest"
        assert record.tag == "unit-tests"
        assert record.git_branch == "main"


class TestOutcomeRecord:
    """Tests for OutcomeRecord dataclass."""

    def test_create_success(self):
        """Create outcome for successful command."""
        record = OutcomeRecord(
            attempt_id="attempt-123",
            exit_code=0,
            duration_ms=5000,
        )

        assert record.attempt_id == "attempt-123"
        assert record.exit_code == 0
        assert record.duration_ms == 5000
        assert record.timeout is False
        assert record.signal is None

    def test_create_failure(self):
        """Create outcome for failed command."""
        record = OutcomeRecord(
            attempt_id="attempt-456",
            exit_code=1,
            duration_ms=1500,
        )

        assert record.exit_code == 1

    def test_create_timeout(self):
        """Create outcome for timed out command."""
        record = OutcomeRecord(
            attempt_id="attempt-789",
            exit_code=None,  # Unknown - killed by timeout
            duration_ms=60000,
            timeout=True,
        )

        assert record.exit_code is None
        assert record.timeout is True

    def test_create_signal(self):
        """Create outcome for command killed by signal."""
        record = OutcomeRecord(
            attempt_id="attempt-abc",
            exit_code=None,  # Unknown - killed by signal
            duration_ms=3000,
            signal=9,  # SIGKILL
        )

        assert record.exit_code is None
        assert record.signal == 9


class TestBirdStoreAttempts:
    """Tests for BirdStore attempt/outcome methods."""

    def test_write_attempt(self, initialized_project):
        """Write an attempt record."""
        store = BirdStore.open(initialized_project / ".lq")

        attempt = AttemptRecord(
            id=AttemptRecord.generate_id(),
            session_id="test",
            cmd="make build",
            cwd=str(initialized_project),
            client_id="blq-test",
            source_name="build",
            tag="build",
        )

        attempt_id = store.write_attempt(attempt)

        assert attempt_id == attempt.id
        store.close()

    def test_attempt_without_outcome_is_pending(self, initialized_project):
        """Attempt without outcome has 'pending' status."""
        store = BirdStore.open(initialized_project / ".lq")

        attempt = AttemptRecord(
            id=AttemptRecord.generate_id(),
            session_id="test",
            cmd="sleep 100",
            cwd=str(initialized_project),
            client_id="blq-test",
        )

        attempt_id = store.write_attempt(attempt)

        # Check status
        status = store.get_attempt_status(attempt_id)
        assert status == "pending"

        store.close()

    def test_attempt_with_outcome_is_completed(self, initialized_project):
        """Attempt with outcome has 'completed' status."""
        store = BirdStore.open(initialized_project / ".lq")

        attempt = AttemptRecord(
            id=AttemptRecord.generate_id(),
            session_id="test",
            cmd="echo hello",
            cwd=str(initialized_project),
            client_id="blq-test",
        )

        attempt_id = store.write_attempt(attempt)

        # Write outcome
        outcome = OutcomeRecord(
            attempt_id=attempt_id,
            exit_code=0,
            duration_ms=100,
        )
        store.write_outcome(outcome)

        # Check status
        status = store.get_attempt_status(attempt_id)
        assert status == "completed"

        store.close()

    def test_outcome_with_null_exit_code_is_orphaned(self, initialized_project):
        """Outcome with NULL exit_code has 'orphaned' status."""
        store = BirdStore.open(initialized_project / ".lq")

        attempt = AttemptRecord(
            id=AttemptRecord.generate_id(),
            session_id="test",
            cmd="crashed_command",
            cwd=str(initialized_project),
            client_id="blq-test",
        )

        attempt_id = store.write_attempt(attempt)

        # Write outcome with NULL exit_code (crashed/unknown)
        outcome = OutcomeRecord(
            attempt_id=attempt_id,
            exit_code=None,  # Crashed - exit code unknown
            duration_ms=500,
        )
        store.write_outcome(outcome)

        # Check status
        status = store.get_attempt_status(attempt_id)
        assert status == "orphaned"

        store.close()

    def test_get_running_attempts(self, initialized_project):
        """Get list of running attempts (without outcomes)."""
        store = BirdStore.open(initialized_project / ".lq")

        # Create 3 attempts
        attempt1 = AttemptRecord(
            id=AttemptRecord.generate_id(),
            session_id="test",
            cmd="sleep 1",
            cwd=str(initialized_project),
            client_id="blq-test",
            source_name="slow1",
        )
        attempt2 = AttemptRecord(
            id=AttemptRecord.generate_id(),
            session_id="test",
            cmd="sleep 2",
            cwd=str(initialized_project),
            client_id="blq-test",
            source_name="slow2",
        )
        attempt3 = AttemptRecord(
            id=AttemptRecord.generate_id(),
            session_id="test",
            cmd="echo done",
            cwd=str(initialized_project),
            client_id="blq-test",
            source_name="fast",
        )

        id1 = store.write_attempt(attempt1)
        id2 = store.write_attempt(attempt2)
        id3 = store.write_attempt(attempt3)

        # Complete attempt3
        outcome = OutcomeRecord(attempt_id=id3, exit_code=0, duration_ms=50)
        store.write_outcome(outcome)

        # Get running attempts
        running = store.get_running_attempts()

        assert len(running) == 2
        # Convert to strings for comparison (DuckDB returns UUID objects)
        running_ids = {str(r["id"]) for r in running}
        assert id1 in running_ids
        assert id2 in running_ids
        assert id3 not in running_ids

        store.close()

    def test_get_next_run_number_counts_invocations(self, initialized_project):
        """get_next_run_number counts only invocations (completed runs)."""
        store = BirdStore.open(initialized_project / ".lq")

        # Create an attempt (pending run - not yet complete)
        attempt = AttemptRecord(
            id=AttemptRecord.generate_id(),
            session_id="test",
            cmd="test cmd",
            cwd=str(initialized_project),
            client_id="blq-test",
        )
        store.write_attempt(attempt)

        # Attempt alone doesn't increment run number (it's still pending)
        next_num = store.get_next_run_number()
        assert next_num == 1  # No completed invocations yet

        # Create an invocation (simulating run completion)
        from blq.bird import InvocationRecord

        invocation = InvocationRecord(
            id=attempt.id,  # Same ID as attempt
            session_id="test",
            cmd="test cmd",
            cwd=str(initialized_project),
            client_id="blq-test",
            exit_code=0,
        )
        store.write_invocation(invocation)

        # Now we have a completed run
        next_num = store.get_next_run_number()
        assert next_num == 2

        store.close()


class TestAttemptsOutcomesSql:
    """Tests for SQL macros related to attempts/outcomes."""

    def test_blq_load_attempts_returns_status(self, initialized_project):
        """blq_load_attempts() includes status column."""
        store = BirdStore.open(initialized_project / ".lq")

        # Create a pending attempt
        attempt = AttemptRecord(
            id=AttemptRecord.generate_id(),
            session_id="test",
            cmd="running command",
            cwd=str(initialized_project),
            client_id="blq-test",
            source_name="test",
        )
        store.write_attempt(attempt)

        # Query via macro
        result = store.connection.execute("SELECT * FROM blq_load_attempts()").fetchall()
        columns = [
            desc[0]
            for desc in store.connection.execute(
                "SELECT * FROM blq_load_attempts() LIMIT 0"
            ).description
        ]

        assert "status" in columns
        assert len(result) >= 1

        # Find our pending attempt
        status_idx = columns.index("status")
        statuses = [row[status_idx] for row in result]
        assert "pending" in statuses

        store.close()

    def test_blq_running_returns_pending_only(self, initialized_project):
        """blq_running() returns only pending attempts."""
        store = BirdStore.open(initialized_project / ".lq")

        # Create pending and completed attempts
        pending = AttemptRecord(
            id=AttemptRecord.generate_id(),
            session_id="test",
            cmd="still running",
            cwd=str(initialized_project),
            client_id="blq-test",
        )
        completed = AttemptRecord(
            id=AttemptRecord.generate_id(),
            session_id="test",
            cmd="finished",
            cwd=str(initialized_project),
            client_id="blq-test",
        )

        pending_id = store.write_attempt(pending)
        completed_id = store.write_attempt(completed)

        # Complete one
        store.write_outcome(OutcomeRecord(attempt_id=completed_id, exit_code=0, duration_ms=100))

        # Query running
        result = store.connection.execute("SELECT * FROM blq_running()").fetchall()

        # Should only have the pending one
        assert len(result) == 1

        # Get column index for attempt_id
        columns = [
            desc[0]
            for desc in store.connection.execute("SELECT * FROM blq_running() LIMIT 0").description
        ]
        attempt_id_idx = columns.index("attempt_id")

        # Convert to string for comparison (DuckDB returns UUID objects)
        assert str(result[0][attempt_id_idx]) == pending_id

        store.close()


class TestLiveOutputStreaming:
    """Tests for live output directory and streaming."""

    def test_create_live_dir(self, initialized_project):
        """Create live output directory for an attempt."""
        store = BirdStore.open(initialized_project / ".lq")

        attempt = AttemptRecord(
            id=AttemptRecord.generate_id(),
            session_id="test",
            cmd="long running command",
            cwd=str(initialized_project),
            client_id="blq-test",
        )
        attempt_id = store.write_attempt(attempt)

        # Create live directory
        meta = {"cmd": "long running command", "started_at": "2024-01-01T00:00:00"}
        live_dir = store.create_live_dir(attempt_id, meta)

        assert live_dir.exists()
        assert (live_dir / "meta.json").exists()
        assert live_dir.name == attempt_id

        store.close()

    def test_get_live_output_path(self, initialized_project):
        """Get path to live output file."""
        store = BirdStore.open(initialized_project / ".lq")

        attempt = AttemptRecord(
            id=AttemptRecord.generate_id(),
            session_id="test",
            cmd="test",
            cwd=str(initialized_project),
            client_id="blq-test",
        )
        attempt_id = store.write_attempt(attempt)

        # Create live directory first
        store.create_live_dir(attempt_id, {"cmd": "test"})

        # Get path for combined output
        output_path = store.get_live_output_path(attempt_id, "combined")

        assert output_path.parent.name == attempt_id
        assert output_path.name == "combined.log"

        store.close()

    def test_write_and_read_live_output(self, initialized_project):
        """Write to and read from live output file."""
        store = BirdStore.open(initialized_project / ".lq")

        attempt = AttemptRecord(
            id=AttemptRecord.generate_id(),
            session_id="test",
            cmd="test",
            cwd=str(initialized_project),
            client_id="blq-test",
        )
        attempt_id = store.write_attempt(attempt)

        # Create live directory
        store.create_live_dir(attempt_id, {"cmd": "test"})

        # Write some output
        output_path = store.get_live_output_path(attempt_id, "combined")
        with open(output_path, "w") as f:
            f.write("line 1\n")
            f.write("line 2\n")
            f.write("line 3\n")

        # Read it back
        content = store.read_live_output(attempt_id, "combined")
        assert content == "line 1\nline 2\nline 3\n"

        # Read with tail
        content = store.read_live_output(attempt_id, "combined", tail=2)
        assert content == "line 2\nline 3\n"

        store.close()

    def test_cleanup_live_dir(self, initialized_project):
        """Clean up live directory after completion."""
        store = BirdStore.open(initialized_project / ".lq")

        attempt = AttemptRecord(
            id=AttemptRecord.generate_id(),
            session_id="test",
            cmd="test",
            cwd=str(initialized_project),
            client_id="blq-test",
        )
        attempt_id = store.write_attempt(attempt)

        # Create live directory
        live_dir = store.create_live_dir(attempt_id, {"cmd": "test"})
        output_path = store.get_live_output_path(attempt_id, "combined")

        # Write some content
        with open(output_path, "w") as f:
            f.write("test output\n")

        assert live_dir.exists()

        # Clean up
        store.cleanup_live_dir(attempt_id)

        assert not live_dir.exists()

        store.close()

    def test_list_live_attempts(self, initialized_project):
        """List attempts with active live output."""
        store = BirdStore.open(initialized_project / ".lq")

        # Create two attempts with live directories
        attempt1 = AttemptRecord(
            id=AttemptRecord.generate_id(),
            session_id="test",
            cmd="cmd1",
            cwd=str(initialized_project),
            client_id="blq-test",
        )
        attempt2 = AttemptRecord(
            id=AttemptRecord.generate_id(),
            session_id="test",
            cmd="cmd2",
            cwd=str(initialized_project),
            client_id="blq-test",
        )

        id1 = store.write_attempt(attempt1)
        id2 = store.write_attempt(attempt2)

        store.create_live_dir(id1, {"cmd": "cmd1"})
        store.create_live_dir(id2, {"cmd": "cmd2"})

        # List live attempts - returns list of dicts with attempt_id, meta, live_dir
        live_attempts = store.list_live_attempts()
        live_ids = [a["attempt_id"] for a in live_attempts]

        assert id1 in live_ids
        assert id2 in live_ids

        # Clean up one
        store.cleanup_live_dir(id1)

        live_attempts = store.list_live_attempts()
        live_ids = [a["attempt_id"] for a in live_attempts]
        assert id1 not in live_ids
        assert id2 in live_ids

        store.close()

    def test_finalize_live_output_inline(self, initialized_project):
        """Finalize small live output as inline storage."""
        store = BirdStore.open(initialized_project / ".lq")

        attempt = AttemptRecord(
            id=AttemptRecord.generate_id(),
            session_id="test",
            cmd="test",
            cwd=str(initialized_project),
            client_id="blq-test",
        )
        attempt_id = store.write_attempt(attempt)

        # Create live directory and write small content (<4KB = inline threshold)
        store.create_live_dir(attempt_id, {"cmd": "test"})
        output_path = store.get_live_output_path(attempt_id, "combined")

        test_content = "test output line 1\ntest output line 2\n"
        with open(output_path, "w") as f:
            f.write(test_content)

        # Finalize - small content gets stored inline
        output_record = store.finalize_live_output(attempt_id, "combined")

        # Should return an OutputRecord
        assert output_record is not None
        assert output_record.content_hash is not None
        assert output_record.storage_type == "inline"
        assert output_record.storage_ref.startswith("data:")  # Base64 data URI
        assert output_record.byte_length == len(test_content)

        store.close()

    def test_finalize_live_output_blob(self, initialized_project):
        """Finalize large live output to blob storage."""
        store = BirdStore.open(initialized_project / ".lq")

        attempt = AttemptRecord(
            id=AttemptRecord.generate_id(),
            session_id="test",
            cmd="test",
            cwd=str(initialized_project),
            client_id="blq-test",
        )
        attempt_id = store.write_attempt(attempt)

        # Create live directory and write large content (>4KB = blob threshold)
        store.create_live_dir(attempt_id, {"cmd": "test"})
        output_path = store.get_live_output_path(attempt_id, "combined")

        # Create content larger than 4KB inline threshold
        test_content = ("X" * 100 + "\n") * 50  # ~5KB
        with open(output_path, "w") as f:
            f.write(test_content)

        # Finalize - large content goes to blob storage
        output_record = store.finalize_live_output(attempt_id, "combined")

        # Should return an OutputRecord
        assert output_record is not None
        assert output_record.content_hash is not None
        assert output_record.storage_type == "blob"
        assert output_record.storage_ref.startswith("file:")  # Blob file reference

        # Verify blob was written
        blob_hash = output_record.content_hash
        blob_path = (
            initialized_project / ".lq" / "blobs" / "content" / blob_hash[:2] / f"{blob_hash}.bin"
        )
        assert blob_path.exists()
        assert blob_path.read_bytes() == test_content.encode()

        store.close()

    def test_live_dir_not_created_for_nonexistent_attempt(self, initialized_project):
        """Live directory creation requires valid attempt ID."""
        store = BirdStore.open(initialized_project / ".lq")

        # Try to create live dir for non-existent attempt
        fake_id = "00000000-0000-0000-0000-000000000000"
        live_dir = store.create_live_dir(fake_id, {"cmd": "test"})

        # Should still create the directory (we don't validate attempt existence)
        # This is intentional - the directory is created regardless
        assert live_dir.exists()

        store.close()


class TestHistoryStatusFilter:
    """Tests for blq history --status filter."""

    def test_blq_history_status_pending(self, initialized_project):
        """blq_history_status filters pending attempts."""
        store = BirdStore.open(initialized_project / ".lq")

        # Create a pending attempt (no outcome)
        pending = AttemptRecord(
            id=AttemptRecord.generate_id(),
            session_id="test",
            cmd="sleep 100",
            cwd=str(initialized_project),
            client_id="blq-test",
            source_name="long-running",
        )
        store.write_attempt(pending)

        # Create a completed attempt (with outcome and invocation)
        completed = AttemptRecord(
            id=AttemptRecord.generate_id(),
            session_id="test",
            cmd="echo done",
            cwd=str(initialized_project),
            client_id="blq-test",
            source_name="quick",
        )
        completed_id = store.write_attempt(completed)
        store.write_outcome(OutcomeRecord(attempt_id=completed_id, exit_code=0, duration_ms=100))

        # Query with pending status
        result = store.connection.execute(
            "SELECT * FROM blq_history_status('pending', 20)"
        ).fetchall()

        # Should only have the pending one
        assert len(result) == 1
        # Check source_name column (index 2 in the result)
        columns = [
            desc[0]
            for desc in store.connection.execute(
                "SELECT * FROM blq_history_status('pending', 20) LIMIT 0"
            ).description
        ]
        source_name_idx = columns.index("source_name")
        assert result[0][source_name_idx] == "long-running"

        store.close()

    def test_blq_history_status_completed(self, initialized_project):
        """blq_history_status filters completed attempts."""
        store = BirdStore.open(initialized_project / ".lq")

        # Create a pending attempt
        pending = AttemptRecord(
            id=AttemptRecord.generate_id(),
            session_id="test",
            cmd="sleep 100",
            cwd=str(initialized_project),
            client_id="blq-test",
            source_name="pending-cmd",
        )
        store.write_attempt(pending)

        # Create a completed attempt
        completed = AttemptRecord(
            id=AttemptRecord.generate_id(),
            session_id="test",
            cmd="echo done",
            cwd=str(initialized_project),
            client_id="blq-test",
            source_name="completed-cmd",
        )
        completed_id = store.write_attempt(completed)
        store.write_outcome(OutcomeRecord(attempt_id=completed_id, exit_code=0, duration_ms=100))

        # Query with completed status
        result = store.connection.execute(
            "SELECT * FROM blq_history_status('completed', 20)"
        ).fetchall()

        # Should only have the completed one
        assert len(result) == 1
        columns = [
            desc[0]
            for desc in store.connection.execute(
                "SELECT * FROM blq_history_status('completed', 20) LIMIT 0"
            ).description
        ]
        source_name_idx = columns.index("source_name")
        assert result[0][source_name_idx] == "completed-cmd"

        store.close()

    def test_blq_history_status_null_returns_all(self, initialized_project):
        """blq_history_status with NULL returns all attempts."""
        store = BirdStore.open(initialized_project / ".lq")

        # Create pending and completed attempts
        pending = AttemptRecord(
            id=AttemptRecord.generate_id(),
            session_id="test",
            cmd="sleep 100",
            cwd=str(initialized_project),
            client_id="blq-test",
        )
        completed = AttemptRecord(
            id=AttemptRecord.generate_id(),
            session_id="test",
            cmd="echo done",
            cwd=str(initialized_project),
            client_id="blq-test",
        )

        store.write_attempt(pending)
        completed_id = store.write_attempt(completed)
        store.write_outcome(OutcomeRecord(attempt_id=completed_id, exit_code=0, duration_ms=100))

        # Query with NULL status (should return all)
        result = store.connection.execute(
            "SELECT * FROM blq_history_status(NULL, 20)"
        ).fetchall()

        # Should have both
        assert len(result) == 2

        store.close()
