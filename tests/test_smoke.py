"""Smoke test — verify the package is importable and version is set."""

from crosspoint_newsletter import __version__


def test_version_is_set():
    assert __version__ == "0.1.0"


def test_config_importable():
    from crosspoint_newsletter import config

    assert hasattr(config, "DATA_DIR")
    assert hasattr(config, "IMAP_HOST")
    assert hasattr(config, "OPDS_PORT")
