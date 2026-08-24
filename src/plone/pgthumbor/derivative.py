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
