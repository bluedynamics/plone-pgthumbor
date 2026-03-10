"""Tests for ICropProvider and ImageCroppingCropProvider."""

from __future__ import annotations

from unittest.mock import MagicMock


class TestImageCroppingCropProvider:
    """Test the plone.app.imagecropping adapter."""

    def test_get_crop_returns_box(self):
        from plone.pgthumbor.addons_compat.imagecropping import (
            ImageCroppingCropProvider,
        )

        ctx = MagicMock()
        annotations = {"plone.app.imagecropping": {"image_preview": (10, 20, 300, 400)}}
        ctx.__annotations__ = annotations
        # IAnnotations fallback: mock it
        provider = ImageCroppingCropProvider(ctx)

        # Patch IAnnotations to return our dict
        import plone.pgthumbor.addons_compat.imagecropping as mod

        original_ia = mod.IAnnotations
        mod.IAnnotations = lambda obj: annotations
        try:
            result = provider.get_crop("image", "preview")
        finally:
            mod.IAnnotations = original_ia

        assert result == (10, 20, 300, 400)

    def test_get_crop_no_annotation_returns_none(self):
        from plone.pgthumbor.addons_compat.imagecropping import (
            ImageCroppingCropProvider,
        )

        ctx = MagicMock()
        provider = ImageCroppingCropProvider(ctx)

        import plone.pgthumbor.addons_compat.imagecropping as mod

        original_ia = mod.IAnnotations
        mod.IAnnotations = lambda obj: {}
        try:
            result = provider.get_crop("image", "preview")
        finally:
            mod.IAnnotations = original_ia

        assert result is None

    def test_get_crop_wrong_key_returns_none(self):
        from plone.pgthumbor.addons_compat.imagecropping import (
            ImageCroppingCropProvider,
        )

        ctx = MagicMock()
        annotations = {"plone.app.imagecropping": {"image_thumb": (10, 20, 300, 400)}}
        provider = ImageCroppingCropProvider(ctx)

        import plone.pgthumbor.addons_compat.imagecropping as mod

        original_ia = mod.IAnnotations
        mod.IAnnotations = lambda obj: annotations
        try:
            result = provider.get_crop("image", "preview")
        finally:
            mod.IAnnotations = original_ia

        assert result is None

    def test_get_crop_annotation_error_returns_none(self):
        from plone.pgthumbor.addons_compat.imagecropping import (
            ImageCroppingCropProvider,
        )

        ctx = MagicMock()
        provider = ImageCroppingCropProvider(ctx)

        import plone.pgthumbor.addons_compat.imagecropping as mod

        original_ia = mod.IAnnotations

        def raise_type_error(obj):
            raise TypeError("no adapter")

        mod.IAnnotations = raise_type_error
        try:
            result = provider.get_crop("image", "preview")
        finally:
            mod.IAnnotations = original_ia

        assert result is None

    def test_get_crop_converts_to_int(self):
        """Values from annotation may be floats — ensure int conversion."""
        from plone.pgthumbor.addons_compat.imagecropping import (
            ImageCroppingCropProvider,
        )

        ctx = MagicMock()
        annotations = {
            "plone.app.imagecropping": {"image_preview": (10.5, 20.3, 300.7, 400.1)}
        }
        provider = ImageCroppingCropProvider(ctx)

        import plone.pgthumbor.addons_compat.imagecropping as mod

        original_ia = mod.IAnnotations
        mod.IAnnotations = lambda obj: annotations
        try:
            result = provider.get_crop("image", "preview")
        finally:
            mod.IAnnotations = original_ia

        assert result == (10, 20, 300, 400)
        assert all(isinstance(v, int) for v in result)
