"""Tests for blob ID extraction from NamedBlobImage objects."""

from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import patch
from ZODB.blob import Blob
from ZODB.FileStorage import FileStorage
from ZODB.utils import u64

import pytest
import struct
import transaction
import ZODB


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

    def test_returns_none_when_serial_lookup_fails(self):
        """A ghost blob whose serial cannot be looked up returns None."""
        from plone.pgthumbor.blob import get_blob_ids

        img = MagicMock()
        blob = MagicMock()
        blob._p_oid = struct.pack(">Q", 0x42)
        blob._p_changed = None  # ghost
        blob._p_jar._storage.load.side_effect = Exception("connection closed")
        img._blob = blob
        result = get_blob_ids(img)

        assert result is None

    def test_returns_none_for_ghost_without_jar(self):
        from plone.pgthumbor.blob import get_blob_ids

        img = MagicMock()
        img._blob = MagicMock()
        img._blob._p_oid = struct.pack(">Q", 0x42)
        img._blob._p_changed = None  # ghost
        img._blob._p_jar = None
        result = get_blob_ids(img)

        assert result is None


@pytest.fixture
def blob_db(tmp_path):
    """A real ZODB with blob support, holding one committed blob at root['b']."""
    db = ZODB.DB(
        FileStorage(str(tmp_path / "Data.fs"), blob_dir=str(tmp_path / "blobs"))
    )
    tm = transaction.TransactionManager()
    conn = db.open(tm)
    conn.root()["b"] = Blob(b"original")
    tm.commit()
    conn.close()
    yield db
    db.close()


def _ghost_blob(db):
    """Open a fresh connection and return (conn, image) with a ghost blob."""
    tm = transaction.TransactionManager()
    conn = db.open(tm)
    img = MagicMock()
    img._blob = conn.root()["b"]
    assert img._blob._p_changed is None  # ghost
    return conn, img


def _rewrite_blob(db, data):
    tm = transaction.TransactionManager()
    conn = db.open(tm)
    with conn.root()["b"].open("w") as f:
        f.write(data)
    tm.commit()
    conn.close()


class TestGetBlobIdsWithoutLoadingTheBlob:
    """The serial is all we need; the blob file must never be fetched.

    Unghosting a ``Blob`` makes ``Connection.setstate`` call
    ``storage.loadBlob``, which on a tiered storage means a full download
    (#45).
    """

    def test_ghost_blob_does_not_load_blob_file(self, blob_db):
        from plone.pgthumbor.blob import get_blob_ids

        conn, img = _ghost_blob(blob_db)
        storage = conn._storage
        with patch.object(
            storage, "loadBlob", side_effect=AssertionError("loadBlob called")
        ):
            result = get_blob_ids(img)

        _, committed_serial = storage.load(img._blob._p_oid)
        assert result == (u64(img._blob._p_oid), u64(committed_serial))
        assert img._blob._p_changed is None  # still a ghost
        conn.close()

    def test_ghost_matches_activated_serial(self, blob_db):
        from plone.pgthumbor.blob import get_blob_ids

        conn, img = _ghost_blob(blob_db)
        from_ghost = get_blob_ids(img)
        img._blob._p_activate()
        assert get_blob_ids(img) == from_ghost
        conn.close()

    def test_invalidated_ghost_reports_new_serial(self, blob_db):
        """An invalidated ghost keeps its stale ``_p_serial``; don't trust it."""
        from plone.pgthumbor.blob import get_blob_ids

        conn, img = _ghost_blob(blob_db)
        img._blob._p_activate()
        before = get_blob_ids(img)

        _rewrite_blob(blob_db, b"rewritten")
        conn.transaction_manager.begin()  # sync: invalidates our copy

        assert img._blob._p_changed is None
        assert u64(img._blob._p_serial) == before[1]  # stale on the ghost
        after = get_blob_ids(img)
        assert after[0] == before[0]
        assert after[1] > before[1]
        conn.close()

    def test_ghost_respects_connection_snapshot(self, blob_db):
        """A commit after our transaction started is not visible to us."""
        from plone.pgthumbor.blob import get_blob_ids

        conn, img = _ghost_blob(blob_db)
        img._blob._p_activate()
        before = get_blob_ids(img)
        img._blob._p_deactivate()

        _rewrite_blob(blob_db, b"rewritten")  # no sync on our side

        assert get_blob_ids(img) == before
        conn.close()
