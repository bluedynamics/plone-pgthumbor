"""GenericSetup install/setup handler for plone.pgthumbor."""

from plone.pgthumbor.interfaces import IThumborSettings
from plone.registry.interfaces import IRegistry
from Products.CMFCore.utils import getToolByName
from Products.Five import BrowserView
from zope.component import getUtility

import logging


logger = logging.getLogger(__name__)

MODIFIER_ID = "SkipThumborSourceDerivatives"
MODIFIER_TITLE = "Keep Thumbor source derivatives out of version snapshots"

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


def upgrade_to_4(context):
    """Register the source_max_edge record introduced in profile version 4.

    ``post_install`` is a profile post-handler and runs on install only, so
    an existing site would otherwise take the new code and never receive a
    registry record for the new setting.
    """
    registry = getUtility(IRegistry)
    registry.registerInterface(IThumborSettings, prefix="plone.pgthumbor.settings")


def install_clone_modifier(site):
    """Register the CMFEditions clone modifier, if this site has a repository.

    Returns True when something was registered.

    Two ways this legitimately does nothing.  ``Products.CMFEditions`` is a
    hard dependency of ``Products.CMFPlone`` but not of this package, so a
    minimal install may not have it at all.  And ``portal_modifier`` exists
    only once the CMFEditions GenericSetup profile has been applied, while
    ``plone.app.versioningbehavior`` reaches a site through
    ``plone.app.contenttypes`` rather than through Plone core — a
    deployment with custom types and neither has no version repository, and
    therefore no snapshots for the modifier to protect.
    """
    try:
        from plone.pgthumbor.modifiers import make_clone_modifier
    except ImportError:
        logger.info(
            "Products.CMFEditions is not available; skipping the "
            "plone.pgthumbor clone modifier. Nothing is lost: without a "
            "version repository there are no snapshots to keep derivatives "
            "out of."
        )
        return False

    registry = getToolByName(site, "portal_modifier", None)
    if registry is None:
        logger.info(
            "No portal_modifier tool on %r; skipping the plone.pgthumbor "
            "clone modifier.",
            site,
        )
        return False

    if MODIFIER_ID in registry.objectIds():
        # Upgrade steps get re-run, and register() would raise on the
        # duplicate id.
        return False

    registry.register(MODIFIER_ID, make_clone_modifier())
    logger.info("Registered the %s clone modifier.", MODIFIER_ID)
    return True


def upgrade_to_5(context):
    """Install the clone modifier on a site that is already running.

    ``post_install`` is a profile post-handler and runs on install only, so
    without this step an existing site would take the new code and never
    receive the modifier — and every version snapshot of an image-bearing
    type would store a derivative whose blob is empty.
    """
    install_clone_modifier(context)


def post_install(context):
    """Register IThumborSettings in the Plone registry."""
    registry = getUtility(IRegistry)
    registry.registerInterface(IThumborSettings, prefix="plone.pgthumbor.settings")
    install_clone_modifier(context)


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
