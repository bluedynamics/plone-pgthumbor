"""Recover the parameters a legacy scale uid was minted from.

plone.scale >= 4 mints deterministic uids of the form
``{fieldname}-{width}-{md5hex32}``.  The md5 covers the whole parameter set
plus the field's modification time, so the parameters cannot be read back out
of the uid.  They can, however, be enumerated and re-hashed: the registered
scales are known and the set of call shapes is small.

This module produces the candidates.  ``ThumborScaleStorage`` does the hashing
and the matching, because only it holds the context and the modification time.
"""

from __future__ import annotations

from plone.registry.interfaces import IRegistry
from zope.component import queryUtility

import re


# plone.scale >= 4 deterministic uid: {fieldname}-{width}-{md5hex}
_LEGACY_UID_RE = re.compile(
    r"^(?P<fieldname>.+)-(?P<width>\d{1,9})-(?P<hash>[0-9a-f]{32})$"
)

# ``hash_key`` hashes the raw mode string the caller passed, not the value
# ``get_scale_mode`` normalises it to, so "keep" and "scale" produce different
# uids for the same scale.  An alias that is not enumerated is a silent miss.
SCALE_MODES = (
    "scale",
    "cover",
    "contain",
    "keep",
    "thumbnail",
    "down",
    "up",
    "scale-crop-to-fit",
    "scale-crop-to-fill",
    None,
)


def parse_legacy_uid(uid):
    """Split a deterministic uid into ``(fieldname, dimension)``.

    Returns None when *uid* is not shaped like one.  ``dimension`` is the
    number plone.scale put in the uid: the requested width, or 0 when the call
    had no width at all.  The width group is length-limited so a multi-thousand
    digit uid cannot turn into an expensive int.
    """
    match = _LEGACY_UID_RE.match(uid)
    if match is None:
        return None
    return match.group("fieldname"), int(match.group("width"))


def registered_scales():
    """Return ``(name, width, height)`` for every ``plone.allowed_sizes`` entry.

    Registry order is preserved and duplicates are kept.  Two scales may share
    a width (``preview 400:0`` and ``Haeuser 400:200``), and collapsing them
    into a width-keyed mapping is what made healing pick the wrong one.
    Malformed lines are skipped rather than raising: the registry is editable
    through the control panel.
    """
    registry = queryUtility(IRegistry)
    if registry is None:
        return ()
    scales = []
    for line in registry.get("plone.allowed_sizes") or ():
        try:
            name, dims = line.split()
            width, height = dims.split(":")
            scales.append((name, int(width), int(height)))
        except ValueError:
            continue
    return tuple(scales)


def candidate_parameters(fieldname, dimension, scales, original_size=None):
    """Yield every parameter set that could have minted a uid.

    ``hash_key`` deletes the ``scale`` key only when width *and* height are
    truthy.  A scale with a zero component is therefore reachable under three
    parameter sets that hash differently: ``@@images/{field}/{name}`` passes
    the scale name, the ``image_scales`` indexer passes ``scale=None``, and
    plone.namedfile's own ``srcset()`` passes no ``scale`` key at all.  Missing
    that splits exactly the ``0:H`` and ``W:0`` scales this fix exists for.

    *original_size* is the field value's ``getImageSize()`` or None.  It covers
    the "download" entry, which is minted at the original's dimensions.
    """
    for name, width, height in scales:
        if width != dimension:
            continue
        for mode in SCALE_MODES:
            base = {
                "fieldname": fieldname,
                "width": width,
                "height": height,
                "mode": mode,
            }
            if width and height:
                # hash_key drops "scale" here, so all three call shapes
                # collapse into one hash: the named call, the image_scales
                # call (scale=None), and the no-scale-key srcset call all
                # mint the same uid, and this fix cannot tell them apart.
                # Recover the name anyway: it is free (the uid is
                # unaffected either way), and it is what keeps a healed
                # uid's configured crop -- _get_crop reads the scale name
                # out of these parameters, not out of the uid, so assuming
                # scale=None would silently drop an editorial crop.
                yield {**base, "scale": name}
            else:
                yield {**base, "scale": None}
                yield {**base, "scale": name}
                yield dict(base)

    if dimension == 0:
        # A bare tag() with no width at all mints {fieldname}-0-{md5} too.
        # Such a call carries no scale name, so there are only two shapes.
        for mode in SCALE_MODES:
            base = {
                "fieldname": fieldname,
                "width": None,
                "height": None,
                "mode": mode,
            }
            yield {**base, "scale": None}
            yield dict(base)

    if original_size and original_size[0] == dimension:
        orig_width, orig_height = original_size
        for mode in SCALE_MODES:
            yield {
                "fieldname": fieldname,
                "width": orig_width,
                "height": orig_height,
                "mode": mode,
                "scale": None,
            }
