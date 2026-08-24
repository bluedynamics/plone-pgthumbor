"""Give existing images a Thumbor source derivative, and reindex them.

Unlike ``purge_legacy_scales.py`` this script is **not** standalone: it
imports ``plone.pgthumbor``, because the outcome vocabulary and the
generator have to be the ones the running instance uses.  Re-spelling
either here is how the backfill and the subscriber quietly stop agreeing
about which images still need work.

How to run (Docker)
-------------------

1. Copy this script into the running container::

       docker cp backfill_thumbor_sources.py <container>:/tmp/backfill.py

2. Run it via zconsole::

       docker exec -it <container> \
           zconsole run etc/zope.conf /tmp/backfill.py

Environment
-----------

======================================  =====================================
``SITE_ID``                             Plone site id (default ``Plone``)
``PGTHUMBOR_SOURCE_MAX_EDGE``           the cap; read through the package's
                                        own configuration, so the value the
                                        backfill uses is the value the
                                        subscriber uses
``PGTHUMBOR_BACKFILL_CHUNK``            objects per chunk (default 100)
``PGTHUMBOR_BACKFILL_PROGRESS``         progress file path
``PGTHUMBOR_BACKFILL_FORCE``            revisit terminal outcomes too
``PGTHUMBOR_BACKFILL_SIZE_ONLY``        only images above the cap (pass 1)
``PGTHUMBOR_BACKFILL_DRY_RUN``          measure, write nothing
======================================  =====================================

Measure before you run
----------------------

``PGTHUMBOR_BACKFILL_DRY_RUN=1`` writes nothing and prints the four numbers
the cap is chosen from: how many candidates there are, what a derivative
costs in bytes, how much of the population has its scale uids move when the
derivative lands, and which scale names actually carry crops.  The last one
is the binding constraint on the cap; the default of 4000 is a generic
starting point, not a decision for any particular site.

Why SQL and not the catalog
---------------------------

A ``zconsole`` brain walk over the result set OOM-killed a production
container during the original scan.  The work list is therefore a keyset
walk over ``object_state``, and phase 1 loads each ``NamedBlobImage`` by
oid without ever waking the content object that owns it.

Why two phases
--------------

A blob gets its TID at commit, so a derivative written in chunk N has no
Thumbor-addressable identity until that transaction has committed.  The
catalog rows for exactly the images this fixes hold direct, signed Thumbor
URLs pointing at the original — a browser fetches those without Plone in
the path, so nothing heals them.  A chunk therefore counts as done only
once *reindexed*, and the progress file tracks the two phases separately.
"""

from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from plone.pgthumbor.derivative import build_derivative_bytes
from plone.pgthumbor.derivative import INFO_ATTRIBUTE
from plone.pgthumbor.derivative import IS_DERIVATIVE_ATTRIBUTE
from plone.pgthumbor.derivative import REASON_GENERATED
from plone.pgthumbor.derivative import set_source_derivative
from plone.pgthumbor.derivative import TERMINAL_REASONS
from plone.pgthumbor.zconsole import establish_request
from plone.pgthumbor.zconsole import require_thumbor_request
from ZODB.utils import p64

import ctypes
import gc
import json
import os
import statistics
import sys
import traceback
import transaction


# --- memory ---------------------------------------------------------------
#
# Both helpers below are the ones from ``purge_legacy_scales.py``, verbatim.
# They are duplicated rather than imported because that script is standalone
# by design — it has to run on a site that has never had plone.pgthumbor
# installed — and because a copied twenty lines is cheaper than making the
# two scripts import each other under zconsole, where sys.path is whatever
# the container happens to have.

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


# --- what the work list is made of ----------------------------------------

# ``NamedBlobImage`` is Persistent in its own right, so a field value is a
# row in ``object_state`` with its own zoid — which is what phase 1 loads
# directly.  Both columns are matched because ``idx_object_class`` is a
# btree on ``(class_mod, class_name)``: dropping the module would leave the
# index prefix unusable and turn every chunk into a sequential scan.  A
# deployment with a NamedBlobImage subclass has to add it here (and its own
# module, which is a different class_mod).
OBJECT_STATE_CLASS_MOD = "plone.namedfile.file"
OBJECT_STATE_CLASS_NAMES = ("NamedBlobImage",)

# A generated derivative is itself a ``NamedBlobImage`` row in
# object_state, carrying none of the outcome attributes, so without an
# exclusion it would look like a fresh candidate: decoded, found to need
# nothing, and stamped with an outcome record of its own.  Phase 2 could do
# nothing with it either — its parent is the original field value, not a
# content object.
#
# The exclusion is the marker ``set_source_derivative`` writes, not a guess
# at the filename: an editorial upload that happened to be named
# ``something-pgthumbor-source.jpg`` would otherwise be skipped for good,
# with nothing in the log to say so.
_CANDIDATE_FROM = f"""\
FROM object_state
WHERE class_mod = %(class_mod)s
  AND class_name = ANY(%(class_names)s::text[])
  AND zoid > %(last_zoid)s
  AND NOT (state ? '{IS_DERIVATIVE_ATTRIBUTE}')
"""

_CANDIDATE_SELECT = "SELECT zoid\n" + _CANDIDATE_FROM

