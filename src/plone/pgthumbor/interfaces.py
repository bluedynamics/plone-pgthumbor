"""Plone registry schema and adapter interfaces for Thumbor integration."""

from __future__ import annotations

from zope import schema
from zope.interface import Interface
from zope.publisher.interfaces.browser import IDefaultBrowserLayer


class IPlonePgthumborLayer(IDefaultBrowserLayer):
    """Browser layer for plone.pgthumbor."""


class ICropProvider(Interface):
    """Adapter providing crop coordinates for Thumbor URLs.

    Implementations are looked up as named adapters on (context,)
    and should return crop box tuples compatible with libthumbor's
    ``crop`` parameter: ``((left, top), (right, bottom))``.
    """

    def get_crop(fieldname, scale_name):
        """Return crop coordinates for a given field and scale.

        Args:
            fieldname: Name of the image field (e.g. "image").
            scale_name: Name of the Plone scale (e.g. "preview", "thumb").

        Returns:
            Tuple ``((left, top), (right, bottom))`` or None if no crop.
        """


class IThumborSettings(Interface):
    """Plone registry settings for Thumbor integration.

    Only settings that can be toggled via the control panel live here.
    server_url, security_key, and unsafe are configured exclusively
    via environment variables (PGTHUMBOR_SERVER_URL, PGTHUMBOR_SECURITY_KEY,
    PGTHUMBOR_UNSAFE).
    """

    smart_cropping = schema.Bool(
        title="Smart Cropping",
        description="Enable Thumbor smart cropping (OpenCV face/feature detection)",
        required=False,
        default=False,
    )

    paranoid_mode = schema.Bool(
        title="Paranoid Mode",
        description=(
            "Always verify image access with Plone for every request, "
            "even for publicly accessible content."
        ),
        required=False,
        default=False,
    )
