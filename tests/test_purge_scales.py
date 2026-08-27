"""Tests for plone.pgthumbor.purge_scales."""

from __future__ import annotations

from plone.pgthumbor.purge_scales import ANNOTATION_KEY
from plone.pgthumbor.purge_scales import purge_scales
from plone.pgthumbor.purge_scales import PurgeScalesView
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest


def _make_brain(obj, path="/site/obj"):
    brain = MagicMock()
    brain._unrestrictedGetObject.return_value = obj
    brain.getPath.return_value = path
    return brain


def _make_obj(has_scales=False):
    obj = MagicMock()
    annotations = {}
    if has_scales:
        annotations[ANNOTATION_KEY] = {"uid1": {"width": 400, "height": 300}}
    obj._annotations = annotations
    return obj, annotations


def _make_sliceable_portal(brains, has_image_scales_meta=True):
    """A portal whose catalog honours b_start/b_size like pgcatalog does."""
    portal = _make_portal(brains, has_image_scales_meta)

    def search(**query):
        start = query.get("b_start", 0)
        size = query.get("b_size")
        return brains[start : start + size] if size else brains[start:]

    portal.portal_catalog.unrestrictedSearchResults.side_effect = search
    return portal


def _make_portal(brains, has_image_scales_meta=True):
    portal = MagicMock()
    portal.portal_catalog.unrestrictedSearchResults.return_value = brains
    if has_image_scales_meta:
        portal.portal_catalog.schema.return_value = ("image_scales", "Title")
    else:
        portal.portal_catalog.schema.return_value = ("Title",)
    return portal


