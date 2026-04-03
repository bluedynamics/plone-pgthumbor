"""Thumbor scale storage — no Pillow, no image data, no ZODB writes.

Overrides AnnotationStorage to prevent any actual image scaling.
Uses pre_scale() for everything — dimension computation only.
The storage property returns a volatile (non-persistent) dict so that
no ScalesDict objects are written to ZODB.

The adapter factory ``thumbor_scale_storage_factory`` checks at runtime
whether IPlonePgthumborLayer is active. If not (e.g. pgthumbor not
installed in this Plone site), it falls back to AnnotationStorage.
"""

from __future__ import annotations

from plone.pgthumbor.interfaces import IPlonePgthumborLayer
from plone.scale.storage import AnnotationStorage
from zope.globalrequest import getRequest

import logging


logger = logging.getLogger(__name__)


def thumbor_scale_storage_factory(context, modified=None):
    """Adapter factory that returns ThumborScaleStorage only when active.

    IImageScaleStorage adapters receive (context, modified_callable) — never
    a request.  We cannot use a layer discriminator in ZCML, so we check
    the browser layer at runtime instead.
    """
    request = getRequest()
    if request is not None and IPlonePgthumborLayer.providedBy(request):
        return ThumborScaleStorage(context, modified)
    return AnnotationStorage(context, modified)


class ThumborScaleStorage(AnnotationStorage):
    """Scale storage that never generates actual image data.

    In a Thumbor setup, all scaling is done by the Thumbor server.
    This storage only stores dimension metadata (uid, width, height)
    for catalog metadata and img tag generation. No Pillow is invoked.

    The ``storage`` property returns a plain dict instead of a
    PersistentMapping/ScalesDict, so no ZODB write transactions are
    created when pre_scale() stores dimension metadata.
    """

    @property
    def storage(self):
        """Return a volatile (non-persistent) dict.

        This replaces the inherited property that returns a ScalesDict
        (PersistentMapping) stored in IAnnotations. Since Thumbor handles
        all image scaling, we don't need to persist scale metadata in ZODB.
        The dict lives only for the lifetime of this adapter instance.
        """
        try:
            return self._volatile_storage
        except AttributeError:
            self._volatile_storage = {}
            return self._volatile_storage

    def scale(self, **parameters):
        """Return pre_scale result — no actual image data generation."""
        return self.pre_scale(**parameters)

    def get_or_generate(self, uid):
        """Return stored info without generating image data.

        Unlike the parent, never calls generate_scale() even if
        data is None — in Thumbor mode, data is always None.
        """
        return self.get(uid)

    def generate_scale(self, uid=None, **parameters):
        """Override to prevent Pillow invocation.

        Delegates to pre_scale which only computes dimensions.
        """
        return self.pre_scale(**parameters)
