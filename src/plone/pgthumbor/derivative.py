"""Thumbor source derivatives — the pixel work.

Pure by construction: bytes in, bytes out.  No ZODB, no ZCA, no request.
Anything that needs a persistent object lives in ``set_source_derivative``;
anything that needs a URL lives in ``scaling.py``.

The point of a derivative is that Thumbor never sees a print-resolution
original.  It is capped on the longest edge and normalised to a clean sRGB
raster, so Thumbor can neither exceed its ``MAX_PIXELS`` limit nor be handed
a colour space it renders wrongly.
"""

from __future__ import annotations

from contextlib import contextmanager
from PIL import Image

import io
import logging
import warnings


try:
    from PIL import ImageCms
except ImportError:  # pragma: no cover - littlecms is optional in some builds
    ImageCms = None

logger = logging.getLogger(__name__)


# Duplicated from ``scaling._SKIP_THUMBOR_TYPES`` on purpose, so this module
# stays free of the scaling import and can be reasoned about on its own.
# ``test_derivative`` pins the two together so they cannot drift apart.
_SKIP_CONTENT_TYPES = frozenset({"image/svg+xml"})

# Colour spaces that are not a clean sRGB raster.  CMYK is the normal case
# for material pulled out of print layouts, and it is why the colour trigger
# is independent of size: tying normalisation to pixel count alone would let
# a 3 MP CMYK press image through unconverted.
_NON_SRGB_MODES = frozenset({"CMYK", "LAB"})

# Our own ceiling on source pixels, checked against the header before any
# pixels are read.
#
# It sits in a narrow band on purpose.  Pillow raises DecompressionBombError
# from inside Image.open above 2 * MAX_IMAGE_PIXELS (178,956,970 px at the
# stock default), before our check could look at im.size — so a higher
# ceiling would never be consulted.  And the largest source found on the
# deployment this exists for is 164.6 MP, which must still get a derivative,
# so a lower one would reject exactly the wrong population.
MAX_SOURCE_PIXELS = 175_000_000

# Only powers of two: a JPEG decoder can skip DCT coefficients at 1/2, 1/4
# and 1/8, and nothing in between.
_DRAFT_DIVISORS = (8, 4, 2, 1)


class SourceTooLargeError(Exception):
    """The source exceeds ``MAX_SOURCE_PIXELS`` and will not be decoded."""


def _is_non_srgb_mode(mode: str) -> bool:
    """True for colour spaces that have to be converted before Thumbor."""
    # I;16, I;16B and I;16L are all 16-bit integer rasters.  The design
    # names "I;16"; the endian variants are the same problem and are
    # included rather than left as a gap nobody would notice.
    return mode in _NON_SRGB_MODES or mode.startswith("I;16")


