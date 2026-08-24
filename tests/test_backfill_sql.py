"""The backfill's SQL, executed against a real PostgreSQL.

Everything else in this suite runs against fakes, which is right for logic
but proves nothing about SQL: a fake cursor records the string it was
handed and answers whatever the test wants.  It cannot tell you that a
predicate parses, that a ``?`` operator survives psycopg's placeholder
scanner, that a LATERAL argument is evaluated where you think, or that
``refs &&`` finds the row you meant.

So this module runs the real queries against the real schema —
``zodb_pgjsonb.schema.install_schema`` plus ``plone.pgcatalog``'s column
additions, which is exactly the shape production has — over rows shaped
like the ones the package writes.

A throwaway container per session, via testcontainers.  Deliberately not
"read a DSN from the environment and skip when it is missing": that is how
SQL ends up shipping untested while the suite reports green.  When Docker
genuinely is not reachable the skip message says what to do about it.
"""

from __future__ import annotations

import json
import os
import pytest


pytestmark = pytest.mark.sql

CLASS_MOD = "plone.namedfile.file"
CLASS_NAME = "NamedBlobImage"


def _backfill():
    """Import the script the way the other backfill tests do."""
    import importlib.util
    import pathlib

    path = (
        pathlib.Path(__file__).parent.parent / "scripts" / "backfill_thumbor_sources.py"
    )
    spec = importlib.util.spec_from_file_location("backfill_thumbor_sources", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def pg_connection():
    """A PostgreSQL with the production schema, thrown away afterwards."""
    docker = pytest.importorskip(
        "testcontainers.community.postgres",
        reason="testcontainers is not installed",
    )
    from psycopg.rows import dict_row

    import psycopg

    # docker-py reads currentContext out of ~/.docker/config.json, so a
    # developer whose context points at a remote host over ssh gets an
    # unhelpful paramiko error rather than a container.  Prefer the local
    # socket unless the environment says otherwise.
    if not os.environ.get("DOCKER_HOST") and os.path.exists("/var/run/docker.sock"):
        os.environ["DOCKER_HOST"] = "unix:///var/run/docker.sock"

    try:
        container = docker.PostgresContainer("postgres:17-alpine", driver=None)
        container.start()
    except Exception as error:  # pragma: no cover - environment dependent
        pytest.skip(
            "Could not start a PostgreSQL container: "
            f"{type(error).__name__}: {error}. "
            "Docker must be reachable; if your docker context points at a "
            "remote host, set DOCKER_HOST=unix:///var/run/docker.sock."
        )

    try:
        with psycopg.connect(
            container.get_connection_url(), row_factory=dict_row
        ) as connection:
            from plone.pgcatalog.schema import CATALOG_COLUMNS
            from zodb_pgjsonb.schema import install_schema

            install_schema(connection)
            connection.execute(CATALOG_COLUMNS)
            connection.execute(
                "INSERT INTO transaction_log (tid) VALUES (1) ON CONFLICT DO NOTHING"
            )
            connection.commit()
            yield connection
    finally:
        container.stop()


@pytest.fixture
def db(pg_connection):
    """A clean object_state and a real cursor for each test.

    A cursor, not the connection: every function under test takes one and
    calls ``execute`` then ``fetchall`` on the same object, which is what
    psycopg's connection-level ``execute`` does *not* give you.
    """
    with pg_connection.cursor() as cursor:
        cursor.execute("TRUNCATE object_state")
        pg_connection.commit()
        yield cursor
    pg_connection.rollback()


def _insert(
    cursor, zoid, state, *, class_name=CLASS_NAME, refs=(), path=None, idx=None
):
    cursor.execute(
        """
        INSERT INTO object_state
            (zoid, tid, class_mod, class_name, state, state_size, refs, path, idx)
        VALUES
            (%(zoid)s, 1, %(mod)s, %(name)s, %(state)s, 0, %(refs)s, %(path)s, %(idx)s)
        """,
        {
            "zoid": zoid,
            "mod": CLASS_MOD,
            "name": class_name,
            "state": json.dumps(state),
            "refs": list(refs),
            "path": path,
            "idx": json.dumps(idx) if idx is not None else None,
        },
    )


def _image(width=11811, height=8858, reason=None, max_edge=None, derivative=False):
    """A state dict shaped the way a stored NamedBlobImage looks."""
    from plone.pgthumbor.derivative import INFO_ATTRIBUTE
    from plone.pgthumbor.derivative import IS_DERIVATIVE_ATTRIBUTE

    state = {"filename": "x.jpg", "_width": width, "_height": height, "_modified": 1}
    if reason is not None:
        state[INFO_ATTRIBUTE] = {
            "reason": reason,
            "max_edge": max_edge,
            "source_ids": None,
        }
    if derivative:
        state[IS_DERIVATIVE_ATTRIBUTE] = True
    return state


def _candidates(db, backfill, **kwargs):
    sql, params = backfill.candidate_query(
        kwargs.pop("max_edge", 4000),
        kwargs.pop("last_zoid", 0),
        kwargs.pop("chunk", 100),
        **kwargs,
    )
    db.execute(sql, params)
    return [row["zoid"] for row in db.fetchall()]


class TestCandidatePredicateAgainstPostgres:
    """The predicate the whole backfill population comes from."""

    def test_a_never_examined_image_is_a_candidate(self, db):
        backfill = _backfill()
        _insert(db, 10, _image())

        assert _candidates(db, backfill) == [10]

    def test_a_terminal_outcome_at_the_same_cap_is_excluded(self, db):
        backfill = _backfill()
        _insert(db, 10, _image(reason="generated", max_edge=4000))

        assert _candidates(db, backfill) == []

    def test_a_terminal_outcome_at_a_different_cap_is_a_candidate(self, db):
        backfill = _backfill()
        _insert(db, 10, _image(reason="generated", max_edge=1000))

        # This is what makes tuning the cap a setting change: an ordinary
        # run picks the image up again, with no force flag to remember.
        assert _candidates(db, backfill) == [10]

    def test_a_retry_outcome_is_still_a_candidate(self, db):
        backfill = _backfill()
        _insert(db, 10, _image(reason="retry", max_edge=4000))

        assert _candidates(db, backfill) == [10]

    def test_an_error_outcome_is_still_a_candidate(self, db):
        backfill = _backfill()
        _insert(db, 10, _image(reason="error", max_edge=4000))

        assert _candidates(db, backfill) == [10]

    def test_a_derivative_is_never_a_candidate(self, db):
        backfill = _backfill()
        _insert(db, 10, _image(derivative=True))

        # It is a NamedBlobImage row like any other and carries no outcome
        # record, so without the marker it would look brand new on every
        # run — and phase 2 has no content object to reindex for it.
        assert _candidates(db, backfill) == []

    def test_force_ignores_every_recorded_outcome(self, db):
        backfill = _backfill()
        _insert(db, 10, _image(reason="generated", max_edge=4000))

        assert _candidates(db, backfill, force=True) == [10]

    def test_another_class_is_not_a_candidate(self, db):
        backfill = _backfill()
        _insert(db, 10, _image(), class_name="NamedBlobFile")

        assert _candidates(db, backfill) == []

    def test_keyset_pagination_resumes_past_the_cursor(self, db):
        backfill = _backfill()
        for zoid in (10, 20, 30):
            _insert(db, zoid, _image())

        assert _candidates(db, backfill, last_zoid=10) == [20, 30]
        assert _candidates(db, backfill, last_zoid=0, chunk=2) == [10, 20]

    def test_size_only_adds_the_dimension_predicate(self, db):
        backfill = _backfill()
        _insert(db, 10, _image(width=11811, height=8858))
        _insert(db, 20, _image(width=800, height=600))

        assert _candidates(db, backfill, size_only=True) == [10]

    def test_size_only_survives_a_non_numeric_dimension(self, db):
        backfill = _backfill()
        # A cast would raise here, and PostgreSQL gives no evaluation-order
        # guarantee that a guarding AND runs first — hence jsonb_typeof.
        _insert(db, 10, {"filename": "x.jpg", "_width": "wide", "_height": None})

        assert _candidates(db, backfill, size_only=True) == []


class TestCandidateSummaryAgainstPostgres:
    """The counts the dry run reports."""

    def test_it_counts_the_same_population_the_walk_returns(self, db):
        backfill = _backfill()
        for zoid in (10, 20):
            _insert(db, zoid, _image())
        _insert(db, 30, _image(reason="generated", max_edge=4000))

        summary = backfill.candidate_summary(db, 4000)

        assert summary["candidates"] == len(_candidates(db, backfill)) == 2

    def test_it_counts_candidates_without_modified(self, db):
        backfill = _backfill()
        _insert(db, 10, _image())
        without = _image()
        del without["_modified"]
        _insert(db, 20, without)

        summary = backfill.candidate_summary(db, 4000)

        # For those, writing a derivative moves _p_mtime and with it every
        # scale uid for that image; the count sizes the blast radius.
        assert summary["without_modified"] == 1


class TestPhaseTwoQueriesAgainstPostgres:
    """Finding what to reindex, and what to reindex it as."""

    def test_only_generated_field_values_are_walked(self, db):
        backfill = _backfill()
        _insert(db, 10, _image(reason="generated", max_edge=4000))
        _insert(db, 20, _image(reason="not_needed", max_edge=4000))
        _insert(db, 30, _image(reason="error", max_edge=4000))

        assert backfill.select_generated(db, 0, 100) == [10]

    def test_a_derivative_row_is_not_walked(self, db):
        backfill = _backfill()
        _insert(db, 10, _image(reason="generated", max_edge=4000, derivative=True))

        assert backfill.select_generated(db, 0, 100) == []

    def test_the_owner_is_found_through_refs(self, db):
        backfill = _backfill()
        _insert(db, 10, _image(reason="generated", max_edge=4000))
        _insert(db, 99, {}, class_name="Document", refs=[10], path="/site/doc")

        assert backfill.owner_paths(db, [10]) == {99: "/site/doc"}

    def test_an_uncatalogued_holder_is_not_an_owner(self, db):
        backfill = _backfill()
        _insert(db, 10, _image(reason="generated", max_edge=4000))
        # An annotation container references the field value but has no
        # path, so it is not something reindexObject can be called on.
        _insert(db, 98, {}, class_name="PersistentMapping", refs=[10], path=None)

        assert backfill.owner_paths(db, [10]) == {}

    def test_two_image_fields_of_one_owner_collapse(self, db):
        backfill = _backfill()
        _insert(db, 10, _image(reason="generated", max_edge=4000))
        _insert(db, 11, _image(reason="generated", max_edge=4000))
        _insert(db, 99, {}, class_name="Document", refs=[10, 11], path="/site/doc")

        assert backfill.owner_paths(db, [10, 11]) == {99: "/site/doc"}


class TestVerificationAgainstPostgres:
    """The three counts that decide whether a run finished."""

    def _owned(self, db, image_scales=None):
        _insert(db, 10, _image(reason="generated", max_edge=4000))
        _insert(
            db,
            99,
            {},
            class_name="Document",
            refs=[10],
            path="/site/doc",
            idx={"image_scales": image_scales} if image_scales is not None else {},
        )

    def test_a_finished_run_verifies(self, db):
        backfill = _backfill()
        self._owned(db, image_scales={"image": [{"scales": {}}]})

        report = backfill.verify(db, 4000)

        assert report == {
            "remaining": 0,
            "unowned": 0,
            "stale_scales": 0,
            "verified": True,
        }

    def test_a_remaining_candidate_fails_it(self, db):
        backfill = _backfill()
        self._owned(db, image_scales={"image": []})
        _insert(db, 20, _image())

        report = backfill.verify(db, 4000)

        assert report["remaining"] == 1
        assert report["verified"] is False

    def test_an_owner_without_scales_fails_it(self, db):
        backfill = _backfill()
        self._owned(db, image_scales=None)

        report = backfill.verify(db, 4000)

        # Both "phase 2 never reached it" and "a request-less reindex
        # nulled the column" land here, and the second is the failure the
        # request-context gate exists to prevent.
        assert report["stale_scales"] == 1
        assert report["verified"] is False

    def test_an_explicit_json_null_counts_as_missing(self, db):
        backfill = _backfill()
        _insert(db, 10, _image(reason="generated", max_edge=4000))
        _insert(
            db,
            99,
            {},
            class_name="Document",
            refs=[10],
            path="/site/doc",
            idx={"image_scales": None},
        )

        # This is exactly what a request-less reindex writes: not a missing
        # key, an explicit null merged over the scales.
        assert backfill.verify(db, 4000)["stale_scales"] == 1

    def test_a_derivative_with_no_owner_fails_it(self, db):
        backfill = _backfill()
        _insert(db, 10, _image(reason="generated", max_edge=4000))

        report = backfill.verify(db, 4000)

        assert report["unowned"] == 1
        assert report["verified"] is False


class TestCropHistogramAgainstPostgres:
    """The dry run's crop scan, including the LATERAL evaluation order."""

    def test_it_survives_a_non_array_annotation(self, db):
        backfill = _backfill()
        # A LATERAL function argument is evaluated before WHERE, so a
        # jsonb_typeof guard in the WHERE clause would not protect it.
        _insert(db, 10, {"filename": "x.jpg"})
        _insert(db, 99, {"__annotations__": "not-a-structure"}, class_name="Document")

        assert isinstance(backfill.crop_histogram(db), dict)

    def test_an_empty_database_yields_an_empty_histogram(self, db):
        backfill = _backfill()

        assert backfill.crop_histogram(db) == {}
