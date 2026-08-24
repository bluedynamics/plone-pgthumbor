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

from Acquisition import aq_base
from plone.namedfile.scaling import ImageScaling
from plone.pgthumbor.interfaces import IPlonePgthumborLayer
from plone.pgthumbor.uid_healing import candidate_parameters
from plone.pgthumbor.uid_healing import parse_legacy_uid
from plone.pgthumbor.uid_healing import registered_scales
from plone.scale.storage import AnnotationStorage
from ZODB.POSException import ConflictError
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

    def _mint_time(self, fieldname):
        """The modification time the requested uid was hashed against.

        ``ImageScaling.modified`` is what minted it.  ``ImageScaling`` is a
        BrowserView whose ``__init__`` only assigns, so instantiating it here
        is a plain constructor call: no component lookup, no ZCML, and no copy
        of that logic to drift.  The method is byte-identical in
        plone.namedfile 7.3.0 and 8.0.0a3.
        """
        return ImageScaling(self.context, getRequest()).modified(fieldname)

    def _original_size(self, fieldname):
        """The original image's ``(width, height)``, or None.

        Only used to offer the "download" candidate, so an empty field or a
        corrupt image simply removes that one candidate.
        """
        value = getattr(aq_base(self.context), fieldname, None)
        get_size = getattr(value, "getImageSize", None)
        if get_size is None:
            return None
        try:
            return get_size()
        except ConflictError:
            raise
        except Exception:
            logger.warning(
                "Could not read image size for %r field %s",
                self.context,
                fieldname,
                exc_info=True,
            )
            return None

    def _match_candidate(self, uid, fieldname, dimension, scales):
        """Return the parameters whose hash_key equals *uid*, or None."""
        original_size = self._original_size(fieldname)
        for parameters in candidate_parameters(
            fieldname, dimension, scales, original_size
        ):
            if self.hash_key(**parameters) == uid:
                return parameters
        return None

    def _heal_legacy_uid(self, uid):
        """Rebuild scale info for a ``{fieldname}-{width}-{md5hex}`` uid.

        The parameters are not readable out of the uid, so they are enumerated
        and re-hashed until one matches.  That identifies the mode, tells two
        scales sharing a width apart, and resolves ``0:H`` scales, all of which
        the previous width-only heuristic got wrong (issue #21).
        """
        parsed = parse_legacy_uid(uid)
        if parsed is None:
            return None
        fieldname, dimension = parsed

        # publishTraverse adapts (context, None), so ``modified_time`` is None
        # here while the uid was hashed against the field's modification time.
        # Without restoring it, no candidate can ever match.
        mint_time = self._mint_time(fieldname)

        def minted():
            return mint_time

        self.modified = minted

        scales = registered_scales()
        parameters = self._match_candidate(uid, fieldname, dimension, scales)
        if parameters is None:
            return None
        info = self.pre_scale(**parameters)
        if info is not None:
            info.setdefault("fieldname", fieldname)
        return info

    def generate_scale(self, uid=None, **parameters):
        """Override to prevent Pillow invocation.

        Delegates to pre_scale which only computes dimensions.
        """
        return self.pre_scale(**parameters)
