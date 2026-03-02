"""Tests for autoprune trigger logic."""

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from blq.commands.execution import (
    _mark_pruned,
    _maybe_auto_prune,
    _resolve_prune_config,
    _should_prune,
)


class TestResolvePruneConfig:
    """Tests for _resolve_prune_config."""

    def test_returns_user_config_defaults(self):
        """With no project overrides, returns user config values."""
        config = MagicMock()
        config.storage_config = {}

        user_config = MagicMock()
        user_config.auto_prune = True
        user_config.prune_days = 14
        user_config.max_runs = 50
        user_config.max_size_mb = 500
        user_config.prune_interval_minutes = 30

        result = _resolve_prune_config(config, user_config)

        assert result["auto_prune"] is True
        assert result["prune_days"] == 14
        assert result["max_runs"] == 50
        assert result["max_size_mb"] == 500
        assert result["prune_interval_minutes"] == 30

    def test_project_config_overrides_user(self):
        """Project-level storage config overrides user config."""
        config = MagicMock()
        config.storage_config = {
            "prune_days": 7,
            "max_runs": 20,
            "auto_prune": True,
        }

        user_config = MagicMock()
        user_config.auto_prune = False
        user_config.prune_days = 30
        user_config.max_runs = 0
        user_config.max_size_mb = 0
        user_config.prune_interval_minutes = 60

        result = _resolve_prune_config(config, user_config)

        assert result["auto_prune"] is True  # Project overrode
        assert result["prune_days"] == 7  # Project overrode
        assert result["max_runs"] == 20  # Project overrode
        assert result["max_size_mb"] == 0  # No project override
        assert result["prune_interval_minutes"] == 60  # No project override

    def test_ignores_non_prune_keys(self):
        """Project config keys not in prune config are ignored."""
        config = MagicMock()
        config.storage_config = {
            "mode": "bird",
            "keep_raw": True,
        }

        user_config = MagicMock()
        user_config.auto_prune = False
        user_config.prune_days = 30
        user_config.max_runs = 0
        user_config.max_size_mb = 0
        user_config.prune_interval_minutes = 60

        result = _resolve_prune_config(config, user_config)
        assert result["prune_days"] == 30  # Unchanged


class TestShouldPrune:
    """Tests for _should_prune."""

    def test_no_stamp_file(self, tmp_path):
        """Returns True when no .last_prune file exists."""
        lq_dir = tmp_path / ".lq"
        lq_dir.mkdir()
        assert _should_prune(lq_dir, 60) is True

    def test_stale_stamp(self, tmp_path):
        """Returns True when stamp is older than interval."""
        lq_dir = tmp_path / ".lq"
        lq_dir.mkdir()
        stamp_file = lq_dir / ".last_prune"
        old_time = (datetime.now() - timedelta(minutes=120)).isoformat()
        stamp_file.write_text(old_time)

        assert _should_prune(lq_dir, 60) is True

    def test_fresh_stamp(self, tmp_path):
        """Returns False when stamp is within interval."""
        lq_dir = tmp_path / ".lq"
        lq_dir.mkdir()
        stamp_file = lq_dir / ".last_prune"
        stamp_file.write_text(datetime.now().isoformat())

        assert _should_prune(lq_dir, 60) is False

    def test_zero_interval(self, tmp_path):
        """interval_minutes=0 always returns True."""
        lq_dir = tmp_path / ".lq"
        lq_dir.mkdir()
        stamp_file = lq_dir / ".last_prune"
        stamp_file.write_text(datetime.now().isoformat())

        assert _should_prune(lq_dir, 0) is True

    def test_corrupt_stamp(self, tmp_path):
        """Returns True when stamp file contains invalid data."""
        lq_dir = tmp_path / ".lq"
        lq_dir.mkdir()
        stamp_file = lq_dir / ".last_prune"
        stamp_file.write_text("not a timestamp")

        assert _should_prune(lq_dir, 60) is True


class TestMarkPruned:
    """Tests for _mark_pruned."""

    def test_creates_stamp_file(self, tmp_path):
        """_mark_pruned creates .last_prune file."""
        lq_dir = tmp_path / ".lq"
        lq_dir.mkdir()

        _mark_pruned(lq_dir)

        stamp_file = lq_dir / ".last_prune"
        assert stamp_file.exists()

        # Should be parseable as ISO timestamp
        content = stamp_file.read_text().strip()
        dt = datetime.fromisoformat(content)
        assert (datetime.now() - dt).total_seconds() < 5

    def test_overwrites_existing(self, tmp_path):
        """_mark_pruned overwrites existing stamp."""
        lq_dir = tmp_path / ".lq"
        lq_dir.mkdir()
        stamp_file = lq_dir / ".last_prune"
        stamp_file.write_text("old content")

        _mark_pruned(lq_dir)

        content = stamp_file.read_text().strip()
        # Should be a valid ISO timestamp, not "old content"
        dt = datetime.fromisoformat(content)
        assert (datetime.now() - dt).total_seconds() < 5


class TestMaybeAutoPrune:
    """Tests for _maybe_auto_prune."""

    def test_disabled(self, initialized_project):
        """Does nothing when auto_prune is disabled."""
        config = MagicMock()
        config.storage_config = {}
        config.lq_dir = Path(".lq")

        user_config = MagicMock()
        user_config.auto_prune = False
        user_config.prune_days = 30
        user_config.max_runs = 0
        user_config.max_size_mb = 0
        user_config.prune_interval_minutes = 60

        # Should not raise, even with no storage
        _maybe_auto_prune(config, user_config)

        # .last_prune should not exist
        assert not (config.lq_dir / ".last_prune").exists()

    def test_creates_stamp_on_prune(self, initialized_project):
        """Creates .last_prune after pruning."""
        config = MagicMock()
        config.storage_config = {}
        config.lq_dir = Path(".lq")

        user_config = MagicMock()
        user_config.auto_prune = True
        user_config.prune_days = 30
        user_config.max_runs = 0
        user_config.max_size_mb = 0
        user_config.prune_interval_minutes = 0  # Always run

        _maybe_auto_prune(config, user_config)

        assert (config.lq_dir / ".last_prune").exists()

    def test_skips_when_recently_pruned(self, initialized_project):
        """Skips pruning when recently pruned."""
        config = MagicMock()
        config.storage_config = {}
        config.lq_dir = Path(".lq")

        user_config = MagicMock()
        user_config.auto_prune = True
        user_config.prune_days = 30
        user_config.max_runs = 0
        user_config.max_size_mb = 0
        user_config.prune_interval_minutes = 60

        # Write a fresh stamp
        _mark_pruned(config.lq_dir)

        # This should not actually open storage (because _should_prune returns False)
        with patch("blq.storage.BlqStorage") as mock_storage:
            _maybe_auto_prune(config, user_config)
            mock_storage.open.assert_not_called()

    def test_exception_does_not_propagate(self):
        """Errors in pruning don't propagate to caller."""
        config = MagicMock()
        config.storage_config = {}
        config.lq_dir = Path("/nonexistent/.lq")

        user_config = MagicMock()
        user_config.auto_prune = True
        user_config.prune_days = 30
        user_config.max_runs = 0
        user_config.max_size_mb = 0
        user_config.prune_interval_minutes = 0

        # Should not raise
        _maybe_auto_prune(config, user_config)
