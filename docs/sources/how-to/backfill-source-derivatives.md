<!-- diataxis: how-to -->

# Backfill Thumbor source derivatives

The subscriber gives an image a Thumbor source derivative when it is added or edited.
Content that already existed when you installed the feature has none, and nothing generates one on read.
The backfill covers that population: it writes the missing derivatives, and it repairs the catalog rows that still point Thumbor at the originals.

Run it once after installing, and again whenever you change `PGTHUMBOR_SOURCE_MAX_EDGE`.

## Prerequisites

- Shell access to the Plone instance, or `docker exec` into the container.
- The `zconsole run` command, which ships with every Zope and Plone installation.
- `plone.pgthumbor` **installed and configured** in the instance you run this in.
  Unlike {doc}`purge-legacy-scales`, this script is not standalone.
  It imports the package so that the outcome vocabulary and the generator are the ones the running site uses, because a second spelling of either is how the backfill and the subscriber quietly stop agreeing about which images still need work.
- The GenericSetup profile upgraded to version 5, so the `source_max_edge` registry record exists and the clone modifier is registered.
- A cap you have chosen deliberately.
  {doc}`choose-source-max-edge` walks through picking one from the dry run.

## Get the script

Copy `backfill_thumbor_sources.py` from the
[plone-pgthumbor repository](https://github.com/bluedynamics/plone-pgthumbor/blob/main/scripts/backfill_thumbor_sources.py).

## Run in Docker

1. Copy the script into the running container:

   ```bash
   docker cp backfill_thumbor_sources.py <container>:/tmp/backfill.py
   ```

2. Measure first.
   The dry run writes nothing:

   ```bash
   docker exec -it <container> \
       env PGTHUMBOR_BACKFILL_DRY_RUN=1 \
       zconsole run etc/zope.conf /tmp/backfill.py
   ```

3. Then run it for real:

   ```bash
   docker exec -it <container> \
       zconsole run etc/zope.conf /tmp/backfill.py
   ```

   If your Plone site id is not `Plone`, set `SITE_ID`:

   ```bash
   docker exec -it <container> \
       env SITE_ID=mysite \
       zconsole run etc/zope.conf /tmp/backfill.py
   ```

## Run on a plain Zope instance

```bash
bin/zconsole run instance/etc/zope.conf backfill_thumbor_sources.py
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `SITE_ID` | `Plone` | Plone site id. |
| `PGTHUMBOR_SOURCE_MAX_EDGE` | `4000` | The cap. Read through the package's own configuration, so the value the backfill uses is the value the subscriber uses. A cap of `0` disables generation, and the script refuses to start rather than run forever against a population it can never settle. |
| `PGTHUMBOR_BACKFILL_CHUNK` | `100` | Objects per chunk. Each chunk is one commit. |
| `PGTHUMBOR_BACKFILL_PROGRESS` | `/tmp/pgthumbor-backfill-progress.json` | Where the resumable cursor lives. Keep it on a volume if the pod can restart. |
| `PGTHUMBOR_BACKFILL_FORCE` | unset | Revisit images whose outcome is terminal **and** whose recorded cap still matches. Not needed for a cap change. |
| `PGTHUMBOR_BACKFILL_SIZE_ONLY` | unset | Select only images whose stored width or height already exceeds the cap. SQL cannot see a colour space, so this pass never finds the 3 MP CMYK press image, and it also skips any field value whose stored state carries no dimensions. Run without it to cover the whole population. |
| `PGTHUMBOR_BACKFILL_DRY_RUN` | unset | Measure and report, write nothing. |

Each flag is read the way the rest of the package reads a boolean value: `true`, `1` or `yes`, case insensitive.

## Why the run has two phases

A ZODB blob receives its transaction id at commit.
A derivative written in chunk N therefore has no Thumbor-addressable identity until that chunk's transaction has committed, which is why the run cannot generate and re-index in one pass.

**Phase 1, generate.** Walks `object_state` by `zoid` and loads each `NamedBlobImage` on its own, without ever waking the content object that holds it.
That is what keeps it memory-light: a catalog walk over the same population is what killed a production container during the original scan.
Each chunk is committed before the next one starts.

**Phase 2, re-index.** Walks the field values that got a derivative, finds the catalogued objects referencing them, and re-indexes `image_scales` for each.

Phase 2 is not an optimisation, and skipping it leaves the run worse than useless.
For exactly the images this feature targets, the existing catalog rows do not hold uid URLs that Plone could heal on the next render.
They hold direct, absolute, signed Thumbor URLs, generated long before anyone noticed that Thumbor answers 400 for them.
A browser fetches those without Plone in the path, so nothing can intervene.
Until phase 2 has run, nothing has improved for a single one of them.

A chunk counts as done only once re-indexed, and the progress file tracks the two phases separately.

## Why it needs a request

Everything `plone.pgthumbor` does is gated on its browser layer, looked up through `zope.globalrequest.getRequest()`.
Under `zconsole` there is no publication, and `makerequest()` alone does not help: it sets `app.REQUEST` but never calls `setRequest()`, so `getRequest()` keeps returning `None`.

A re-index run that way does not merely miss the derivative.
It overwrites `image_scales` with null for every object it touches, because the indexer's "do not index" signal is an `AttributeError` and `plone.pgcatalog` reads metadata columns with a `getattr` default that swallows it.
The column is not skipped, it is overwritten, site-wide.

The script establishes a request carrying the layer as its very first action, before it even resolves the site.
Phase 2 then verifies three things before its first write: that a request exists, that it provides the browser layer, and that `@@images` really resolves to this package's view rather than the stock one.
If any check fails the run aborts, having written nothing in that phase.

The same defect used to sit in `plone.pgthumbor.purge_scales`, which walks the whole catalog.
It is fixed the same way.

## Resuming, and what a chunk costs

The work list is a cursor walk over `object_state` ordered by `zoid`, so it is stable and resumable and never drifts the way an offset would.
Each chunk commits, records its position in the progress file, invalidates the ZODB cache and returns freed heap to the operating system.
A run interrupted anywhere resumes from the last recorded position when you start it again, in whichever phase it stopped.

A per-object failure is counted, logged and stepped over, and the cursor advances past it, so one unreadable blob neither ends the run nor loops forever.
A failing commit stays fatal.

## Verification

The run ends by printing three counts, and a finished run has zero of each:

| Count | Meaning |
|---|---|
| `candidates remaining` | Phase 1 did not cover the population. |
| `derivatives with no owner` | A derivative-bearing field value that no catalogued object references, so nothing can re-index it. An image field nested in an annotation lands here, and so does an object deleted between the two phases. Reported rather than silently counted as done. |
| `owners without scales` | A content object holding a derivative-bearing image has no `image_scales` metadata at all. That covers both a phase 2 that never reached the object and a request-less run that wrote null over the column. |

## After the run

Purge the caches in front of Plone.
The failing requests this feature fixes were answered with HTTP 400, and error responses cache too: one survived 5.4 hours in Varnish despite a 60 second `s-maxage`.
A ban or purge for the affected URLs is the last step, otherwise the fix stays invisible for as long as the cache holds.

## Memory considerations

The script is written for the same conditions as the legacy scale purge: large sites in memory-constrained containers.

- Phase 1 never loads a content object, only the image field values themselves.
- Chunks are small and each one commits.
- After every chunk the ZODB cache is fully invalidated rather than ghosted, and `malloc_trim` returns freed heap to the operating system.
- The decode itself is the peak: a print resolution original costs roughly 79 to 105 MB of pixel buffer while it is being read, and the package allows one decode at a time per process.

Run it in a dedicated pod or container rather than alongside live traffic.