# ``_modified`` is what ``NamedBlobFile._setData`` writes, so a field value
# without it predates the upload path that sets it.  On those, writing the
# derivative moves ``_p_mtime`` — and every scale uid for that image with
# it, because ``hash_key`` folds ``modified_time`` and
# ``ModifiedPropertyMixin.modified`` falls back to ``_p_mtime`` when
# ``_modified`` is absent.  Counting them is how the dry run sizes the
# cache-invalidation blast radius before anything is written.
MODIFIED_ATTRIBUTE = "_modified"

# The dry run's first two numbers, over the whole population and in one
# pass.  Counted in SQL rather than by loading field values: the number that
# matters is the population's, not a sample's, and the attribute is a plain
# JSONB key like the others this file reads.
_CANDIDATE_SUMMARY = (
    f"""\
SELECT count(*) AS candidates,
       count(*) FILTER (WHERE NOT (state ? '{MODIFIED_ATTRIBUTE}')) AS without_modified
"""
    + _CANDIDATE_FROM
)

# The candidate rule, mirroring ``derivative.needs_processing``:  no record
# at all, a record that is not an outcome record, a *non-terminal* reason,
# or a cap that is no longer the configured one.
#
# ``"retry"`` (semaphore timeout) and ``"error"`` (failed decode) are
# deliberately absent from TERMINAL_REASONS and therefore stay candidates
# without ``force``.  Excluding every recorded outcome — the obvious
# reading of "already processed" — is what would let one contended upload
# drop its image out of the population for good, while the terminal
# verification still reported success.
#
# ``coalesce(..., '')`` and not a bare ``<> ALL``: a record without a
# reason yields NULL, and ``NULL <> ALL (...)`` is NULL, which is not true
# and would silently exclude the row.
_NOT_TERMINAL = f"""\
  AND (
    NOT (state ? '{INFO_ATTRIBUTE}')
    OR jsonb_typeof(state->'{INFO_ATTRIBUTE}') <> 'object'
    OR coalesce(state->'{INFO_ATTRIBUTE}'->>'reason', '') <> ALL (%(terminal_reasons)s::text[])
    OR state->'{INFO_ATTRIBUTE}'->'max_edge' IS DISTINCT FROM to_jsonb(%(max_edge)s::int)
  )
"""

# Pass 1 of the two passes over the population: the size trigger is the
# only one SQL can see.  The colour-space trigger — a 3 MP CMYK press
# image — is invisible here and needs the ordinary pass.
#
# Compared as jsonb rather than through ``(state->>'_width')::int``: a cast
# raises on anything non-numeric, and PostgreSQL promises no evaluation
# order that would let a guarding AND run first.  jsonb comparison cannot
# raise, and the typeof guard keeps a stray string from sorting above a
# number.
_OVERSIZED = """\
  AND (
    (jsonb_typeof(state->'_width') = 'number'
     AND state->'_width' > to_jsonb(%(max_edge)s::int))
    OR (jsonb_typeof(state->'_height') = 'number'
        AND state->'_height' > to_jsonb(%(max_edge)s::int))
  )
"""

# Keyset, never OFFSET: the population changes underneath a resumable run
# — every chunk writes to the very rows the predicate selects on — so an
# offset would skip and repeat rows.  ``zoid`` is the primary key, so the
# order is stable and free.
_CANDIDATE_TAIL = """\
ORDER BY zoid
LIMIT %(chunk)s
"""

# --- how the crop histogram is read ---------------------------------------
#
# Crops live in an annotation on the *content object*, not on the field
# value, so they cannot be reached the way phase 1 reaches an image — and
# waking content objects to read them is the thing this whole script exists
# to avoid.  They are read out of the stored state instead.
#
# ``zope.annotation``'s attribute annotations are an OOBTree, which
# zodb-pgjsonb encodes as ``{"@kv": [[key, value], ...]}``.  The value under
# the crop key is normally a reference to a persistent mapping of its own —
# ``{"@ref": ["<zoid hex>", "<class>"]}`` — and occasionally a plain dict
# stored inline.  Both shapes are handled; anything else is skipped.
#
# There is no index for this, so it is a sequential scan over object_state.
# That is affordable exactly once, in a dry run, and nowhere else: the
# ordinary run never issues it.
#
# The CASE is not decoration.  A LATERAL function in the FROM list is
# evaluated before the WHERE clause, so a ``jsonb_typeof(...) = 'array'``
# guard sitting in WHERE would not stop ``jsonb_array_elements`` from
# raising on the first row whose ``@kv`` is not an array.  Feeding it an
# empty array instead makes the guard part of the argument, where the
# evaluation order is not in question.  (``state`` is nullable and the key
# is absent on nearly every row; both yield NULL, and a strict
# set-returning function on NULL simply produces no rows.)
_CROP_ANNOTATION_SELECT = """\
SELECT entry -> 1 AS crops
FROM object_state,
     LATERAL jsonb_array_elements(
         CASE WHEN jsonb_typeof(state -> '@kv') = 'array'
              THEN state -> '@kv'
              ELSE '[]'::jsonb
         END
     ) AS entry
WHERE entry ->> 0 = %(annotation_key)s
"""

_CROP_STORAGE_SELECT = """\
SELECT state
FROM object_state
WHERE zoid = ANY(%(zoids)s::bigint[])
"""

DEFAULT_CHUNK_SIZE = 100

