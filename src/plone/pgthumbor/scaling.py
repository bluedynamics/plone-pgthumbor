"""Thumbor-based image scaling for Plone.

Overrides plone.namedfile's ImageScale and ImageScaling to generate
Thumbor URLs instead of ZODB-stored scaled images.
"""

from __future__ import annotations

from AccessControl.PermissionRole import rolesForPermissionOn
from plone.namedfile.scaling import _image_tag_from_values
from plone.namedfile.scaling import _marker
from plone.namedfile.scaling import ImageScale
from plone.namedfile.scaling import ImageScaling
from plone.pgthumbor.blob import get_blob_ids
from plone.pgthumbor.config import get_thumbor_config
from plone.pgthumbor.interfaces import ICropProvider
from plone.pgthumbor.url import scale_mode_to_thumbor
from plone.pgthumbor.url import thumbor_url
from plone.rfc822.interfaces import IPrimaryFieldInfo
from ZODB.utils import u64
from zope.component import queryAdapter

import logging


logger = logging.getLogger(__name__)

# Content types that should NOT go through Thumbor
_SKIP_THUMBOR_TYPES = {"image/svg+xml"}


def _needs_auth_url(
    context, zoid: int | None = None, paranoid_mode: bool = False
) -> bool:
    """Return True if content_zoid should be appended to the Thumbor URL.

    Paranoid mode: always True — 3-segment URL for every image.
    Normal mode: True only if 'Anonymous' is NOT among the roles that hold
                 View on ``context`` (i.e. content is not publicly readable).

    Uses Zope's in-memory role/permission map (``rolesForPermissionOn``),
    which is the source of truth that plone-pgcatalog's ``allowed_roles``
    column merely caches.  No DB round-trip, no pool acquisition, and no
    catalog-lag skew vs. live workflow state.

    The ``zoid`` argument is kept for back-compat but unused.
    """
    if paranoid_mode:
        return True

    try:
        roles = rolesForPermissionOn("View", context) or ()
        return "Anonymous" not in roles
    except Exception:
        logger.warning(
            "Failed to check auth requirement for context=%r", context, exc_info=True
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


def _skip_type_fallback_url(context, data, fieldname, base_url=None):
    """Original-field URL for types Thumbor cannot process (SVG).

    Browsers scale vector images themselves; with the volatile
    ThumborScaleStorage a uid-based scale URL can never resolve
    (issue #17), so the field URL is the only stable target.  The
    ``?v=`` modification-time cache buster compensates for the weaker
    HTTP caching of non-unique URLs.
    """
    if base_url is None:
        base_url = context.absolute_url()
    url = f"{base_url}/@@images/{fieldname}"
    modified = getattr(data, "modified", None)
    if modified is None:
        modified = getattr(context, "_p_mtime", None)
    try:
        return f"{url}?v={int(float(modified) * 1000)}"
    except (TypeError, ValueError):
        return url


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
            elif getattr(self.data, "contentType", "") in _SKIP_THUMBOR_TYPES:
                fieldname = info.get("fieldname") or getattr(self, "fieldname", None)
                if fieldname:
                    self.url = _skip_type_fallback_url(context, self.data, fieldname)

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
        if scale_info and getattr(self.data, "contentType", "") in _SKIP_THUMBOR_TYPES:
            fieldname = scale_info.get("fieldname") or getattr(self, "fieldname", None)
            if fieldname:
                return _skip_type_fallback_url(
                    self.context, self.data, fieldname, base_url
                )
        if _HAS_SCALE_URL:
            return super()._scale_url(uid, extension, base_url, scale_info=scale_info)
        return _default_scale_url(self.context, uid, extension, base_url)

    def srcset_attribute(self):
        """HiDPI srcset with Thumbor URLs — uid URLs never resolve here.

        Skip-types get no srcset (vector scales itself); entries where no
        Thumbor URL can be built are dropped rather than emitted dead.
        """
        if not self.srcset:
            return ""
        if getattr(self.data, "contentType", "") in _SKIP_THUMBOR_TYPES:
            return ""
        fieldname = getattr(self, "fieldname", None)
        parts = []
        for entry in self.srcset:
            factor = entry.get("scale")
            if not factor:
                continue
            crop = _get_crop(self.context, fieldname, entry)
            url = _build_thumbor_url(
                self.context,
                self.data,
                entry.get("width", 0) or 0,
                entry.get("height", 0) or 0,
                entry.get("mode", "scale"),
                crop=crop,
            )
            if url:
                parts.append(f"{url} {factor}x")
        return ", ".join(parts)

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
                if getattr(data, "contentType", "") in _SKIP_THUMBOR_TYPES:
                    return _skip_type_fallback_url(
                        self.context, data, scale_info["fieldname"], base_url
                    )
        if _HAS_SCALE_URL:
            return super()._scale_url(uid, extension, base_url, scale_info=scale_info)
        return _default_scale_url(self.context, uid, extension, base_url)

    def srcset(
        self,
        fieldname=None,
        scale_in_src="huge",
        sizes="",
        alt=_marker,
        css_class=None,
        title=_marker,
        **kwargs,
    ):
        """Reimplementation of plone.namedfile's srcset().

        The parent builds ``@@images/{uid}`` URLs straight from
        ``storage.pre_scale`` — dead under the volatile storage
        (issue #17).  Build every URL from a scale view instead, whose
        ``.url`` is a Thumbor URL (raster) or field URL (skip-types).
        """
        if fieldname is None:
            try:
                primary = IPrimaryFieldInfo(self.context, None)
            except TypeError:
                return
            if primary is None:
                return
            fieldname = primary.fieldname

        data = getattr(self.context, fieldname, None)
        if getattr(data, "contentType", "") in _SKIP_THUMBOR_TYPES:
            # Vector: one URL fits all widths — plain img tag suffices.
            return self.tag(
                fieldname=fieldname,
                alt=alt,
                css_class=css_class,
                title=title,
                **kwargs,
            )

        original_width, original_height = self.getImageSize(fieldname)
        if not original_width or not original_height:
            return None

        srcset_urls = []

        # Back-fill an original-size entry when no configured scale
        # already covers it — mirrors the parent's guard so an
        # undersized original still yields a non-empty srcset. The URL
        # always comes from a scale view, never a bare uid string.
        available_widths = [width for (width, _height) in self.available_sizes.values()]
        if original_width not in available_widths:
            scale_view = self.scale(
                fieldname=fieldname,
                width=original_width,
                height=original_height,
                pre=True,
                include_srcset=False,
            )
            if scale_view is not None:
                srcset_urls.append(f"{scale_view.url} {scale_view.width}w")

        for _name, (width, height) in self.available_sizes.items():
            if width <= original_width:
                scale_view = self.scale(
                    fieldname=fieldname,
                    width=width,
                    height=height,
                    pre=True,
                    include_srcset=False,
                )
                if scale_view is not None:
                    srcset_urls.append(f"{scale_view.url} {scale_view.width}w")

        attributes = {}
        if title is _marker:
            attributes["title"] = self.context.Title()
        elif title:
            attributes["title"] = title
        if alt is _marker:
            attributes["alt"] = self.context.Title()
        else:
            attributes["alt"] = alt
        if css_class is not None:
            attributes["class"] = css_class
        attributes.update(**kwargs)
        attributes["sizes"] = sizes
        attributes["srcset"] = ", ".join(srcset_urls)

        if scale_in_src not in self.available_sizes:
            for key, (width, _height) in self.available_sizes.items():
                if width <= original_width:
                    scale_in_src = key
                    break

        scale_view = self.scale(fieldname=fieldname, scale=scale_in_src, pre=True)
        if scale_view is None:
            return None
        attributes["src"] = scale_view.url
        if "width" not in attributes:
            attributes["width"] = scale_view.width
        if "height" not in attributes:
            attributes["height"] = scale_view.height

        return _image_tag_from_values(*attributes.items())
