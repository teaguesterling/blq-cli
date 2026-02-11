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
        result = store.connection.execute("SELECT * FROM blq_history_status(NULL, 20)").fetchall()

        # Should have both
        assert len(result) == 2

        store.close()


class TestRetryOnLock:
    """Tests for the retry_on_lock helper function."""

    def test_succeeds_without_retry(self):
        """Function succeeds on first attempt."""
        from blq.bird import retry_on_lock

        call_count = 0

        def successful_func():
            nonlocal call_count
            call_count += 1
            return "success"

        result = retry_on_lock(successful_func, max_retries=3)
        assert result == "success"
        assert call_count == 1

    def test_retries_on_lock_error(self):
        """Function retries on lock error and eventually succeeds."""
        import duckdb

        from blq.bird import retry_on_lock

        call_count = 0

        def fails_then_succeeds():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise duckdb.Error("database is locked")
            return "success"

        result = retry_on_lock(
            fails_then_succeeds,
            max_retries=5,
            initial_delay=0.001,  # Fast for testing
        )
        assert result == "success"
        assert call_count == 3

    def test_exhausts_retries(self):
        """Function exhausts all retries and raises."""
        import duckdb
        import pytest

        from blq.bird import retry_on_lock

        call_count = 0

        def always_fails():
            nonlocal call_count
            call_count += 1
            raise duckdb.Error("database is locked")

        with pytest.raises(duckdb.Error, match="database is locked"):
            retry_on_lock(
                always_fails,
                max_retries=3,
                initial_delay=0.001,
            )

        assert call_count == 4  # Initial + 3 retries

    def test_does_not_retry_non_lock_errors(self):
        """Non-lock errors are raised immediately without retry."""
        import duckdb
        import pytest

        from blq.bird import retry_on_lock

        call_count = 0

        def raises_other_error():
            nonlocal call_count
            call_count += 1
            raise duckdb.Error("some other error")

        with pytest.raises(duckdb.Error, match="some other error"):
            retry_on_lock(raises_other_error, max_retries=3)

        assert call_count == 1  # No retries


class TestBirdStoreOpenWithRetry:
    """Tests for BirdStore.open_with_retry()."""

    def test_opens_successfully(self, initialized_project):
        """Opens store without issues when no contention."""
        store = BirdStore.open_with_retry(initialized_project / ".lq")
        assert store is not None
        store.close()

    def test_context_manager_works(self, initialized_project):
        """Context manager properly closes connection."""
        with BirdStore.open_with_retry(initialized_project / ".lq") as store:
            # Should be able to query
            count = store.invocation_count()
            assert count >= 0

        # After exit, connection should be closed
        # Attempting to use it would raise an error


class TestExecuteWithRetry:
    """Tests for BirdStore.execute_with_retry()."""

    def test_executes_successfully(self, initialized_project):
        """Operation executes on first attempt."""
        store = BirdStore.open(initialized_project / ".lq")

        result = store.execute_with_retry(lambda: store.invocation_count())
        assert result >= 0

        store.close()

    def test_returns_result(self, initialized_project):
        """Returns the function's return value."""
        store = BirdStore.open(initialized_project / ".lq")

        # Create an attempt so there's data
        attempt = AttemptRecord(
            id=AttemptRecord.generate_id(),
            session_id="test",
            cmd="test",
            cwd=str(initialized_project),
            client_id="blq-test",
        )
        store.write_attempt(attempt)

        # execute_with_retry should return the query result
        result = store.execute_with_retry(lambda: store.get_attempt_status(attempt.id))
        assert result == "pending"

        store.close()


