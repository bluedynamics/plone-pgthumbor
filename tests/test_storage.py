"""Tests for ThumborScaleStorage — no Pillow, no image data, no ZODB writes."""

from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import patch
from ZODB.POSException import ConflictError

import pytest


def _make_storage():
    from plone.pgthumbor.storage import ThumborScaleStorage

    ctx = MagicMock()
    return ThumborScaleStorage(ctx, modified=None)


class TestThumborScaleStorage:
    """Test that ThumborScaleStorage never generates actual image data."""

    def test_scale_calls_pre_scale(self):
        """scale() should delegate to pre_scale() — no Pillow."""
        storage = _make_storage()

        with patch.object(
            storage, "pre_scale", return_value={"uid": "test-uid", "data": None}
        ) as mock_pre:
            result = storage.scale(fieldname="image", width=400, height=300)

        mock_pre.assert_called_once_with(fieldname="image", width=400, height=300)
        assert result["data"] is None

    def test_get_or_generate_returns_existing(self):
        """get_or_generate() should return stored info without generating data."""
        storage = _make_storage()

        info = {
            "uid": "image-400-abc123",
            "data": None,
            "width": 400,
            "height": 300,
            "mimetype": "image/jpeg",
            "key": ("hash",),
            "modified": 1000,
        }
        storage.storage["image-400-abc123"] = info

        result = storage.get_or_generate("image-400-abc123")
        assert result is not None
        assert result["data"] is None
        assert result["width"] == 400

    def test_get_or_generate_missing_returns_none(self):
        """get_or_generate() returns None for unknown uids."""
        storage = _make_storage()
        result = storage.get_or_generate("nonexistent-uid")
        assert result is None

    def test_no_pillow_invoked(self):
        """Verify IImageScaleFactory is never called."""
        storage = _make_storage()

        with (
            patch.object(
                storage, "pre_scale", return_value={"uid": "test", "data": None}
            ),
            patch("plone.scale.storage.IImageScaleFactory") as mock_factory,
        ):
            storage.scale(fieldname="image", width=400, height=300)

        mock_factory.assert_not_called()

    def test_storage_uid_deterministic(self):
        """Same parameters should produce same uid."""
        storage = _make_storage()
        uid1 = storage.hash_key(fieldname="image", width=400, height=300)
        uid2 = storage.hash_key(fieldname="image", width=400, height=300)
        assert uid1 == uid2

    def test_storage_different_params_different_uid(self):
        """Different parameters should produce different uid."""
        storage = _make_storage()
        uid1 = storage.hash_key(fieldname="image", width=400, height=300)
        uid2 = storage.hash_key(fieldname="image", width=800, height=600)
        assert uid1 != uid2

    def test_storage_is_volatile(self):
        """storage property returns a plain dict, not a PersistentMapping."""
        storage = _make_storage()
        assert type(storage.storage) is dict

    def test_storage_not_persistent(self):
        """Writing to storage must not touch IAnnotations."""
        storage = _make_storage()
        storage.storage["test"] = {"data": "value"}
        # IAnnotations should never have been accessed
        storage.context.__getitem__.assert_not_called()

    def test_separate_instances_separate_storage(self):
        """Each adapter instance has its own volatile storage."""
        s1 = _make_storage()
        s2 = _make_storage()
        s1.storage["key"] = "value"
        assert "key" not in s2.storage


class TestMintTime:
    """The modification time a uid was hashed against."""

    def test_uses_the_field_value_modification_time(self):
        storage = _make_storage()
        storage.context._p_mtime = 1700000000.0
        storage.context.image.modified = 1755000000.0

        assert storage._mint_time("image") == 1755000000000

    def test_falls_back_to_the_context_mtime(self):
        from plone.pgthumbor.storage import ThumborScaleStorage

        ctx = MagicMock(spec=["_p_mtime"])
        ctx._p_mtime = 1700000000.0
        storage = ThumborScaleStorage(ctx, modified=None)

        assert storage._mint_time("image") == 1700000000000


