"""Tests for plone.pgthumbor.purge_scales."""

from __future__ import annotations

from plone.pgthumbor.purge_scales import ANNOTATION_KEY
from plone.pgthumbor.purge_scales import purge_scales
from plone.pgthumbor.purge_scales import PurgeScalesView
from unittest.mock import MagicMock
from unittest.mock import patch


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

        purged, reindexed, skipped, total = purge_scales(portal)

        assert purged == 2
        assert reindexed == 3
        assert skipped == 0
        assert total == 3
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

        purged, reindexed, _skipped, _total = purge_scales(portal)

        assert purged == 1
        assert reindexed == 0
        obj.reindexObject.assert_not_called()

    @patch("plone.pgthumbor.purge_scales.transaction")
    @patch("plone.pgthumbor.purge_scales.IAnnotations")
    def test_skips_broken_objects(self, mock_ia, mock_txn):
        brain = MagicMock()
        brain._unrestrictedGetObject.side_effect = Exception("broken")

        portal = _make_portal([brain])

        purged, reindexed, skipped, total = purge_scales(portal)

        assert purged == 0
        assert reindexed == 0
        assert skipped == 1
        assert total == 1
        mock_txn.commit.assert_not_called()

    @patch("plone.pgthumbor.purge_scales.transaction")
    @patch("plone.pgthumbor.purge_scales.IAnnotations")
    def test_skips_non_annotatable(self, mock_ia, mock_txn):
        mock_ia.side_effect = TypeError("not adaptable")

        portal = _make_portal([_make_brain(MagicMock())])

        purged, reindexed, skipped, total = purge_scales(portal)

        assert purged == 0
        assert reindexed == 0
        assert skipped == 1
        assert total == 1

    @patch("plone.pgthumbor.purge_scales.transaction")
    @patch("plone.pgthumbor.purge_scales.IAnnotations")
    def test_no_commit_when_nothing_to_purge(self, mock_ia, mock_txn):
        _, ann = _make_obj(has_scales=False)
        mock_ia.return_value = ann

        portal = _make_portal([_make_brain(MagicMock())], has_image_scales_meta=False)

        purged, _reindexed, _skipped, _total = purge_scales(portal)

        assert purged == 0
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

        purged, reindexed, skipped, _total = purge_scales(portal)

        assert purged == 1
        assert reindexed == 0
        assert skipped == 0


class TestPurgeScalesView:
    @patch("plone.pgthumbor.purge_scales.purge_scales")
    def test_view_calls_purge_and_returns_text(self, mock_purge):
        mock_purge.return_value = (10, 45, 2, 50)
        context = MagicMock()
        request = MagicMock()

        view = PurgeScalesView(context, request)
        result = view()

        mock_purge.assert_called_once_with(context)
        assert "10" in result
        assert "45" in result
        assert "2 skipped" in result
        assert "50 total" in result
        request.response.setHeader.assert_called_once_with("Content-Type", "text/plain")
