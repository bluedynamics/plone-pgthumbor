"""Pin what the shared test factories produce.

The derivative trigger matrix *is* Pillow behaviour — mode sniffing,
``is_animated``, palette transparency, DCT reduction — so the factories that
feed it have to produce exactly what they claim.  Without this file a Pillow
upgrade that quietly changes one of them would move the tests that depend on
it instead of failing here, and the failure would surface as a confusing
assertion three modules away.

Everything is generated in-process.  Binary fixtures in git would be
unreviewable, and ``check-added-large-files`` is in the pre-commit config.
"""

from __future__ import annotations

from PIL import Image
from PIL import UnidentifiedImageError
from tests.conftest import animated_gif_bytes
from tests.conftest import big_jpeg_bytes
from tests.conftest import cmyk_jpeg_bytes
from tests.conftest import CORRUPT_BYTES
from tests.conftest import exif_jpeg_bytes
from tests.conftest import jpeg_bytes
from tests.conftest import namedfile_storables
from tests.conftest import palette_png_bytes
from tests.conftest import png_bytes
from tests.conftest import SVG_BYTES
from tests.conftest import truncated_jpeg_bytes
from tests.conftest import zodb_db

import io
import pytest


def _open(data):
    return Image.open(io.BytesIO(data))


class TestJpegFactory:
    """``jpeg_bytes`` — the plain, clean, small raster."""

    def test_is_an_rgb_jpeg(self):
        img = _open(jpeg_bytes())

        assert img.format == "JPEG"
        assert img.mode == "RGB"

    def test_default_size_is_small(self):
        assert _open(jpeg_bytes()).size == (80, 60)

    def test_size_is_configurable(self):
        assert _open(jpeg_bytes(size=(120, 40))).size == (120, 40)

    def test_carries_no_exif(self):
        assert not _open(jpeg_bytes()).getexif()

    def test_is_not_animated(self):
        assert getattr(_open(jpeg_bytes()), "is_animated", False) is False


class TestBigJpegFactory:
    """``big_jpeg_bytes`` — large enough that ``draft()`` has work to do."""

    def test_default_size(self):
        assert _open(big_jpeg_bytes()).size == (2400, 1800)

    def test_size_is_configurable(self):
        assert _open(big_jpeg_bytes(size=(800, 400))).size == (800, 400)

    def test_content_is_not_flat(self):
        # A single-colour image would survive any DCT reduction unchanged,
        # so the draft assertions below would prove nothing about content.
        extrema = _open(big_jpeg_bytes()).getextrema()

        assert any(lo != hi for lo, hi in extrema)

    def test_draft_underdelivers_against_a_square_target(self):
        img = _open(big_jpeg_bytes())
        img.draft(None, (600, 600))

        # Not (600, 450).  JpegImageFile.draft computes a single shared
        # divisor as min(w // target_w, h // target_h) — here min(4, 3) == 3
        # — and then drops to the largest power of two not exceeding it, so
        # 1/2.  This underdelivery is exactly why derivative.py computes the
        # divisor itself and passes explicit per-axis targets rather than
        # calling draft(None, (cap, cap)).
        assert img.size == (1200, 900)

    def test_draft_is_a_no_op_when_no_reduction_fits(self):
        img = _open(big_jpeg_bytes())
        img.draft(None, (2000, 2000))

        assert img.size == (2400, 1800)


class TestColourSpaceFactories:
    """The factories feeding the colour-space half of the trigger matrix."""

    def test_cmyk_jpeg_is_cmyk(self):
        img = _open(cmyk_jpeg_bytes())

        assert img.format == "JPEG"
        assert img.mode == "CMYK"

    def test_cmyk_jpeg_is_small_enough_to_be_a_pure_colour_trigger(self):
        # It must not also trip the size trigger, or tests using it cannot
        # tell the two conditions apart.
        assert max(_open(cmyk_jpeg_bytes()).size) < 200

    def test_png_defaults_to_rgba(self):
        img = _open(png_bytes())

        assert img.format == "PNG"
        assert img.mode == "RGBA"

    def test_png_mode_is_configurable(self):
        assert _open(png_bytes(mode="RGB")).mode == "RGB"

    def test_palette_png_with_transparency(self):
        img = _open(palette_png_bytes(transparency=True))

        assert img.mode == "P"
        assert "transparency" in img.info

    def test_palette_png_without_transparency(self):
        img = _open(palette_png_bytes(transparency=False))

        assert img.mode == "P"
        assert "transparency" not in img.info


