"""Tests for ThumborScaleStorage — no Pillow, no image data, no ZODB writes."""

from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import patch


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
