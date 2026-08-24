"""Tests for ``scripts/backfill_thumbor_sources.py``.

The script is not part of the importable package — it lives in ``scripts/``
and is run through ``zconsole run`` — so it is loaded from its path here.
That it *can* be loaded at all is itself an assertion: the zconsole
bootstrap sits under ``if "app" in dir():`` rather than the sibling
script's module-scope ``sys.exit(1)``, precisely so the pure parts below
are reachable from a test.

No database is involved.  The candidate query is exercised against a fake
cursor that records ``execute(sql, params)``, which is the only part of
psycopg's contract these functions depend on.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import ast
import functools
import importlib.util
import json
import pytest
import statistics


@functools.cache
def _backfill():
    """Load the script as a module, once per session."""
    path = (
        Path(__file__).resolve().parents[1] / "scripts" / "backfill_thumbor_sources.py"
    )
    spec = importlib.util.spec_from_file_location("backfill_thumbor_sources", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeCursor:
    """The whole of the psycopg surface the candidate query needs.

    Rows are dicts because both connection sources — zodb-pgjsonb's
    storage connection and pgcatalog's pool — build their connections with
    psycopg's ``dict_row`` factory.
    """

    def __init__(self, rows=()):
        self.rows = [dict(row) for row in rows]
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self

    def fetchall(self):
        return list(self.rows)

    @property
    def sql(self):
        return " ".join(self.calls[-1][0].split())

    @property
    def params(self):
        return self.calls[-1][1]


class _Recorded:
    """A field value carrying nothing but an outcome record."""

    def __init__(self, info):
        self._pgthumbor_source_info = info


def _select(rows=(), **kwargs):
    """Run one candidate selection and hand back the cursor and the zoids."""
    backfill = _backfill()
    kwargs.setdefault("max_edge", 4000)
    cursor = _FakeCursor(rows)
    zoids = backfill.select_candidates(cursor, **kwargs)
    return cursor, zoids


# --- fakes for the runners -------------------------------------------------
#
# Still no database and no Zope: a run is a cursor, a ZODB connection and a
# transaction manager, and all three are small enough to fake honestly.


class _RunnerCursor:
    """A fake cursor answering every query one run asks.

    Dispatch is on what the SQL asks for, not on call order.  A dry run
    interleaves the candidate walk with the summary and the two crop
    queries, and a positional script would freeze whichever order the
    implementation happens to use today.
    """

    def __init__(self, chunks=(), summary=None, annotations=(), storages=()):
        self.chunks = [list(chunk) for chunk in chunks]
        self.summary = dict(summary or {"candidates": 0, "without_modified": 0})
        self.annotations = [dict(row) for row in annotations]
        self.storages = [dict(row) for row in storages]
        self.calls = []
        self._rows = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        text = " ".join(sql.split())
        if "count(*)" in text:
            self._rows = [dict(self.summary)]
        elif "jsonb_array_elements" in text:
            self._rows = list(self.annotations)
        elif "zoid = ANY" in text:
            self._rows = list(self.storages)
        else:
            chunk = self.chunks.pop(0) if self.chunks else []
            self._rows = [{"zoid": zoid} for zoid in chunk]
        return self

    def fetchall(self):
        return list(self._rows)

    @property
    def walk(self):
        """Only the candidate-walk calls, in order."""
        return [call for call in self.calls if "SELECT zoid" in call[0]]


class _RaisingCursor:
    """A cursor that refuses every query, the way an older schema would."""

    def execute(self, sql, params=None):
        raise RuntimeError("no such column")

    def fetchall(self):  # pragma: no cover - never reached
        raise RuntimeError("no such column")


class _FakeCache:
    """The two methods ``_invalidate_cache`` uses on a ZODB cache."""

    def __init__(self, oids=(b"\x00" * 7 + b"\x01",)):
        self.oids = list(oids)
        self.invalidated = []

    def items(self):
        return [(oid, object()) for oid in self.oids]

    def invalidate(self, oid):
        self.invalidated.append(oid)


class _FakeImage:
    """A field value that records what a run did to it."""

    def __init__(self, zoid, payload=b""):
        self.zoid = zoid
        self.payload = payload
        self.deactivated = 0

    def open(self, mode="r"):
        import io

        return io.BytesIO(self.payload)

    def _p_deactivate(self):
        self.deactivated += 1


class _FakeConnection:
    """The whole of the ZODB ``Connection`` surface a run uses.

    ``get(p64(zoid))`` and a cache, and nothing else.  No traversal, no
    catalog, no content object — which is exactly the property that keeps
    phase 1 memory-light, so the fake would break loudly if that changed.
    """

    def __init__(self, payloads=None, unreadable=()):
        self.payloads = dict(payloads or {})
        self.unreadable = set(unreadable)
        self.loaded = []
        self.images = {}
        self._cache = _FakeCache()

    def get(self, oid):
        from ZODB.utils import u64

        zoid = u64(oid)
        self.loaded.append(zoid)
        if zoid in self.unreadable:
            raise RuntimeError(f"blob {zoid} cannot be read")
        image = self.images.get(zoid)
        if image is None:
            image = _FakeImage(zoid, self.payloads.get(zoid, b""))
            self.images[zoid] = image
        return image


class _FakeTransaction:
    """A transaction manager that only remembers what it was asked to do."""

    def __init__(self):
        self.events = []

    @property
    def commits(self):
        return self.events.count("commit")

    @property
    def aborts(self):
        return self.events.count("abort")

    def commit(self):
        self.events.append("commit")

    def abort(self):
        self.events.append("abort")


def _generate(
    monkeypatch,
    tmp_path,
    chunks,
    *,
    max_edge=4000,
    chunk=2,
    force=False,
    generator=None,
    connection=None,
    progress=None,
):
    """Run phase 1 over *chunks* and hand back everything it touched."""
    backfill = _backfill()
    transaction = _FakeTransaction()
    monkeypatch.setattr(backfill, "transaction", transaction)

    generated = []

    def default_generator(image, max_edge=None, force=False):
        generated.append((image.zoid, max_edge, force))
        return True

    monkeypatch.setattr(
        backfill, "set_source_derivative", generator or default_generator
    )

    cursor = _RunnerCursor(chunks=chunks)
    connection = connection if connection is not None else _FakeConnection()
    progress = (
        progress if progress is not None else backfill.Progress(tmp_path / "p.json")
    )
    stats = backfill.run_generate(
        connection,
        cursor,
        max_edge=max_edge,
        progress=progress,
        chunk=chunk,
        force=force,
    )
    return SimpleNamespace(
        backfill=backfill,
        stats=stats,
        cursor=cursor,
        connection=connection,
        progress=progress,
        transaction=transaction,
        generated=generated,
    )


class TestModuleBootstrap:
    """Importing the script must not run it."""

    def test_the_module_loads_without_an_injected_app(self):
        # A module-scope ``sys.exit(1)`` — what purge_legacy_scales.py does
        # — would raise SystemExit right here.
        backfill = _backfill()

        assert callable(backfill.main)

    def test_the_bootstrap_is_guarded_by_the_injected_global(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "backfill_thumbor_sources.py"
        )
        source = path.read_text()

        assert 'if "app" in dir():' in source


class TestCandidateQueryShape:
    """The work list is a keyset walk over object_state, not a catalog walk.

    A brain walk over the result set OOM-killed a production container
    during the original scan, and OFFSET drifts as soon as the population
    changes under a resumable run.
    """

    def test_selects_zoids_from_object_state(self):
        cursor, _ = _select()

        assert "SELECT zoid" in cursor.sql
        assert "FROM object_state" in cursor.sql

    def test_filters_on_the_named_blob_image_class(self):
        backfill = _backfill()
        cursor, _ = _select()

        assert "class_mod = %(class_mod)s" in cursor.sql
        assert "class_name = ANY(%(class_names)s::text[])" in cursor.sql
        assert cursor.params["class_mod"] == "plone.namedfile.file"
        assert "NamedBlobImage" in cursor.params["class_names"]
        assert backfill.OBJECT_STATE_CLASS_MOD == "plone.namedfile.file"

    def test_paginates_on_the_keyset_and_never_on_offset(self):
        cursor, _ = _select(last_zoid=4711)

        assert "zoid > %(last_zoid)s" in cursor.sql
        assert "ORDER BY zoid" in cursor.sql
        assert "LIMIT %(chunk)s" in cursor.sql
        assert "OFFSET" not in cursor.sql.upper()
        assert cursor.params["last_zoid"] == 4711

    def test_the_chunk_size_is_a_parameter(self):
        cursor, _ = _select(chunk=25)

        assert cursor.params["chunk"] == 25

    def test_returns_the_zoids_in_row_order(self):
        _, zoids = _select(rows=[{"zoid": 3}, {"zoid": 9}, {"zoid": 12}])

        assert zoids == [3, 9, 12]

    def test_one_execute_per_chunk(self):
        cursor, _ = _select()

        assert len(cursor.calls) == 1

    def test_the_like_pattern_is_a_parameter_not_inlined_sql(self):
        # psycopg parses ``%`` in the query text as a placeholder marker, so
        # an inlined LIKE pattern would have to be written ``%%`` — a
        # doubling nobody remembers when editing the SQL later.
        cursor, _ = _select()

        assert "%" not in cursor.sql.replace("%(", "").replace(")s", "")


class TestCandidatePredicate:
    """Only *terminal* outcomes are excluded from an ordinary run.

    A recorded ``"retry"`` (semaphore timeout) or ``"error"`` (failed
    decode) is transient: the whole point of recording it is that a later
    run picks the image up again.  If this predicate and
    ``derivative.needs_processing`` drift apart, one contended upload
    excludes its image permanently while the terminal verification still
    reports success.
    """

    def test_an_image_with_no_outcome_record_is_a_candidate(self):
        cursor, _ = _select()

        assert "NOT (state ? '_pgthumbor_source_info')" in cursor.sql

    def test_the_attribute_the_sql_reads_is_the_one_the_generator_writes(
        self, monkeypatch
    ):
        from plone.namedfile.file import NamedBlobImage
        from plone.pgthumbor.derivative import set_source_derivative
        from tests.conftest import jpeg_bytes
        from tests.conftest import namedfile_storables

        backfill = _backfill()
        with namedfile_storables():
            image = NamedBlobImage(
                data=jpeg_bytes(), filename="t.jpg", contentType="image/jpeg"
            )
            set_source_derivative(image, max_edge=4000)

            # Spelling the attribute name twice is the only way these two
            # can disagree, so the test reads it off a real generated
            # record rather than off a second string literal.
            assert hasattr(image, backfill.INFO_ATTRIBUTE)

    def test_only_terminal_reasons_are_excluded(self):
        from plone.pgthumbor.derivative import TERMINAL_REASONS

        cursor, _ = _select()

        assert cursor.params["terminal_reasons"] == sorted(TERMINAL_REASONS)

    def test_the_reason_vocabulary_is_imported_never_respelled(self):
        from plone.pgthumbor.derivative import REASON_ERROR
        from plone.pgthumbor.derivative import REASON_RETRY

        backfill = _backfill()
        cursor, _ = _select()

        assert REASON_RETRY not in cursor.params["terminal_reasons"]
        assert REASON_ERROR not in cursor.params["terminal_reasons"]
        # And no reason string is written out in the script at all: the AST
        # is read rather than the text, so prose in a comment explaining
        # why "retry" is not terminal does not count as a re-spelling.
        tree = ast.parse(Path(backfill.__file__).read_text())
        literals = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }

        assert literals.isdisjoint(
            {"generated", "not_needed", "skipped_type", "retry", "error"}
        )

    def test_a_non_terminal_reason_survives_the_exclusion(self):
        cursor, _ = _select()

        assert (
            "coalesce(state->'_pgthumbor_source_info'->>'reason', '') "
            "<> ALL (%(terminal_reasons)s::text[])" in cursor.sql
        )

    def test_the_excluded_set_is_exactly_what_the_generator_skips(self):
        """The SQL and ``_should_process`` must agree, reason by reason."""
        from plone.pgthumbor.derivative import needs_processing
        from plone.pgthumbor.derivative import REASON_ERROR
        from plone.pgthumbor.derivative import REASON_GENERATED
        from plone.pgthumbor.derivative import REASON_NOT_NEEDED
        from plone.pgthumbor.derivative import REASON_RETRY
        from plone.pgthumbor.derivative import REASON_SKIPPED_TYPE

        cursor, _ = _select(max_edge=4000)
        excluded = cursor.params["terminal_reasons"]

        for reason in (
            REASON_GENERATED,
            REASON_NOT_NEEDED,
            REASON_SKIPPED_TYPE,
            REASON_RETRY,
            REASON_ERROR,
        ):
            recorded = _Recorded({"reason": reason, "max_edge": 4000})
            skipped_by_generator = not needs_processing(recorded, 4000, force=False)

            assert (reason in excluded) is skipped_by_generator

    def test_the_generator_also_reprocesses_under_a_changed_cap(self):
        from plone.pgthumbor.derivative import needs_processing
        from plone.pgthumbor.derivative import REASON_GENERATED

        recorded = _Recorded({"reason": REASON_GENERATED, "max_edge": 4000})

        # The SQL half of this is the ``max_edge IS DISTINCT FROM`` term
        # asserted below; this half proves the rule it mirrors is real.
        assert needs_processing(recorded, 5000, force=False) is True

    def test_a_cap_mismatch_keeps_an_image_a_candidate(self):
        # Recording the cap is what turns tuning it into a setting change
        # rather than a migration: no force flag for anyone to forget.
        cursor, _ = _select(max_edge=5000)

        assert (
            "state->'_pgthumbor_source_info'->'max_edge' "
            "IS DISTINCT FROM to_jsonb(%(max_edge)s::int)" in cursor.sql
        )
        assert cursor.params["max_edge"] == 5000

    def test_a_malformed_record_is_a_candidate(self):
        cursor, _ = _select()

        assert "jsonb_typeof(state->'_pgthumbor_source_info') <> 'object'" in cursor.sql

    def test_force_drops_the_outcome_predicate_entirely(self):
        cursor, _ = _select(force=True)

        assert "_pgthumbor_source_info" not in cursor.sql
        assert "terminal_reasons" not in cursor.params
        # Still a keyset walk over the same class — force widens the
        # population, it does not change how it is paged.
        assert "zoid > %(last_zoid)s" in cursor.sql


class TestDerivativesAreNotCandidates:
    """A generated derivative is itself a NamedBlobImage row in object_state.

    Left in, it would be decoded on every run, found to need nothing and
    stamped with an outcome record of its own — and phase 2 has no content
    object to reindex for it, because its parent is the original field
    value rather than a content item.
    """

    def test_the_exclusion_is_the_marker_the_generator_writes(self, monkeypatch):
        from plone.pgthumbor.derivative import IS_DERIVATIVE_ATTRIBUTE
        from plone.pgthumbor.derivative import set_source_derivative
        from tests.conftest import big_jpeg_bytes
        from tests.conftest import env_override
        from tests.conftest import namedfile_storables

        # Structure, not a guess at the filename: an editorial upload named
        # "something-pgthumbor-source.jpg" must not be skipped for good with
        # nothing in the log to say so.
        env_override(
            monkeypatch,
            PGTHUMBOR_SERVER_URL="http://thumbor:8888",
            PGTHUMBOR_SECURITY_KEY="key",
        )
        with namedfile_storables():
            from plone.namedfile.file import NamedBlobImage

            image = NamedBlobImage(
                data=big_jpeg_bytes(), filename="t.jpg", contentType="image/jpeg"
            )
            set_source_derivative(image, max_edge=1000)

            assert getattr(image._pgthumbor_source, IS_DERIVATIVE_ATTRIBUTE) is True
            # And never on the original, or the backfill would exclude
            # every image it had already processed.
            assert not hasattr(image, IS_DERIVATIVE_ATTRIBUTE)

    def test_the_predicate_names_that_marker(self):
        from plone.pgthumbor.derivative import IS_DERIVATIVE_ATTRIBUTE

        cursor, _ = _select()

        assert f"NOT (state ? '{IS_DERIVATIVE_ATTRIBUTE}')" in cursor.sql


class TestSizeOnlyPass:
    """Pass 1 selects by pixel count; the colour trigger is invisible to SQL."""

    def test_size_only_adds_the_dimension_predicate(self):
        cursor, _ = _select(max_edge=4000, size_only=True)

        assert "state->'_width' > to_jsonb(%(max_edge)s::int)" in cursor.sql
        assert "state->'_height' > to_jsonb(%(max_edge)s::int)" in cursor.sql
        assert cursor.params["max_edge"] == 4000

    def test_the_ordinary_pass_carries_no_dimension_predicate(self):
        cursor, _ = _select()

        assert "state->'_width'" not in cursor.sql

    def test_dimensions_are_only_compared_when_they_are_numbers(self):
        # A jsonb comparison never raises; a ``::int`` cast on a stored
        # string would, and PostgreSQL gives no evaluation-order guarantee
        # that a guarding AND runs first.
        cursor, _ = _select(size_only=True)

        assert "jsonb_typeof(state->'_width') = 'number'" in cursor.sql
        assert "jsonb_typeof(state->'_height') = 'number'" in cursor.sql

    def test_size_only_still_excludes_terminal_outcomes(self):
        cursor, _ = _select(size_only=True)

        assert "_pgthumbor_source_info" in cursor.sql

    def test_size_only_with_force_still_needs_the_cap(self):
        cursor, _ = _select(max_edge=6000, size_only=True, force=True)

        assert cursor.params["max_edge"] == 6000


class TestProgressRoundTrip:
    """Progress lives outside ZODB and survives a killed pod."""

    def test_round_trips_through_the_file(self, tmp_path):
        backfill = _backfill()
        path = tmp_path / "progress.json"

        progress = backfill.Progress(path)
        progress.record_chunk(backfill.PHASE_GENERATE, last_zoid=120, objects=17)
        reloaded = backfill.Progress.load(path)

        assert reloaded.last_zoid(backfill.PHASE_GENERATE) == 120
        assert reloaded.objects(backfill.PHASE_GENERATE) == 17
        assert reloaded.chunks(backfill.PHASE_GENERATE) == 1

    def test_a_missing_file_starts_at_zero(self, tmp_path):
        backfill = _backfill()

        progress = backfill.Progress.load(tmp_path / "nothing-here.json")

        assert progress.last_zoid(backfill.PHASE_GENERATE) == 0
        assert progress.last_zoid(backfill.PHASE_REINDEX) == 0

    def test_a_corrupt_file_starts_at_zero(self, tmp_path):
        backfill = _backfill()
        path = tmp_path / "progress.json"
        path.write_text("{not json at all")

        progress = backfill.Progress.load(path)

        # Re-running a chunk is a no-op — the outcome record makes it one —
        # while trusting a truncated cursor would skip images silently.
        assert progress.last_zoid(backfill.PHASE_GENERATE) == 0

    def test_a_json_document_of_the_wrong_shape_starts_at_zero(self, tmp_path):
        backfill = _backfill()
        path = tmp_path / "progress.json"
        path.write_text('["generate", 12]')

        assert backfill.Progress.load(path).last_zoid(backfill.PHASE_GENERATE) == 0

    def test_junk_inside_a_phase_is_ignored(self, tmp_path):
        backfill = _backfill()
        path = tmp_path / "progress.json"
        path.write_text(json.dumps({"phases": {"generate": {"last_zoid": "soon"}}}))

        assert backfill.Progress.load(path).last_zoid(backfill.PHASE_GENERATE) == 0

    def test_an_unknown_phase_in_the_file_is_ignored(self, tmp_path):
        backfill = _backfill()
        path = tmp_path / "progress.json"
        path.write_text(json.dumps({"phases": {"polish": {"last_zoid": 5}}}))

        progress = backfill.Progress.load(path)

        assert progress.as_dict()["phases"].keys() == {
            backfill.PHASE_GENERATE,
            backfill.PHASE_REINDEX,
        }

    def test_the_file_is_replaced_atomically(self, tmp_path):
        backfill = _backfill()
        path = tmp_path / "progress.json"

        backfill.Progress(path).record_chunk(backfill.PHASE_GENERATE, last_zoid=1)

        # A half-written file after a SIGKILL would otherwise read as a
        # plausible-but-wrong cursor.
        assert json.loads(path.read_text())["phases"][backfill.PHASE_GENERATE]
        assert list(tmp_path.iterdir()) == [path]


class TestProgressPhases:
    """A chunk counts as done only once reindexed, so the phases are separate."""

    def test_recording_one_phase_leaves_the_other_alone(self, tmp_path):
        backfill = _backfill()
        progress = backfill.Progress(tmp_path / "progress.json")

        progress.record_chunk(backfill.PHASE_GENERATE, last_zoid=99)

        assert progress.last_zoid(backfill.PHASE_REINDEX) == 0

    def test_the_phases_advance_independently(self, tmp_path):
        backfill = _backfill()
        progress = backfill.Progress(tmp_path / "progress.json")

        progress.record_chunk(backfill.PHASE_GENERATE, last_zoid=99, objects=5)
        progress.record_chunk(backfill.PHASE_REINDEX, last_zoid=40, objects=2)

        assert progress.last_zoid(backfill.PHASE_GENERATE) == 99
        assert progress.last_zoid(backfill.PHASE_REINDEX) == 40
        assert progress.objects(backfill.PHASE_REINDEX) == 2

    def test_work_is_unfinished_while_the_reindex_trails(self, tmp_path):
        backfill = _backfill()
        progress = backfill.Progress(tmp_path / "progress.json")

        progress.record_chunk(backfill.PHASE_GENERATE, last_zoid=99)

        # Between the two phases nothing has improved for the affected
        # images: the catalog still holds a direct Thumbor URL pointing at
        # the original, which a browser fetches without Plone in the path.
        assert progress.reindex_pending is True

        progress.record_chunk(backfill.PHASE_REINDEX, last_zoid=99)

        assert progress.reindex_pending is False

    def test_the_keyset_cursor_never_moves_backwards(self, tmp_path):
        backfill = _backfill()
        progress = backfill.Progress(tmp_path / "progress.json")

        progress.record_chunk(backfill.PHASE_GENERATE, last_zoid=99)
        progress.record_chunk(backfill.PHASE_GENERATE, last_zoid=12)

        assert progress.last_zoid(backfill.PHASE_GENERATE) == 99
        assert progress.chunks(backfill.PHASE_GENERATE) == 2

    def test_an_unknown_phase_is_refused(self, tmp_path):
        backfill = _backfill()
        progress = backfill.Progress(tmp_path / "progress.json")

        with pytest.raises(ValueError):
            progress.record_chunk("polish", last_zoid=1)

    def test_stats_are_a_copy_not_the_live_state(self, tmp_path):
        backfill = _backfill()
        progress = backfill.Progress(tmp_path / "progress.json")

        progress.stats(backfill.PHASE_GENERATE)["last_zoid"] = 5000

        assert progress.last_zoid(backfill.PHASE_GENERATE) == 0


class TestRuntimeSettings:
    """What the run reads out of the environment before it starts."""

    def test_the_cap_comes_from_the_package_configuration(self, monkeypatch):
        from tests.conftest import env_override

        backfill = _backfill()
        env_override(
            monkeypatch,
            PGTHUMBOR_SERVER_URL="http://thumbor:8888",
            PGTHUMBOR_SECURITY_KEY="key",
            PGTHUMBOR_SOURCE_MAX_EDGE="5000",
        )

        assert backfill.resolve_max_edge() == 5000

    def test_no_configuration_refuses_to_run(self, monkeypatch):
        from tests.conftest import env_override

        backfill = _backfill()
        env_override(monkeypatch)

        with pytest.raises(RuntimeError):
            backfill.resolve_max_edge()

    def test_the_kill_switch_refuses_to_run(self, monkeypatch):
        from tests.conftest import env_override

        backfill = _backfill()
        env_override(
            monkeypatch,
            PGTHUMBOR_SERVER_URL="http://thumbor:8888",
            PGTHUMBOR_SECURITY_KEY="key",
            PGTHUMBOR_SOURCE_MAX_EDGE="0",
        )

        # At cap 0 ``set_source_derivative`` writes nothing at all — no
        # derivative and no outcome record — so every candidate would stay
        # a candidate and the run could never terminate.
        with pytest.raises(RuntimeError):
            backfill.resolve_max_edge()

    def test_the_chunk_size_defaults_and_can_be_overridden(self, monkeypatch):
        backfill = _backfill()

        monkeypatch.delenv("PGTHUMBOR_BACKFILL_CHUNK", raising=False)
        assert backfill.chunk_size() == backfill.DEFAULT_CHUNK_SIZE

        monkeypatch.setenv("PGTHUMBOR_BACKFILL_CHUNK", "10")
        assert backfill.chunk_size() == 10

    def test_a_nonsense_chunk_size_falls_back(self, monkeypatch):
        backfill = _backfill()

        monkeypatch.setenv("PGTHUMBOR_BACKFILL_CHUNK", "plenty")
        assert backfill.chunk_size() == backfill.DEFAULT_CHUNK_SIZE

        monkeypatch.setenv("PGTHUMBOR_BACKFILL_CHUNK", "0")
        assert backfill.chunk_size() == backfill.DEFAULT_CHUNK_SIZE

    def test_the_progress_file_can_be_moved_off_the_default(
        self, monkeypatch, tmp_path
    ):
        backfill = _backfill()

        monkeypatch.setenv("PGTHUMBOR_BACKFILL_PROGRESS", str(tmp_path / "p.json"))
        assert backfill.progress_path() == tmp_path / "p.json"

        monkeypatch.delenv("PGTHUMBOR_BACKFILL_PROGRESS")
        assert backfill.progress_path() == Path(backfill.DEFAULT_PROGRESS_PATH)

    def test_flags_read_the_environment_the_way_the_package_does(self, monkeypatch):
        backfill = _backfill()

        monkeypatch.setenv("PGTHUMBOR_BACKFILL_FORCE", "yes")
        assert backfill.env_flag("PGTHUMBOR_BACKFILL_FORCE") is True

        monkeypatch.setenv("PGTHUMBOR_BACKFILL_FORCE", "no")
        assert backfill.env_flag("PGTHUMBOR_BACKFILL_FORCE") is False

        monkeypatch.delenv("PGTHUMBOR_BACKFILL_FORCE")
        assert backfill.env_flag("PGTHUMBOR_BACKFILL_FORCE") is False


class TestPhaseOneLoadsFieldValuesDirectly:
    """The content object is never woken, on any path.

    A ``zconsole`` brain walk over the result set OOM-killed a production
    container during the original scan.  Phase 1 exists in its current
    shape because a ``NamedBlobImage`` is a row in ``object_state`` with
    its own zoid, so it can be fetched on its own — the owner, its
    annotations and its scales all stay ghosts.
    """

    def test_a_field_value_is_fetched_by_oid(self):
        from ZODB.utils import p64

        backfill = _backfill()
        connection = _FakeConnection()

        image = backfill.load_field_value(connection, 4711)

        assert image.zoid == 4711
        assert connection.loaded == [4711]
        # p64, not the raw int: ZODB oids are eight bytes.
        assert p64(4711) == p64(image.zoid)

    def test_the_run_fetches_nothing_but_its_candidates(self, monkeypatch, tmp_path):
        run = _generate(monkeypatch, tmp_path, chunks=[[3, 9], []])

        assert run.connection.loaded == [3, 9]

    def test_no_phase_one_function_can_reach_a_content_object(self):
        # Structural, not incidental: the OOM came from walking content
        # objects, so the names that would do it must not appear anywhere
        # phase 1 executes.  Read off the AST rather than the text, so a
        # comment explaining *why* phase 1 does not traverse is not itself
        # a violation.
        #
        # Scoped to the phase-1 functions rather than the module: phase 2
        # legitimately traverses, because image_scales is metadata of the
        # content object and reindexObject reaches its catalog through
        # acquisition. Widening this to the whole file again would forbid
        # the one place that has to.
        backfill = _backfill()
        tree = ast.parse(Path(backfill.__file__).read_text())
        phase_one = {
            "run_generate",
            "load_field_value",
            "candidate_query",
            "select_candidates",
            "work_list_cursor",
        }
        forbidden = {
            "unrestrictedTraverse",
            "getObject",
            "portal_catalog",
            "objectValues",
            "getPhysicalPath",
        }

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name not in phase_one:
                continue
            used = {
                child.attr
                for child in ast.walk(node)
                if isinstance(child, ast.Attribute)
            }
            used |= {
                child.id for child in ast.walk(node) if isinstance(child, ast.Name)
            }
            assert used.isdisjoint(forbidden), node.name

    def test_every_field_value_is_deactivated_again(self, monkeypatch, tmp_path):
        run = _generate(monkeypatch, tmp_path, chunks=[[3, 9], []])

        # Deactivation is a no-op on the ones just modified — they are
        # ghosted by the cache invalidation after the commit instead — but
        # it is the only thing that frees the untouched ones before then.
        assert [image.deactivated for image in run.connection.images.values()] == [1, 1]


class TestPhaseOneRunner:
    """Every candidate is processed, and the chunk commits before it counts."""

    def test_every_candidate_in_the_chunk_reaches_the_generator(
        self, monkeypatch, tmp_path
    ):
        run = _generate(monkeypatch, tmp_path, chunks=[[3, 9], []])

        assert [zoid for zoid, _, _ in run.generated] == [3, 9]

    def test_the_generator_is_the_one_the_subscriber_uses(self):
        from plone.pgthumbor.derivative import set_source_derivative

        backfill = _backfill()

        # Not a re-implementation of the trigger rules in the script: the
        # backfill and the subscriber have to make the same decision about
        # the same image, or the run never terminates.
        assert backfill.set_source_derivative is set_source_derivative

    def test_the_configured_cap_reaches_the_generator(self, monkeypatch, tmp_path):
        run = _generate(monkeypatch, tmp_path, chunks=[[3], []], max_edge=5000)

        assert run.generated == [(3, 5000, False)]

    def test_force_reaches_the_generator_too(self, monkeypatch, tmp_path):
        # Without this, ``force`` widens the SQL population and then the
        # generator skips every extra row it selected: a run that reads the
        # whole table and writes nothing.
        run = _generate(monkeypatch, tmp_path, chunks=[[3], []], force=True)

        assert run.generated == [(3, 4000, True)]

    def test_one_commit_per_chunk(self, monkeypatch, tmp_path):
        run = _generate(monkeypatch, tmp_path, chunks=[[3, 9], [12, 15], []], chunk=2)

        assert run.transaction.commits == 2

    def test_the_chunk_commits_before_the_cursor_advances(self, monkeypatch, tmp_path):
        backfill = _backfill()
        progress = backfill.Progress(tmp_path / "p.json")
        transaction_events = []
        original = progress.record_chunk

        def record(*args, **kwargs):
            transaction_events.append("record")
            return original(*args, **kwargs)

        monkeypatch.setattr(progress, "record_chunk", record)
        run = _generate(monkeypatch, tmp_path, chunks=[[3], []], progress=progress)
        run.transaction.events.extend(transaction_events)

        # The other order loses work: a pod killed between the two would
        # resume past objects whose derivatives were never committed, and
        # nothing would ever select them again.
        assert run.transaction.events == ["commit", "record"]

    def test_the_keyset_advances_to_the_last_zoid_of_the_chunk(
        self, monkeypatch, tmp_path
    ):
        run = _generate(monkeypatch, tmp_path, chunks=[[3, 9], [12, 15], []], chunk=2)

        assert [params["last_zoid"] for _, params in run.cursor.walk] == [0, 9, 15]
        assert run.progress.last_zoid(run.backfill.PHASE_GENERATE) == 15

    def test_it_resumes_from_the_persisted_cursor(self, monkeypatch, tmp_path):
        backfill = _backfill()
        progress = backfill.Progress(tmp_path / "p.json")
        progress.record_chunk(backfill.PHASE_GENERATE, last_zoid=400)

        run = _generate(monkeypatch, tmp_path, chunks=[[]], progress=progress)

        assert run.cursor.walk[0][1]["last_zoid"] == 400

    def test_it_stops_when_no_candidates_remain(self, monkeypatch, tmp_path):
        run = _generate(monkeypatch, tmp_path, chunks=[[3], []])

        # One walk for the chunk, one for the empty answer that ends it.
        assert len(run.cursor.walk) == 2
        assert run.stats["chunks"] == 1
        assert run.stats["objects"] == 1

    def test_an_empty_population_does_no_work_at_all(self, monkeypatch, tmp_path):
        run = _generate(monkeypatch, tmp_path, chunks=[[]])

        assert run.transaction.commits == 0
        assert run.progress.chunks(run.backfill.PHASE_GENERATE) == 0

    def test_the_counters_separate_what_was_written_from_what_was_not(
        self, monkeypatch, tmp_path
    ):
        def generator(image, max_edge=None, force=False):
            # False is the ordinary "no derivative needed" answer, and it
            # still leaves an outcome record behind.
            return image.zoid == 3

        run = _generate(monkeypatch, tmp_path, chunks=[[3, 9], []], generator=generator)

        assert run.stats["written"] == 1
        assert run.stats["objects"] == 2
        assert run.stats["failed"] == 0


class TestPhaseOneSurvivesOneBadObject:
    """One unreadable blob must not end a run of tens of thousands."""

    def test_a_failing_object_is_skipped_and_the_rest_proceed(
        self, monkeypatch, tmp_path
    ):
        def generator(image, max_edge=None, force=False):
            if image.zoid == 9:
                raise RuntimeError("this one is broken")
            return True

        run = _generate(
            monkeypatch, tmp_path, chunks=[[3, 9, 12], []], chunk=3, generator=generator
        )

        assert run.stats["failed"] == 1
        assert run.stats["written"] == 2

    def test_an_unloadable_oid_is_skipped_too(self, monkeypatch, tmp_path):
        connection = _FakeConnection(unreadable={9})

        run = _generate(
            monkeypatch,
            tmp_path,
            chunks=[[3, 9, 12], []],
            chunk=3,
            connection=connection,
        )

        # A POSKeyError from a missing blob record is the realistic case,
        # and it arrives from ``get`` rather than from the generator.
        assert run.stats["failed"] == 1
        assert [zoid for zoid, _, _ in run.generated] == [3, 12]

    def test_a_failing_object_still_advances_the_cursor(self, monkeypatch, tmp_path):
        def generator(image, max_edge=None, force=False):
            raise RuntimeError("all of them are broken")

        run = _generate(monkeypatch, tmp_path, chunks=[[3, 9], []], generator=generator)

        # Otherwise the run never terminates: the same chunk is selected,
        # fails, and is selected again for as long as the pod lives.
        assert run.progress.last_zoid(run.backfill.PHASE_GENERATE) == 9
        assert run.transaction.commits == 1


class TestPhaseOneReleasesMemory:
    """Ghosts accumulate; a chunk boundary is where they get dropped."""

    def test_the_zodb_cache_is_invalidated_after_every_chunk(
        self, monkeypatch, tmp_path
    ):
        run = _generate(monkeypatch, tmp_path, chunks=[[3], [9], []])

        # cacheMinimize() only ghosts objects and keeps them in the cache
        # dict; ~200 bytes per ghost is what OOM-killed the purge run.
        assert len(run.connection._cache.invalidated) == 2

    def test_freed_memory_is_returned_to_the_os_after_every_chunk(
        self, monkeypatch, tmp_path
    ):
        backfill = _backfill()
        calls = []
        monkeypatch.setattr(backfill, "_release_memory", lambda: calls.append(1))

        _generate(monkeypatch, tmp_path, chunks=[[3], [9], []])

        # Without malloc_trim the arena keeps the freed blocks and RSS
        # grows until the pod is killed, garbage collection notwithstanding.
        assert len(calls) == 2

    def test_the_memory_helpers_are_the_ones_from_the_purge_script(self):
        import inspect

        backfill = _backfill()
        purge_source = (
            Path(backfill.__file__).parent / "purge_legacy_scales.py"
        ).read_text()

        assert inspect.getsource(backfill._invalidate_cache) in purge_source
        assert "malloc_trim" in Path(backfill.__file__).read_text()


class TestDryRunWritesNothing:
    """A dry run is the measurement that precedes the decision."""

    def test_it_never_calls_the_generator(self, monkeypatch, tmp_path):
        backfill = _backfill()
        transaction = _FakeTransaction()
        monkeypatch.setattr(backfill, "transaction", transaction)
        calls = []
        monkeypatch.setattr(
            backfill,
            "set_source_derivative",
            lambda *args, **kwargs: calls.append(args),
        )
        cursor = _RunnerCursor(
            chunks=[[]], summary={"candidates": 0, "without_modified": 0}
        )

        backfill.dry_run_report(cursor, _FakeConnection(), max_edge=4000)

        assert calls == []

    def test_it_commits_nothing_and_abandons_what_it_read(self, monkeypatch, tmp_path):
        from tests.conftest import big_jpeg_bytes

        backfill = _backfill()
        transaction = _FakeTransaction()
        monkeypatch.setattr(backfill, "transaction", transaction)
        cursor = _RunnerCursor(chunks=[[3]])

        backfill.dry_run_report(
            cursor, _FakeConnection(payloads={3: big_jpeg_bytes()}), max_edge=1000
        )

        # Reading a blob joins the transaction; aborting keeps the "writes
        # nothing" promise true even if something upstream marked an object.
        assert transaction.commits == 0
        assert transaction.aborts == 1

    def test_the_progress_file_is_not_touched(self, monkeypatch, tmp_path):
        backfill = _backfill()
        monkeypatch.setattr(backfill, "transaction", _FakeTransaction())
        progress_path = tmp_path / "p.json"
        portal = SimpleNamespace(_p_jar=_FakeConnection())

        backfill.run(
            portal,
            max_edge=4000,
            progress=backfill.Progress(progress_path),
            chunk=10,
            dry_run=True,
            cursor=_RunnerCursor(chunks=[[]]),
        )

        assert not progress_path.exists()


class TestDryRunNumbers:
    """The four numbers a deployment picks its cap from."""

    def test_it_reports_the_candidate_count(self, monkeypatch, tmp_path):
        backfill = _backfill()
        monkeypatch.setattr(backfill, "transaction", _FakeTransaction())
        cursor = _RunnerCursor(
            chunks=[[]], summary={"candidates": 263, "without_modified": 0}
        )

        report = backfill.dry_run_report(cursor, _FakeConnection(), max_edge=4000)

        assert report["candidates"] == 263

    def test_the_count_uses_the_candidate_predicate(self, monkeypatch, tmp_path):
        from plone.pgthumbor.derivative import IS_DERIVATIVE_ATTRIBUTE

        backfill = _backfill()
        monkeypatch.setattr(backfill, "transaction", _FakeTransaction())
        cursor = _RunnerCursor(chunks=[[]])

        backfill.dry_run_report(cursor, _FakeConnection(), max_edge=4000)
        summary = next(
            " ".join(sql.split()) for sql, _ in cursor.calls if "count(*)" in sql
        )

        # A count over a different population than the run walks is worse
        # than no count: it is a number that looks like an estimate.
        assert f"NOT (state ? '{IS_DERIVATIVE_ATTRIBUTE}')" in summary
        assert "_pgthumbor_source_info" in summary
        assert "LIMIT" not in summary

    def test_it_reports_the_median_encoded_derivative_size(self, monkeypatch, tmp_path):
        from plone.pgthumbor.derivative import build_derivative_bytes
        from tests.conftest import big_jpeg_bytes

        import io

        backfill = _backfill()
        monkeypatch.setattr(backfill, "transaction", _FakeTransaction())
        payloads = {
            3: big_jpeg_bytes(size=(2400, 1800)),
            9: big_jpeg_bytes(size=(1600, 1200)),
            12: big_jpeg_bytes(size=(2000, 1500)),
        }
        expected = statistics.median(
            len(build_derivative_bytes(io.BytesIO(data), 1000)[0])
            for data in payloads.values()
        )
        cursor = _RunnerCursor(chunks=[[3, 9, 12]])

        report = backfill.dry_run_report(
            cursor, _FakeConnection(payloads=payloads), max_edge=1000
        )

        # Real bytes through the real encoder: the number an operator reads
        # off this line is a storage estimate, and a mocked one would be a
        # storage estimate of the mock.
        assert report["median_bytes"] == int(expected)
        assert report["sampled"] == 3

    def test_images_needing_no_derivative_stay_out_of_the_median(
        self, monkeypatch, tmp_path
    ):
        from tests.conftest import jpeg_bytes

        backfill = _backfill()
        monkeypatch.setattr(backfill, "transaction", _FakeTransaction())
        cursor = _RunnerCursor(chunks=[[3]])

        report = backfill.dry_run_report(
            cursor, _FakeConnection(payloads={3: jpeg_bytes()}), max_edge=4000
        )

        assert report["sampled"] == 0
        assert report["median_bytes"] is None

    def test_an_undecodable_sample_does_not_end_the_dry_run(
        self, monkeypatch, tmp_path
    ):
        from tests.conftest import big_jpeg_bytes
        from tests.conftest import CORRUPT_BYTES

        backfill = _backfill()
        monkeypatch.setattr(backfill, "transaction", _FakeTransaction())
        cursor = _RunnerCursor(chunks=[[3, 9]])
        payloads = {3: CORRUPT_BYTES, 9: big_jpeg_bytes()}

        report = backfill.dry_run_report(
            cursor, _FakeConnection(payloads=payloads), max_edge=1000
        )

        assert report["sampled"] == 1

    def test_it_counts_the_field_values_with_no_modified_attribute(
        self, monkeypatch, tmp_path
    ):
        backfill = _backfill()
        monkeypatch.setattr(backfill, "transaction", _FakeTransaction())
        cursor = _RunnerCursor(
            chunks=[[]], summary={"candidates": 263, "without_modified": 41}
        )

        report = backfill.dry_run_report(cursor, _FakeConnection(), max_edge=4000)

        # This is the cache-invalidation blast radius.  On those, writing
        # the derivative moves _p_mtime, and every scale uid for the image
        # moves with it — hash_key folds modified_time, and
        # ModifiedPropertyMixin.modified falls back to _p_mtime.
        assert report["without_modified"] == 41

    def test_the_attribute_counted_is_the_one_namedfile_writes(
        self, monkeypatch, tmp_path
    ):
        from plone.namedfile.file import NamedBlobImage
        from tests.conftest import jpeg_bytes
        from tests.conftest import namedfile_storables

        backfill = _backfill()
        monkeypatch.setattr(backfill, "transaction", _FakeTransaction())
        cursor = _RunnerCursor(chunks=[[]])
        backfill.dry_run_report(cursor, _FakeConnection(), max_edge=4000)
        summary = next(
            " ".join(sql.split()) for sql, _ in cursor.calls if "count(*)" in sql
        )

        with namedfile_storables():
            image = NamedBlobImage(
                data=jpeg_bytes(), filename="t.jpg", contentType="image/jpeg"
            )

            # _setData writes it, so anything uploaded through the ordinary
            # path is stable; the ones without it are the legacy population
            # the number exists to size.
            assert hasattr(image, backfill.MODIFIED_ATTRIBUTE)
        assert f"NOT (state ? '{backfill.MODIFIED_ATTRIBUTE}')" in summary


class TestCropHistogram:
    """Which scale names actually carry crops — the binding S when choosing a cap."""

    def test_it_counts_scale_names_behind_the_annotation_key(self):
        backfill = _backfill()
        cursor = _RunnerCursor(
            annotations=[
                {"crops": {"@ref": ["17db849812c6bd21", "persistent.mapping"]}}
            ],
            storages=[
                {
                    "state": {
                        "data": {
                            "image_albumfull": {"@t": [1, 2, 3, 4]},
                            "image_preview": {"@t": [0, 0, 9, 9]},
                        }
                    }
                }
            ],
        )

        assert backfill.crop_histogram(cursor) == {"albumfull": 1, "preview": 1}

    def test_the_annotation_key_is_the_one_the_crop_provider_reads(self):
        from plone.pgthumbor.addons_compat.imagecropping import ANNOTATION_KEY

        backfill = _backfill()
        cursor = _RunnerCursor()

        backfill.crop_histogram(cursor)

        assert cursor.calls[0][1]["annotation_key"] == ANNOTATION_KEY

    def test_the_reference_is_resolved_as_a_hex_zoid(self):
        backfill = _backfill()
        cursor = _RunnerCursor(
            annotations=[{"crops": {"@ref": ["17db849812c6bd21", "x"]}}],
            storages=[{"state": {"data": {"image_teaser": {}}}}],
        )

        backfill.crop_histogram(cursor)

        assert cursor.calls[1][1]["zoids"] == [0x17DB849812C6BD21]

    def test_crops_stored_inline_are_counted_without_a_second_query(self):
        backfill = _backfill()
        cursor = _RunnerCursor(annotations=[{"crops": {"lead_image_teaser": {}}}])

        # A plain dict under the annotation key has no @ref to follow, and
        # the keys are right there in the container's own state.
        assert backfill.crop_histogram(cursor) == {"teaser": 1}
        assert len(cursor.calls) == 1

    def test_a_scale_name_containing_an_underscore_is_not_split_in_half(self):
        backfill = _backfill()
        cursor = _RunnerCursor(annotations=[{"crops": {"image_banner_large": {}}}])

        # "{fieldname}_{scalename}" is ambiguous on its own; the registered
        # names resolve it.  Without them the last segment is the only
        # defensible guess.
        assert backfill.crop_histogram(
            cursor, scale_names=("banner_large", "large")
        ) == {"banner_large": 1}
        assert backfill.crop_histogram(cursor) == {"large": 1}

    def test_no_crops_anywhere_is_an_empty_histogram(self):
        backfill = _backfill()
        cursor = _RunnerCursor()

        assert backfill.crop_histogram(cursor) == {}
        # And no pointless second query for an empty set of references.
        assert len(cursor.calls) == 1

    def test_a_query_that_cannot_run_is_an_empty_histogram_not_a_crash(self):
        backfill = _backfill()

        # plone.app.imagecropping absent, an older schema, a state shape
        # this does not know: the dry run reports the other three numbers
        # rather than dying on the optional one.
        assert backfill.crop_histogram(_RaisingCursor()) == {}

    def test_the_array_handed_to_the_lateral_is_always_an_array(self):
        backfill = _backfill()
        cursor = _RunnerCursor()

        backfill.crop_histogram(cursor)
        sql = " ".join(cursor.calls[0][0].split())

        # A LATERAL function in the FROM list runs before WHERE, so a
        # typeof guard sitting in WHERE would not stop the first row whose
        # ``@kv`` is not an array from raising.
        assert "CASE WHEN jsonb_typeof(state -> '@kv') = 'array'" in sql
        assert "ELSE '[]'::jsonb" in sql

    def test_an_ordinary_run_never_pays_for_the_scan(self, monkeypatch, tmp_path):
        run = _generate(monkeypatch, tmp_path, chunks=[[3], []])

        # There is no index for the crop query — it is a sequential scan
        # over object_state, affordable once in a dry run and nowhere else.
        assert not [
            call for call in run.cursor.calls if "jsonb_array_elements" in call[0]
        ]

    def test_the_histogram_reaches_the_report(self, monkeypatch, tmp_path):
        backfill = _backfill()
        monkeypatch.setattr(backfill, "transaction", _FakeTransaction())
        cursor = _RunnerCursor(
            chunks=[[]], annotations=[{"crops": {"image_albumfull": {}}}]
        )

        report = backfill.dry_run_report(cursor, _FakeConnection(), max_edge=4000)

        assert report["crops"] == {"albumfull": 1}


_NO_REINDEX = {"objects": 0, "chunks": 0, "reindexed": 0, "unowned": 0, "failed": 0}
_VERIFIED = {"remaining": 0, "unowned": 0, "stale_scales": 0, "verified": True}


class TestRunDispatch:
    """``run`` drives both phases and then reports whether it believes itself."""

    def test_a_dry_run_never_enters_phase_one(self, monkeypatch, tmp_path):
        backfill = _backfill()
        transaction = _FakeTransaction()
        monkeypatch.setattr(backfill, "transaction", transaction)
        entered = []
        monkeypatch.setattr(
            backfill, "run_generate", lambda *a, **kw: entered.append(1)
        )

        backfill.run(
            SimpleNamespace(_p_jar=_FakeConnection()),
            max_edge=4000,
            progress=backfill.Progress(tmp_path / "p.json"),
            chunk=10,
            dry_run=True,
            cursor=_RunnerCursor(chunks=[[]]),
        )

        assert entered == []

    def test_an_ordinary_run_walks_the_population(self, monkeypatch, tmp_path):
        backfill = _backfill()
        monkeypatch.setattr(backfill, "transaction", _FakeTransaction())
        monkeypatch.setattr(backfill, "set_source_derivative", lambda *a, **kw: True)
        progress = backfill.Progress(tmp_path / "p.json")

        monkeypatch.setattr(backfill, "run_reindex", lambda *a, **kw: _NO_REINDEX)
        monkeypatch.setattr(backfill, "verify", lambda *a, **kw: _VERIFIED)
        backfill.run(
            SimpleNamespace(_p_jar=_FakeConnection()),
            max_edge=4000,
            progress=progress,
            chunk=10,
            cursor=_RunnerCursor(chunks=[[3, 9], []]),
        )

        assert progress.last_zoid(backfill.PHASE_GENERATE) == 9

    def test_phase_one_alone_is_never_reported_as_finished(
        self, monkeypatch, tmp_path, capsys
    ):
        """Writing the derivatives is half the job.

        Between the phases the catalog still holds direct, signed Thumbor
        URLs pointing at the originals, and a browser fetches those without
        Plone in the path.  Claiming success there would be a lie, so a run
        whose phase 2 found nothing to do must still fail verification if
        candidates remain.
        """
        backfill = _backfill()
        monkeypatch.setattr(backfill, "transaction", _FakeTransaction())
        monkeypatch.setattr(backfill, "set_source_derivative", lambda *a, **kw: True)
        monkeypatch.setattr(backfill, "run_reindex", lambda *a, **kw: _NO_REINDEX)
        monkeypatch.setattr(
            backfill,
            "verify",
            lambda *a, **kw: {
                "remaining": 4,
                "unowned": 0,
                "stale_scales": 0,
                "verified": False,
            },
        )
        progress = backfill.Progress(tmp_path / "p.json")

        result = backfill.run(
            SimpleNamespace(_p_jar=_FakeConnection()),
            max_edge=4000,
            progress=progress,
            chunk=10,
            cursor=_RunnerCursor(chunks=[[3], []]),
        )

        assert result["verification"]["verified"] is False
        assert "NOT VERIFIED" in capsys.readouterr().out

    def test_a_run_reports_both_phases_and_the_verdict(self, monkeypatch, tmp_path):
        backfill = _backfill()
        monkeypatch.setattr(backfill, "transaction", _FakeTransaction())
        monkeypatch.setattr(backfill, "set_source_derivative", lambda *a, **kw: True)
        monkeypatch.setattr(backfill, "run_reindex", lambda *a, **kw: _NO_REINDEX)
        monkeypatch.setattr(backfill, "verify", lambda *a, **kw: _VERIFIED)

        result = backfill.run(
            SimpleNamespace(_p_jar=_FakeConnection()),
            max_edge=4000,
            progress=backfill.Progress(tmp_path / "p.json"),
            chunk=10,
            cursor=_RunnerCursor(chunks=[[3], []]),
        )

        assert set(result) == {"generate", "reindex", "verification"}


class TestWorkListCursorLifecycle:
    """The work-list connection is borrowed from the pool, and given back."""

    def _pool(self, monkeypatch):
        from unittest.mock import MagicMock

        backfill = _backfill()
        connection = MagicMock()
        connection.autocommit = False
        pool = MagicMock()
        pool.getconn.return_value = connection
        monkeypatch.setitem(
            __import__("sys").modules,
            "plone.pgcatalog.pool",
            MagicMock(get_pool=lambda portal: pool),
        )
        return backfill, pool, connection

    def test_the_connection_goes_back_to_the_pool(self, monkeypatch):
        backfill, pool, connection = self._pool(monkeypatch)

        with backfill.work_list_cursor(object()):
            assert pool.putconn.call_count == 0

        pool.putconn.assert_called_once_with(connection)

    def test_autocommit_is_reset_before_it_goes_back(self, monkeypatch):
        backfill, pool, connection = self._pool(monkeypatch)
        seen = []
        pool.putconn.side_effect = lambda conn: seen.append(conn.autocommit)

        with backfill.work_list_cursor(object()) as cursor:
            assert cursor is not None
            assert connection.autocommit is True

        # psycopg_pool rolls back an open transaction on putconn but does
        # not restore autocommit, so a connection handed back as-is would
        # leave the next borrower in autocommit without knowing it.
        assert seen == [False]

    def test_it_goes_back_even_when_the_walk_raises(self, monkeypatch):
        backfill, pool, connection = self._pool(monkeypatch)

        with pytest.raises(RuntimeError), backfill.work_list_cursor(object()):
            raise RuntimeError("chunk exploded")

        pool.putconn.assert_called_once_with(connection)


class _ReindexCursor:
    """Answers the phase-2 walk and the owner lookup, dispatching on SQL."""

    def __init__(self, chunks=(), owners=(), counts=None):
        self.chunks = [list(chunk) for chunk in chunks]
        self.owners = [dict(row) for row in owners]
        self.counts = dict(counts or {})
        self.calls = []
        self._rows = []

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))
        text = " ".join(sql.split())
        if "count(*) AS unowned" in text:
            self._rows = [{"unowned": self.counts.get("unowned", 0)}]
        elif "count(*) AS stale" in text:
            self._rows = [{"stale": self.counts.get("stale", 0)}]
        elif "count(*)" in text:
            summary = {"candidates": 0, "without_modified": 0}
            summary.update(self.counts.get("summary", {}))
            self._rows = [summary]
        elif "refs &&" in text:
            self._rows = list(self.owners)
        else:
            chunk = self.chunks.pop(0) if self.chunks else []
            self._rows = [{"zoid": zoid} for zoid in chunk]
        return self

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Reindexable:
    def __init__(self, path):
        self.path = path
        self.calls = []
        self.deactivated = 0

    def reindexObject(self, idxs=None):
        self.calls.append(idxs)

    def _p_deactivate(self):
        self.deactivated += 1


class _Portal:
    def __init__(self, objects=None, explode=()):
        self.objects = objects or {}
        self.explode = set(explode)
        self._p_jar = object()

    def unrestrictedTraverse(self, path):
        if path in self.explode:
            raise KeyError(path)
        return self.objects.setdefault(path, _Reindexable(path))


def _reindex(monkeypatch, tmp_path, chunks, owners=(), explode=(), portal=None):
    backfill = _backfill()
    monkeypatch.setattr(backfill, "transaction", _FakeTransaction())
    monkeypatch.setattr(backfill, "require_thumbor_request", lambda: None)
    monkeypatch.setattr(backfill, "_invalidate_cache", lambda conn: None)
    monkeypatch.setattr(backfill, "_release_memory", lambda: None)
    cursor = _ReindexCursor(chunks=chunks, owners=owners)
    portal = portal if portal is not None else _Portal(explode=explode)
    progress = backfill.Progress(tmp_path / "p.json")
    stats = backfill.run_reindex(portal, cursor, progress, chunk=2)
    return SimpleNamespace(
        backfill=backfill,
        cursor=cursor,
        portal=portal,
        progress=progress,
        stats=stats,
    )


class TestPhaseTwoRequiresARequest:
    """The gate. Nothing may be written before the context is proven."""

    def test_it_refuses_without_one(self, monkeypatch, tmp_path):
        from plone.pgthumbor.zconsole import RequestContextError

        backfill = _backfill()
        cursor = _ReindexCursor(chunks=[[1]])

        def refuse():
            raise RequestContextError("no request")

        monkeypatch.setattr(backfill, "require_thumbor_request", refuse)

        with pytest.raises(RequestContextError):
            backfill.run_reindex(
                _Portal(), cursor, backfill.Progress(tmp_path / "p.json"), chunk=2
            )

        # Not one query issued: a reindex without a request overwrites
        # image_scales with null for everything it touches, so the check
        # has to come before the walk rather than inside it.
        assert cursor.calls == []


class TestPhaseTwoReindex:
    """Walking the derivative-bearing field values and reindexing owners."""

    def test_it_reindexes_only_image_scales(self, monkeypatch, tmp_path):
        run = _reindex(
            monkeypatch,
            tmp_path,
            chunks=[[3, 9], []],
            owners=[{"zoid": 1, "path": "/s/a"}],
        )

        # An empty idxs calls notifyModified() and would bump the
        # modification date of every object touched, breaking
        # recently-modified listings and every cache key downstream.
        assert run.portal.objects["/s/a"].calls == [["image_scales"]]

    def test_one_owner_of_two_image_fields_is_reindexed_once(
        self, monkeypatch, tmp_path
    ):
        run = _reindex(
            monkeypatch,
            tmp_path,
            chunks=[[3, 9], []],
            owners=[{"zoid": 1, "path": "/s/a"}, {"zoid": 1, "path": "/s/a"}],
        )

        assert len(run.portal.objects["/s/a"].calls) == 1

    def test_a_field_value_with_no_catalogued_owner_is_counted(
        self, monkeypatch, tmp_path
    ):
        run = _reindex(monkeypatch, tmp_path, chunks=[[3, 9], []], owners=[])

        # An annotation-nested behaviour, or an object deleted between the
        # phases. Nothing can reindex it, so it is reported rather than
        # silently counted as done.
        assert run.stats["unowned"] == 2
        assert run.stats["reindexed"] == 0

    def test_a_failing_object_is_skipped_not_fatal(self, monkeypatch, tmp_path):
        run = _reindex(
            monkeypatch,
            tmp_path,
            chunks=[[3], []],
            owners=[{"zoid": 1, "path": "/s/gone"}],
            explode=["/s/gone"],
        )

        assert run.stats["failed"] == 1

    def test_progress_advances_per_chunk(self, monkeypatch, tmp_path):
        run = _reindex(
            monkeypatch,
            tmp_path,
            chunks=[[3, 9], []],
            owners=[{"zoid": 1, "path": "/s/a"}],
        )

        assert run.progress.last_zoid(run.backfill.PHASE_REINDEX) == 9

    def test_the_walk_uses_its_own_phase_cursor(self, monkeypatch, tmp_path):
        run = _reindex(
            monkeypatch,
            tmp_path,
            chunks=[[3, 9], []],
            owners=[{"zoid": 1, "path": "/s/a"}],
        )
        walks = [call for call in run.cursor.calls if "ORDER BY zoid" in call[0]]

        # Two passes over the same ordered population, each with its own
        # resume point — that is what makes a chunk "done only once
        # reindexed" rather than "done once written".
        assert walks[0][1]["last_zoid"] == 0
        assert walks[1][1]["last_zoid"] == 9

    def test_it_selects_only_generated_outcomes(self, monkeypatch, tmp_path):
        from plone.pgthumbor.derivative import REASON_GENERATED

        run = _reindex(monkeypatch, tmp_path, chunks=[[], []])
        walk = next(call for call in run.cursor.calls if "ORDER BY zoid" in call[0])

        assert walk[1]["generated_reason"] == REASON_GENERATED


class TestVerification:
    """Three counts that must all be zero before a run counts as finished."""

    def _verify(self, **counts):
        backfill = _backfill()
        cursor = _ReindexCursor(counts=counts)
        return backfill.verify(cursor, 4000)

    def test_a_clean_run_verifies(self):
        report = self._verify(summary={"candidates": 0}, unowned=0, stale=0)

        assert report["verified"] is True

    def test_remaining_candidates_fail_it(self):
        report = self._verify(summary={"candidates": 7}, unowned=0, stale=0)

        assert report["verified"] is False
        assert report["remaining"] == 7

    def test_an_owner_without_scales_fails_it(self):
        report = self._verify(summary={"candidates": 0}, unowned=0, stale=3)

        # Both "phase 2 never reached it" and "something nulled the
        # column" land here, and the second is the failure a request-less
        # reindex causes.
        assert report["verified"] is False
        assert report["stale_scales"] == 3

    def test_a_derivative_with_no_owner_fails_it(self):
        report = self._verify(summary={"candidates": 0}, unowned=2, stale=0)

        assert report["verified"] is False
        assert report["unowned"] == 2
