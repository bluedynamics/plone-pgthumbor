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

   By default, scales younger than 6 months are kept.  Override with::

       env MAX_AGE_DAYS=90 zconsole run ...

   The script commits in batches and logs progress to stdout.
   Memory-safe: uses malloc_trim to return freed memory to the OS.

How to run (plain Zope instance)
--------------------------------

    bin/zconsole run instance/etc/zope.conf purge_legacy_scales.py

What it does
------------

- Walks every cataloged object via the catalog's internal path mapping
- Deletes the ``plone.scale`` annotation (where Plone stores cached
  image scale blobs and metadata) older than MAX_AGE_DAYS
- Commits every BATCH_SIZE objects, minimizes ZODB cache, and calls
  malloc_trim to return freed memory to the OS
- Prints a summary at the end

It does NOT touch original images — only the generated scale copies.
"""

from AccessControl.SecurityManagement import newSecurityManager
from Testing.makerequest import makerequest
from zope.component.hooks import setSite

import ctypes
import gc
import os
import sys
import time
import traceback
import transaction


ANNOTATION_KEY = "plone.scale"
BATCH_SIZE = 50
# Keep scales younger than this (seconds). Default: 6 months.
MAX_AGE = int(os.environ.get("MAX_AGE_DAYS", "180")) * 86400

# glibc malloc_trim — releases freed heap memory back to the OS.
# Without this, Python keeps freed memory in its internal arena allocator
# and the RSS grows until OOM even though objects have been garbage collected.
try:
    _libc = ctypes.CDLL("libc.so.6")

    def _release_memory():
        gc.collect()
        _libc.malloc_trim(0)

except OSError:

    def _release_memory():
        gc.collect()


def _invalidate_cache(conn):
    """Invalidate ALL cached objects — removes ghosts from cache entirely.

    cacheMinimize() only ghosts objects but keeps them in the cache dict.
    Ghost objects still consume ~200 bytes each of Python heap.  After 20k+
    objects, ghost accumulation causes OOM.  invalidate() truly removes
    unreferenced objects from the cache, allowing Python (and malloc_trim)
    to free the memory.
    """
    oids = [oid for oid, _ in conn._cache.items()]
    for oid in oids:
        try:
            conn._cache.invalidate(oid)
        except KeyError:
            pass


def purge_scales(app, portal, site_id):
    catalog = portal.portal_catalog
    conn = portal._p_jar
    db = conn.db()

    # Reduce ZODB cache to minimum
    original_cache_size = db.getCacheSize()
    db.setCacheSize(50)

    # Read RIDs (integers, ~4 MB for 130k) from catalog internals.
    cat = catalog._catalog
    rids = sorted(cat.paths.keys())
    total = len(rids)
    conn.cacheMinimize()
    _release_memory()

    purged = 0
    scales_removed = 0
    skipped = 0
    has_annotations = 0
    has_scale_key = 0
    cutoff = time.time() - MAX_AGE

    print(f"Processing {total} objects (cutoff={cutoff}) ...", flush=True)

    for i in range(total):
        rid = rids[i]
        try:
            path = cat.paths[rid]
            obj = portal.unrestrictedTraverse(path)
        except Exception:
            skipped += 1
            obj = None

        if obj is not None:
            # Try direct __annotations__ access first (bypasses adapter)
            ann_dict = getattr(obj, "__annotations__", None)
            if ann_dict is not None:
                has_annotations += 1
                if ANNOTATION_KEY in ann_dict:
                    has_scale_key += 1
                    storage = ann_dict[ANNOTATION_KEY]
                    to_delete = []
                    try:
                        for key, val in list(storage.items()):
                            modified = 0
                            if isinstance(val, dict):
                                modified = val.get("modified", 0)
                                # plone.scale stores modified in milliseconds
                                if modified > 1e12:
                                    modified = modified / 1000.0
                            if modified < cutoff:
                                to_delete.append(key)
                    except Exception:
                        # Storage not iterable — remove entirely
                        to_delete = None

                    if to_delete is None:
                        del ann_dict[ANNOTATION_KEY]
                        scales_removed += 1
                        purged += 1
                    elif to_delete:
                        for key in to_delete:
                            del storage[key]
                        scales_removed += len(to_delete)
                        if not len(storage):
                            del ann_dict[ANNOTATION_KEY]
                        purged += 1

            obj._p_deactivate()

        if (i + 1) % BATCH_SIZE == 0:
            transaction.commit()
            # Invalidate all cached objects — ghosts included — to truly
            # free memory.  Referenced objects (app, portal) become ghosts
            # and are transparently reloaded on next attribute access.
            _invalidate_cache(conn)
            _release_memory()

            # Re-establish references after cache invalidation.
            # Accessing app[site_id] reloads portal from ZODB.
            portal = app[site_id]
            setSite(portal)
            cat = portal.portal_catalog._catalog

            print(
                f"Progress: {i + 1} / {total} scanned, "
                f"{purged} purged, {scales_removed} scales removed, "
                f"{has_annotations} with ann, {has_scale_key} with scales",
                flush=True,
            )

    # Final commit
    if purged > 0:
        transaction.commit()

    db.setCacheSize(original_cache_size)

    print(
        f"Done. Purged {scales_removed} scales from {purged} objects "
        f"({skipped} skipped, {total} total).",
        flush=True,
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
try:
    purge_scales(app, portal, site_id)
except Exception:
    traceback.print_exc()
    sys.exit(1)
