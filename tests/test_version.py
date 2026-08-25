"""The exported version must match the installed distribution.

`__version__` was a hand-written literal and drifted two releases behind
pyproject.toml before anyone noticed — nothing consumes it (the CLI reads
importlib.metadata directly), so nothing failed. The same defect in squackit
reached four releases of drift.

Asserting a hardcoded string here would recreate the problem: the module and
its test would agree with each other and with nothing else. Assert the
correspondence instead.
"""

from importlib.metadata import PackageNotFoundError, version


def test_version_matches_the_installed_distribution():
    import blq

    try:
        expected = version("blq-cli")
    except PackageNotFoundError:
        expected = "0.0.0.dev0"
    assert blq.__version__ == expected


def test_version_is_not_a_hardcoded_literal():
    """Guard the mechanism, not just today's value."""
    import pathlib

    src = pathlib.Path(blq_init()).read_text()
    assert "_dist_version" in src, "version is no longer derived from metadata"


def blq_init():
    import blq

    return blq.__file__