# How many candidates the dry run decodes for the size estimate.  Small on
# purpose: every one of them is a full decode of a print-resolution image,
# and the number wanted out of it is a median, not a total.
DEFAULT_SAMPLE_SIZE = 25
DEFAULT_PROGRESS_PATH = "/tmp/pgthumbor-backfill-progress.json"

PHASE_GENERATE = "generate"
PHASE_REINDEX = "reindex"
PHASES = (PHASE_GENERATE, PHASE_REINDEX)

_PROGRESS_VERSION = 1


def _candidate_filters(max_edge, last_zoid, force, size_only, class_names):
    """The predicates and parameters both candidate queries share.

    Shared so the dry run's count cannot drift away from the population the
    run actually walks.  A count over a slightly different predicate is
    worse than no count at all: it still looks like a number.
    """
    sql = ""
    params = {
        "class_mod": OBJECT_STATE_CLASS_MOD,
        "class_names": list(class_names),
        "last_zoid": last_zoid,
    }
    if not force:
        sql += _NOT_TERMINAL
        params["terminal_reasons"] = sorted(TERMINAL_REASONS)
        params["max_edge"] = max_edge
    if size_only:
        sql += _OVERSIZED
        params["max_edge"] = max_edge
    return sql, params


def candidate_query(
    max_edge,
    last_zoid=0,
    chunk=DEFAULT_CHUNK_SIZE,
    force=False,
    size_only=False,
    class_names=OBJECT_STATE_CLASS_NAMES,
):
    """Build the candidate query and its parameters.

    Returns ``(sql, params)``.  Parameters are built to match the SQL that
    was actually assembled, so ``force`` really does drop the outcome
    predicate rather than merely widening it.
    """
    filters, params = _candidate_filters(
        max_edge, last_zoid, force, size_only, class_names
    )
    params["chunk"] = chunk
    return _CANDIDATE_SELECT + filters + _CANDIDATE_TAIL, params


def candidate_summary_query(
    max_edge,
    force=False,
    size_only=False,
    class_names=OBJECT_STATE_CLASS_NAMES,
):
    """Build the dry run's counting query and its parameters.

    No keyset and no LIMIT: this counts the whole population, including
    whatever a resumed run has already passed.
    """
    filters, params = _candidate_filters(max_edge, 0, force, size_only, class_names)
    return _CANDIDATE_SUMMARY + filters, params


def select_candidates(
    cursor,
    max_edge,
    last_zoid=0,
    chunk=DEFAULT_CHUNK_SIZE,
    force=False,
    size_only=False,
):
    """Fetch the next chunk of candidate zoids, in zoid order."""
    sql, params = candidate_query(
        max_edge, last_zoid, chunk, force=force, size_only=size_only
    )
    cursor.execute(sql, params)
    # Rows are mappings: both connection sources — zodb-pgjsonb's storage
    # connection and pgcatalog's pool — build psycopg connections with the
    # ``dict_row`` factory.
    return [row["zoid"] for row in cursor.fetchall()]


def candidate_summary(cursor, max_edge, force=False, size_only=False):
    """Count the candidates, and how many of them have no ``_modified``."""
    sql, params = candidate_summary_query(max_edge, force=force, size_only=size_only)
    cursor.execute(sql, params)
    row = cursor.fetchall()[0]
    return {
        "candidates": int(row["candidates"] or 0),
        "without_modified": int(row["without_modified"] or 0),
    }


class Progress:
    """A keyset cursor per phase, persisted outside ZODB.

    The two phases are tracked separately on purpose.  Writing a
    derivative is not the end of the work for an image: until its owner
    has been reindexed the catalog still hands out a direct Thumbor URL
    pointing at the original, and a browser fetches that without Plone in
    the path.  A chunk counts as done only once reindexed — which is what
    ``reindex_pending`` reports — so phase 1 advancing on its own must not
    be readable as "finished".
    """

    def __init__(self, path):
        self.path = Path(path)
        self._phases = {
            phase: {"last_zoid": 0, "chunks": 0, "objects": 0} for phase in PHASES
        }

    @classmethod
    def load(cls, path):
        """Read the progress file, or start from the beginning."""
        progress = cls(path)
        try:
            raw = json.loads(progress.path.read_text())
        except (OSError, ValueError):
            # Missing and corrupt are the same answer.  A file truncated by
            # a SIGKILL is indistinguishable from garbage, and re-running a
            # chunk is a no-op — the outcome record makes it one — while
            # trusting a half-written cursor would skip images in silence.
            return progress
        progress._merge(raw)
        return progress

    def _merge(self, raw):
        """Absorb whatever of *raw* is usable, ignoring the rest."""
        phases = raw.get("phases") if isinstance(raw, dict) else None
        if not isinstance(phases, dict):
            return
        for phase, state in self._phases.items():
            stored = phases.get(phase)
            if not isinstance(stored, dict):
                continue
            for key in state:
                value = stored.get(key)
                # bool is an int subclass and would sneak through as 0/1.
                if isinstance(value, int) and not isinstance(value, bool):
                    state[key] = value

    def _state(self, phase):
        if phase not in self._phases:
            raise ValueError(f"unknown phase {phase!r}, expected one of {PHASES}")
        return self._phases[phase]

    def last_zoid(self, phase):
        """Highest zoid this phase has finished."""
        return self._state(phase)["last_zoid"]

    def chunks(self, phase):
        """Chunks this phase has completed."""
        return self._state(phase)["chunks"]

    def objects(self, phase):
        """Objects this phase has touched."""
        return self._state(phase)["objects"]

    def stats(self, phase):
        """A copy of one phase's counters."""
        return dict(self._state(phase))

    @property
    def reindex_pending(self):
        """True while phase 2 trails phase 1 — i.e. work is still unfinished."""
        return (
            self._state(PHASE_REINDEX)["last_zoid"]
            < self._state(PHASE_GENERATE)["last_zoid"]
        )

    def record_chunk(self, phase, last_zoid, objects=0):
        """Record one completed chunk of *phase* and persist it."""
        state = self._state(phase)
        # max(), not assignment: the cursor is a high-water mark.  A retry
        # pass or a verification run starting from zero must not be able to
        # rewind a phase and hand a later resume a zoid it has passed.
        state["last_zoid"] = max(state["last_zoid"], int(last_zoid))
        state["chunks"] += 1
        state["objects"] += int(objects)
        self.save()

    def as_dict(self):
        """The document that gets written."""
        return {
            "version": _PROGRESS_VERSION,
            "phases": {phase: dict(state) for phase, state in self._phases.items()},
        }

    def save(self):
        """Write the progress file atomically."""
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(self.as_dict(), indent=2, sort_keys=True))
        # os.replace is atomic within a filesystem, so a pod killed
        # mid-write leaves the previous cursor intact rather than a
        # plausible-looking truncated one.
        os.replace(temporary, self.path)