class TestOriginalSize:
    """The original image's ``(width, height)``, read off the field value."""

    def test_missing_field_returns_none(self):
        from plone.pgthumbor.storage import ThumborScaleStorage

        ctx = MagicMock(spec=["_p_mtime"])
        storage = ThumborScaleStorage(ctx, modified=None)

        assert storage._original_size("image") is None

    def test_value_without_get_image_size_returns_none(self):
        storage = _make_storage()
        storage.context.image = object()

        assert storage._original_size("image") is None

    def test_conflict_error_propagates(self):
        """A ConflictError must reach the publisher's retry loop, not be
        swallowed as "no download candidate"."""
        storage = _make_storage()
        storage.context.image.getImageSize.side_effect = ConflictError()

        with pytest.raises(ConflictError):
            storage._original_size("image")

    def test_other_exception_returns_none(self):
        storage = _make_storage()
        storage.context.image.getImageSize.side_effect = ValueError("corrupt")

        assert storage._original_size("image") is None

    def test_happy_path_returns_the_size(self):
        storage = _make_storage()
        storage.context.image.getImageSize.return_value = (400, 300)

        assert storage._original_size("image") == (400, 300)


class TestHealByHashMatch:
    """Mint a uid, then heal it: the recovered parameters must be identical.

    This is the whole point of the fix. Most tests here patch ``_mint_time``
    to the constant it was minted against and use it to reach a match;
    proving that ``_mint_time`` reconstructs that value correctly is
    ``TestMintTime``'s job, not this class's. The two 404 tests below never
    reach ``_mint_time`` at all — they short-circuit in ``parse_legacy_uid``.
    """

    MINT_TIME = 1755000000000

    def _storages(self):
        """Return (minting storage, healing storage) over the same context."""
        from plone.pgthumbor.storage import ThumborScaleStorage

        ctx = MagicMock()
        minting = ThumborScaleStorage(ctx, modified=lambda: self.MINT_TIME)
        healing = ThumborScaleStorage(ctx, modified=None)
        return minting, healing

    def _heal(self, healing, uid, scales, original_size=None):
        """Run the healing path with the mint time and registry stubbed out."""
        recorded = {}

        def fake_pre_scale(**parameters):
            recorded.update(parameters)
            return {"uid": uid, "data": None}

        with (
            patch.object(healing, "pre_scale", side_effect=fake_pre_scale),
            patch.object(healing, "_mint_time", return_value=self.MINT_TIME),
            patch.object(healing, "_original_size", return_value=original_size),
            patch("plone.pgthumbor.storage.registered_scales", return_value=scales),
        ):
            result = healing.get_or_generate(uid)
        return result, recorded

    def test_recovers_cover_mode(self):
        """Issue #21 defect 1: mode was hardcoded to "scale"."""
        minting, healing = self._storages()
        uid = minting.hash_key(
            fieldname="image", width=400, height=200, mode="cover", scale=None
        )

        result, recorded = self._heal(healing, uid, (("Haeuser", 400, 200),))

        assert result is not None
        assert recorded["mode"] == "cover"
        assert (recorded["width"], recorded["height"]) == (400, 200)

    def test_recovers_the_scale_name_for_a_width_and_height_scale(self):
        """Issue #21 finding 3: a healed W:H uid must keep its configured
        crop. hash_key drops "scale" from the hash whenever both dimensions
        are truthy, so the named call (tag(scale="Haeuser")) and the
        image_scales call (scale=None) mint the identical uid -- proven
        below. _get_crop reads the scale name out of the recovered
        parameters, not out of the uid, so healing must still recover the
        name rather than None."""
        minting, healing = self._storages()
        named_uid = minting.hash_key(
            fieldname="image", width=400, height=200, mode="scale", scale="Haeuser"
        )
        explicit_uid = minting.hash_key(
            fieldname="image", width=400, height=200, mode="scale", scale=None
        )
        assert named_uid == explicit_uid  # the collision this finding is about

        _result, recorded = self._heal(healing, named_uid, (("Haeuser", 400, 200),))

        assert recorded["scale"] == "Haeuser"

    def test_disambiguates_two_scales_sharing_a_width(self):
        """Issue #21 defect 2: Haeuser 400:200 healed as preview 400:0."""
        minting, healing = self._storages()
        uid = minting.hash_key(
            fieldname="image", width=400, height=200, mode="scale", scale=None
        )

        _result, recorded = self._heal(
            healing, uid, (("preview", 400, 0), ("Haeuser", 400, 200))
        )

        assert (recorded["width"], recorded["height"]) == (400, 200)

    def test_recovers_a_height_driven_scale(self):
        """Issue #21 defect 3: Header 0:460 healed to the original size."""
        minting, healing = self._storages()
        uid = minting.hash_key(
            fieldname="image", width=0, height=460, mode="scale", scale="Header"
        )

        _result, recorded = self._heal(healing, uid, (("Header", 0, 460),))

        assert (recorded["width"], recorded["height"]) == (0, 460)
        assert recorded["scale"] == "Header"

    def test_recovers_the_explicit_dimensions_shape_of_a_zero_height_scale(self):
        """The image_scales indexer passes scale=None, not the name."""
        minting, healing = self._storages()
        uid = minting.hash_key(
            fieldname="image", width=400, height=0, mode="scale", scale=None
        )

        _result, recorded = self._heal(healing, uid, (("preview", 400, 0),))

        assert recorded["scale"] is None

    def test_recovers_the_no_scale_key_shape(self):
        """plone.namedfile's own srcset() passes no scale key at all."""
        minting, healing = self._storages()
        uid = minting.hash_key(fieldname="image", width=400, height=0, mode="scale")

        _result, recorded = self._heal(healing, uid, (("preview", 400, 0),))

        assert "scale" not in recorded

    def test_recovers_the_original_size_download_entry(self):
        minting, healing = self._storages()
        uid = minting.hash_key(
            fieldname="image", width=900, height=600, mode="scale", scale=None
        )

        _result, recorded = self._heal(healing, uid, (), original_size=(900, 600))

        assert (recorded["width"], recorded["height"]) == (900, 600)

    def test_fieldname_with_dashes_round_trips(self):
        minting, healing = self._storages()
        uid = minting.hash_key(
            fieldname="my-logo-field",
            width=400,
            height=200,
            mode="scale",
            scale=None,
        )

        _result, recorded = self._heal(healing, uid, (("Haeuser", 400, 200),))

        assert recorded["fieldname"] == "my-logo-field"

    def test_unregistered_width_stays_a_404(self):
        """The #17 gate: arbitrary dimensions must not be signed on demand."""
        _minting, healing = self._storages()

        result, recorded = self._heal(
            healing, "image-999-" + "b" * 32, (("Haeuser", 400, 200),)
        )

        assert result is None
        assert recorded == {}

    def test_malformed_uid_stays_a_404(self):
        storage = _make_storage()

        with patch.object(storage, "pre_scale") as mock_pre:
            assert storage.get_or_generate("image-400-nothex") is None
            assert storage.get_or_generate("image-400") is None

        mock_pre.assert_not_called()

    def test_oversized_width_stays_a_404(self):
        storage = _make_storage()

        with patch.object(storage, "pre_scale") as mock_pre:
            assert (
                storage.get_or_generate("image-" + "9" * 5000 + "-" + "a" * 32) is None
            )

        mock_pre.assert_not_called()

    def test_pre_scale_returning_none_stays_a_404(self):
        minting, healing = self._storages()
        uid = minting.hash_key(
            fieldname="image", width=400, height=200, mode="scale", scale=None
        )

        with (
            patch.object(healing, "pre_scale", return_value=None),
            patch.object(healing, "_mint_time", return_value=self.MINT_TIME),
            patch.object(healing, "_original_size", return_value=None),
            patch(
                "plone.pgthumbor.storage.registered_scales",
                return_value=(("Haeuser", 400, 200),),
            ),
        ):
            assert healing.get_or_generate(uid) is None


