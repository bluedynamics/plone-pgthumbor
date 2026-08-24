"""Shared test fixtures for plone-pgthumbor.

Plain module-level functions, not pytest fixtures — tests import them by
name.  ``tests/test_image_fixtures.py`` pins what each image factory
produces; change a factory and check there first.
"""

from __future__ import annotations

from contextlib import contextmanager
from PIL import Image
from PIL import ImageDraw

import io


def env_override(monkeypatch, **kwargs):
    """Set PGTHUMBOR_* env vars for a test, clearing any unset ones."""
    all_vars = [
        "PGTHUMBOR_SERVER_URL",
        "PGTHUMBOR_SECURITY_KEY",
        "PGTHUMBOR_UNSAFE",
        "PGTHUMBOR_SMART_CROPPING",
        "PGTHUMBOR_PARANOID_MODE",
        "PGTHUMBOR_SOURCE_MAX_EDGE",
    ]
    for var in all_vars:
        if var in kwargs:
            monkeypatch.setenv(var, kwargs[var])
        else:
            monkeypatch.delenv(var, raising=False)


# --- image byte factories --------------------------------------------------
#
# Everything is generated in-process.  Binary fixtures in git would be
# unreviewable, and check-added-large-files is in the pre-commit config.

CORRUPT_BYTES = b"this is definitely not an image, not even a little"

SVG_BYTES = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<svg xmlns="http://www.w3.org/2000/svg" width="80" height="60">'
    b'<rect width="80" height="60" fill="#c0ffee"/>'
    b"</svg>"
)


def _encode(img, image_format, **params):
    buffer = io.BytesIO()
    img.save(buffer, image_format, **params)
    return buffer.getvalue()


def _draw_diagonals(img):
    """Fill an image with diagonal lines.

    A flat colour survives any DCT reduction unchanged, which would make a
    draft() assertion prove nothing about the decode actually happening.
    Diagonals also give the JPEG encoder enough entropy that a truncated
    copy really is short of scan data.
    """
    draw = ImageDraw.Draw(img)
    width, height = img.size
    step = max(4, width // 40)
    for offset in range(-height, width, step):
        draw.line(
            [(offset, 0), (offset + height, height)],
            fill=(255, 96, 0),
            width=max(1, step // 3),
        )
    return img


def jpeg_bytes(size=(80, 60), quality=90):
    """A small, clean, sRGB JPEG — the "no derivative needed" case."""
    return _encode(
        _draw_diagonals(Image.new("RGB", size, (16, 32, 64))), "JPEG", quality=quality
    )


def big_jpeg_bytes(size=(2400, 1800), quality=85):
    """A JPEG large enough for ``draft()`` to have a reduction available."""
    return _encode(
        _draw_diagonals(Image.new("RGB", size, (16, 32, 64))), "JPEG", quality=quality
    )


def cmyk_jpeg_bytes(size=(120, 90)):
    """A CMYK JPEG under any sensible cap — the colour-space trigger alone.

    Deliberately small: if it also tripped the size trigger, no test could
    tell the two conditions apart.
    """
    return _encode(Image.new("CMYK", size, (0, 40, 80, 10)), "JPEG", quality=90)


def png_bytes(size=(80, 60), mode="RGBA"):
    """A PNG, RGBA by default — alpha picks the encoder, it is not a trigger."""
    fill = (16, 32, 64, 128) if mode == "RGBA" else (16, 32, 64)
    return _encode(Image.new(mode, size, fill), "PNG")


def palette_png_bytes(size=(80, 60), transparency=True):
    """A palette PNG, with or without a tRNS chunk.

    Palette *plus* transparency is a trigger; palette alone is not.
    """
    img = Image.new("P", size)
    img.putpalette([0, 0, 0, 255, 0, 0, 0, 255, 0] + [0] * (768 - 9))
    ImageDraw.Draw(img).rectangle([0, 0, size[0] // 2, size[1]], fill=1)
    if transparency:
        return _encode(img, "PNG", transparency=0)
    return _encode(img, "PNG")


def animated_gif_bytes(size=(40, 30), frames=3):
    """A multi-frame GIF — excluded, because a derivative would flatten it."""
    images = []
    for index in range(frames):
        frame = Image.new("P", size)
        frame.putpalette([0, 0, 0, 255, 0, 0, 0, 0, 255] + [0] * (768 - 9))
        ImageDraw.Draw(frame).rectangle(
            [index * 4, 0, index * 4 + 8, size[1]], fill=index % 3
        )
        images.append(frame)
    return _encode(
        images[0], "GIF", save_all=True, append_images=images[1:], duration=80, loop=0
    )


def exif_jpeg_bytes(size=(80, 60), orientation=6):
    """A JPEG carrying an EXIF orientation tag.

    Orientation is deliberately *not* applied by the generator — see the
    design's "Deliberately excluded: EXIF orientation".  This factory exists
    to prove EXIF is dropped, not honoured.
    """
    exif = Image.Exif()
    exif[0x0112] = orientation
    return _encode(
        _draw_diagonals(Image.new("RGB", size, (16, 32, 64))),
        "JPEG",
        quality=90,
        exif=exif,
    )


def truncated_jpeg_bytes(size=(80, 60), keep=0.55):
    """A JPEG whose header survives but whose scan data runs out.

    ``Image.open`` succeeds and reports a plausible size; ``load()`` raises.
    That asymmetry is the point: anything reading ``im.size`` before
    ``load()`` sees a size for an image it cannot decode.
    """
    data = jpeg_bytes(size=size, quality=95)
    return data[: int(len(data) * keep)]


# --- Zope / ZODB support ---------------------------------------------------
#
# Imported lazily: conftest is imported for every test run, and the pure
# pytest suite should not pay for the Zope stack unless a test asks for it.


@contextmanager
def namedfile_storables():
    """Register the IStorage utility ``NamedBlobImage`` needs, then remove it.

    ``NamedBlobFile._setData`` looks the storable up with ``getUtility``, so
    without this you get a ``ComponentLookupError`` rather than a graceful
    miss.  Unregistering on the way out is not optional: a leaked global
    utility contaminates every later test in the run.
    """
    from plone.namedfile.interfaces import IStorage
    from plone.namedfile.storages import BytesStorable
    from zope.component import getGlobalSiteManager

    manager = getGlobalSiteManager()
    storable = BytesStorable()
    manager.registerUtility(storable, IStorage, name="builtins.bytes")
    try:
        yield storable
    finally:
        manager.unregisterUtility(storable, IStorage, name="builtins.bytes")


@contextmanager
def zodb_db():
    """Yield an in-memory ZODB that supports blobs.

    ``DemoStorage`` already provides ``IBlobStorage`` and manages its own
    temporary blob directory, which it removes when the database closes —
    wrapping it in ``BlobStorage`` yourself asserts out.  This is what makes
    "no ids before commit, real ids after" testable at all; mocks cannot
    reproduce it, and it is the whole of the design's commit-semantics table.
    """
    from ZODB import DB
    from ZODB.DemoStorage import DemoStorage

    import transaction

    database = DB(DemoStorage())
    try:
        yield database
    finally:
        transaction.abort()
        database.close()
