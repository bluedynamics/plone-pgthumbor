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


class TestUpgradeTo4:
    """Test upgrade_to_4 registers the record added with source_max_edge.

    An existing site never runs post_install again, so without this step it
    would carry the new code and no registry record for the new setting.
    """

    def test_registers_the_interface(self):
        from plone.pgthumbor.interfaces import IThumborSettings
        from plone.pgthumbor.setuphandlers import upgrade_to_4

        mock_registry = MagicMock()

        with patch(
            "plone.pgthumbor.setuphandlers.getUtility", return_value=mock_registry
        ):
            upgrade_to_4(MagicMock())

        mock_registry.registerInterface.assert_called_once_with(
            IThumborSettings, prefix="plone.pgthumbor.settings"
        )


class TestProfileWiring:
    """The profile version and the upgrade step have to agree.

    Cheap to check by parsing, and it catches the typo that would otherwise
    only surface as a site that silently never upgrades.
    """

    def _package_dir(self):
        import pathlib
        import plone.pgthumbor

        return pathlib.Path(plone.pgthumbor.__file__).parent

    def test_profile_version_is_4(self):
        from xml.etree import ElementTree

        metadata = self._package_dir() / "profiles" / "default" / "metadata.xml"
        version = ElementTree.parse(metadata).getroot().findtext("version")

        assert version == "4"

    def test_upgrade_step_3_to_4_is_registered(self):
        from xml.etree import ElementTree

        zcml = ElementTree.parse(self._package_dir() / "configure.zcml").getroot()
        steps = [
            element
            for element in zcml.iter()
            if element.tag.endswith("upgradeStep")
            and element.get("source") == "3"
            and element.get("destination") == "4"
        ]

        assert len(steps) == 1
        assert steps[0].get("handler") == ".setuphandlers.upgrade_to_4"
