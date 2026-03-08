# Purge legacy ZODB image scales

Before migrating to plone.pgthumbor, you should remove the cached image scales
that Plone stores in ZODB annotations. These scales are no longer needed once
Thumbor handles all image scaling, and they can consume significant blob storage
(often tens of GB on image-heavy sites).

## Prerequisites

- Shell access to the Plone instance (or `docker exec` into the container)
- The `zconsole run` command (ships with every Zope/Plone installation)
- **No need to install plone.pgthumbor** -- the script is fully standalone

## Get the script

Download or copy `purge_legacy_scales.py` from the
[plone-pgthumbor repository](https://github.com/bluedynamics/plone-pgthumbor/blob/main/scripts/purge_legacy_scales.py).

## Run in Docker

1. Copy the script into the running container:

   ```bash
   docker cp purge_legacy_scales.py <container>:/tmp/purge_legacy_scales.py
   ```

2. Execute via `zconsole run`:

   ```bash
   docker exec -it <container> \
       zconsole run etc/zope.conf /tmp/purge_legacy_scales.py
   ```

   If your Plone site id is not `Plone`, set the `SITE_ID` environment variable:

   ```bash
   docker exec -it <container> \
       env SITE_ID=mysite \
       zconsole run etc/zope.conf /tmp/purge_legacy_scales.py
   ```

## Run on a plain Zope instance

```bash
bin/zconsole run instance/etc/zope.conf purge_legacy_scales.py
```

## Keep recent scales

By default, scales younger than **180 days** (6 months) are preserved.
Override with the `MAX_AGE_DAYS` environment variable:

```bash
# Keep scales from the last 90 days
env MAX_AGE_DAYS=90 zconsole run etc/zope.conf /tmp/purge_legacy_scales.py

# Remove ALL scales regardless of age
env MAX_AGE_DAYS=0 zconsole run etc/zope.conf /tmp/purge_legacy_scales.py
```

## What the script does

- Walks every cataloged object via the catalog's internal path mapping
- Checks each object's `plone.scale` annotation for cached image scales
- Deletes individual scales older than `MAX_AGE_DAYS` (default 180);
  handles both second and millisecond timestamps
- Commits every 50 objects, invalidates the ZODB cache, and calls
  `malloc_trim` to return freed memory to the OS
- Prints progress to standard output

The script does **not** touch original images -- only the generated scale copies.

## Memory considerations

The script is designed for large sites (100k+ objects) running in
memory-constrained Docker containers:

- Objects are processed in small batches (50 at a time)
- Each object is explicitly deactivated (`_p_deactivate`) after processing
- After every batch commit, the ZODB cache is fully invalidated (not just
  ghosted) to truly free memory---ghost objects still consume ~200 bytes each
- `malloc_trim` is called after each batch to return freed heap memory to the OS
  (Python's allocator otherwise retains freed memory in its arena)
- The ZODB cache size is reduced to 50 objects for the duration of the run

A container with at least **768 MB** of memory is recommended for sites with
100k+ objects.
