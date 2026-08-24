"""The request context every reindexing entry point has to establish.

These tests are the gate on the most destructive path in the package.  A
reindex run without a request does not skip ``image_scales``; it overwrites
the column with null for every object it walks.  See the module docstring
of ``plone.pgthumbor.zconsole`` for the chain.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _no_leaked_request():
    """The global request is process-wide; never let it leak between tests."""
    from zope.globalrequest import setRequest

    setRequest(None)
    yield
    setRequest(None)


def _layered_request():
    from plone.pgthumbor.interfaces import IPlonePgthumborLayer
    from zope.interface import alsoProvides

    request = MagicMock()
    alsoProvides(request, IPlonePgthumborLayer)
    return request


def _view_lookup(monkeypatch, factory):
    from plone.pgthumbor import zconsole

    manager = MagicMock()
    manager.adapters.lookup.return_value = factory
    monkeypatch.setattr(zconsole, "getSiteManager", lambda: manager, raising=False)
    monkeypatch.setattr(
        "zope.component.getSiteManager", lambda *a, **k: manager, raising=False
    )
    return manager


class TestRequireThumborRequest:
    """Three ways to get it wrong, each with its own failure."""

    def test_no_request_at_all_aborts(self):
        from plone.pgthumbor.zconsole import RequestContextError
        from plone.pgthumbor.zconsole import require_thumbor_request

        # The zconsole default. makerequest() sets app.REQUEST but never
        # calls setRequest(), so getRequest() stays None.
        with pytest.raises(RequestContextError, match="getRequest"):
            require_thumbor_request()

    def test_a_request_without_the_layer_aborts(self):
        from plone.pgthumbor.zconsole import RequestContextError
        from plone.pgthumbor.zconsole import require_thumbor_request
        from zope.globalrequest import setRequest

        setRequest(MagicMock())

        # Dormant package: image_scales would be rewritten with @@images
        # URLs rather than Thumbor URLs, which is issue #14.
        with pytest.raises(RequestContextError, match="IPlonePgthumborLayer"):
            require_thumbor_request()

    def test_a_layer_without_the_zcml_aborts(self, monkeypatch):
        from plone.pgthumbor.zconsole import RequestContextError
        from plone.pgthumbor.zconsole import require_thumbor_request
        from zope.globalrequest import setRequest

        setRequest(_layered_request())
        _view_lookup(monkeypatch, None)

        # Looks right from the request, but the stock ImageScaling view is
        # still in place, so the rewrite has the same effect as no layer.
        with pytest.raises(RequestContextError, match="@@images"):
            require_thumbor_request()

    def test_a_foreign_images_view_aborts(self, monkeypatch):
        from plone.pgthumbor.zconsole import RequestContextError
        from plone.pgthumbor.zconsole import require_thumbor_request
        from zope.globalrequest import setRequest

        setRequest(_layered_request())

        class SomeoneElsesView:
            pass

        _view_lookup(monkeypatch, SomeoneElsesView)

        with pytest.raises(RequestContextError, match="@@images"):
            require_thumbor_request()

    def test_a_fully_wired_process_passes(self, monkeypatch):
        from plone.pgthumbor.scaling import ThumborImageScaling
        from plone.pgthumbor.zconsole import require_thumbor_request
        from zope.globalrequest import setRequest

        request = _layered_request()
        setRequest(request)

        # Five's metaconfigure registers a subclass, never the class itself.
        class ImagesView(ThumborImageScaling):
            pass

        _view_lookup(monkeypatch, ImagesView)

        assert require_thumbor_request() is request

    def test_it_aborts_before_anything_is_written(self):
        """The abort is the point, not the exception type.

        A caller must be able to rely on nothing having happened, so the
        check goes first and raises rather than logging and continuing.
        """
        from plone.pgthumbor.zconsole import require_thumbor_request

        with pytest.raises(Exception):  # noqa: B017 - any abort will do
            require_thumbor_request()


class TestEstablishRequest:
    """What an entry point has to do before it may index."""

    def test_it_sets_the_global_request(self, monkeypatch):
        from plone.pgthumbor import zconsole
        from zope.globalrequest import getRequest

        app = MagicMock()
        monkeypatch.setattr(
            "Testing.makerequest.makerequest", lambda given: given, raising=False
        )

        zconsole.establish_request(app)

        # makerequest alone leaves getRequest() at None; setRequest is the
        # step that makes the indexer able to find it.
        assert getRequest() is app.REQUEST

    def test_it_marks_the_request_with_the_browser_layer(self, monkeypatch):
        from plone.pgthumbor import zconsole
        from plone.pgthumbor.interfaces import IPlonePgthumborLayer

        app = MagicMock()
        monkeypatch.setattr(
            "Testing.makerequest.makerequest", lambda given: given, raising=False
        )

        zconsole.establish_request(app)

        assert IPlonePgthumborLayer.providedBy(app.REQUEST)

    def test_the_result_satisfies_the_guard(self, monkeypatch):
        from plone.pgthumbor import zconsole
        from plone.pgthumbor.scaling import ThumborImageScaling

        app = MagicMock()
        monkeypatch.setattr(
            "Testing.makerequest.makerequest", lambda given: given, raising=False
        )

        class ImagesView(ThumborImageScaling):
            pass

        _view_lookup(monkeypatch, ImagesView)
        zconsole.establish_request(app)

        # The pair has to compose: establishing a request must be enough to
        # get past the guard, or every entry point would need its own dance.
        assert zconsole.require_thumbor_request() is app.REQUEST