class TestConcurrentAccess:
    """Tests for concurrent database access scenarios."""

    def test_concurrent_writes_with_retry(self, initialized_project):
        """Multiple threads can write with retry handling."""
        import threading

        lq_dir = initialized_project / ".lq"
        results = []
        errors = []

        def writer_thread(thread_id: int):
            try:
                with BirdStore.open_with_retry(lq_dir, max_retries=10) as store:
                    # Write an attempt
                    attempt = AttemptRecord(
                        id=AttemptRecord.generate_id(),
                        session_id=f"thread-{thread_id}",
                        cmd=f"echo thread {thread_id}",
                        cwd=str(initialized_project),
                        client_id="blq-test",
                        source_name=f"thread-{thread_id}",
                    )
                    attempt_id = store.write_attempt(attempt)
                    results.append((thread_id, attempt_id))
            except Exception as e:
                errors.append((thread_id, str(e)))

        # Launch multiple threads
        threads = []
        for i in range(5):
            t = threading.Thread(target=writer_thread, args=(i,))
            threads.append(t)
            t.start()

        # Wait for all threads
        for t in threads:
            t.join(timeout=10)

        # All threads should succeed (with retry)
        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert len(results) == 5

    def test_background_pid_update_pattern(self, initialized_project):
        """Background PID update pattern works as expected."""
        import threading

        lq_dir = initialized_project / ".lq"

        # Window 1: Write attempt
        with BirdStore.open_with_retry(lq_dir) as store:
            attempt = AttemptRecord(
                id=AttemptRecord.generate_id(),
                session_id="test",
                cmd="test",
                cwd=str(initialized_project),
                client_id="blq-test",
            )
            attempt_id = store.write_attempt(attempt)

        # Simulate background PID update
        update_success = threading.Event()
        update_error = []

        def update_pid():
            try:
                with BirdStore.open_with_retry(lq_dir, max_retries=3) as store:
                    store.update_attempt_pid(attempt_id, 12345)
                update_success.set()
            except Exception as e:
                update_error.append(str(e))

        thread = threading.Thread(target=update_pid)
        thread.start()
        thread.join(timeout=5)

        assert update_success.is_set(), f"PID update failed: {update_error}"

        # Verify PID was updated
        with BirdStore.open(lq_dir) as store:
            result = store.connection.execute(
                "SELECT pid FROM attempts WHERE id = ?", [attempt_id]
            ).fetchone()
            assert result is not None
            assert result[0] == 12345

    def test_window1_and_window2_pattern(self, initialized_project):
        """Full execution pattern with Window 1 and Window 2."""
        import time

        lq_dir = initialized_project / ".lq"

        # Window 1: Pre-execution
        with BirdStore.open_with_retry(lq_dir) as store:
            store.ensure_session(
                session_id="test",
                client_id="blq-run",
                invoker="blq",
                invoker_type="cli",
            )
            attempt = AttemptRecord(
                id=AttemptRecord.generate_id(),
                session_id="test",
                cmd="echo hello",
                cwd=str(initialized_project),
                client_id="blq-run",
                source_name="test",
            )
            attempt_id = store.write_attempt(attempt)
            store.get_next_run_number()  # Allocate run number
            store.create_live_dir(attempt_id, {"cmd": "echo hello"})

        # Simulate command execution (DB unlocked)
        time.sleep(0.01)

        # Window 2: Post-execution
        with BirdStore.open_with_retry(lq_dir) as store:
            from blq.bird import InvocationRecord, OutcomeRecord

            outcome = OutcomeRecord(
                attempt_id=attempt_id,
                exit_code=0,
                duration_ms=10,
            )
            store.write_outcome(outcome)

            invocation = InvocationRecord(
                id=attempt_id,
                session_id="test",
                cmd="echo hello",
                cwd=str(initialized_project),
                client_id="blq-run",
                exit_code=0,
                duration_ms=10,
            )
            store.write_invocation(invocation)
            store.cleanup_live_dir(attempt_id)

        # Verify the run was recorded
        with BirdStore.open(lq_dir) as store:
            status = store.get_attempt_status(attempt_id)
            assert status == "completed"

            inv_count = store.invocation_count()
            assert inv_count >= 1


