"""Tests for derivative.py — decoding, the pixel ceiling, and the triggers.

``derivative.py`` is pure: bytes in, bytes out, no ZODB and no ZCA.  That
makes it the one place in this package where real image bytes are cheaper
than mocks, because everything asserted here *is* Pillow behaviour.
"""

from __future__ import annotations

from PIL import Image
from tests.conftest import animated_gif_bytes
from tests.conftest import big_jpeg_bytes
from tests.conftest import cmyk_jpeg_bytes
from tests.conftest import exif_jpeg_bytes
from tests.conftest import jpeg_bytes
from tests.conftest import palette_png_bytes
from tests.conftest import png_bytes
from tests.conftest import truncated_jpeg_bytes

import io
import pytest


def _open(data):
    return Image.open(io.BytesIO(data))


class TestDraftTarget:
    """The reduction factor is computed here, not delegated to draft().

    ``JpegImageFile.draft`` derives a single shared divisor from
    ``min(w // target_w, h // target_h)``, so handing it ``(cap, cap)``
    underdelivers whenever the image is not square — see
    ``test_image_fixtures.TestBigJpegFactory``.  These targets are explicit
    per axis so the decoder does what we asked.
    """

    def test_picks_the_largest_power_of_two_keeping_the_result_above_the_cap(self):
        from plone.pgthumbor.derivative import _draft_target

        # 11811 / 2 == 5905.5, still >= 4000; / 4 would undershoot at 2952.
        assert _draft_target((11811, 8858), 4000) == (5906, 4429)

    def test_no_reduction_when_halving_would_undershoot(self):
        from plone.pgthumbor.derivative import _draft_target

        # 7000 / 2 == 3500 < 4000, so a full decode is the price.
        assert _draft_target((7000, 5000), 4000) == (7000, 5000)

    def test_image_below_the_cap_is_untouched(self):
        from plone.pgthumbor.derivative import _draft_target

        assert _draft_target((800, 600), 4000) == (800, 600)

    def test_deep_reduction_for_a_very_large_source(self):
        from plone.pgthumbor.derivative import _draft_target

        # 16000 / 4 == 4000, exactly at the cap and therefore allowed.
        assert _draft_target((16000, 12000), 4000) == (4000, 3000)

    def test_rounds_up_per_axis(self):
        from plone.pgthumbor.derivative import _draft_target

        # An odd edge must not lose its last column to floor division.
        assert _draft_target((9001, 8999), 4000) == (4501, 4500)

    def test_a_disabled_cap_asks_for_no_reduction(self):
        from plone.pgthumbor.derivative import _draft_target

        assert _draft_target((11811, 8858), 0) == (11811, 8858)


class TestNeedsDerivative:
    """The trigger matrix.  Size or colour space, either one on its own."""

    def test_longest_edge_above_the_cap_triggers(self):
        from plone.pgthumbor.derivative import _needs_derivative

        assert _needs_derivative(_open(jpeg_bytes()), max_edge=40) is True

    def test_exactly_at_the_cap_does_not_trigger(self):
        from plone.pgthumbor.derivative import _needs_derivative

        # The boundary is ">", not ">=": an image already at the cap would
        # only be re-encoded to the same dimensions.
        assert _needs_derivative(_open(jpeg_bytes()), max_edge=80) is False

    def test_clean_small_srgb_does_not_trigger(self):
        from plone.pgthumbor.derivative import _needs_derivative

        assert _needs_derivative(_open(jpeg_bytes()), max_edge=4000) is False

    def test_cmyk_under_the_cap_triggers(self):
        from plone.pgthumbor.derivative import _needs_derivative

        # The motivating case: press material is CMYK at any size, and
        # tying normalisation to size alone would let it through raw.
        assert _needs_derivative(_open(cmyk_jpeg_bytes()), max_edge=4000) is True

    def test_palette_with_transparency_triggers(self):
        from plone.pgthumbor.derivative import _needs_derivative

        image = _open(palette_png_bytes(transparency=True))

        assert _needs_derivative(image, max_edge=4000) is True

    def test_palette_without_transparency_does_not_trigger(self):
        from plone.pgthumbor.derivative import _needs_derivative

        image = _open(palette_png_bytes(transparency=False))

        assert _needs_derivative(image, max_edge=4000) is False

    def test_rgba_under_the_cap_does_not_trigger(self):
        from plone.pgthumbor.derivative import _needs_derivative

        # Alpha picks the encoder in build_derivative_bytes; on its own it
        # is not a reason to re-encode anything.
        assert _needs_derivative(_open(png_bytes()), max_edge=4000) is False


