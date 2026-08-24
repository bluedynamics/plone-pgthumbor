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

import ast
import functools
import importlib.util
import json
import pytest


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
