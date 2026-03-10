"""Plone registry schema and adapter interfaces for Thumbor integration."""

from __future__ import annotations

from zope import schema
from zope.interface import Interface


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

    Environment variables take precedence over these settings.
    """

    server_url = schema.TextLine(
        title="Thumbor Server URL",
        description="Public URL of the Thumbor server (e.g., http://thumbor:8888)",
        required=False,
        default="",
    )

    security_key = schema.TextLine(
        title="Security Key",
        description="Shared HMAC-SHA1 key for signing Thumbor URLs",
        required=False,
        default="",
    )

    unsafe = schema.Bool(
        title="Unsafe Mode",
        description="Generate unsigned URLs (development only!)",
        required=False,
        default=False,
    )

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