class TestExcludedImages:
    """What we refuse to touch even when a trigger would fire."""

    def test_animated_gif_is_excluded(self):
        from plone.pgthumbor.derivative import _is_excluded_image

        # A derivative would flatten it to a single frame.
        assert _is_excluded_image(_open(animated_gif_bytes())) is True

    def test_a_still_image_is_not_excluded(self):
        from plone.pgthumbor.derivative import _is_excluded_image

        assert _is_excluded_image(_open(jpeg_bytes())) is False

    def test_skip_content_types_cannot_drift_from_scaling(self):
        from plone.pgthumbor import scaling
        from plone.pgthumbor.derivative import _SKIP_CONTENT_TYPES

        # Duplicated on purpose — derivative.py stays free of the scaling
        # import — but the two must never disagree about SVG.
        assert _SKIP_CONTENT_TYPES == scaling._SKIP_THUMBOR_TYPES


class TestOpenAndDraft:
    """Opening with a reduced-resolution read, without touching colour."""

    def test_draft_engages_for_a_large_jpeg(self):
        from plone.pgthumbor.derivative import _open_and_draft

        image = _open_and_draft(io.BytesIO(big_jpeg_bytes()), max_edge=1000)

        # 2400 / 2 == 1200, still >= 1000; / 4 would undershoot at 600.
        assert image.size == (1200, 900)

    def test_draft_is_a_no_op_for_png(self):
        from plone.pgthumbor.derivative import _open_and_draft

        data = png_bytes(size=(2400, 1800), mode="RGB")
        image = _open_and_draft(io.BytesIO(data), max_edge=1000)

        assert image.size == (2400, 1800)

    def test_colour_space_is_left_alone(self):
        from plone.pgthumbor.derivative import _open_and_draft

        # draft(None, ...) — the mode argument stays None so the CMYK
        # conversion happens deliberately later, through the ICC profile
        # when one is present, rather than as a decoder side effect.
        image = _open_and_draft(io.BytesIO(cmyk_jpeg_bytes()), max_edge=4000)

        assert image.mode == "CMYK"

    def test_accepts_raw_bytes_as_well_as_a_file_object(self):
        from plone.pgthumbor.derivative import _open_and_draft

        assert _open_and_draft(jpeg_bytes(), max_edge=4000).size == (80, 60)