def resolve_max_edge():
    """The configured cap, or a loud failure.

    Read through ``get_thumbor_config()`` so the backfill and the
    subscriber cannot disagree about the cap — a disagreement would show
    up as a run that writes derivatives and leaves every one of them a
    candidate.

    Refusing at 0 is not pedantry.  ``set_source_derivative`` writes
    nothing at all at that cap — no derivative *and* no outcome record —
    so every candidate would stay a candidate and the run could never
    terminate, having reported that it processed everything.
    """
    from plone.pgthumbor.config import get_thumbor_config

    config = get_thumbor_config()
    if config is None:
        raise RuntimeError(
            "No Thumbor configuration found. Set PGTHUMBOR_SERVER_URL and "
            "PGTHUMBOR_SECURITY_KEY, or run this inside the instance that has them."
        )
    if config.source_max_edge <= 0:
        raise RuntimeError(
            "PGTHUMBOR_SOURCE_MAX_EDGE is 0 — derivative generation is disabled, "
            "so this run would write nothing and never terminate."
        )
    return config.source_max_edge


def chunk_size():
    """Objects per chunk, from the environment."""
    try:
        value = int(os.environ.get("PGTHUMBOR_BACKFILL_CHUNK", ""))
    except ValueError:
        return DEFAULT_CHUNK_SIZE
    return value if value > 0 else DEFAULT_CHUNK_SIZE


def progress_path():
    """Where the resumable cursor lives."""
    return Path(os.environ.get("PGTHUMBOR_BACKFILL_PROGRESS", DEFAULT_PROGRESS_PATH))


def env_flag(name):
    """Read a boolean the way ``config.py`` reads its own."""
    return os.environ.get(name, "").strip().lower() in ("true", "1", "yes")


def resolve_portal(app, site_id):
    """Find the site, elevate to a Manager, and make it the local site."""
    from AccessControl.SecurityManagement import newSecurityManager
    from zope.component.hooks import setSite

    if site_id not in app.objectIds():
        raise RuntimeError(
            f"Site {site_id!r} not found. Available: {list(app.objectIds())}"
        )

    acl = app.acl_users
    admin = acl.getUserById("admin")
    if admin is None:
        users = acl.getUsers()
        if not users:
            raise RuntimeError("No users found in root acl_users. Cannot elevate.")
        admin = users[0]
    newSecurityManager(None, admin.__of__(acl))

    portal = app[site_id]
    setSite(portal)
    return portal


# --- phase 1: generate -----------------------------------------------------


@contextmanager
def work_list_cursor(portal):
    """A psycopg cursor for the work list, on a connection of its own.

    Deliberately *not* ``get_storage_connection()``, which is what the
    request path uses.  That connection is the one zodb-pgjsonb runs its
    ``BEGIN ISOLATION LEVEL REPEATABLE READ`` on, and a SELECT issued on it
    between two ZODB transactions opens an implicit transaction first,
    which silently downgrades the next snapshot.  One extra pooled
    connection, held for the lifetime of a dedicated backfill pod, is the
    cheaper trade.

    Autocommit, because the walk runs for hours.  A single snapshot held
    open that long pins the dead tuples in ``object_state`` — the table
    every chunk writes to — and would also hide the derivatives the
    previous chunks just committed.

    Borrowed and given back.  Returning it matters more than it looks:
    ``psycopg_pool`` rolls back an open transaction on ``putconn`` but does
    **not** restore ``autocommit``, so handing this connection back as-is
    would leave the next borrower in autocommit without knowing it.  The
    flag is reset before the connection goes home.
    """
    from plone.pgcatalog.pool import get_pool

    pool = get_pool(portal)
    connection = pool.getconn()
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            yield cursor
    finally:
        connection.autocommit = False
        pool.putconn(connection)


