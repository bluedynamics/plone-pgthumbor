"""Purge legacy ZODB image scales after plone.pgthumbor installation.

One-time cleanup script that removes all plone.scale annotation data
from content objects and reindexes the image_scales catalog metadata.
After plone.pgthumbor is active, Thumbor handles all image scaling —
the old ZODB-stored scales just waste storage space.

Usage with zconsole::

    .venv/bin/zconsole run instance/etc/zope.conf \\
        -s Plone \\
        -m plone.pgthumbor.purge_scales

Or as a browser view (Manager only)::

    https://your-site/@@thumbor-purge-scales
"""

from __future__ import annotations

from plone.pgthumbor.zconsole import establish_request
from plone.pgthumbor.zconsole import require_thumbor_request
from zope.annotation.interfaces import IAnnotations

import logging
import os
import transaction


logger = logging.getLogger(__name__)

ANNOTATION_KEY = "plone.scale"


def _brains(catalog, start, limit):
    """A slice of the catalog that is safe to resume into.

    ``sort_on="path"`` whenever a slice is asked for.  The default order is
    whatever PostgreSQL hands back and is not stable across queries, so an
    offset into it would silently skip objects on the next call.  Paths do
    not change during a purge, which is what makes ordering on them safe
    here — a rename mid-run would not be.

    Without a *limit* the whole catalog comes back unsorted, as before:
    there is nothing to resume into, so the sort would be pure cost.
    """
    if limit is None:
        return catalog.unrestrictedSearchResults()
    return catalog.unrestrictedSearchResults(
        sort_on="path", b_start=start, b_size=limit
    )


def _annotations_of(obj):
    """The annotation mapping for *obj*, or None when it has none."""
    try:
        return IAnnotations(obj, None)
    except TypeError:
        return None


def _purge_one(brain, reindex):
    """Purge one object.  Returns ``"purged"``, ``"clean"`` or ``"skipped"``."""
    try:
        obj = brain._unrestrictedGetObject()
    except Exception:
        return "skipped"

    annotations = _annotations_of(obj)
    if annotations is None:
        return "skipped"
    if ANNOTATION_KEY not in annotations:
        return "clean"

    del annotations[ANNOTATION_KEY]
    if reindex:
        try:
            # Only here.  The catalog schema is constant for a run, so
            # deciding on it alone reindexed every object in the site — an
            # O(site) pile of catalog writes for O(objects-with-scales) of
            # actual work.  idxs= keeps notifyModified() out of it.
            obj.reindexObject(idxs=["image_scales"])
            return "purged+reindexed"
        except Exception:
            logger.debug(
                "Could not reindex image_scales for %s",
                brain.getPath(),
                exc_info=True,
            )
    return "purged"


def purge_scales(portal, batch_size=500, limit=None, start=0):
    """Remove plone.scale annotations and reindex what changed.

    Walks the catalog, deletes legacy scale annotations, and reindexes
    ``image_scales`` **only for the objects it actually changed**.

    Pass *limit* to bound the walk and *start* to resume it, so a site too
    large to finish in one request or one process can be done in slices.
    The returned ``next_start`` is what to pass back in; ``done`` says the
    catalog is exhausted.

    Returns a dict: purged, reindexed, skipped, processed, next_start, done.
    """
    catalog = portal.portal_catalog
    # Once per run, not once per object: it asks the catalog schema, which
    # cannot change underneath a single walk.
    reindex = _has_image_scales_metadata(catalog)
    brains = _brains(catalog, start, limit)

    counts = {"purged": 0, "reindexed": 0, "skipped": 0}
    processed = 0
    changed = 0

    for brain in brains:
        processed += 1
        outcome = _purge_one(brain, reindex)
        if outcome == "skipped":
            counts["skipped"] += 1
            continue
        if outcome == "clean":
            continue
        counts["purged"] += 1
        changed += 1
        if outcome == "purged+reindexed":
            counts["reindexed"] += 1

        if changed % batch_size == 0:
            transaction.commit()
            logger.info(
                "Progress: %d purged, %d reindexed...",
                counts["purged"],
                counts["reindexed"],
            )

    if changed > 0:
        transaction.commit()

    counts["processed"] = processed
    counts["next_start"] = start + processed
    counts["done"] = limit is None or processed < limit
    logger.info(
        "Purged %d, reindexed %d (%d skipped, %d processed%s).",
        counts["purged"],
        counts["reindexed"],
        counts["skipped"],
        processed,
        "" if counts["done"] else f", resume at {counts['next_start']}",
    )
    return counts


