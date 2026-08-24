"""Request context for ``zconsole`` entry points.

Everything this package does is gated on ``IPlonePgthumborLayer``, looked up
through ``zope.globalrequest.getRequest()``.  Under ``zconsole`` there is no
publication, so ``makerequest()`` alone is not enough: it sets
``app.REQUEST`` but never calls ``setRequest()``, and ``getRequest()`` keeps
returning ``None``.

A reindex run that way does not merely miss the derivative.  It **overwrites
``image_scales`` with null for every object it touches**, and the chain is
worth spelling out because it is not the obvious one:

1. ``Products.CMFPlone.image_scales.indexer.image_scales`` calls
   ``queryMultiAdapter((obj, getRequest()), IImageScalesAdapter)``.  With
   ``None`` as the request the lookup misses and the indexer raises
   ``AttributeError`` — plone.indexer's deliberate "do not index" signal.
2. ``plone.pgcatalog``'s ``extraction.extract_idx`` reads every metadata
   column as ``getattr(wrapper, name, None)``.  The default **swallows**
   that signal, and the value becomes a plain ``None``.
3. ``None`` is then written rather than skipped, through a JSONB merge that
   puts an explicit ``null`` where the scales used to be.

So the column is not left alone.  It is overwritten, silently, for the
whole population the run walks.  Hence: check first, write second.
"""

from __future__ import annotations

import logging


logger = logging.getLogger(__name__)


class RequestContextError(RuntimeError):
    """This process would not produce Thumbor URLs, so it must not index."""


def establish_request(app):
    """Wrap *app* in a request carrying the pgthumbor browser layer.

    Returns the wrapped app.  ``makerequest`` is what gives ``app`` a
    REQUEST at all; ``alsoProvides`` is what makes this package recognise
    it; ``setRequest`` is what makes ``getRequest()`` find it from the
    indexer, which is the only place that matters.
    """
    from plone.pgthumbor.interfaces import IPlonePgthumborLayer
    from Testing.makerequest import makerequest
    from zope.globalrequest import setRequest
    from zope.interface import alsoProvides

    app = makerequest(app)
    request = app.REQUEST
    alsoProvides(request, IPlonePgthumborLayer)
    setRequest(request)
    return app


def require_thumbor_request():
    """Raise unless this process would produce Thumbor URLs.

    Call before the first write of any script that reindexes.  Three checks,
    each catching a different way of getting it wrong:

    no request at all — the ``zconsole`` default, and the one that nulls the
    column; a request without the layer — the package is dormant and
    ``image_scales`` would be rewritten with ``@@images`` URLs instead of
    Thumbor ones (issue #14); and a layer present while the package's ZCML
    was never loaded, which looks right from the request but leaves the
    stock ``ImageScaling`` view in place, with the same result.
    """
    from plone.pgthumbor.interfaces import IPlonePgthumborLayer
    from zope.globalrequest import getRequest

    request = getRequest()
    if request is None:
        raise RequestContextError(
            "zope.globalrequest.getRequest() is None. Reindexing now would "
            "overwrite image_scales with null for every object touched. Call "
            "establish_request(app) first."
        )
    if not IPlonePgthumborLayer.providedBy(request):
        raise RequestContextError(
            "The request does not provide IPlonePgthumborLayer, so "
            "plone.pgthumbor is dormant. Reindexing now would rewrite "
            "image_scales with @@images URLs instead of Thumbor URLs."
        )
    _require_thumbor_images_view(request)
    return request


def _require_thumbor_images_view(request):
    """Check that ``@@images`` really resolves to this package's view."""
    from plone.namedfile.interfaces import IImageScaleTraversable
    from plone.pgthumbor.scaling import ThumborImageScaling
    from zope.component import getSiteManager
    from zope.interface import Interface
    from zope.interface import providedBy

    factory = getSiteManager().adapters.lookup(
        (IImageScaleTraversable, providedBy(request)), Interface, "images"
    )
    if factory is None or not issubclass(factory, ThumborImageScaling):
        raise RequestContextError(
            "The @@images view does not resolve to ThumborImageScaling "
            f"(got {factory!r}). The browser layer is on the request but "
            "this package's ZCML is not in force, so image_scales would be "
            "rewritten without Thumbor URLs."
        )
    return factory