def load_field_value(connection, zoid):
    """Load one ``NamedBlobImage`` by oid, without waking its owner.

    This is the whole reason phase 1 is memory-light.  A ``NamedBlobImage``
    is a row in ``object_state`` with a zoid of its own, so it can be
    fetched on its own: the content object that holds it, its annotations,
    its scales and its catalog entry all stay untouched.  Walking content
    objects instead is what OOM-killed a production container during the
    original scan.
    """
    return connection.get(p64(zoid))


# --- phase 2: reindex ------------------------------------------------------
#
# Phase 1 worked on field values by oid and never woke a content object.
# Phase 2 has to, because ``image_scales`` is catalog metadata *of the
# content object*, not of the image.
#
# The link back is ``object_state.refs``, which carries the oids a row
# points at, with a GIN index on it.  ``path IS NOT NULL`` is what makes a
# row a catalogued content object: field values and annotations never have
# one.  Traversing by path rather than loading by oid is deliberate —
# ``reindexObject`` reaches its catalog through acquisition, and an object
# fetched straight from the connection has no wrapper to reach through.

_GENERATED_SELECT = f"""\
SELECT zoid
FROM object_state
WHERE class_mod = %(class_mod)s
  AND class_name = ANY(%(class_names)s::text[])
  AND zoid > %(last_zoid)s
  AND NOT (state ? '{IS_DERIVATIVE_ATTRIBUTE}')
  AND state->'{INFO_ATTRIBUTE}'->>'reason' = %(generated_reason)s
ORDER BY zoid
LIMIT %(chunk)s
"""

_OWNERS_SELECT = """\
SELECT zoid, path
FROM object_state
WHERE refs && %(field_zoids)s::bigint[]
  AND path IS NOT NULL
"""


def generated_query(last_zoid, chunk, class_names=OBJECT_STATE_CLASS_NAMES):
    """Field values that got a derivative, in the same order phase 1 walked."""
    return _GENERATED_SELECT, {
        "class_mod": OBJECT_STATE_CLASS_MOD,
        "class_names": list(class_names),
        "last_zoid": last_zoid,
        "generated_reason": REASON_GENERATED,
        "chunk": chunk,
    }


def select_generated(cursor, last_zoid, chunk, class_names=OBJECT_STATE_CLASS_NAMES):
    sql, params = generated_query(last_zoid, chunk, class_names=class_names)
    cursor.execute(sql, params)
    return [row["zoid"] for row in cursor.fetchall()]


def owner_paths(cursor, field_zoids):
    """Catalogued content objects referencing any of *field_zoids*.

    Returns ``{field_zoid_is_not_returned: path}`` — a mapping of owner zoid
    to path.  One owner can hold several image fields, so this collapses
    naturally and each object is reindexed once per chunk.
    """
    if not field_zoids:
        return {}
    cursor.execute(_OWNERS_SELECT, {"field_zoids": list(field_zoids)})
    return {row["zoid"]: row["path"] for row in cursor.fetchall()}


def run_reindex(portal, cursor, progress, chunk):
    """Reindex ``image_scales`` for every object phase 1 gave a derivative.

    This is load-bearing rather than an optimisation.  For exactly the
    images this feature targets, the existing catalog rows do not hold uid
    URLs — they hold direct, absolute, signed Thumbor URLs, because
    ``_build_thumbor_url`` succeeded all along and it was Thumbor that
    answered 400 afterwards.  A browser fetches those without Plone in the
    path, so uid healing can never intervene.  Between phase 1 and phase 2
    nothing improves for them.
    """
    require_thumbor_request()

    stats = {"objects": 0, "chunks": 0, "reindexed": 0, "unowned": 0, "failed": 0}
    while True:
        last = progress.last_zoid(PHASE_REINDEX)
        zoids = select_generated(cursor, last, chunk)
        if not zoids:
            break

        owners = owner_paths(cursor, zoids)
        if len(owners) == 0 and zoids:
            # Every field value in this chunk is held by something that is
            # not catalogued — an annotation-nested behaviour, or an object
            # removed since phase 1.  Counted, never silent.
            stats["unowned"] += len(zoids)

        for path in owners.values():
            try:
                obj = portal.unrestrictedTraverse(path)
                # idxs= is not decoration: an empty idxs calls
                # notifyModified() and would bump the modification date of
                # every object touched, breaking recently-modified listings
                # and every downstream cache key with them.
                obj.reindexObject(idxs=["image_scales"])
                obj._p_deactivate()
                stats["reindexed"] += 1
            except Exception:
                stats["failed"] += 1
                print(f"  reindex failed for {path}", flush=True)

        transaction.commit()
        stats["objects"] += len(zoids)
        stats["chunks"] += 1
        progress.record_chunk(PHASE_REINDEX, max(zoids), len(zoids))
        _invalidate_cache(portal._p_jar)
        _release_memory()
    return stats