class TestSourcePixelCeiling:
    """Our own ceiling, and the reason it is not Pillow's."""

    def test_refuses_a_source_above_the_ceiling(self, monkeypatch):
        from plone.pgthumbor import derivative

        monkeypatch.setattr(derivative, "MAX_SOURCE_PIXELS", 1000)

        with pytest.raises(derivative.SourceTooLargeError):
            derivative._open_and_draft(jpeg_bytes(), max_edge=4000)

    def test_accepts_a_source_exactly_at_the_ceiling(self, monkeypatch):
        from plone.pgthumbor import derivative

        monkeypatch.setattr(derivative, "MAX_SOURCE_PIXELS", 80 * 60)

        assert derivative._open_and_draft(jpeg_bytes(), max_edge=4000)

    def test_the_ceiling_is_read_from_the_header_not_the_pixels(self, monkeypatch):
        from plone.pgthumbor import derivative

        # Truncated bytes have an intact header and no scan data.  If the
        # ceiling needed real pixels this would fail inside load() with an
        # OSError; it raises our own error instead, which is only possible
        # because im.size comes off the header before anything is decoded.
        monkeypatch.setattr(derivative, "MAX_SOURCE_PIXELS", 1000)

        with pytest.raises(derivative.SourceTooLargeError):
            derivative._open_and_draft(truncated_jpeg_bytes(), max_edge=4000)

    def test_ceiling_sits_below_pillows_own_hard_limit(self):
        from plone.pgthumbor.derivative import MAX_SOURCE_PIXELS

        # Pillow raises DecompressionBombError inside Image.open above
        # 2 * MAX_IMAGE_PIXELS, before our check can look at im.size.  A
        # ceiling above that would never be consulted and could not be
        # tested without mutating the global we refuse to mutate.
        assert MAX_SOURCE_PIXELS < 2 * Image.MAX_IMAGE_PIXELS

    def test_ceiling_sits_above_the_largest_image_in_the_field(self):
        from plone.pgthumbor.derivative import MAX_SOURCE_PIXELS

        # 164.6 MP is the largest source found on the deployment this
        # feature exists for, and it has to get a derivative.  A lower
        # ceiling would reject exactly the wrong population.
        assert MAX_SOURCE_PIXELS > 164_600_000

    def test_pillows_global_is_never_assigned_at_runtime(self):
        from plone.pgthumbor.derivative import _open_and_draft

        before = Image.MAX_IMAGE_PIXELS
        _open_and_draft(big_jpeg_bytes(), max_edge=1000)

        assert before == Image.MAX_IMAGE_PIXELS

    def test_the_module_contains_no_assignment_to_pillows_global(self):
        from plone.pgthumbor import derivative

        import ast
        import pathlib

        tree = ast.parse(pathlib.Path(derivative.__file__).read_text())
        targets = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == "MAX_IMAGE_PIXELS"
            for parent in [node]
            if isinstance(getattr(parent, "ctx", None), ast.Store)
        ]

        # It is a process global consulted by every Image.open in the
        # process, including plone.namedfile's EXIF handling on upload.
        # Mutating it from a worker thread disables bomb protection for
        # everyone for the duration.
        assert targets == []


class TestDecompressionBombWarning:
    """The 89.5-179 MP band warns; under -W error that would go silent."""

    def test_the_warning_is_suppressed_locally(self):
        from plone.pgthumbor.derivative import _quiet_decompression_bomb_warning

        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("error")

            with _quiet_decompression_bomb_warning():
                # Would raise without the suppression, and the blanket
                # handler in build_derivative_bytes would turn that into a
                # silent "no derivative" — the worst outcome available.
                warnings.warn("bomb", Image.DecompressionBombWarning, stacklevel=1)

    def test_other_warnings_still_propagate(self):
        from plone.pgthumbor.derivative import _quiet_decompression_bomb_warning

        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("error")

            with pytest.raises(UserWarning), _quiet_decompression_bomb_warning():
                warnings.warn("unrelated", UserWarning, stacklevel=1)


class TestTruncatedImageTolerance:
    """plone.scale flips a Pillow global, and it changes what we produce."""

    def test_plone_scale_enables_truncated_loading_process_wide(self):
        from PIL import ImageFile

        import plone.scale.scale  # noqa: F401

        # plone/scale/scale.py:53 sets this at module import, so it is True
        # in every Plone process that has loaded plone.namedfile — which is
        # every process running plone.pgthumbor.  Recorded here because it
        # decides whether a truncated source yields no derivative or a
        # grey-padded one, and because it is not ours to change.
        assert ImageFile.LOAD_TRUNCATED_IMAGES is True