class TestThumborScaleStorageFactory:
    """Test that the factory respects the browser layer."""

    def test_returns_thumbor_storage_when_layer_active(self):
        from plone.pgthumbor.interfaces import IPlonePgthumborLayer
        from plone.pgthumbor.storage import thumbor_scale_storage_factory
        from plone.pgthumbor.storage import ThumborScaleStorage

        request = MagicMock()
        request.__provides__ = None
        from zope.interface import alsoProvides

        alsoProvides(request, IPlonePgthumborLayer)

        ctx = MagicMock()
        with patch("plone.pgthumbor.storage.getRequest", return_value=request):
            result = thumbor_scale_storage_factory(ctx, modified=None)

        assert isinstance(result, ThumborScaleStorage)
        assert type(result.storage) is dict

    def test_returns_annotation_storage_when_layer_inactive(self):
        from plone.pgthumbor.storage import thumbor_scale_storage_factory
        from plone.pgthumbor.storage import ThumborScaleStorage
        from plone.scale.storage import AnnotationStorage

        request = MagicMock()  # no IPlonePgthumborLayer

        ctx = MagicMock()
        with patch("plone.pgthumbor.storage.getRequest", return_value=request):
            result = thumbor_scale_storage_factory(ctx, modified=None)

        assert isinstance(result, AnnotationStorage)
        assert not isinstance(result, ThumborScaleStorage)

    def test_returns_annotation_storage_when_no_request(self):
        from plone.pgthumbor.storage import thumbor_scale_storage_factory
        from plone.pgthumbor.storage import ThumborScaleStorage
        from plone.scale.storage import AnnotationStorage

        ctx = MagicMock()
        with patch("plone.pgthumbor.storage.getRequest", return_value=None):
            result = thumbor_scale_storage_factory(ctx, modified=None)

        assert isinstance(result, AnnotationStorage)
        assert not isinstance(result, ThumborScaleStorage)