class TestPurgeScales:
    @patch("plone.pgthumbor.purge_scales.transaction")
    @patch("plone.pgthumbor.purge_scales.IAnnotations")
    def test_purges_objects_with_scales(self, mock_ia, mock_txn):
        obj1, ann1 = _make_obj(has_scales=True)
        obj2, ann2 = _make_obj(has_scales=False)
        obj3, ann3 = _make_obj(has_scales=True)
        mock_ia.side_effect = [ann1, ann2, ann3]

        portal = _make_portal(
            [
                _make_brain(obj1),
                _make_brain(obj2),
                _make_brain(obj3),
            ]
        )

        result = purge_scales(portal)

        assert result["purged"] == 2
        # Two, not three: only the objects actually changed are reindexed.
        assert result["reindexed"] == 2
        assert result["skipped"] == 0
        assert result["processed"] == 3
        assert ANNOTATION_KEY not in ann1
        assert ANNOTATION_KEY not in ann3
        mock_txn.commit.assert_called()

    @patch("plone.pgthumbor.purge_scales.transaction")
    @patch("plone.pgthumbor.purge_scales.IAnnotations")
    def test_reindexes_image_scales(self, mock_ia, mock_txn):
        obj, ann = _make_obj(has_scales=True)
        mock_ia.return_value = ann

        portal = _make_portal([_make_brain(obj)])

        purge_scales(portal)

        obj.reindexObject.assert_called_once_with(idxs=["image_scales"])

    @patch("plone.pgthumbor.purge_scales.transaction")
    @patch("plone.pgthumbor.purge_scales.IAnnotations")
    def test_no_reindex_without_metadata_column(self, mock_ia, mock_txn):
        obj, ann = _make_obj(has_scales=True)
        mock_ia.return_value = ann

        portal = _make_portal([_make_brain(obj)], has_image_scales_meta=False)

        result = purge_scales(portal)

        assert result["purged"] == 1
        assert result["reindexed"] == 0
        obj.reindexObject.assert_not_called()

    @patch("plone.pgthumbor.purge_scales.transaction")
    @patch("plone.pgthumbor.purge_scales.IAnnotations")
    def test_skips_broken_objects(self, mock_ia, mock_txn):
        brain = MagicMock()
        brain._unrestrictedGetObject.side_effect = Exception("broken")

        portal = _make_portal([brain])

        result = purge_scales(portal)

        assert result["purged"] == 0
        assert result["reindexed"] == 0
        assert result["skipped"] == 1
        assert result["processed"] == 1
        mock_txn.commit.assert_not_called()

    @patch("plone.pgthumbor.purge_scales.transaction")
    @patch("plone.pgthumbor.purge_scales.IAnnotations")
    def test_skips_non_annotatable(self, mock_ia, mock_txn):
        mock_ia.side_effect = TypeError("not adaptable")

        portal = _make_portal([_make_brain(MagicMock())])

        result = purge_scales(portal)

        assert result["purged"] == 0
        assert result["reindexed"] == 0
        assert result["skipped"] == 1
        assert result["processed"] == 1

    @patch("plone.pgthumbor.purge_scales.transaction")
    @patch("plone.pgthumbor.purge_scales.IAnnotations")
    def test_no_commit_when_nothing_to_purge(self, mock_ia, mock_txn):
        _, ann = _make_obj(has_scales=False)
        mock_ia.return_value = ann

        portal = _make_portal([_make_brain(MagicMock())], has_image_scales_meta=False)

        result = purge_scales(portal)

        assert result["purged"] == 0
        mock_txn.commit.assert_not_called()

    @patch("plone.pgthumbor.purge_scales.transaction")
    @patch("plone.pgthumbor.purge_scales.IAnnotations")
    def test_batch_commit(self, mock_ia, mock_txn):
        """Commits every batch_size changes."""
        objs_and_anns = [_make_obj(has_scales=True) for _ in range(5)]
        mock_ia.side_effect = [ann for _, ann in objs_and_anns]

        portal = _make_portal(
            [_make_brain(obj) for obj, _ in objs_and_anns],
            has_image_scales_meta=False,
        )

        purge_scales(portal, batch_size=2)

        # batch commits at 2, 4 + final commit = 3
        assert mock_txn.commit.call_count == 3

    @patch("plone.pgthumbor.purge_scales.transaction")
    @patch("plone.pgthumbor.purge_scales.IAnnotations")
    def test_reindex_failure_does_not_abort(self, mock_ia, mock_txn):
        """A failing reindex is logged but does not stop the purge."""
        obj, ann = _make_obj(has_scales=True)
        obj.reindexObject.side_effect = Exception("reindex failed")
        mock_ia.return_value = ann

        portal = _make_portal([_make_brain(obj)])

        result = purge_scales(portal)

        # Still purged, just not reindexed — the annotation is gone either
        # way, and a stale image_scales row is better than an aborted run.
        assert result["purged"] == 1
        assert result["reindexed"] == 0
        assert result["skipped"] == 0


class TestPurgeScalesView:
    @staticmethod
    def _request(**form):
        request = MagicMock()
        request.form = dict(form)
        return request

    @staticmethod
    def _result(**overrides):
        base = {
            "purged": 10,
            "reindexed": 45,
            "skipped": 2,
            "processed": 50,
            "next_start": 50,
            "done": True,
        }
        base.update(overrides)
        return base

    @patch("plone.pgthumbor.purge_scales.purge_scales")
    def test_view_calls_purge_and_returns_text(self, mock_purge):
        mock_purge.return_value = self._result()
        context = MagicMock()
        request = self._request()

        result = PurgeScalesView(context, request)()

        mock_purge.assert_called_once_with(context, limit=None, start=0)
        assert "10" in result
        assert "45" in result
        assert "2 skipped" in result
        assert "50 processed" in result
        assert "Done." in result
        request.response.setHeader.assert_called_once_with("Content-Type", "text/plain")

    @patch("plone.pgthumbor.purge_scales.purge_scales")
    def test_the_view_passes_limit_and_start_through(self, mock_purge):
        mock_purge.return_value = self._result(done=False, next_start=1000)

        result = PurgeScalesView(MagicMock(), self._request(limit="500", start="500"))()

        mock_purge.assert_called_once_with(
            mock_purge.call_args.args[0], limit=500, start=500
        )
        # The whole point: the caller is told where to pick up, so a site
        # too large for one request can be walked in bounded ones.
        assert "resume with ?start=1000" in result

    @patch("plone.pgthumbor.purge_scales.purge_scales")
    def test_rubbish_parameters_fall_back_rather_than_raise(self, mock_purge):
        mock_purge.return_value = self._result()

        PurgeScalesView(MagicMock(), self._request(limit="lots", start="-5"))()

        mock_purge.assert_called_once_with(
            mock_purge.call_args.args[0], limit=None, start=0
        )