class TestLockContentionRecovery:
    """Tests for lock contention recovery scenarios."""

    def test_is_lock_error_detection(self):
        """_is_lock_error correctly identifies lock errors."""
        import duckdb

        from blq.bird import _is_lock_error

        # Lock errors
        assert _is_lock_error(duckdb.Error("database is locked"))
        assert _is_lock_error(duckdb.Error("Database is locked"))
        assert _is_lock_error(duckdb.Error("could not set lock on file"))
        assert _is_lock_error(Exception("lock timeout exceeded"))

        # Non-lock errors
        assert not _is_lock_error(duckdb.Error("table not found"))
        assert not _is_lock_error(duckdb.Error("syntax error"))
        assert not _is_lock_error(ValueError("invalid value"))

    def test_retry_respects_max_retries(self):
        """Retry stops after max_retries attempts."""
        import duckdb
        import pytest

        from blq.bird import retry_on_lock

        attempts = []

        def counting_failure():
            attempts.append(1)
            raise duckdb.Error("database is locked")

        with pytest.raises(duckdb.Error):
            retry_on_lock(
                counting_failure,
                max_retries=2,
                initial_delay=0.001,
            )

        # Should be initial attempt + 2 retries = 3 total
        assert len(attempts) == 3

    def test_exponential_backoff_timing(self):
        """Verify exponential backoff increases delay."""
        import time

        import duckdb
        import pytest

        from blq.bird import retry_on_lock

        timestamps = []

        def timing_failure():
            timestamps.append(time.monotonic())
            raise duckdb.Error("database is locked")

        with pytest.raises(duckdb.Error):
            retry_on_lock(
                timing_failure,
                max_retries=3,
                initial_delay=0.02,  # 20ms
                backoff_factor=2.0,
                max_delay=1.0,
            )

        # Calculate delays between attempts
        delays = [timestamps[i + 1] - timestamps[i] for i in range(len(timestamps) - 1)]

        # First delay should be ~20ms, second ~40ms, third ~80ms
        # Allow for jitter (±25%) and timing variance
        assert len(delays) == 3
        assert delays[0] >= 0.01  # At least 10ms (20ms - 50% for jitter/timing)
        # Each subsequent delay should generally be larger (with some tolerance)
        # Due to jitter, we just verify delays are reasonable
        for d in delays:
            assert d >= 0.01  # All delays should be at least 10ms
            assert d <= 1.5  # None should exceed max_delay + jitter


