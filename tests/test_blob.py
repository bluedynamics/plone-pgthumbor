"""Tests for blob ID extraction from NamedBlobImage objects."""

from __future__ import annotations

from unittest.mock import MagicMock

import struct


def _mock_blob(oid_int, serial_int):
    """Create a mock ZODB.blob.Blob with _p_oid and _p_serial."""
    blob = MagicMock()
    blob._p_oid = struct.pack(">Q", oid_int)
    blob._p_serial = struct.pack(">Q", serial_int)
    return blob


def _mock_named_image(blob_oid=0x42, blob_tid=0xFF):
    """Create a mock NamedBlobImage with a _blob attribute."""
    img = MagicMock()
    img._blob = _mock_blob(blob_oid, blob_tid)
    img.contentType = "image/jpeg"
    return img


class TestGetBlobIds:
    """Test get_blob_ids() extraction of ZOID/TID from named images."""

    def test_from_named_blob_image(self):
        from plone.pgthumbor.blob import get_blob_ids

        img = _mock_named_image(blob_oid=0x42, blob_tid=0xFF)
        result = get_blob_ids(img)

        assert result == (0x42, 0xFF)

    def test_returns_none_for_no_blob(self):
        from plone.pgthumbor.blob import get_blob_ids

        img = MagicMock(spec=[])  # no _blob attribute
        result = get_blob_ids(img)

        assert result is None

    def test_returns_none_for_none_oid(self):
        from plone.pgthumbor.blob import get_blob_ids

        img = MagicMock()
        img._blob = MagicMock()
        img._blob._p_oid = None
        result = get_blob_ids(img)

        assert result is None

    def test_zoid_conversion(self):
        """Verify u64() correctly converts 8-byte OID to int."""
        from plone.pgthumbor.blob import get_blob_ids

        img = _mock_named_image(blob_oid=0xDEADBEEF, blob_tid=0xCAFEBABE)
        result = get_blob_ids(img)

        assert result == (0xDEADBEEF, 0xCAFEBABE)

    def test_returns_none_for_none_serial(self):
        from plone.pgthumbor.blob import get_blob_ids

        img = MagicMock()
        img._blob = MagicMock()
        img._blob._p_oid = struct.pack(">Q", 0x42)
        img._blob._p_serial = None
        result = get_blob_ids(img)

        assert result is None

    def test_field_without_blob(self):
        """A text field or other non-blob field returns None."""
        from plone.pgthumbor.blob import get_blob_ids

        field = MagicMock()
        del field._blob  # ensure _blob doesn't exist
        result = get_blob_ids(field)

        assert result is None
