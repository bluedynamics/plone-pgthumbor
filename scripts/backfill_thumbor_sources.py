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
======================================  =====================================

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

from pathlib import Path
from plone.pgthumbor.derivative import INFO_ATTRIBUTE
from plone.pgthumbor.derivative import IS_DERIVATIVE_ATTRIBUTE
from plone.pgthumbor.derivative import TERMINAL_REASONS

import json
import os
import sys
import traceback


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
_CANDIDATE_SELECT = f"""\
SELECT zoid
FROM object_state
WHERE class_mod = %(class_mod)s
  AND class_name = ANY(%(class_names)s::text[])
  AND zoid > %(last_zoid)s
  AND NOT (state ? '{IS_DERIVATIVE_ATTRIBUTE}')
"""

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

DEFAULT_CHUNK_SIZE = 100
DEFAULT_PROGRESS_PATH = "/tmp/pgthumbor-backfill-progress.json"

PHASE_GENERATE = "generate"
PHASE_REINDEX = "reindex"
PHASES = (PHASE_GENERATE, PHASE_REINDEX)

_PROGRESS_VERSION = 1


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
    sql = _CANDIDATE_SELECT
    params = {
        "class_mod": OBJECT_STATE_CLASS_MOD,
        "class_names": list(class_names),
        "last_zoid": last_zoid,
        "chunk": chunk,
    }
    if not force:
        sql += _NOT_TERMINAL
        params["terminal_reasons"] = sorted(TERMINAL_REASONS)
        params["max_edge"] = max_edge
    if size_only:
        sql += _OVERSIZED
        params["max_edge"] = max_edge
    return sql + _CANDIDATE_TAIL, params


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


def run(portal, max_edge, progress, chunk, force=False, size_only=False):
    """Run phase 1 and then phase 2.

    Not implemented yet, and loudly so: the two runners are the next two
    tasks of the source-derivative plan.  Everything they need — the
    candidate query, the resumable per-phase cursor, the site and the cap
    — is above.
    """
    raise NotImplementedError(
        "phase 1 (generate) and phase 2 (reindex) are not implemented yet"
    )


def main(app):
    """Entry point for ``zconsole run``.

    Phase 2 has a further requirement this does not satisfy yet: the
    reindex must run with a request that carries the browser layer, or it
    overwrites ``image_scales`` with null for every object it touches.
    That wiring belongs to the phase-2 task and lands with it.
    """
    site_id = os.environ.get("SITE_ID", "Plone")
    portal = resolve_portal(app, site_id)
    max_edge = resolve_max_edge()
    progress = Progress.load(progress_path())

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

    run(
        portal,
        max_edge=max_edge,
        progress=progress,
        chunk=chunk_size(),
        force=env_flag("PGTHUMBOR_BACKFILL_FORCE"),
        size_only=env_flag("PGTHUMBOR_BACKFILL_SIZE_ONLY"),
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
