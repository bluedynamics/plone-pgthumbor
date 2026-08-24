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


class TestCandidateParameters:
    """Every parameter set that could have minted a given uid."""

    def _candidates(self, fieldname, dimension, scales, original_size=None):
        from plone.pgthumbor.uid_healing import candidate_parameters

        return list(candidate_parameters(fieldname, dimension, scales, original_size))

    def test_both_dimensions_truthy_yields_one_shape_per_mode(self):
        """hash_key deletes the scale key when width and height are truthy,
        so the name path and the explicit-dimensions path collapse to one
        uid. The recovered parameters must still carry the scale name: it is
        free (the uid is unaffected either way), and it is what keeps a
        healed uid's configured crop, since _get_crop reads the name out of
        the parameters pre_scale was called with, not out of the uid
        (issue #21 finding 3)."""
        from plone.pgthumbor.uid_healing import SCALE_MODES

        candidates = self._candidates("image", 400, (("Haeuser", 400, 200),))

        assert len(candidates) == len(SCALE_MODES)
        assert all(c["scale"] == "Haeuser" for c in candidates)
        assert {c["mode"] for c in candidates} == set(SCALE_MODES)
        assert all((c["width"], c["height"]) == (400, 200) for c in candidates)

    def test_zero_height_yields_three_shapes_per_mode(self):
        """width 400, height 0: the scale key survives, so scale=None,
        scale="preview" and no scale key at all are three distinct uids."""
        from plone.pgthumbor.uid_healing import SCALE_MODES

        candidates = self._candidates("image", 400, (("preview", 400, 0),))

        assert len(candidates) == 3 * len(SCALE_MODES)

        shapes = [
            c.get("scale", "<absent>") for c in candidates if c["mode"] == "scale"
        ]
        assert len(shapes) == 3
        assert set(shapes) == {None, "preview", "<absent>"}

    def test_zero_width_scale_is_reachable(self):
        """Issue #21 defect 3: Header 0:460 must be a candidate, not a
        request for the original dimensions."""
        candidates = self._candidates("image", 0, (("Header", 0, 460),))

        assert any(
            (c["width"], c["height"]) == (0, 460) and c["scale"] == "Header"
            for c in candidates
        )

    def test_dimension_zero_also_offers_the_original(self):
        """A bare tag() without a width mints image-0-... too."""
        candidates = self._candidates("image", 0, (("Header", 0, 460),))

        no_dims = [c for c in candidates if c["width"] is None]
        assert no_dims
        assert all(c["height"] is None for c in no_dims)
        assert all("scale" not in c or c["scale"] is None for c in no_dims)

    def test_non_zero_dimension_never_offers_the_original(self):
        candidates = self._candidates("image", 400, (("preview", 400, 0),))

        assert all(c["width"] is not None for c in candidates)

    def test_scales_with_other_widths_are_skipped(self):
        candidates = self._candidates(
            "image", 400, (("preview", 400, 0), ("large", 800, 800))
        )

        assert all(c["width"] in (400, None) for c in candidates)

    def test_original_size_candidate_when_width_matches(self):
        """The image_scales "download" entry is minted at the original size."""
        candidates = self._candidates("image", 900, (), original_size=(900, 600))

        assert candidates
        assert all((c["width"], c["height"]) == (900, 600) for c in candidates)

    def test_original_size_ignored_when_width_differs(self):
        candidates = self._candidates("image", 400, (), original_size=(900, 600))

        assert candidates == []

    def test_no_original_size_is_tolerated(self):
        """Empty field or unreadable image: every other candidate survives."""
        candidates = self._candidates(
            "image", 400, (("Haeuser", 400, 200),), original_size=None
        )

        assert candidates

    def test_fieldname_is_threaded_through(self):
        candidates = self._candidates("my-logo-field", 400, (("Haeuser", 400, 200),))

        assert all(c["fieldname"] == "my-logo-field" for c in candidates)
