"""Tests for the v2 -> v3 upgrade step."""

from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import patch


_REMOVED_KEYS = [
    "plone.pgthumbor.settings.server_url",
    "plone.pgthumbor.settings.security_key",
    "plone.pgthumbor.settings.unsafe",
]

_KEPT_KEYS = [
    "plone.pgthumbor.settings.smart_cropping",
    "plone.pgthumbor.settings.paranoid_mode",
]


class TestUpgradeTo3:
    """Test upgrade_to_3 removes dead registry records."""

    def test_removes_dead_keys(self):
        from plone.pgthumbor.setuphandlers import upgrade_to_3

        mock_registry = MagicMock()
        mock_records = {}
        for key in _REMOVED_KEYS + _KEPT_KEYS:
            mock_records[key] = MagicMock()
        mock_registry.records = mock_records

        with patch(
            "plone.pgthumbor.setuphandlers.getUtility", return_value=mock_registry
        ):
            upgrade_to_3(MagicMock())

        for key in _REMOVED_KEYS:
            assert key not in mock_registry.records

    def test_keeps_valid_keys(self):
        from plone.pgthumbor.setuphandlers import upgrade_to_3

        mock_registry = MagicMock()
        mock_records = {}
        for key in _REMOVED_KEYS + _KEPT_KEYS:
            mock_records[key] = MagicMock()
        mock_registry.records = mock_records

        with patch(
            "plone.pgthumbor.setuphandlers.getUtility", return_value=mock_registry
        ):
            upgrade_to_3(MagicMock())

        for key in _KEPT_KEYS:
            assert key in mock_registry.records

    def test_ignores_missing_keys(self):
        """Upgrade step should not fail if keys are already absent."""
        from plone.pgthumbor.setuphandlers import upgrade_to_3

        mock_registry = MagicMock()
        mock_registry.records = {}

        with patch(
            "plone.pgthumbor.setuphandlers.getUtility", return_value=mock_registry
        ):
            upgrade_to_3(MagicMock())  # should not raise