def run_generate(
    connection, cursor, max_edge, progress, chunk, force=False, size_only=False
):
    """Write derivatives, one committed chunk at a time.

    Takes the ZODB connection rather than the portal, because phase 1
    genuinely needs nothing else: no site, no request, no catalog.  Phase 2
    is the one that needs all three.
    """
    written = 0
    failed = 0
    while True:
        zoids = select_candidates(
            cursor,
            max_edge,
            last_zoid=progress.last_zoid(PHASE_GENERATE),
            chunk=chunk,
            force=force,
            size_only=size_only,
        )
        if not zoids:
            # The termination criterion: nothing left above the cursor.
            break

        for zoid in zoids:
            try:
                image = load_field_value(connection, zoid)
                # force is passed through, not re-derived: without it a
                # forced run widens the SQL population and then skips every
                # extra row it selected — a full table read that writes
                # nothing.
                if set_source_derivative(image, max_edge=max_edge, force=force):
                    written += 1
                # A no-op on the ones just modified, which the cache
                # invalidation below ghosts instead; it is what frees the
                # ones that turned out to need nothing.
                image._p_deactivate()
            except Exception:
                # One unreadable blob must not end a run of tens of
                # thousands.  The traceback is printed and the object is
                # left a candidate: it has no outcome record, so the next
                # run selects it again.
                failed += 1
                print(f"FAILED zoid={zoid}", flush=True)
                traceback.print_exc()

        # Commit first, record second.  The other order loses work: a pod
        # killed between the two would resume past objects whose
        # derivatives were never committed, and nothing would select them
        # again.  Re-running a committed chunk is a no-op instead — the
        # outcome record makes it one.
        transaction.commit()
        # The cursor advances past failures too, deliberately.  Otherwise
        # one permanently broken blob makes the run select, fail and
        # re-select the same chunk for as long as the pod lives.
        progress.record_chunk(PHASE_GENERATE, last_zoid=zoids[-1], objects=len(zoids))

        _invalidate_cache(connection)
        _release_memory()

        print(
            f"{PHASE_GENERATE}: {progress.objects(PHASE_GENERATE)} objects, "
            f"{written} written, {failed} failed, cursor at {zoids[-1]}",
            flush=True,
        )

    return {
        "chunks": progress.chunks(PHASE_GENERATE),
        "objects": progress.objects(PHASE_GENERATE),
        "written": written,
        "failed": failed,
    }


# --- the dry run -----------------------------------------------------------


def _known_scale_names():
    """Registered scale names, longest first, or nothing.

    Used only to split ``{fieldname}_{scalename}`` crop keys, which are
    ambiguous on their own — both halves may contain an underscore.  A
    registered name resolves it; without a registry (no site, no Plone)
    the histogram falls back to the last segment.
    """
    try:
        from plone.pgthumbor.uid_healing import registered_scales

        names = {name for name, _width, _height in registered_scales()}
    except Exception:
        return ()
    return tuple(sorted(names, key=len, reverse=True))


def _scale_name(key, scale_names):
    """The scale half of a ``{fieldname}_{scalename}`` crop key."""
    for name in scale_names:
        if key.endswith(f"_{name}"):
            return name
    _fieldname, separator, scale = key.rpartition("_")
    return scale if separator else None


def _crop_keys(state):
    """Every crop key in one stored mapping, whatever shape it has."""
    if not isinstance(state, dict):
        return []
    data = state.get("data")
    if isinstance(data, dict):
        # PersistentMapping / PersistentDict.
        return [key for key in data if isinstance(key, str)]
    pairs = state.get("@kv")
    if isinstance(pairs, list):
        # A BTree, if a deployment stored one here.
        return [
            pair[0]
            for pair in pairs
            if isinstance(pair, list) and pair and isinstance(pair[0], str)
        ]
    # Stored inline as a plain dict: the crop keys are the state.  The "@"
    # keys are the codec's own markers, never crop names.
    return [key for key in state if isinstance(key, str) and not key.startswith("@")]


def crop_histogram(cursor, scale_names=None):
    """How many crops each scale name carries, across the whole site.

    This is the binding *S* in the design's ``X >= S / cap`` threshold, and
    therefore the entire input to choosing a cap: a scale that carries no
    crop puts no floor on anything.

    Never fatal.  ``plone.app.imagecropping`` may not be installed at all,
    and the stored shapes it leaves behind are not this script's contract —
    an empty histogram costs the operator one of four numbers, while an
    exception costs them the other three as well.
    """
    if scale_names is None:
        scale_names = _known_scale_names()
    try:
        # The key comes from the adapter that reads these crops at request
        # time, so the dry run and the renderer cannot look in two
        # different places.  Imported inside the guard: everything about
        # this number is optional, including its import.
        from plone.pgthumbor.addons_compat.imagecropping import ANNOTATION_KEY

        cursor.execute(_CROP_ANNOTATION_SELECT, {"annotation_key": ANNOTATION_KEY})
        rows = cursor.fetchall()

        states = []
        references = set()
        for row in rows:
            crops = row["crops"]
            if not isinstance(crops, dict):
                continue
            reference = crops.get("@ref")
            if isinstance(reference, list) and reference:
                # ``["17db849812c6bd21", "<class>"]`` — the zoid is hex.
                references.add(int(reference[0], 16))
            else:
                states.append(crops)

        if references:
            cursor.execute(_CROP_STORAGE_SELECT, {"zoids": sorted(references)})
            states.extend(row["state"] for row in cursor.fetchall())
    except Exception:
        print("Crop histogram unavailable:", flush=True)
        traceback.print_exc()
        return {}

    counts = Counter()
    for state in states:
        for key in _crop_keys(state):
            name = _scale_name(key, scale_names)
            if name:
                counts[name] += 1
    return dict(counts.most_common())


