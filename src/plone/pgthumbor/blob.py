"""Blob ID extraction from NamedBlobImage/NamedBlobFile objects."""

from __future__ import annotations

from ZODB.utils import u64
from ZODB.utils import z64

import logging


logger = logging.getLogger(__name__)


def get_blob_ids(named_image) -> tuple[int, int] | None:
    """Extract (blob_zoid, blob_tid) from a NamedBlobImage/NamedBlobFile.

    Returns None if the object doesn't have a blob or the blob lacks OID/serial.

    The blob is never activated.  Unghosting a ``Blob`` makes
    ``Connection.setstate`` call ``storage.loadBlob``, which materializes the
    whole file locally (a full download on tiered storages such as S3), just
    to read a serial.  For a ghost the serial is looked up in the
    connection's storage instead, which respects the connection's snapshot.
    A ghost's own ``_p_serial`` cannot be trusted: after an invalidation it
    still holds the old serial.
    """
    blob = getattr(named_image, "_blob", None)
    if blob is None:
        return None

    oid = getattr(blob, "_p_oid", None)
    if oid is None:
        return None

    if blob._p_changed is None:  # ghost
        jar = blob._p_jar
        if jar is None:
            return None
        try:
            _, serial = jar._storage.load(oid)
        except Exception:
            logger.debug("Could not look up serial of blob %r", blob)
            return None
    else:
        serial = getattr(blob, "_p_serial", None)

    if serial is None or serial == z64:  # never committed
        return None

    return u64(oid), u64(serial)