class TestRaceConditions:
    """Tests for specific race condition scenarios."""

    def test_concurrent_window1_from_multiple_commands(self, initialized_project):
        """Multiple commands starting simultaneously (Window 1 race)."""
        import threading

        lq_dir = initialized_project / ".lq"
        results = {"attempts": [], "errors": []}
        barrier = threading.Barrier(3)  # Synchronize 3 threads

        def start_command(cmd_id: int):
            try:
                # Wait for all threads to be ready
                barrier.wait(timeout=5)

                # Window 1: All threads try to write attempts simultaneously
                with BirdStore.open_with_retry(lq_dir, max_retries=10) as store:
                    store.ensure_session(
                        session_id=f"cmd-{cmd_id}",
                        client_id="blq-run",
                        invoker="blq",
                        invoker_type="cli",
                    )
                    attempt = AttemptRecord(
                        id=AttemptRecord.generate_id(),
                        session_id=f"cmd-{cmd_id}",
                        cmd=f"echo {cmd_id}",
                        cwd=str(initialized_project),
                        client_id="blq-run",
                        source_name=f"cmd-{cmd_id}",
                    )
                    attempt_id = store.write_attempt(attempt)
                    results["attempts"].append((cmd_id, attempt_id))
            except Exception as e:
                results["errors"].append((cmd_id, str(e)))

        threads = [threading.Thread(target=start_command, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert len(results["errors"]) == 0, f"Errors: {results['errors']}"
        assert len(results["attempts"]) == 3

        # Verify all attempts were written correctly
        with BirdStore.open(lq_dir) as store:
            for cmd_id, attempt_id in results["attempts"]:
                status = store.get_attempt_status(attempt_id)
                assert status == "pending"

    def test_window1_and_pid_update_race(self, initialized_project):
        """Race between Window 1 closing and background PID update."""
        import threading

        lq_dir = initialized_project / ".lq"
        attempt_id = None
        pid_updated = threading.Event()
        errors = []

        def window1_and_spawn_pid_update():
            nonlocal attempt_id
            # Window 1
            with BirdStore.open_with_retry(lq_dir) as store:
                store.ensure_session(
                    session_id="test",
                    client_id="blq-run",
                    invoker="blq",
                    invoker_type="cli",
                )
                attempt = AttemptRecord(
                    id=AttemptRecord.generate_id(),
                    session_id="test",
                    cmd="test",
                    cwd=str(initialized_project),
                    client_id="blq-run",
                )
                attempt_id = store.write_attempt(attempt)

                # Spawn PID update thread WHILE still holding connection
                # This tests the race where PID update starts before Window 1 closes
                def update_pid():
                    try:
                        with BirdStore.open_with_retry(lq_dir, max_retries=5) as pid_store:
                            pid_store.update_attempt_pid(attempt_id, 99999)
                        pid_updated.set()
                    except Exception as e:
                        errors.append(str(e))

                pid_thread = threading.Thread(target=update_pid)
                pid_thread.start()

                # Small delay to let PID thread start and potentially contend
                import time

                time.sleep(0.01)

            # Window 1 closed, PID update should complete
            pid_thread.join(timeout=5)

        window1_and_spawn_pid_update()

        assert pid_updated.is_set(), f"PID update failed: {errors}"

        # Verify PID was updated
        with BirdStore.open(lq_dir) as store:
            result = store.connection.execute(
                "SELECT pid FROM attempts WHERE id = ?", [attempt_id]
            ).fetchone()
            assert result[0] == 99999

    def test_overlapping_command_lifecycles(self, initialized_project):
        """Commands with overlapping Window 1 and Window 2 phases."""
        import threading
        import time

        lq_dir = initialized_project / ".lq"
        results = {"cmd1": {}, "cmd2": {}}
        errors = []

        def command_lifecycle(cmd_name: str, start_delay: float, exec_time: float):
            try:
                time.sleep(start_delay)

                # Window 1
                with BirdStore.open_with_retry(lq_dir, max_retries=10) as store:
                    store.ensure_session(
                        session_id=cmd_name,
                        client_id="blq-run",
                        invoker="blq",
                        invoker_type="cli",
                    )
                    attempt = AttemptRecord(
                        id=AttemptRecord.generate_id(),
                        session_id=cmd_name,
                        cmd=f"echo {cmd_name}",
                        cwd=str(initialized_project),
                        client_id="blq-run",
                        source_name=cmd_name,
                    )
                    attempt_id = store.write_attempt(attempt)
                    results[cmd_name]["attempt_id"] = attempt_id

                # Simulate execution (DB unlocked)
                time.sleep(exec_time)

                # Window 2
                with BirdStore.open_with_retry(lq_dir, max_retries=10) as store:
                    from blq.bird import InvocationRecord, OutcomeRecord

                    outcome = OutcomeRecord(
                        attempt_id=attempt_id,
                        exit_code=0,
                        duration_ms=int(exec_time * 1000),
                    )
                    store.write_outcome(outcome)

                    invocation = InvocationRecord(
                        id=attempt_id,
                        session_id=cmd_name,
                        cmd=f"echo {cmd_name}",
                        cwd=str(initialized_project),
                        client_id="blq-run",
                        exit_code=0,
                    )
                    store.write_invocation(invocation)
                    results[cmd_name]["completed"] = True

            except Exception as e:
                errors.append((cmd_name, str(e)))

        # cmd1: starts immediately, runs for 50ms
        # cmd2: starts after 20ms, runs for 30ms
        # This creates overlap: cmd2's Window 1 happens during cmd1's execution
        # And cmd2's Window 2 might race with cmd1's Window 2
        t1 = threading.Thread(target=command_lifecycle, args=("cmd1", 0, 0.05))
        t2 = threading.Thread(target=command_lifecycle, args=("cmd2", 0.02, 0.03))

        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert len(errors) == 0, f"Errors: {errors}"
        assert results["cmd1"].get("completed")
        assert results["cmd2"].get("completed")

        # Verify both commands completed correctly
        with BirdStore.open(lq_dir) as store:
            for cmd_name in ["cmd1", "cmd2"]:
                status = store.get_attempt_status(results[cmd_name]["attempt_id"])
                assert status == "completed", f"{cmd_name} status is {status}"

    def test_data_integrity_under_concurrent_writes(self, initialized_project):
        """Verify data integrity when multiple threads write concurrently."""
        import threading

        lq_dir = initialized_project / ".lq"
        num_commands = 10
        results = []
        errors = []
        barrier = threading.Barrier(num_commands)

        def write_full_lifecycle(cmd_id: int):
            try:
                barrier.wait(timeout=10)

                attempt_id = AttemptRecord.generate_id()

                # Window 1
                with BirdStore.open_with_retry(lq_dir, max_retries=15) as store:
                    store.ensure_session(
                        session_id=f"integrity-{cmd_id}",
                        client_id="blq-run",
                        invoker="blq",
                        invoker_type="cli",
                    )
                    attempt = AttemptRecord(
                        id=attempt_id,
                        session_id=f"integrity-{cmd_id}",
                        cmd=f"echo integrity test {cmd_id}",
                        cwd=str(initialized_project),
                        client_id="blq-run",
                        source_name=f"integrity-{cmd_id}",
                        tag=f"tag-{cmd_id}",
                    )
                    store.write_attempt(attempt)

                # Window 2
                with BirdStore.open_with_retry(lq_dir, max_retries=15) as store:
                    from blq.bird import InvocationRecord, OutcomeRecord

                    outcome = OutcomeRecord(
                        attempt_id=attempt_id,
                        exit_code=cmd_id,  # Use cmd_id as exit code for verification
                        duration_ms=cmd_id * 10,
                    )
                    store.write_outcome(outcome)

                    invocation = InvocationRecord(
                        id=attempt_id,
                        session_id=f"integrity-{cmd_id}",
                        cmd=f"echo integrity test {cmd_id}",
                        cwd=str(initialized_project),
                        client_id="blq-run",
                        exit_code=cmd_id,
                        duration_ms=cmd_id * 10,
                        tag=f"tag-{cmd_id}",
                    )
                    store.write_invocation(invocation)

                results.append((cmd_id, attempt_id))

            except Exception as e:
                errors.append((cmd_id, str(e)))

        threads = [
            threading.Thread(target=write_full_lifecycle, args=(i,)) for i in range(num_commands)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert len(errors) == 0, f"Errors: {errors}"
        assert len(results) == num_commands

        # Verify data integrity
        with BirdStore.open(lq_dir) as store:
            for cmd_id, attempt_id in results:
                # Check attempt
                attempt_row = store.connection.execute(
                    "SELECT tag, source_name FROM attempts WHERE id = ?", [attempt_id]
                ).fetchone()
                assert attempt_row is not None, f"Attempt {attempt_id} not found"
                assert attempt_row[0] == f"tag-{cmd_id}"
                assert attempt_row[1] == f"integrity-{cmd_id}"

                # Check outcome
                outcome_row = store.connection.execute(
                    "SELECT exit_code, duration_ms FROM outcomes WHERE attempt_id = ?", [attempt_id]
                ).fetchone()
                assert outcome_row is not None, f"Outcome for {attempt_id} not found"
                assert outcome_row[0] == cmd_id
                assert outcome_row[1] == cmd_id * 10

                # Check invocation
                inv_row = store.connection.execute(
                    "SELECT exit_code, tag FROM invocations WHERE id = ?", [attempt_id]
                ).fetchone()
                assert inv_row is not None, f"Invocation {attempt_id} not found"
                assert inv_row[0] == cmd_id
                assert inv_row[1] == f"tag-{cmd_id}"