class TestStaleUidFallback:
    """A uid older than the image's last modification cannot be identified."""

    def _fallback(self, dimension, scales):
        storage = _make_storage()
        return storage._fallback_parameters("image", dimension, scales)

    def test_picks_the_first_registered_scale_with_that_width(self):
        parameters = self._fallback(400, (("preview", 400, 0), ("Haeuser", 400, 200)))

        assert (parameters["width"], parameters["height"]) == (400, 0)
        assert parameters["mode"] == "scale"
        assert parameters["scale"] is None

    def test_zero_width_prefers_a_registered_height_driven_scale(self):
        """Never request the original's dimensions while a 0:H scale exists:
        that is the variant that can push Thumbor past MAX_PIXELS."""
        parameters = self._fallback(0, (("Header", 0, 460),))

        assert (parameters["width"], parameters["height"]) == (0, 460)

    def test_zero_width_without_a_registered_scale_means_the_original(self):
        """The genuine tag()-without-a-width case, and the only reading left."""
        parameters = self._fallback(0, (("preview", 400, 0),))

        assert (parameters["width"], parameters["height"]) == (None, None)

    def test_unregistered_width_has_no_fallback(self):
        assert self._fallback(999, (("preview", 400, 0),)) is None

    def test_stale_uid_reaches_the_fallback(self):
        """End to end: a uid whose hash matches nothing still renders."""
        storage = _make_storage()
        recorded = {}

        def fake_pre_scale(**parameters):
            recorded.update(parameters)
            return {"uid": "x", "data": None}

        with (
            patch.object(storage, "pre_scale", side_effect=fake_pre_scale),
            patch.object(storage, "_mint_time", return_value=1755000000000),
            patch.object(storage, "_original_size", return_value=None),
            patch(
                "plone.pgthumbor.storage.registered_scales",
                return_value=(("Haeuser", 400, 200),),
            ),
        ):
            result = storage.get_or_generate("image-400-" + "f" * 32)

        assert result is not None
        assert (recorded["width"], recorded["height"]) == (400, 200)
        assert recorded["mode"] == "scale"
