"""Tests for legacy uid parsing and candidate parameter recovery."""

from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import patch


class TestParseLegacyUid:
    """The deterministic uid shape from plone.scale >= 4."""

    def test_parses_fieldname_and_width(self):
        from plone.pgthumbor.uid_healing import parse_legacy_uid

        assert parse_legacy_uid("image-400-" + "a" * 32) == ("image", 400)

    def test_fieldname_may_contain_dashes(self):
        from plone.pgthumbor.uid_healing import parse_legacy_uid

        assert parse_legacy_uid("my-logo-field-200-" + "b" * 32) == (
            "my-logo-field",
            200,
        )

    def test_width_zero_is_kept_as_zero(self):
        from plone.pgthumbor.uid_healing import parse_legacy_uid

        assert parse_legacy_uid("image-0-" + "c" * 32) == ("image", 0)

    def test_rejects_non_hex_tail(self):
        from plone.pgthumbor.uid_healing import parse_legacy_uid

        assert parse_legacy_uid("image-400-nothex") is None

    def test_rejects_missing_hash(self):
        from plone.pgthumbor.uid_healing import parse_legacy_uid

        assert parse_legacy_uid("image-400") is None

    def test_rejects_oversized_width_without_raising(self):
        from plone.pgthumbor.uid_healing import parse_legacy_uid

        assert parse_legacy_uid("image-" + "9" * 5000 + "-" + "a" * 32) is None


class TestRegisteredScales:
    """Registry parsing. Order matters and duplicates must survive."""

    def _registry_with(self, lines):
        registry = MagicMock()
        registry.get.return_value = lines
        return registry

    def test_parses_registered_sizes(self):
        from plone.pgthumbor.uid_healing import registered_scales

        registry = self._registry_with(["preview 400:400", "large 800:65536"])
        with patch("plone.pgthumbor.uid_healing.queryUtility", return_value=registry):
            scales = registered_scales()

        assert scales == (("preview", 400, 400), ("large", 800, 65536))
        registry.get.assert_called_once_with("plone.allowed_sizes")

    def test_keeps_both_scales_sharing_a_width(self):
        """Issue #21 defect 2: collapsing these made Haeuser heal as preview."""
        from plone.pgthumbor.uid_healing import registered_scales

        registry = self._registry_with(["preview 400:0", "Haeuser 400:200"])
        with patch("plone.pgthumbor.uid_healing.queryUtility", return_value=registry):
            assert registered_scales() == (
                ("preview", 400, 0),
                ("Haeuser", 400, 200),
            )

    def test_keeps_height_driven_scales(self):
        from plone.pgthumbor.uid_healing import registered_scales

        registry = self._registry_with(["Header 0:460", "Bottom 0:270"])
        with patch("plone.pgthumbor.uid_healing.queryUtility", return_value=registry):
            assert registered_scales() == (("Header", 0, 460), ("Bottom", 0, 270))

    def test_skips_malformed_lines(self):
        from plone.pgthumbor.uid_healing import registered_scales

        registry = self._registry_with(
            ["broken", "no-dims 400", "preview 400:400", "bad 400x300"]
        )
        with patch("plone.pgthumbor.uid_healing.queryUtility", return_value=registry):
            assert registered_scales() == (("preview", 400, 400),)

    def test_no_registry_returns_empty(self):
        from plone.pgthumbor.uid_healing import registered_scales

        with patch("plone.pgthumbor.uid_healing.queryUtility", return_value=None):
            assert registered_scales() == ()

    def test_none_record_returns_empty(self):
        from plone.pgthumbor.uid_healing import registered_scales

        registry = self._registry_with(None)
        with patch("plone.pgthumbor.uid_healing.queryUtility", return_value=registry):
            assert registered_scales() == ()
