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
        running_ids = {r["id"] for r in running}
        assert id1 in running_ids
        assert id2 in running_ids
        assert id3 not in running_ids

        store.close()

    def test_get_next_run_number_includes_attempts(self, initialized_project):
        """get_next_run_number counts both invocations and attempts."""
        store = BirdStore.open(initialized_project / ".lq")

        # Create an attempt
        attempt = AttemptRecord(
            id=AttemptRecord.generate_id(),
            session_id="test",
            cmd="test cmd",
            cwd=str(initialized_project),
            client_id="blq-test",
        )
        store.write_attempt(attempt)

        # Next run number should be 2
        next_num = store.get_next_run_number()
        assert next_num >= 2  # At least 2, may be higher if fixtures create data

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

        assert result[0][attempt_id_idx] == pending_id

        store.close()
