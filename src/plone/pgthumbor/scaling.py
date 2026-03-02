"""Thumbor-based image scaling for Plone.

Overrides plone.namedfile's ImageScale and ImageScaling to generate
Thumbor URLs instead of ZODB-stored scaled images.
"""

from __future__ import annotations

from plone.namedfile.scaling import ImageScale
from plone.namedfile.scaling import ImageScaling
from plone.pgcatalog.pool import get_pool
from plone.pgcatalog.pool import get_request_connection
from plone.pgthumbor.blob import get_blob_ids
from plone.pgthumbor.config import get_thumbor_config
from plone.pgthumbor.interfaces import IThumborSettings
from plone.pgthumbor.url import scale_mode_to_thumbor
from plone.pgthumbor.url import thumbor_url
from plone.registry.interfaces import IRegistry
from ZODB.utils import u64
from zope.component import queryUtility

import logging


logger = logging.getLogger(__name__)

# Content types that should NOT go through Thumbor
_SKIP_THUMBOR_TYPES = {"image/svg+xml"}


def _needs_auth_url(context, zoid: int) -> bool:
    """Return True if content_zoid should be appended to the Thumbor URL.

    Paranoid mode: always True — 3-segment URL for every image.
    Normal mode: True only if 'Anonymous' is NOT in allowedRolesAndUsers
                 (i.e. content is not publicly accessible).

    Fails safe (returns True) if the registry or PG is unavailable.
    """
    # Check paranoid mode from Plone registry
    try:
        registry = queryUtility(IRegistry)
        if registry is not None:
            settings = registry.forInterface(IThumborSettings, check=False)
            if getattr(settings, "paranoid_mode", False):
                return True
    except Exception:
        pass

    # Direct PG query: check if 'Anonymous' is in allowedRolesAndUsers
    try:
        pool = get_pool(context)
        conn = get_request_connection(pool)
        row = conn.execute(
            "SELECT ((idx->'allowedRolesAndUsers') ? 'Anonymous') AS is_anon "
            "FROM object_state WHERE zoid = %s",
            (zoid,),
        ).fetchone()
        if row is None:
            return True  # not in catalog → be conservative
        return not row["is_anon"]
    except Exception:
        logger.warning(
            "Failed to check auth requirement for zoid=%d", zoid, exc_info=True
        )
        return True  # fail safe → use auth URL


class ThumborImageScale(ImageScale):
    """Scale view that returns Thumbor URLs instead of ZODB-stored data.

    Falls back to standard Plone behavior for:
    - SVG images (Thumbor can't process them)
    - Original images (no scale dimensions — served by Plone directly)
    - When Thumbor is not configured
    """

    _thumbor_url = None  # Set in __init__ if Thumbor is applicable

    def __init__(self, context, request, **info):
        # Let parent set up all standard attributes first
        super().__init__(context, request, **info)

        # Check if this is an original image (no uid = no scale requested)
        if "uid" not in info:
            return

        # Skip Thumbor for SVGs
        content_type = getattr(self.data, "contentType", "") if self.data else ""
        if content_type in _SKIP_THUMBOR_TYPES:
            return

        # Get Thumbor config
        cfg = get_thumbor_config()
        if cfg is None:
            return

        # Get blob IDs from the image data
        blob_ids = get_blob_ids(self.data)
        if blob_ids is None:
            return

        zoid, tid = blob_ids
        width = info.get("width", 0) or 0
        height = info.get("height", 0) or 0
        mode = info.get("mode", "scale")

        # Determine whether to append content_zoid for access control
        content_zoid = None
        oid = getattr(context, "_p_oid", None)
        if isinstance(oid, bytes) and len(oid) == 8:
            content_zoid_int = u64(oid)
            if _needs_auth_url(context, content_zoid_int):
                content_zoid = content_zoid_int

        # Map Plone scale mode to Thumbor params
        thumbor_params = scale_mode_to_thumbor(mode, smart_cropping=cfg.smart_cropping)

        # Generate Thumbor URL
        self._thumbor_url = thumbor_url(
            server_url=cfg.server_url,
            security_key=cfg.security_key,
            zoid=zoid,
            tid=tid,
            width=width,
            height=height,
            unsafe=cfg.unsafe,
            content_zoid=content_zoid,
            **thumbor_params,
        )
        self.url = self._thumbor_url

    def index_html(self):
        """302 redirect to Thumbor URL instead of streaming ZODB data."""
        if self._thumbor_url:
            self.request.response.redirect(self._thumbor_url)
            return b""
        return super().index_html()


class ThumborImageScaling(ImageScaling):
    """@@images view override that uses ThumborImageScale."""

    _scale_view_class = ThumborImageScale