class TestMainRequestContext:
    """Issue #16: the zconsole path had no browser layer.

    This entry point reindexes image_scales for every object it walks, so
    running it without a request does not merely miss the Thumbor URLs — it
    overwrites the column with null site-wide.
    """

    def _args(self, site="Plone"):
        args = MagicMock()
        args.site = site
        return args

    def test_it_aborts_without_a_usable_request(self, monkeypatch):
        from plone.pgthumbor import purge_scales as module
        from plone.pgthumbor.zconsole import RequestContextError

        purged = []
        monkeypatch.setattr(module, "establish_request", lambda app: app)
        monkeypatch.setattr(
            module, "purge_scales", lambda portal: purged.append(portal)
        )

        with pytest.raises(RequestContextError):
            module.main(MagicMock(), self._args())

        # Nothing walked, nothing reindexed, nothing nulled.
        assert purged == []

    def test_it_establishes_the_request_before_checking(self, monkeypatch):
        from plone.pgthumbor import purge_scales as module

        order = []
        monkeypatch.setattr(
            module, "establish_request", lambda app: order.append("establish") or app
        )
        monkeypatch.setattr(
            module,
            "require_thumbor_request",
            lambda: order.append("require"),
        )
        monkeypatch.setattr(
            module,
            "purge_scales",
            lambda portal, **kw: (
                order.append("purge")
                or {
                    "purged": 0,
                    "reindexed": 0,
                    "skipped": 0,
                    "processed": 0,
                    "next_start": 0,
                    "done": True,
                }
            ),
        )
        monkeypatch.setattr(
            "AccessControl.SecurityManagement.newSecurityManager",
            lambda *a: None,
            raising=False,
        )

        # A real stub, not a MagicMock: MagicMock does not auto-create
        # dunder-looking attributes, and Acquisition's __of__ is one.
        class _User:
            def __of__(self, container):
                return self

        app = MagicMock()
        app.acl_users.getUserById.return_value = _User()
        module.main(app, self._args())

        assert order == ["establish", "require", "purge"]


class TestReindexOnlyWhatWasPurged:
    """Issue #16, problem 1.

    ``_has_image_scales_metadata`` asks the *catalog schema*, which is
    constant for the whole run, so the old code reindexed every catalogued
    object rather than the ones it actually changed.  On a site with 139k
    objects and a handful of legacy scales that is O(site) catalog writes
    for O(handful) of work.
    """

    @patch("plone.pgthumbor.purge_scales.transaction")
    @patch("plone.pgthumbor.purge_scales.IAnnotations")
    def test_an_untouched_object_is_not_reindexed(self, mock_ia, mock_txn):
        from plone.pgthumbor.purge_scales import purge_scales

        purged_obj, purged_ann = _make_obj(has_scales=True)
        clean_obj, clean_ann = _make_obj(has_scales=False)
        mock_ia.side_effect = [purged_ann, clean_ann]
        portal = _make_portal([_make_brain(purged_obj), _make_brain(clean_obj)])

        result = purge_scales(portal)

        purged_obj.reindexObject.assert_called_once_with(idxs=["image_scales"])
        clean_obj.reindexObject.assert_not_called()
        assert result["purged"] == 1
        assert result["reindexed"] == 1

    @patch("plone.pgthumbor.purge_scales.transaction")
    @patch("plone.pgthumbor.purge_scales.IAnnotations")
    def test_the_schema_is_asked_once_not_per_object(self, mock_ia, mock_txn):
        from plone.pgthumbor.purge_scales import purge_scales

        objs = [_make_obj(has_scales=True) for _ in range(5)]
        mock_ia.side_effect = [ann for _, ann in objs]
        portal = _make_portal([_make_brain(obj) for obj, _ in objs])

        purge_scales(portal)

        # It answers the same for every object in a run, so asking per
        # object is a catalog round trip per object for nothing.
        assert portal.portal_catalog.schema.call_count == 1