class TestBuildDerivativeBytes:
    """The whole pipeline: decode, normalise, resize, encode.

    ``build_derivative_bytes`` never raises.  Generation must not be able to
    fail an upload, so every outcome is a return value.
    """

    def test_small_clean_jpeg_needs_nothing(self):
        from plone.pgthumbor.derivative import build_derivative_bytes

        assert build_derivative_bytes(jpeg_bytes(), 4000) is None

    def test_oversized_jpeg_is_capped(self):
        from plone.pgthumbor.derivative import build_derivative_bytes

        data, content_type, extension = build_derivative_bytes(big_jpeg_bytes(), 1000)
        image = _open(data)

        assert image.size == (1000, 750)
        assert content_type == "image/jpeg"
        assert extension == "jpeg"

    def test_cmyk_under_the_cap_becomes_rgb_at_the_same_size(self):
        from plone.pgthumbor.derivative import build_derivative_bytes

        # Dimensions unchanged: thumbnail() never enlarges, which is why it
        # is used rather than ImageOps.contain.  A small CMYK image that
        # triggered on colour space alone must not be blown up to the cap.
        data, content_type, _ = build_derivative_bytes(cmyk_jpeg_bytes(), 4000)
        image = _open(data)

        assert image.mode == "RGB"
        assert image.size == (120, 90)
        assert content_type == "image/jpeg"

    def test_alpha_is_written_as_png(self):
        from plone.pgthumbor.derivative import build_derivative_bytes

        data, content_type, extension = build_derivative_bytes(
            png_bytes(size=(2400, 1800), mode="RGBA"), 1000
        )
        image = _open(data)

        assert image.mode == "RGBA"
        assert content_type == "image/png"
        assert extension == "png"

    def test_palette_with_transparency_is_written_as_png(self):
        from plone.pgthumbor.derivative import build_derivative_bytes

        data, content_type, _ = build_derivative_bytes(
            palette_png_bytes(transparency=True), 4000
        )

        assert content_type == "image/png"
        assert _open(data).mode == "RGBA"

    def test_animated_gif_is_left_alone(self):
        from plone.pgthumbor.derivative import build_derivative_bytes

        # Oversized on purpose, so the size trigger would fire: this proves
        # the exclusion wins, not that the trigger simply missed.
        oversized = animated_gif_bytes(size=(2400, 1800))

        assert build_derivative_bytes(oversized, 1000) is None

    def test_svg_yields_nothing(self):
        from plone.pgthumbor.derivative import build_derivative_bytes
        from tests.conftest import SVG_BYTES

        assert build_derivative_bytes(SVG_BYTES, 4000) is None

    def test_corrupt_bytes_yield_nothing_and_warn(self, caplog):
        from plone.pgthumbor.derivative import build_derivative_bytes
        from tests.conftest import CORRUPT_BYTES

        import logging

        with caplog.at_level(logging.WARNING, logger="plone.pgthumbor.derivative"):
            result = build_derivative_bytes(CORRUPT_BYTES, 4000)

        assert result is None
        assert caplog.records

    def test_truncated_bytes_still_produce_a_derivative(self):
        # Deliberate, and pinned so nobody "fixes" it into silence.
        # plone.scale sets LOAD_TRUNCATED_IMAGES=True process-wide, so Plone
        # already renders these bytes grey-padded rather than refusing them.
        # A package replacing Plone's scaling should not judge the same
        # bytes more harshly than the scaling it replaces.
        from PIL import ImageFile
        from plone.pgthumbor.derivative import build_derivative_bytes
        from tests.conftest import truncated_jpeg_bytes

        assert ImageFile.LOAD_TRUNCATED_IMAGES is True

        result = build_derivative_bytes(truncated_jpeg_bytes(size=(2400, 1800)), 1000)

        assert result is not None

    def test_a_disabled_cap_yields_nothing(self):
        from plone.pgthumbor.derivative import build_derivative_bytes

        # 0 is the documented kill switch, and it has to short-circuit
        # before any blob is decoded.
        assert build_derivative_bytes(big_jpeg_bytes(), 0) is None

    def test_a_file_object_is_accepted(self):
        from plone.pgthumbor.derivative import build_derivative_bytes

        result = build_derivative_bytes(io.BytesIO(big_jpeg_bytes()), 1000)

        assert result is not None

    def test_exif_is_dropped(self):
        from plone.pgthumbor.derivative import build_derivative_bytes

        source = exif_jpeg_bytes(size=(2400, 1800))
        assert _open(source).getexif()

        data, _, _ = build_derivative_bytes(source, 1000)

        # Matches Thumbor's own PRESERVE_EXIF_INFO = False default.
        assert not _open(data).getexif()

    def test_jpeg_is_encoded_without_chroma_subsampling(self):
        from PIL.JpegImagePlugin import get_sampling
        from plone.pgthumbor.derivative import build_derivative_bytes

        data, _, _ = build_derivative_bytes(big_jpeg_bytes(), 1000)

        # 4:4:4.  The derivative is an intermediate that Thumbor scales
        # again, and subsampling applied twice gives colour fringing.
        assert get_sampling(_open(data)) == 0

    def test_a_larger_derivative_is_kept_but_warned_about(self, caplog):
        from plone.pgthumbor.derivative import build_derivative_bytes

        import logging

        # Byte size and pixel count are independent: a well-compressed
        # original can be smaller than its own capped derivative while
        # still being the oversized image Thumbor refuses.  Dropping it
        # would reintroduce the 400, so it is kept and only logged.
        source = png_bytes(size=(2400, 1800), mode="RGBA")
        with caplog.at_level(logging.WARNING, logger="plone.pgthumbor.derivative"):
            data, _, _ = build_derivative_bytes(source, 2000)

        assert data is not None


