"""GenericSetup install/setup handler for plone.pgthumbor."""

from plone.pgthumbor.interfaces import IThumborSettings
from plone.registry.interfaces import IRegistry
from Products.Five import BrowserView
from zope.component import getUtility


_REMOVED_REGISTRY_KEYS = (
    "plone.pgthumbor.settings.server_url",
    "plone.pgthumbor.settings.security_key",
    "plone.pgthumbor.settings.unsafe",
)


def upgrade_to_3(context):
    """Remove server_url, security_key, and unsafe from registry.

    These fields are configured exclusively via environment variables
    and were never read from the registry.
    """
    registry = getUtility(IRegistry)
    for key in _REMOVED_REGISTRY_KEYS:
        if key in registry.records:
            del registry.records[key]


def post_install(context):
    """Register IThumborSettings in the Plone registry."""
    registry = getUtility(IRegistry)
    registry.registerInterface(IThumborSettings, prefix="plone.pgthumbor.settings")


class SetupView(BrowserView):
    """@@thumbor-setup — diagnose registry state."""

    def __call__(self):
        registry = getUtility(IRegistry)
        lines = ["Registry diagnostics:"]
        key = "plone.pgthumbor.settings.smart_cropping"

        # Check all access paths
        lines.append(f"key in registry: {key in registry}")
        lines.append(f"key in registry.records: {key in registry.records}")

        # Check _values OOBTree directly
        values = registry.records._values
        lines.append(f"type(_values): {type(values)}")
        lines.append(f"key in _values: {key in values}")
        lines.append(f"_values._p_oid: {values._p_oid!r}")
        lines.append(f"_values._p_serial: {values._p_serial!r}")

        # List all keys matching pgthumbor
        matching = [k for k in values if "pgthumbor" in k]
        lines.append(f"pgthumbor keys in _values: {matching}")

        # Try forInterface with check=False (skip __contains__)
        try:
            proxy = registry.forInterface(
                IThumborSettings,
                prefix="plone.pgthumbor.settings",
                check=False,
            )
            lines.append(
                f"forInterface(check=False) OK: smart_cropping={proxy.smart_cropping!r}"
            )
        except Exception as e:
            lines.append(f"forInterface(check=False) FAILED: {e}")

        # Try forInterface with check=True (the one that fails)
        try:
            proxy = registry.forInterface(
                IThumborSettings,
                prefix="plone.pgthumbor.settings",
            )
            lines.append(
                f"forInterface(check=True) OK: smart_cropping={proxy.smart_cropping!r}"
            )
        except KeyError as e:
            lines.append(f"forInterface(check=True) FAILED: {e}")

        self.request.response.setHeader("Content-Type", "text/plain")
        return "\n".join(lines)