class TestChunking:
    """Issue #16, problem 2: the walk has to be resumable in bounded slices."""

    @patch("plone.pgthumbor.purge_scales.transaction")
    @patch("plone.pgthumbor.purge_scales.IAnnotations")
    def test_a_limit_bounds_the_slice(self, mock_ia, mock_txn):
        from plone.pgthumbor.purge_scales import purge_scales

        objs = [_make_obj(has_scales=True) for _ in range(10)]
        mock_ia.side_effect = [ann for _, ann in objs]
        portal = _make_sliceable_portal([_make_brain(obj) for obj, _ in objs])

        result = purge_scales(portal, limit=4)

        assert result["processed"] == 4
        assert result["next_start"] == 4
        assert result["done"] is False

    @patch("plone.pgthumbor.purge_scales.transaction")
    @patch("plone.pgthumbor.purge_scales.IAnnotations")
    def test_it_resumes_from_next_start(self, mock_ia, mock_txn):
        from plone.pgthumbor.purge_scales import purge_scales

        objs = [_make_obj(has_scales=True) for _ in range(10)]
        mock_ia.side_effect = [ann for _, ann in objs]
        brains = [
            _make_brain(obj, path=f"/site/{i:02d}") for i, (obj, _) in enumerate(objs)
        ]
        portal = _make_sliceable_portal(brains)

        first = purge_scales(portal, limit=4)
        second = purge_scales(portal, limit=4, start=first["next_start"])

        assert second["next_start"] == 8
        # Objects 4..7, not 0..3 again.
        walked = [obj for obj, _ in objs]
        assert walked[4].reindexObject.called
        assert walked[0].reindexObject.call_count == 1

    @patch("plone.pgthumbor.purge_scales.transaction")
    @patch("plone.pgthumbor.purge_scales.IAnnotations")
    def test_a_short_slice_means_done(self, mock_ia, mock_txn):
        from plone.pgthumbor.purge_scales import purge_scales

        objs = [_make_obj(has_scales=True) for _ in range(3)]
        mock_ia.side_effect = [ann for _, ann in objs]
        portal = _make_sliceable_portal([_make_brain(obj) for obj, _ in objs])

        result = purge_scales(portal, limit=10)

        assert result["processed"] == 3
        assert result["done"] is True

    @patch("plone.pgthumbor.purge_scales.transaction")
    @patch("plone.pgthumbor.purge_scales.IAnnotations")
    def test_a_chunked_walk_sorts_on_path(self, mock_ia, mock_txn):
        from plone.pgthumbor.purge_scales import purge_scales

        mock_ia.side_effect = []
        portal = _make_sliceable_portal([])

        purge_scales(portal, limit=5)

        # Without an explicit sort the order is whatever PostgreSQL
        # returns, which is not stable across queries — and an offset into
        # an unstable order silently skips objects on resume.  Paths do not
        # change during a purge, so ordering on them is safe.
        query = portal.portal_catalog.unrestrictedSearchResults.call_args.kwargs
        assert query["sort_on"] == "path"
        assert query["b_start"] == 0
        assert query["b_size"] == 5

    @patch("plone.pgthumbor.purge_scales.transaction")
    @patch("plone.pgthumbor.purge_scales.IAnnotations")
    def test_an_unchunked_walk_stays_as_before(self, mock_ia, mock_txn):
        from plone.pgthumbor.purge_scales import purge_scales

        mock_ia.side_effect = []
        portal = _make_portal([])

        result = purge_scales(portal)

        # No limit means the whole catalog in one go, the old behaviour,
        # and no cost for sorting it.
        query = portal.portal_catalog.unrestrictedSearchResults.call_args.kwargs
        assert "b_size" not in query
        assert result["done"] is True
