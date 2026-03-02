"""Thumbor scale storage — no Pillow, no image data.

Overrides AnnotationStorage to prevent any actual image scaling.
Uses pre_scale() for everything — dimension computation only.
"""

from __future__ import annotations

from plone.scale.storage import AnnotationStorage

import logging


logger = logging.getLogger(__name__)


class ThumborScaleStorage(AnnotationStorage):
    """Scale storage that never generates actual image data.

    In a Thumbor setup, all scaling is done by the Thumbor server.
    This storage only stores dimension metadata (uid, width, height)
    for catalog metadata and img tag generation. No Pillow is invoked.
    """

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