def _draft_target(size: tuple[int, int], max_edge: int) -> tuple[int, int]:
    """Target size to hand ``Image.draft()``, as explicit per-axis pixels.

    Do not call ``draft(None, (cap, cap))``.  ``JpegImageFile.draft``
    computes a single shared divisor as ``min(w // target_w, h // target_h)``
    and then drops to the largest power of two not exceeding it, so a square
    target underdelivers on every non-square image: an 11811x8858 source
    against a 4000 cap reduces by 1/2 rather than the 1/2 we want *because*
    of the height, and a 7000x5000 source reduces not at all.

    Computing the divisor here and passing the resulting dimensions makes
    the decoder do what was asked.  The rule is the largest power of two
    that keeps the result at or above the cap — never below it, because the
    resize afterwards must still have pixels to work with.
    """
    width, height = size
    if max_edge <= 0:
        return (width, height)
    longest = max(width, height)
    for divisor in _DRAFT_DIVISORS:
        if longest / divisor >= max_edge:
            # Round up per axis; floor division would drop the last column
            # or row of an odd-sized source.
            return (-(-width // divisor), -(-height // divisor))
    return (width, height)


def _needs_derivative(image: Image.Image, max_edge: int) -> bool:
    """True when *image* wants a derivative, on either trigger.

    Size and colour space are independent conditions, not a conjunction.
    """
    # ">" and not ">=": an image already at the cap would only be
    # re-encoded to the same dimensions, at a loss and for nothing.
    if max(image.size) > max_edge:
        return True
    if _is_non_srgb_mode(image.mode):
        return True
    # Palette *plus* transparency, not palette alone.  Alpha on its own
    # only picks the encoder later; it is not a reason to re-encode.
    return image.mode == "P" and "transparency" in image.info


def _is_excluded_image(image: Image.Image) -> bool:
    """True for images a derivative would damage rather than help."""
    # An animated GIF would be flattened to its first frame.
    return bool(getattr(image, "is_animated", False))


@contextmanager
def _quiet_decompression_bomb_warning():
    """Silence Pillow's decompression-bomb *warning* around one decode.

    Between ``MAX_IMAGE_PIXELS`` and twice it, ``Image.open`` warns.  Under
    default filters that is a log line, which is what we want.  Under
    ``-W error`` it becomes an exception, and the blanket handler around
    generation would turn that into a silent "no derivative" — the worst
    outcome available, because the image stays broken and nothing says so.

    The filter state this manipulates is process-global for the duration,
    which is the same objection this module raises against assigning
    ``MAX_IMAGE_PIXELS``.  It is accepted here for two reasons: the decode
    semaphore bounds the window to a single thread, and the effect of the
    suppression is a missing log line rather than disabled protection.  The
    error above 2x is untouched and still raises.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", Image.DecompressionBombWarning)
        yield


def _coerce_to_stream(source):
    """Accept raw bytes or an already-seekable file object."""
    if isinstance(source, (bytes, bytearray, memoryview)):
        return io.BytesIO(source)
    return source


def _open_and_draft(source, max_edge: int) -> Image.Image:
    """Open *source* and ask the decoder for a reduced-resolution read.

    Returns an image that has not been loaded yet; its ``size`` already
    reflects the reduction the decoder agreed to.

    ``Image.MAX_IMAGE_PIXELS`` is deliberately not touched.  It is a module
    global consulted by every ``Image.open`` in the process, including
    ``plone.namedfile.utils.getImageInfo`` on every upload, so raising it
    from a worker thread would disable bomb protection for everyone for the
    duration.  The ceiling enforced here is our own, read from the header
    before ``load()`` is ever called.
    """
    with _quiet_decompression_bomb_warning():
        image = Image.open(_coerce_to_stream(source))

    width, height = image.size
    if width * height > MAX_SOURCE_PIXELS:
        raise SourceTooLargeError(
            f"source is {width}x{height} = {width * height} px, "
            f"above MAX_SOURCE_PIXELS ({MAX_SOURCE_PIXELS})"
        )

    # mode=None: leave the colour space alone.  The CMYK conversion is done
    # deliberately afterwards, through the embedded ICC profile when one is
    # present, rather than as a decoder side effect.
    image.draft(None, _draft_target(image.size, max_edge))
    return image


# Quality 92 at 4:4:4.  The derivative is an intermediate that Thumbor
# scales again, and chroma subsampling applied twice produces visible
# colour fringing on edges.
_JPEG_QUALITY = 92

_ALPHA_MODES = frozenset({"RGBA", "LA", "La", "PA"})


def _has_alpha(image: Image.Image) -> bool:
    """True when the image carries transparency the encoder must preserve."""
    return image.mode in _ALPHA_MODES or "transparency" in image.info


def _to_srgb(image: Image.Image) -> Image.Image:
    """Normalise *image* to a clean sRGB raster, keeping alpha if present.

    Goes through the embedded ICC profile when there is one, because a
    plain ``convert("RGB")`` on CMYK applies a naive formula that shifts
    press colours noticeably.  Falls back to ``convert`` when there is no
    profile, when littlecms is missing from the Pillow build, or when the
    profile turns out to be unusable — a broken profile is not a reason to
    lose the derivative.
    """
    target = "RGBA" if _has_alpha(image) else "RGB"
    profile = image.info.get("icc_profile")
    if image.mode == target and not profile:
        return image
    if profile and ImageCms is not None:
        try:
            return ImageCms.profileToProfile(
                image,
                ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                ImageCms.createProfile("sRGB"),
                outputMode=target,
            )
        except Exception:
            logger.warning(
                "ICC conversion failed, falling back to a plain convert",
                exc_info=True,
            )
    return image.convert(target)


def _log_if_larger(encoded: bytes, source_length: int | None) -> None:
    """Note a derivative that costs more bytes than the original it fronts.

    It is kept regardless.  Byte size and pixel count are independent: a
    well-compressed 11811 px original can be smaller than its own 4000 px
    derivative while still being the 104 MP image Thumbor refuses to
    process.  Dropping the derivative on byte size would reintroduce the
    exact HTTP 400 this package exists to remove, so the size is a
    reporting concern, not a decision.
    """
    if source_length is None or len(encoded) <= source_length:
        return
    logger.warning(
        "Thumbor source derivative is larger than its original "
        "(%d > %d bytes) — keeping it, because the pixel reduction is what "
        "Thumbor's MAX_PIXELS limit cares about",
        len(encoded),
        source_length,
    )


def _source_length(source) -> int | None:
    """Byte length of *source*, without consuming a file object."""
    if isinstance(source, (bytes, bytearray, memoryview)):
        return len(source)
    try:
        position = source.tell()
        source.seek(0, io.SEEK_END)
        length = source.tell()
        source.seek(position)
        return length
    except Exception:
        return None


def _encode(image: Image.Image) -> tuple[bytes, str, str]:
    """Encode to JPEG, or PNG when transparency has to survive."""
    buffer = io.BytesIO()
    if _has_alpha(image):
        image.save(buffer, "PNG", optimize=True)
        return buffer.getvalue(), "image/png", "png"
    # No exif= argument, so EXIF and IPTC are dropped — matching Thumbor's
    # own PRESERVE_EXIF_INFO = False default.  Orientation is deliberately
    # not applied; see the design's "Deliberately excluded".
    image.save(buffer, "JPEG", quality=_JPEG_QUALITY, subsampling=0)
    return buffer.getvalue(), "image/jpeg", "jpeg"


def build_derivative_bytes(source, max_edge: int):
    """Build a capped, sRGB-normalised derivative from image bytes.

    Accepts raw bytes or a seekable file object.  Returns
    ``(bytes, content_type, extension)``, or ``None`` when no derivative is
    needed or none could be produced.

    **Never raises.**  Derivative generation must not be able to fail an
    upload, so every outcome is a return value and the caller falls back to
    the original untouched.
    """
    if max_edge <= 0:
        # 0 is the documented kill switch; short-circuit before any decode.
        return None

    source_length = _source_length(source)
    try:
        image = _open_and_draft(source, max_edge)
        if _is_excluded_image(image):
            return None
        if not _needs_derivative(image, max_edge):
            return None

        # Colour before geometry.  Resampling a palette image in palette
        # space interpolates palette *indices* and produces garbage, so P
        # has to become RGB or RGBA before thumbnail() touches it.
        image = _to_srgb(image)

        # thumbnail(), not ImageOps.contain: contain upscales to fill the
        # box, which would enlarge a small CMYK image that triggered on
        # colour space alone.  thumbnail never enlarges.
        image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)

        encoded, content_type, extension = _encode(image)
        _log_if_larger(encoded, source_length)
        return encoded, content_type, extension
    except Exception:
        logger.warning(
            "Could not build a Thumbor source derivative; using the original",
            exc_info=True,
        )
        return None