def sample_derivative_sizes(
    cursor,
    connection,
    max_edge,
    sample=DEFAULT_SAMPLE_SIZE,
    force=False,
    size_only=False,
):
    """Encoded derivative sizes for the first *sample* candidates.

    The first by zoid, not a random draw: a random sample needs a full scan
    of the filtered set, and the keyset walk is already there.  The bias is
    real — low zoids are old content — and the report says so rather than
    hiding it behind the word "sample".

    Nothing is written.  ``build_derivative_bytes`` is the same encoder the
    run uses, called directly, so the number is bytes that would really
    land in blob storage rather than an estimate of them.
    """
    zoids = select_candidates(
        cursor, max_edge, last_zoid=0, chunk=sample, force=force, size_only=size_only
    )
    sizes = []
    for zoid in zoids:
        try:
            image = load_field_value(connection, zoid)
            with image.open("r") as stream:
                built = build_derivative_bytes(stream, max_edge)
            if built is not None:
                # None means no trigger fired — nothing would be written
                # for this one, so it contributes no storage either.
                sizes.append(len(built[0]))
            image._p_deactivate()
        except Exception:
            print(f"Sample failed for zoid={zoid}", flush=True)
            traceback.print_exc()
    return sizes


def dry_run_report(
    cursor,
    connection,
    max_edge,
    force=False,
    size_only=False,
    sample=DEFAULT_SAMPLE_SIZE,
):
    """Measure the work without doing any of it."""
    try:
        summary = candidate_summary(cursor, max_edge, force=force, size_only=size_only)
        sizes = sample_derivative_sizes(
            cursor,
            connection,
            max_edge,
            sample=sample,
            force=force,
            size_only=size_only,
        )
        crops = crop_histogram(cursor)
    finally:
        # Reading a blob joins the transaction.  Aborting keeps "the dry
        # run writes nothing" true by construction rather than by argument.
        transaction.abort()

    return {
        "max_edge": max_edge,
        "sample_size": sample,
        "candidates": summary["candidates"],
        "without_modified": summary["without_modified"],
        "median_bytes": int(statistics.median(sizes)) if sizes else None,
        "sampled": len(sizes),
        "crops": crops,
    }


def print_dry_run_report(report):
    """The four numbers, and what each one is for."""
    print("", flush=True)
    print(f"DRY RUN at cap {report['max_edge']}px — nothing was written.", flush=True)
    print(f"  candidates:        {report['candidates']}", flush=True)
    median = report["median_bytes"]
    print(
        "  median derivative: "
        + (f"{median} bytes" if median is not None else "no sample produced one")
        + f" (from {report['sampled']} of the first {report['sample_size']} by zoid)",
        flush=True,
    )
    print(
        f"  uids that will move: {report['without_modified']} "
        f"(candidates with no {MODIFIED_ATTRIBUTE}; every scale uid for "
        "those images changes when the derivative is written)",
        flush=True,
    )
    if report["crops"]:
        print("  scales carrying crops:", flush=True)
        for name, count in report["crops"].items():
            print(f"    {name}: {count}", flush=True)
    else:
        print("  scales carrying crops: none found", flush=True)
    print("", flush=True)


# --- the run ---------------------------------------------------------------


_UNOWNED_GENERATED = f"""\
SELECT count(*) AS unowned
FROM object_state AS fv
WHERE fv.class_mod = %(class_mod)s
  AND fv.class_name = ANY(%(class_names)s::text[])
  AND NOT (fv.state ? '{IS_DERIVATIVE_ATTRIBUTE}')
  AND fv.state->'{INFO_ATTRIBUTE}'->>'reason' = %(generated_reason)s
  AND NOT EXISTS (
    SELECT 1 FROM object_state AS owner
    WHERE owner.refs @> ARRAY[fv.zoid] AND owner.path IS NOT NULL
  )
"""

_STALE_SCALES = f"""\
SELECT count(*) AS stale
FROM object_state AS owner
WHERE owner.path IS NOT NULL
  AND (owner.idx IS NULL OR owner.idx->'image_scales' IS NULL
       OR jsonb_typeof(owner.idx->'image_scales') = 'null')
  AND EXISTS (
    SELECT 1 FROM object_state AS fv
    WHERE fv.zoid = ANY(owner.refs)
      AND NOT (fv.state ? '{IS_DERIVATIVE_ATTRIBUTE}')
      AND fv.state->'{INFO_ATTRIBUTE}'->>'reason' = %(generated_reason)s
  )
"""


def verify(cursor, max_edge, force=False, size_only=False):
    """Three counts that must all be zero for the run to have finished.

    *remaining* — phase 1 still has candidates, so the population was not
    covered.

    *stale_scales* — a content object holding a derivative-bearing image has
    no ``image_scales`` metadata at all.  That is both "phase 2 never
    reached it" and "something nulled the column", which is the failure a
    request-less reindex causes and the reason this script refuses to start
    without one.

    *unowned* — a derivative-bearing field value that no catalogued object
    references.  Nothing can reindex it, so it is reported rather than
    silently counted as done: an annotation-nested behaviour would land
    here, and so would an object deleted between the two phases.

    The spec also asks for a URL-level assertion — no catalog row carrying a
    Thumbor URL whose blob zoid belongs to an original that now has a
    derivative.  That is deliberately *not* implemented: it needs per-row
    hex matching inside JSONB, it cannot be expressed as one indexed query,
    and the failure it detects is precisely the one
    ``require_thumbor_request`` refuses to let happen.  The two counts above
    catch the outcomes that survive that guard.
    """
    summary = candidate_summary(cursor, max_edge, force=force, size_only=size_only)
    params = {
        "class_mod": OBJECT_STATE_CLASS_MOD,
        "class_names": list(OBJECT_STATE_CLASS_NAMES),
        "generated_reason": REASON_GENERATED,
    }
    cursor.execute(_UNOWNED_GENERATED, params)
    unowned = cursor.fetchone()["unowned"]
    cursor.execute(_STALE_SCALES, {"generated_reason": REASON_GENERATED})
    stale = cursor.fetchone()["stale"]
    return {
        "remaining": summary["candidates"],
        "unowned": unowned,
        "stale_scales": stale,
        "verified": summary["candidates"] == 0 and unowned == 0 and stale == 0,
    }


