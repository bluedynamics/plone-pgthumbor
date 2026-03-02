"""Blob ID extraction from NamedBlobImage/NamedBlobFile objects."""

from __future__ import annotations

from ZODB.utils import u64

import logging


logger = logging.getLogger(__name__)


def get_blob_ids(named_image) -> tuple[int, int] | None:
    """Extract (blob_zoid, blob_tid) from a NamedBlobImage/NamedBlobFile.

    Returns None if the object doesn't have a blob or the blob lacks OID/serial.
    Accessing _p_serial triggers lightweight ghost activation if needed.
    """
    blob = getattr(named_image, "_blob", None)
    if blob is None:
        return None

    oid = getattr(blob, "_p_oid", None)
    if oid is None:
        return None

    # Activate the blob to load its state from storage — this sets _p_serial
    # to the real committed TID. Without this, ghost objects have _p_serial=z64.
    try:
        blob._p_activate()
    except Exception:
        logger.debug("Could not activate blob %r", blob)
        return None

    serial = getattr(blob, "_p_serial", None)
    if serial is None or serial == b"\x00" * 8:  # z64 = never committed
        return None

    return u64(oid), u64(serial)
