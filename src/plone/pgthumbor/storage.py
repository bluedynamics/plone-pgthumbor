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
from plone.registry.interfaces import IRegistry
from plone.scale.storage import AnnotationStorage
from zope.component import queryUtility
from zope.globalrequest import getRequest

import logging
import re


logger = logging.getLogger(__name__)

# plone.scale >= 4 deterministic uid: {fieldname}-{width}-{md5hex}
_LEGACY_UID_RE = re.compile(
    r"^(?P<fieldname>.+)-(?P<width>\d{1,9})-(?P<hash>[0-9a-f]{32})$"
)


def _allowed_scale_sizes():
    """Map width -> (width, height) for all registered plone.allowed_sizes."""
    registry = queryUtility(IRegistry)
    if registry is None:
        return {}
    sizes = {}
    for line in registry.get("plone.allowed_sizes") or ():
        try:
            _name, dims = line.split()
            width, height = dims.split(":")
            sizes.setdefault(int(width), (int(width), int(height)))
        except ValueError:
            continue
    return sizes


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
        """Return stored info, or heal a legacy uid without stored state.

        The volatile storage is empty on every fresh adapter instance, so
        uid URLs from cached HTML or stale image_scales catalog metadata
        would always 404 (issue #17).  The uid format is deterministic
        and parseable — regenerate the info on the fly instead.
        """
        info = self.get(uid)
        if info is not None:
            return info
        return self._heal_legacy_uid(uid)

    def _heal_legacy_uid(self, uid):
        """Rebuild scale info from a ``{fieldname}-{width}-{md5hex}`` uid.

        Only widths registered in ``plone.allowed_sizes`` are accepted
        (width 0 = original dimensions), so this cannot be abused to get
        arbitrary dimensions signed on demand.  ``pre_scale`` itself
        returns None for unknown fields or empty values.
        """
        match = _LEGACY_UID_RE.match(uid)
        if match is None:
            return None
        fieldname = match.group("fieldname")
        width = int(match.group("width"))
        if width == 0:
            dims = (None, None)
        else:
            dims = _allowed_scale_sizes().get(width)
            if dims is None:
                return None
        info = self.pre_scale(
            fieldname=fieldname, width=dims[0], height=dims[1], mode="scale"
        )
        if info is not None:
            info.setdefault("fieldname", fieldname)
        return info

    def generate_scale(self, uid=None, **parameters):
        """Override to prevent Pillow invocation.

        Delegates to pre_scale which only computes dimensions.
        """
        return self.pre_scale(**parameters)