class TestColourNormalisation:
    """Which conversion path is chosen, not what the pixels become."""

    def test_icc_profile_goes_through_image_cms(self, monkeypatch):
        from plone.pgthumbor import derivative

        calls = []
        real = derivative.ImageCms.profileToProfile

        def spy(image, source_profile, target_profile, **kwargs):
            calls.append(kwargs.get("outputMode"))
            return real(image, source_profile, target_profile, **kwargs)

        monkeypatch.setattr(derivative.ImageCms, "profileToProfile", spy)
        derivative.build_derivative_bytes(_icc_cmyk_bytes(), 4000)

        assert calls == ["RGB"]

    def test_without_a_profile_a_plain_convert_is_used(self, monkeypatch):
        from plone.pgthumbor import derivative

        calls = []
        monkeypatch.setattr(
            derivative.ImageCms,
            "profileToProfile",
            lambda *a, **k: calls.append(1),
        )
        # cmyk_jpeg_bytes carries no ICC profile.
        result = derivative.build_derivative_bytes(cmyk_jpeg_bytes(), 4000)

        assert calls == []
        assert _open(result[0]).mode == "RGB"

    def test_image_cms_is_importable(self):
        from plone.pgthumbor import derivative

        # littlecms is optional in some Pillow builds; the module must not
        # fail to import when it is missing, and must not silently skip the
        # ICC path when it is present.
        assert derivative.ImageCms is not None

    def test_a_broken_profile_falls_back_instead_of_failing(self, monkeypatch):
        from plone.pgthumbor import derivative

        monkeypatch.setattr(
            derivative.ImageCms,
            "profileToProfile",
            _raise_value_error,
        )
        result = derivative.build_derivative_bytes(_icc_cmyk_bytes(), 4000)

        assert result is not None
        assert _open(result[0]).mode == "RGB"


def _raise_value_error(*args, **kwargs):
    raise ValueError("broken profile")


def _icc_cmyk_bytes(size=(120, 90)):
    """A CMYK JPEG carrying an ICC profile, for the ImageCms path."""
    from PIL import ImageCms

    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
    buffer = io.BytesIO()
    Image.new("CMYK", size, (0, 40, 80, 10)).save(
        buffer, "JPEG", quality=90, icc_profile=profile.tobytes()
    )
    return buffer.getvalue()


class TestLargerDerivativeReporting:
    """A derivative bigger than its original is reported, never dropped."""

    def test_logs_when_the_derivative_is_larger(self, caplog):
        from plone.pgthumbor.derivative import _log_if_larger

        import logging

        with caplog.at_level(logging.WARNING, logger="plone.pgthumbor.derivative"):
            _log_if_larger(b"x" * 100, 10)

        assert "larger than its original" in caplog.text

    def test_says_nothing_when_it_is_smaller(self, caplog):
        from plone.pgthumbor.derivative import _log_if_larger

        import logging

        with caplog.at_level(logging.WARNING, logger="plone.pgthumbor.derivative"):
            _log_if_larger(b"x" * 10, 100)

        assert caplog.text == ""

    def test_says_nothing_when_the_source_length_is_unknown(self, caplog):
        from plone.pgthumbor.derivative import _log_if_larger

        import logging

        with caplog.at_level(logging.WARNING, logger="plone.pgthumbor.derivative"):
            _log_if_larger(b"x" * 100, None)

        assert caplog.text == ""

    def test_an_unseekable_source_has_no_measurable_length(self):
        from plone.pgthumbor.derivative import _source_length

        class Unseekable:
            def tell(self):
                raise OSError("not seekable")

        assert _source_length(Unseekable()) is None

    def test_a_file_object_keeps_its_position(self):
        from plone.pgthumbor.derivative import _source_length

        stream = io.BytesIO(b"0123456789")
        stream.seek(4)

        assert _source_length(stream) == 10
        assert stream.tell() == 4