class TestAnimatedAndExifFactories:
    """The factories for the two deliberate exclusions."""

    def test_animated_gif_reports_multiple_frames(self):
        img = _open(animated_gif_bytes())

        assert img.format == "GIF"
        assert img.is_animated is True
        assert img.n_frames > 1

    def test_exif_jpeg_carries_an_orientation_tag(self):
        exif = _open(exif_jpeg_bytes()).getexif()

        assert exif
        assert exif.get(0x0112) == 6


class TestUnreadableFactories:
    """Bytes that must fail, and fail in the documented way."""

    def test_corrupt_bytes_cannot_be_identified(self):
        with pytest.raises(UnidentifiedImageError):
            _open(CORRUPT_BYTES)

    def test_svg_bytes_cannot_be_identified(self):
        # SVG never reaches Pillow in production — it is skipped by content
        # type — but the generator must survive being handed it anyway.
        with pytest.raises(UnidentifiedImageError):
            _open(SVG_BYTES)

    def test_truncated_jpeg_opens_but_fails_to_load(self, monkeypatch):
        # The header survives, so `open` succeeds and only `load` raises.
        # Anything reading `im.size` before `load()` therefore sees a
        # plausible size for an image it cannot decode.
        #
        # The flag is pinned explicitly because plone.scale flips it to True
        # process-wide at import (see test_derivative), and whether that
        # import has happened yet depends on test order.
        from PIL import ImageFile

        monkeypatch.setattr(ImageFile, "LOAD_TRUNCATED_IMAGES", False)
        img = _open(truncated_jpeg_bytes())

        assert img.size == (80, 60)
        with pytest.raises(OSError):
            img.load()

    def test_truncated_jpeg_decodes_when_pillow_is_told_to_tolerate_it(
        self, monkeypatch
    ):
        from PIL import ImageFile

        monkeypatch.setattr(ImageFile, "LOAD_TRUNCATED_IMAGES", True)
        img = _open(truncated_jpeg_bytes())
        img.load()

        assert img.size == (80, 60)


class TestNamedfileStorables:
    """The IStorage registration NamedBlobImage needs in pure pytest."""

    def test_utility_is_available_inside_the_block(self):
        from plone.namedfile.interfaces import IStorage
        from zope.component import queryUtility

        with namedfile_storables():
            assert queryUtility(IStorage, name="builtins.bytes") is not None

    def test_utility_is_gone_afterwards(self):
        # A leaked global utility contaminates every later test in the run.
        from plone.namedfile.interfaces import IStorage
        from zope.component import queryUtility

        with namedfile_storables():
            pass

        assert queryUtility(IStorage, name="builtins.bytes") is None

    def test_a_named_blob_image_can_be_built(self):
        from plone.namedfile.file import NamedBlobImage

        with namedfile_storables():
            image = NamedBlobImage(data=jpeg_bytes(), filename="t.jpg")

            assert image.getImageSize() == (80, 60)


class TestZodbDb:
    """The minimal ZODB the commit-semantics tests need."""

    def test_blob_has_no_ids_before_commit(self):
        from plone.pgthumbor.blob import get_blob_ids

        with namedfile_storables(), zodb_db() as db:
            connection = db.open()
            image = NamedBlobImageForTest(jpeg_bytes())
            connection.root()["image"] = image

            # No commit yet: ZODB assigns oids during _store_objects, so the
            # blob has neither oid nor serial and no Thumbor URL can name it.
            assert get_blob_ids(image) is None

    def test_blob_has_ids_after_commit(self):
        from plone.pgthumbor.blob import get_blob_ids

        import transaction

        with namedfile_storables(), zodb_db() as db:
            connection = db.open()
            image = NamedBlobImageForTest(jpeg_bytes())
            connection.root()["image"] = image
            transaction.commit()

            ids = get_blob_ids(image)

            assert ids is not None
            assert all(isinstance(part, int) for part in ids)


def NamedBlobImageForTest(data):
    from plone.namedfile.file import NamedBlobImage

    return NamedBlobImage(data=data, filename="t.jpg")
