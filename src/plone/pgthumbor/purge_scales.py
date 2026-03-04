"""Purge legacy ZODB image scales after plone.pgthumbor installation.

One-time cleanup script that removes all plone.scale annotation data
from content objects. After plone.pgthumbor is active, Thumbor handles
all image scaling — the old ZODB-stored scales just waste storage space.

Usage with zconsole::

    .venv/bin/zconsole run instance/etc/zope.conf \\
        -s Plone \\
        -m plone.pgthumbor.purge_scales

Or as a browser view (Manager only)::

    https://your-site/@@thumbor-purge-scales
"""

from __future__ import annotations

from zope.annotation.interfaces import IAnnotations

import logging
import transaction


logger = logging.getLogger(__name__)

ANNOTATION_KEY = "plone.scale"


def purge_scales(portal, batch_size=500):
    """Remove plone.scale annotations from all content objects.

    Walks the catalog, checks each object for scale annotations,
    and deletes them. Commits in batches to avoid unbounded memory use.

    Returns (purged_count, skipped_count, total_count).
    """
    catalog = portal.portal_catalog
    brains = catalog.unrestrictedSearchResults()
    total = len(brains)

    purged = 0
    skipped = 0

    for brain in brains:
        try:
            obj = brain._unrestrictedGetObject()
        except Exception:
            skipped += 1
            continue

        try:
            annotations = IAnnotations(obj, None)
        except TypeError:
            skipped += 1
            continue

        if annotations is None:
            skipped += 1
            continue

        if ANNOTATION_KEY in annotations:
            del annotations[ANNOTATION_KEY]
            purged += 1

        if purged > 0 and purged % batch_size == 0:
            transaction.commit()
            logger.info("Purged %d/%d objects so far...", purged, total)

    if purged > 0:
        transaction.commit()

    logger.info(
        "Done. Purged scales from %d objects (%d skipped, %d total).",
        purged,
        skipped,
        total,
    )
    return purged, skipped, total


class PurgeScalesView:
    """@@thumbor-purge-scales browser view."""

    def __init__(self, context, request):
        self.context = context
        self.request = request

    def __call__(self):
        purged, skipped, total = purge_scales(self.context)
        self.request.response.setHeader("Content-Type", "text/plain")
        return (
            f"Purged scales from {purged} objects ({skipped} skipped, {total} total)."
        )


def main(app, args):
    """Entry point for ``zconsole run -m plone.pgthumbor.purge_scales``."""
    from AccessControl.SecurityManagement import newSecurityManager
    from Testing.makerequest import makerequest

    # args.site is set by zconsole's -s flag
    site_id = getattr(args, "site", None) or "Plone"
    app = makerequest(app)

    # Elevate privileges
    admin = app.acl_users.getUserById("admin")
    if admin is None:
        admin = app.acl_users.getUsers()[0]
    newSecurityManager(None, admin.__of__(app.acl_users))

    portal = app[site_id]
    purged, skipped, total = purge_scales(portal)
    logger.info(
        "Purged scales from %d objects (%d skipped, %d total).",
        purged,
        skipped,
        total,
    )
