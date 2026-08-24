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