def print_verification(report):
    print("", flush=True)
    print("Verification", flush=True)
    print(f"  candidates remaining     {report['remaining']}", flush=True)
    print(f"  derivatives with no owner {report['unowned']}", flush=True)
    print(f"  owners without scales     {report['stale_scales']}", flush=True)
    print(
        "  VERIFIED" if report["verified"] else "  NOT VERIFIED — see the counts above",
        flush=True,
    )


def run(
    portal,
    max_edge,
    progress,
    chunk,
    force=False,
    size_only=False,
    dry_run=False,
    cursor=None,
):
    """Measure, or run phase 1.

    *cursor* is injectable so the runners can be exercised without a
    database; left alone it is borrowed from the pool for the duration.
    """
    if cursor is None:
        with work_list_cursor(portal) as borrowed:
            return run(
                portal,
                max_edge,
                progress,
                chunk,
                force=force,
                size_only=size_only,
                dry_run=dry_run,
                cursor=borrowed,
            )
    connection = portal._p_jar

    if dry_run:
        report = dry_run_report(
            cursor, connection, max_edge, force=force, size_only=size_only
        )
        print_dry_run_report(report)
        return report

    stats = run_generate(
        connection,
        cursor,
        max_edge=max_edge,
        progress=progress,
        chunk=chunk,
        force=force,
        size_only=size_only,
    )
    print(
        f"{PHASE_GENERATE} finished: {stats['objects']} objects in "
        f"{stats['chunks']} chunks, {stats['written']} derivatives written, "
        f"{stats['failed']} failed.",
        flush=True,
    )

    # Phase 2 is not an optimisation.  For exactly the images phase 1 just
    # fixed, the catalog holds direct, signed Thumbor URLs pointing at the
    # originals — a browser fetches those without Plone in the path, so uid
    # healing can never reach them.  Until this runs, nothing improved.
    reindex_stats = run_reindex(portal, cursor, progress, chunk)
    print(
        f"{PHASE_REINDEX} finished: {reindex_stats['reindexed']} objects "
        f"reindexed, {reindex_stats['unowned']} derivatives with no "
        f"catalogued owner, {reindex_stats['failed']} failed.",
        flush=True,
    )

    report = verify(cursor, max_edge, force=force, size_only=size_only)
    print_verification(report)
    return {"generate": stats, "reindex": reindex_stats, "verification": report}


def main(app):
    """Entry point for ``zconsole run``.

    The first thing it does is establish a request carrying the browser
    layer.  Phase 2 reindexes ``image_scales``, and without one that
    overwrites the column with null for every object it touches — see
    ``plone.pgthumbor.zconsole`` for why the failure is silent.
    """
    site_id = os.environ.get("SITE_ID", "Plone")
    # Before resolving the site, and long before any write.  Phase 2
    # reindexes image_scales, and without a request carrying the browser
    # layer that overwrites the column with null for every object touched.
    app = establish_request(app)
    portal = resolve_portal(app, site_id)
    max_edge = resolve_max_edge()
    progress = Progress.load(progress_path())
    dry_run = env_flag("PGTHUMBOR_BACKFILL_DRY_RUN")

    print(
        f"Site {site_id}: cap {max_edge}px, chunk {chunk_size()}, "
        f"progress file {progress.path}",
        flush=True,
    )
    print(
        f"Resuming at {PHASE_GENERATE}={progress.last_zoid(PHASE_GENERATE)}, "
        f"{PHASE_REINDEX}={progress.last_zoid(PHASE_REINDEX)}",
        flush=True,
    )
    if dry_run:
        # Said before the work rather than only after it: the crop
        # histogram is a sequential scan and the sample is a decode per
        # image, so this can sit silent for minutes.
        print("DRY RUN: measuring only, nothing will be written.", flush=True)

    run(
        portal,
        max_edge=max_edge,
        progress=progress,
        chunk=chunk_size(),
        force=env_flag("PGTHUMBOR_BACKFILL_FORCE"),
        size_only=env_flag("PGTHUMBOR_BACKFILL_SIZE_ONLY"),
        dry_run=dry_run,
    )


# --- zconsole run entry point ---
#
# ``zconsole run zope.conf <script>`` injects ``app`` into globals.  The
# guard is a plain ``if`` and not the sibling script's module-scope
# ``sys.exit(1)``: this module is loaded by the test suite, and an exit at
# import time would make the candidate query and the progress file
# untestable — the two parts most worth testing, because neither is
# exercised anywhere else.

if "app" in dir():
    try:
        main(app)  # noqa: F821 — injected by zconsole
    except Exception:
        traceback.print_exc()
        sys.exit(1)