def _has_image_scales_metadata(catalog):
    """Check if the catalog has image_scales in its schema (metadata columns)."""
    try:
        return "image_scales" in catalog.schema()
    except Exception:
        return False


def _env_int(name, default=None):
    """Read a positive integer from the environment, or *default*."""
    try:
        value = int(os.environ[name])
    except (KeyError, ValueError):
        return default
    return value if value >= 0 else default


def _int_param(request, name, default=None):
    """Read a positive integer from the request, or *default*."""
    try:
        value = int(request.form[name])
    except (KeyError, TypeError, ValueError):
        return default
    return value if value >= 0 else default


class PurgeScalesView:
    """@@thumbor-purge-scales browser view."""

    def __init__(self, context, request):
        self.context = context
        self.request = request

    def __call__(self):
        """Purge, optionally one bounded slice at a time.

        ``?limit=1000&start=0`` walks that many objects and reports where
        to resume.  Without a limit this runs the whole catalog in one
        request, which on a large site does not finish before the request
        does — hence the parameters (issue #16).
        """
        result = purge_scales(
            self.context,
            limit=_int_param(self.request, "limit"),
            start=_int_param(self.request, "start", default=0) or 0,
        )
        self.request.response.setHeader("Content-Type", "text/plain")
        line = (
            f"Purged {result['purged']}, reindexed {result['reindexed']} "
            f"({result['skipped']} skipped, {result['processed']} processed)."
        )
        if result["done"]:
            return line + " Done."
        return line + f" Not done — resume with ?start={result['next_start']}"


def main(app, args):
    """Entry point for ``zconsole run -m plone.pgthumbor.purge_scales``.

    Issue #16.  This walks the whole catalog and reindexes ``image_scales``
    for every object it touches, so without a request carrying the browser
    layer it does not merely miss the Thumbor URLs — it overwrites the
    column with null, site-wide.  ``makerequest`` alone never did that:
    it sets ``app.REQUEST`` but leaves ``getRequest()`` at ``None``.
    """
    from AccessControl.SecurityManagement import newSecurityManager

    # args.site is set by zconsole's -s flag
    site_id = getattr(args, "site", None) or "Plone"
    app = establish_request(app)
    # Before the walk, not after: the caller has to be able to rely on
    # nothing having been written when this raises.
    require_thumbor_request()

    # Elevate privileges
    admin = app.acl_users.getUserById("admin")
    if admin is None:
        admin = app.acl_users.getUsers()[0]
    newSecurityManager(None, admin.__of__(app.acl_users))

    portal = app[site_id]
    # Bounded slices, so one process does not have to hold the whole walk.
    # PURGE_LIMIT with no PURGE_START runs a single slice and prints where
    # to resume; leaving both unset keeps the old whole-catalog behaviour.
    limit = _env_int("PURGE_LIMIT")
    result = purge_scales(portal, limit=limit, start=_env_int("PURGE_START", default=0))
    logger.info(
        "Purged %d, reindexed %d (%d skipped, %d processed).",
        result["purged"],
        result["reindexed"],
        result["skipped"],
        result["processed"],
    )
    if not result["done"]:
        logger.info("Not done. Resume with PURGE_START=%d.", result["next_start"])
    return result
