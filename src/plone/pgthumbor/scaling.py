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
from plone.pgthumbor.interfaces import ICropProvider
from plone.pgthumbor.url import scale_mode_to_thumbor
from plone.pgthumbor.url import thumbor_url
from ZODB.utils import u64
from zope.component import queryAdapter

import logging


logger = logging.getLogger(__name__)

# Content types that should NOT go through Thumbor
_SKIP_THUMBOR_TYPES = {"image/svg+xml"}


def _needs_auth_url(context, zoid: int, paranoid_mode: bool = False) -> bool:
    """Return True if content_zoid should be appended to the Thumbor URL.

    Paranoid mode: always True — 3-segment URL for every image.
    Normal mode: True only if 'Anonymous' is NOT in allowedRolesAndUsers
                 (i.e. content is not publicly accessible).

    Fails safe (returns True) if the registry or PG is unavailable.
    """
    if paranoid_mode:
        return True

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


def _build_thumbor_url(context, data, width, height, mode, crop=None):
    """Build a Thumbor URL for the given image data and dimensions.

    Returns None if Thumbor is not applicable (SVG, no config, no blob).

    When *crop* is set, Thumbor performs an explicit crop before resizing.
    In that case fit_in is forced True and smart is forced False (explicit
    crop overrides smart detection).
    """
    content_type = getattr(data, "contentType", "") if data else ""
    if content_type in _SKIP_THUMBOR_TYPES:
        return None

    cfg = get_thumbor_config()
    if cfg is None:
        return None

    blob_ids = get_blob_ids(data)
    if blob_ids is None:
        return None

    zoid, tid = blob_ids

    # Determine whether to append content_zoid for access control
    content_zoid = None
    oid = getattr(context, "_p_oid", None)
    if isinstance(oid, bytes) and len(oid) == 8:
        content_zoid_int = u64(oid)
        if _needs_auth_url(context, content_zoid_int, cfg.paranoid_mode):
            content_zoid = content_zoid_int

    thumbor_params = scale_mode_to_thumbor(mode, smart_cropping=cfg.smart_cropping)
    if crop is not None:
        # Explicit crop overrides smart detection — let Thumbor crop
        # the specified region and then fit the result.
        thumbor_params["fit_in"] = True
        thumbor_params["smart"] = False

    return thumbor_url(
        server_url=cfg.server_url,
        security_key=cfg.security_key,
        zoid=zoid,
        tid=tid,
        width=width,
        height=height,
        unsafe=cfg.unsafe,
        content_zoid=content_zoid,
        crop=crop,
        **thumbor_params,
    )


def _get_crop(context, fieldname, scale_info):
    """Look up crop coordinates via an ICropProvider adapter.

    Returns ``((left, top), (right, bottom))`` or None.
    """
    provider = queryAdapter(context, ICropProvider)
    if provider is None:
        return None

    # Extract scale name from plone.namedfile's key tuple, e.g.
    # (("fieldname", "image"), ("scale", "preview"), ...)
    scale_name = None
    key = scale_info.get("key") if scale_info else None
    if key:
        scale_name = dict(key).get("scale")

    if not fieldname or not scale_name:
        return None

    box = provider.get_crop(fieldname, scale_name)
    if box is None:
        return None
    # Convert (left, top, right, bottom) to ((left, top), (right, bottom))
    if len(box) == 4:
        return ((box[0], box[1]), (box[2], box[3]))
    return box


def _default_scale_url(context, uid, extension, base_url=None):
    """Default @@images URL (used when parent has no _scale_url)."""
    if base_url is None:
        base_url = context.absolute_url()
    return f"{base_url}/@@images/{uid}.{extension}"


# True if installed plone.namedfile has _scale_url (>= 8.0.0a2)
_HAS_SCALE_URL = hasattr(ImageScale, "_scale_url")


class ThumborImageScale(ImageScale):
    """Scale view that returns Thumbor URLs instead of ZODB-stored data.

    Falls back to standard Plone behavior for:
    - SVG images (Thumbor can't process them)
    - Original images (no scale dimensions — served by Plone directly)
    - When Thumbor is not configured
    """

    _thumbor_url = None

    def __init__(self, context, request, **info):
        super().__init__(context, request, **info)
        # With new plone.namedfile, _scale_url was already called by
        # parent __init__. With old versions, set up Thumbor URL here.
        if not _HAS_SCALE_URL and self._thumbor_url is None and "uid" in info:
            crop = _get_crop(context, info.get("fieldname"), info)
            url = _build_thumbor_url(
                context,
                self.data,
                info.get("width", 0) or 0,
                info.get("height", 0) or 0,
                info.get("mode", "scale"),
                crop=crop,
            )
            if url:
                self._thumbor_url = url
                self.url = url

    def _scale_url(self, uid, extension, base_url=None, scale_info=None):
        """Generate Thumbor URL if possible, otherwise fall back to default."""
        if scale_info and "uid" in scale_info:
            crop = _get_crop(self.context, scale_info.get("fieldname"), scale_info)
            url = _build_thumbor_url(
                self.context,
                self.data,
                scale_info.get("width", 0) or 0,
                scale_info.get("height", 0) or 0,
                scale_info.get("mode", "scale"),
                crop=crop,
            )
            if url:
                self._thumbor_url = url
                return url
        if _HAS_SCALE_URL:
            return super()._scale_url(uid, extension, base_url, scale_info=scale_info)
        return _default_scale_url(self.context, uid, extension, base_url)

    def index_html(self):
        """302 redirect to Thumbor URL instead of streaming ZODB data."""
        if self._thumbor_url:
            self.request.response.redirect(self._thumbor_url)
            return b""
        return super().index_html()


class ThumborImageScaling(ImageScaling):
    """@@images view override that uses ThumborImageScale."""

    _scale_view_class = ThumborImageScale

    def _scale_url(self, uid, extension, base_url=None, scale_info=None):
        """Generate Thumbor URL for srcset entries."""
        if scale_info and scale_info.get("fieldname"):
            data = getattr(self.context, scale_info["fieldname"], None)
            if data is not None:
                crop = _get_crop(self.context, scale_info["fieldname"], scale_info)
                url = _build_thumbor_url(
                    self.context,
                    data,
                    scale_info.get("width", 0) or 0,
                    scale_info.get("height", 0) or 0,
                    scale_info.get("mode", "scale"),
                    crop=crop,
                )
                if url:
                    return url
        if _HAS_SCALE_URL:
            return super()._scale_url(uid, extension, base_url, scale_info=scale_info)
        return _default_scale_url(self.context, uid, extension, base_url)
