"""Shared test fixtures for plone-pgthumbor."""

from __future__ import annotations


def env_override(monkeypatch, **kwargs):
    """Set PGTHUMBOR_* env vars for a test, clearing any unset ones."""
    all_vars = [
        "PGTHUMBOR_SERVER_URL",
        "PGTHUMBOR_SECURITY_KEY",
        "PGTHUMBOR_UNSAFE",
        "PGTHUMBOR_SMART_CROPPING",
        "PGTHUMBOR_PARANOID_MODE",
    ]
    for var in all_vars:
        if var in kwargs:
            monkeypatch.setenv(var, kwargs[var])
        else:
            monkeypatch.delenv(var, raising=False)
