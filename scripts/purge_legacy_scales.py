"""Purge all legacy ZODB image scales from a Plone site.

Standalone script — does NOT require plone.pgthumbor to be installed.
Use this BEFORE migration to free blob storage from cached image scales.

How to run (Docker)
-------------------

1. Copy this script into the running container:

       docker cp purge_legacy_scales.py <container>:/tmp/purge_legacy_scales.py

2. Run it via zconsole:

       docker exec -it <container> \
           zconsole run etc/zope.conf /tmp/purge_legacy_scales.py

   If your Plone site id is not "Plone", set the SITE_ID env var:

       docker exec -it <container> \
           env SITE_ID=mysite \
           zconsole run etc/zope.conf /tmp/purge_legacy_scales.py

   The script commits in batches (default 500 objects) and logs progress
   to stdout.  On a site with 100k objects expect a few minutes.

How to run (plain Zope instance)
--------------------------------

    bin/zconsole run instance/etc/zope.conf purge_legacy_scales.py

What it does
------------

- Walks every cataloged object via unrestrictedSearchResults
- Deletes the ``plone.scale`` annotation (where Plone stores cached
  image scale blobs and metadata)
- Commits every BATCH_SIZE objects to keep memory bounded
- Prints a summary at the end

It does NOT touch original images — only the generated scale copies.
"""

from AccessControl.SecurityManagement import newSecurityManager
from Testing.makerequest import makerequest
from zope.annotation.interfaces import IAnnotations
from zope.component.hooks import setSite

import os
import sys
import transaction


ANNOTATION_KEY = "plone.scale"
BATCH_SIZE = 500


def purge_scales(portal):
    catalog = portal.portal_catalog
    site_path = "/".join(portal.getPhysicalPath())
    brains = catalog.unrestrictedSearchResults(path=site_path)
    total = len(brains)

    purged = 0
    scales_removed = 0
    skipped = 0

    print(f"Scanning {total} cataloged objects ...")

    for i, brain in enumerate(brains, 1):
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
            continue

        if ANNOTATION_KEY in annotations:
            storage = annotations[ANNOTATION_KEY]
            try:
                scales_removed += len(storage)
            except TypeError:
                pass
            del annotations[ANNOTATION_KEY]
            purged += 1

        if purged > 0 and purged % BATCH_SIZE == 0:
            transaction.commit()
            print(
                f"Progress: {purged} objects purged, "
                f"{scales_removed} scales removed ({i} / {total} scanned)"
            )

    if purged > 0:
        transaction.commit()

    print(
        f"Done. Purged {scales_removed} scales from {purged} objects "
        f"({skipped} skipped, {total} total)."
    )
    return purged


# --- zconsole run entry point ---
# When executed via ``zconsole run zope.conf this_script.py``,
# the variable ``app`` is injected into globals by zconsole.

if "app" not in dir():
    print("This script must be run via: zconsole run <zope.conf> <script.py>")
    sys.exit(1)

app = makerequest(app)  # noqa: F821 — injected by zconsole

# Elevate to Manager
acl = app.acl_users
admin = acl.getUserById("admin")
if admin is None:
    users = acl.getUsers()
    if not users:
        print("ERROR: No users found in root acl_users. Cannot elevate.")
        sys.exit(1)
    admin = users[0]
newSecurityManager(None, admin.__of__(acl))

site_id = os.environ.get("SITE_ID", "Plone")
if site_id not in app.objectIds():
    print(f"ERROR: Site '{site_id}' not found. Available: {list(app.objectIds())}")
    sys.exit(1)

portal = app[site_id]
setSite(portal)
purge_scales(portal)
