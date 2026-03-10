"""ICropProvider adapter for plone.app.imagecropping."""

from __future__ import annotations

from plone.pgthumbor.interfaces import ICropProvider
from zope.annotation.interfaces import IAnnotations
from zope.interface import implementer

import logging


log = logging.getLogger(__name__)

ANNOTATION_KEY = "plone.app.imagecropping"


@implementer(ICropProvider)
class ImageCroppingCropProvider:
    """Read crop coordinates stored by plone.app.imagecropping.

    plone.app.imagecropping stores crops in
    ``IAnnotations(context)["plone.app.imagecropping"]``
    keyed as ``"{fieldname}_{scalename}"`` with values
    ``(left, top, right, bottom)``.
    """

    def __init__(self, context):
        self.context = context

    def get_crop(self, fieldname, scale_name):
        """Return crop box or None."""
        try:
            annotations = IAnnotations(self.context)
        except TypeError:
            return None

        crops = annotations.get(ANNOTATION_KEY)
        if not crops:
            return None

        key = f"{fieldname}_{scale_name}"
        box = crops.get(key)
        if box is None or len(box) != 4:
            return None

        return tuple(int(v) for v in box)
