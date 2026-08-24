"""Tests for subscribers.py — which objects, which fields, how much at once.

``derivative.py`` owns the pixels; this module owns only the wiring, so
everything asserted here is about field selection, idempotence and bounded
concurrency.  Two properties are load-bearing and have their own classes:
nothing here may be able to fail an upload, and a semaphore timeout must
leave the image a backfill candidate rather than a permanently lost one.
"""

from __future__ import annotations

from contextlib import contextmanager
from tests.conftest import big_jpeg_bytes
from tests.conftest import jpeg_bytes


def _fake_schema(second_image=False):
    """An interface with one image field and one field that is not an image.

    Built with ``InterfaceClass`` rather than a ``class`` statement so the
    optional second image field can be constructed *inside* the function.
    ``getFieldsInOrder`` sorts on zope.schema's global creation counter, so
    a field built in the caller's argument list would come out first and
    the field order would silently be the reverse of the one written here.
    """
    from plone.namedfile.field import NamedBlobImage
    from zope.interface import Interface
    from zope.interface.interface import InterfaceClass
    from zope.schema import TextLine

    attributes = {
        "image": NamedBlobImage(title="Image"),
        "title": TextLine(title="Title"),
    }
    if second_image:
        attributes["lead_image"] = NamedBlobImage(title="Lead image")
    return InterfaceClass("IFakeSchema", (Interface,), attributes)


class _Content:
    """A plain object, deliberately not a ``MagicMock``.

    ``getattr`` on a MagicMock auto-creates a child for every name asked
    for, so a mock can never exhibit the "attribute genuinely missing"
    branch — which is the only branch the behaviour-adapter fallback
    exists for.
    """

    def __init__(self, **values):
        self.__dict__.update(values)


class _BehaviourContent:
    """A content object whose field values live on an adapter.

    ``__conform__`` is consulted by ``zope.interface`` before the adapter
    registry, so this stands in for a registered behaviour factory without
    a global registration — and therefore without a teardown that could
    leak into the rest of the run.
    """

    def __init__(self, adapter):
        self._adapter = adapter

    def __conform__(self, interface):
        return self._adapter


def _named_image(data=None, content_type="image/jpeg", filename="t.jpg"):
    from plone.namedfile.file import NamedBlobImage

    return NamedBlobImage(
        data=jpeg_bytes() if data is None else data,
        filename=filename,
        contentType=content_type,
    )


def _configured(monkeypatch, **kwargs):
    from tests.conftest import env_override

    env_override(
        monkeypatch,
        PGTHUMBOR_SERVER_URL="http://thumbor:8888",
        PGTHUMBOR_SECURITY_KEY="key",
        **kwargs,
    )


def _pin_schema(monkeypatch, schema):
    """Replace schema discovery, so no Dexterity FTI has to exist."""
    from plone.pgthumbor import subscribers

    monkeypatch.setattr(subscribers, "iterSchemata", lambda obj: [schema])


@contextmanager
def _semaphore_held(monkeypatch):
    """Hold the process-wide decode semaphore with an instant timeout.

    The ``finally`` is not decoration: the semaphore is a module global,
    and leaking it acquired would make every later test in the process
    time out instead of generating.
    """
    from plone.pgthumbor import subscribers

    monkeypatch.setattr(subscribers, "DECODE_TIMEOUT", 0)
    subscribers._DECODE_SEMAPHORE.acquire()
    try:
        yield
    finally:
        subscribers._DECODE_SEMAPHORE.release()


class TestImageFieldDiscovery:
    """Only real image values are yielded, and a broken walk yields nothing."""

    def test_only_image_fields_are_yielded(self):
        from plone.pgthumbor import subscribers
        from tests.conftest import namedfile_storables

        with namedfile_storables():
            image = _named_image()
            content = _Content(image=image, title="not an image")

            found = list(subscribers._image_fields(content, _fake_schema()))

            assert found == [image]

    def test_a_none_value_is_skipped(self):
        from plone.pgthumbor import subscribers

        content = _Content(image=None, title="x")

        assert list(subscribers._image_fields(content, _fake_schema())) == []

    def test_a_value_that_is_not_a_named_blob_image_is_skipped(self):
        from plone.pgthumbor import subscribers

        # A plain string in an image field is a broken migration, not a
        # reason to raise inside an upload.
        content = _Content(image="/some/path.jpg", title="x")

        assert list(subscribers._image_fields(content, _fake_schema())) == []

    def test_a_missing_attribute_falls_back_to_the_behaviour_adapter(self):
        from plone.pgthumbor import subscribers
        from tests.conftest import namedfile_storables

        with namedfile_storables():
            image = _named_image()
            # The value lives on the adapter only — getattr on the content
            # object misses it entirely, which is exactly how a behaviour
            # field behaves.
            content = _BehaviourContent(_Content(image=image, title="x"))

            found = list(subscribers._image_fields(content, _fake_schema()))

            assert found == [image]

    def test_a_missing_attribute_with_no_adapter_yields_nothing(self):
        from plone.pgthumbor import subscribers

        content = _Content(title="x")

        assert list(subscribers._image_fields(content, _fake_schema())) == []

    def test_an_iter_schemata_failure_yields_nothing(self, monkeypatch, caplog):
        from plone.pgthumbor import subscribers

        def explode(obj):
            raise RuntimeError("no FTI for you")

        monkeypatch.setattr(subscribers, "iterSchemata", explode)

        with caplog.at_level("WARNING"):
            assert list(subscribers.iter_image_fields(_Content())) == []

        assert "schemata" in caplog.text.lower()

    def test_every_schema_contributes_its_image_fields(self, monkeypatch):
        from plone.pgthumbor import subscribers
        from tests.conftest import namedfile_storables

        with namedfile_storables():
            first = _named_image()
            second = _named_image(filename="lead.jpg")
            schema = _fake_schema(second_image=True)
            _pin_schema(monkeypatch, schema)
            content = _Content(image=first, lead_image=second, title="x")

            assert list(subscribers.iter_image_fields(content)) == [first, second]


class TestShortCircuits:
    """Nothing is walked, opened or decoded when generation is switched off."""

    def test_no_configuration_stops_before_any_field_walk(self, monkeypatch):
        from plone.pgthumbor import subscribers

        # Nothing configured at all: get_thumbor_config() returns None.
        _pin_schema(monkeypatch, _fake_schema())
        walked = []
        monkeypatch.setattr(
            subscribers, "iterSchemata", lambda obj: walked.append(obj) or []
        )
        from tests.conftest import env_override

        env_override(monkeypatch)

        subscribers.generate_source_derivatives(_Content(), None)

        assert walked == []

    def test_a_disabled_cap_stops_before_any_field_walk(self, monkeypatch):
        from plone.pgthumbor import subscribers

        _configured(monkeypatch, PGTHUMBOR_SOURCE_MAX_EDGE="0")
        walked = []
        monkeypatch.setattr(
            subscribers, "iterSchemata", lambda obj: walked.append(obj) or []
        )

        subscribers.generate_source_derivatives(_Content(), None)

        # 0 is the documented kill switch.  Reaching a blob at all here
        # would defeat the point of reaching for it during an incident.
        assert walked == []


class TestTheSubscriberNeverRaises:
    """Derivative generation must not be able to fail an upload."""

    def test_a_failing_configuration_lookup_is_swallowed(self, monkeypatch, caplog):
        from plone.pgthumbor import subscribers

        def explode():
            raise RuntimeError("registry is on fire")

        monkeypatch.setattr(subscribers, "get_thumbor_config", explode)

        with caplog.at_level("WARNING"):
            assert subscribers.generate_source_derivatives(_Content(), None) is None

        assert "derivative" in caplog.text.lower()

    def test_a_failing_generator_is_swallowed(self, monkeypatch, caplog):
        from plone.pgthumbor import subscribers
        from tests.conftest import namedfile_storables

        _configured(monkeypatch)

        def explode(*args, **kwargs):
            raise RuntimeError("pillow fell over")

        monkeypatch.setattr(subscribers, "set_source_derivative", explode)
        with namedfile_storables():
            content = _Content(image=_named_image(), title="x")
            _pin_schema(monkeypatch, _fake_schema())

            with caplog.at_level("WARNING"):
                assert subscribers.generate_source_derivatives(content, None) is None

        assert "derivative" in caplog.text.lower()
        # The semaphore is released through a finally, so a raising
        # generator must not wedge every later request in the process.
        assert subscribers._DECODE_SEMAPHORE.acquire(timeout=0) is True
        subscribers._DECODE_SEMAPHORE.release()


class TestGeneration:
    """Every image field is offered to the generator, exactly once."""

    def test_every_image_field_is_processed(self, monkeypatch):
        from plone.pgthumbor import subscribers
        from tests.conftest import namedfile_storables

        _configured(monkeypatch)
        seen = []
        monkeypatch.setattr(
            subscribers,
            "set_source_derivative",
            lambda image, max_edge: seen.append((image, max_edge)),
        )
        with namedfile_storables():
            first = _named_image()
            second = _named_image(filename="lead.jpg")
            _pin_schema(monkeypatch, _fake_schema(second_image=True))
            content = _Content(image=first, lead_image=second, title="x")

            subscribers.generate_source_derivatives(content, None)

            assert seen == [(first, 4000), (second, 4000)]

    def test_the_configured_cap_is_used(self, monkeypatch):
        from plone.pgthumbor import subscribers
        from tests.conftest import namedfile_storables

        _configured(monkeypatch, PGTHUMBOR_SOURCE_MAX_EDGE="1000")
        seen = []
        monkeypatch.setattr(
            subscribers,
            "set_source_derivative",
            lambda image, max_edge: seen.append(max_edge),
        )
        with namedfile_storables():
            _pin_schema(monkeypatch, _fake_schema())
            content = _Content(image=_named_image(), title="x")

            subscribers.generate_source_derivatives(content, None)

            assert seen == [1000]

    def test_an_oversized_image_really_gets_a_derivative(self, monkeypatch):
        from plone.namedfile.file import NamedBlobImage
        from plone.pgthumbor import subscribers
        from tests.conftest import namedfile_storables

        _configured(monkeypatch, PGTHUMBOR_SOURCE_MAX_EDGE="1000")
        with namedfile_storables():
            _pin_schema(monkeypatch, _fake_schema())
            image = _named_image(big_jpeg_bytes())
            content = _Content(image=image, title="x")

            subscribers.generate_source_derivatives(content, None)

            assert isinstance(image._pgthumbor_source, NamedBlobImage)
            assert image._pgthumbor_source.getImageSize() == (1000, 750)


class TestIdempotence:
    """Firing twice must be free — that is what makes rename and paste safe.

    ``IObjectAddedEvent`` fires on rename, move, paste and content import,
    not only on the original upload.  None of those change a byte of the
    image, so a second run has to be a no-op rather than a second decode.
    """

    def test_firing_twice_decodes_once(self, monkeypatch):
        from plone.pgthumbor import derivative
        from plone.pgthumbor import subscribers
        from tests.conftest import namedfile_storables

        _configured(monkeypatch, PGTHUMBOR_SOURCE_MAX_EDGE="1000")
        with namedfile_storables():
            _pin_schema(monkeypatch, _fake_schema())
            content = _Content(image=_named_image(big_jpeg_bytes()), title="x")

            calls = []
            real = derivative.build_derivative_bytes
            monkeypatch.setattr(
                derivative,
                "build_derivative_bytes",
                lambda *a, **k: (calls.append(1), real(*a, **k))[1],
            )

            subscribers.generate_source_derivatives(content, None)
            subscribers.generate_source_derivatives(content, None)

            assert calls == [1]

    def test_replacing_the_image_regenerates(self, monkeypatch):
        from plone.pgthumbor import derivative
        from plone.pgthumbor import subscribers
        from tests.conftest import namedfile_storables

        _configured(monkeypatch, PGTHUMBOR_SOURCE_MAX_EDGE="1000")
        with namedfile_storables():
            _pin_schema(monkeypatch, _fake_schema())
            content = _Content(image=_named_image(big_jpeg_bytes()), title="x")

            calls = []
            real = derivative.build_derivative_bytes
            monkeypatch.setattr(
                derivative,
                "build_derivative_bytes",
                lambda *a, **k: (calls.append(1), real(*a, **k))[1],
            )

            subscribers.generate_source_derivatives(content, None)
            # A replaced image is a *new* NamedBlobImage carrying no record.
            content.image = _named_image(big_jpeg_bytes(size=(1600, 1200)))
            subscribers.generate_source_derivatives(content, None)

            assert calls == [1, 1]
            assert content.image._pgthumbor_source.getImageSize() == (1000, 750)


class TestDecodeSemaphore:
    """A contended decode is deferred, never lost.

    A ``"retry"`` record must stay a backfill candidate.  A terminal marker
    here would be the difference between an image that gets its derivative
    on the next run and one that never gets it at all, while the backfill's
    terminal verification still reported success.
    """

    def test_a_timeout_records_a_retry_outcome(self, monkeypatch):
        from plone.pgthumbor import subscribers
        from plone.pgthumbor.derivative import REASON_RETRY
        from tests.conftest import namedfile_storables

        _configured(monkeypatch, PGTHUMBOR_SOURCE_MAX_EDGE="1000")
        with namedfile_storables():
            _pin_schema(monkeypatch, _fake_schema())
            image = _named_image(big_jpeg_bytes())
            content = _Content(image=image, title="x")

            with _semaphore_held(monkeypatch):
                subscribers.generate_source_derivatives(content, None)

            assert image._pgthumbor_source_info["reason"] == REASON_RETRY
            assert image._pgthumbor_source_info["max_edge"] == 1000

    def test_a_timeout_does_not_decode(self, monkeypatch):
        from plone.pgthumbor import subscribers
        from tests.conftest import namedfile_storables

        _configured(monkeypatch, PGTHUMBOR_SOURCE_MAX_EDGE="1000")
        seen = []
        monkeypatch.setattr(
            subscribers, "set_source_derivative", lambda *a, **k: seen.append(1)
        )
        with namedfile_storables():
            _pin_schema(monkeypatch, _fake_schema())
            content = _Content(image=_named_image(big_jpeg_bytes()), title="x")

            with _semaphore_held(monkeypatch):
                subscribers.generate_source_derivatives(content, None)

            # Skipped, not queued: queueing would hold a request thread for
            # the length of somebody else's 100 MP decode.
            assert seen == []

    def test_the_retry_outcome_is_not_terminal(self, monkeypatch):
        from plone.pgthumbor.derivative import REASON_RETRY
        from plone.pgthumbor.derivative import TERMINAL_REASONS

        assert REASON_RETRY not in TERMINAL_REASONS

    def test_an_image_recorded_as_retry_is_still_a_backfill_candidate(
        self, monkeypatch
    ):
        from plone.pgthumbor import subscribers
        from plone.pgthumbor.derivative import needs_processing
        from tests.conftest import namedfile_storables

        _configured(monkeypatch, PGTHUMBOR_SOURCE_MAX_EDGE="1000")
        with namedfile_storables():
            _pin_schema(monkeypatch, _fake_schema())
            image = _named_image(big_jpeg_bytes())
            content = _Content(image=image, title="x")

            with _semaphore_held(monkeypatch):
                subscribers.generate_source_derivatives(content, None)

            # The candidate rule the backfill's SQL mirrors, applied to the
            # record the timeout just wrote — without force.
            assert needs_processing(image, 1000) is True

    def test_a_deferred_image_is_generated_on_the_next_run(self, monkeypatch):
        from plone.namedfile.file import NamedBlobImage
        from plone.pgthumbor import subscribers
        from tests.conftest import namedfile_storables

        _configured(monkeypatch, PGTHUMBOR_SOURCE_MAX_EDGE="1000")
        with namedfile_storables():
            _pin_schema(monkeypatch, _fake_schema())
            image = _named_image(big_jpeg_bytes())
            content = _Content(image=image, title="x")

            with _semaphore_held(monkeypatch):
                subscribers.generate_source_derivatives(content, None)
            subscribers.generate_source_derivatives(content, None)

            assert isinstance(image._pgthumbor_source, NamedBlobImage)
            assert image._pgthumbor_source_info["reason"] == "generated"

    def test_a_timeout_does_not_destroy_an_existing_derivative(self, monkeypatch):
        from plone.pgthumbor import subscribers
        from plone.pgthumbor.derivative import REASON_RETRY
        from tests.conftest import namedfile_storables

        _configured(monkeypatch, PGTHUMBOR_SOURCE_MAX_EDGE="1000")
        with namedfile_storables():
            _pin_schema(monkeypatch, _fake_schema())
            image = _named_image(big_jpeg_bytes())
            content = _Content(image=image, title="x")
            subscribers.generate_source_derivatives(content, None)
            existing = image._pgthumbor_source
            recorded_ids = image._pgthumbor_source_info["source_ids"]

            # Raising the cap makes the image a candidate again.  If the
            # timeout then wrote a bare record, a working 1000 px
            # derivative would be replaced by nothing at all and the
            # original — the one Thumbor answers with a 400 — would go
            # back into service until the backfill next ran.
            _configured(monkeypatch, PGTHUMBOR_SOURCE_MAX_EDGE="1200")
            with _semaphore_held(monkeypatch):
                subscribers.generate_source_derivatives(content, None)

            assert image._pgthumbor_source is existing
            assert image._pgthumbor_source_info["reason"] == REASON_RETRY
            # The provenance belongs to the derivative that is still there,
            # not to the run that failed to make a new one — otherwise an
            # in-place `image.data = ...` between the two would be papered
            # over and a stale derivative would stay in service.
            assert image._pgthumbor_source_info["source_ids"] == recorded_ids

    def test_an_already_processed_image_never_takes_the_semaphore(self, monkeypatch):
        from plone.pgthumbor import subscribers
        from tests.conftest import namedfile_storables

        _configured(monkeypatch, PGTHUMBOR_SOURCE_MAX_EDGE="1000")
        with namedfile_storables():
            _pin_schema(monkeypatch, _fake_schema())
            image = _named_image()
            content = _Content(image=image, title="x")
            subscribers.generate_source_derivatives(content, None)
            assert image._pgthumbor_source_info["reason"] == "not_needed"

            with _semaphore_held(monkeypatch):
                subscribers.generate_source_derivatives(content, None)

            # Contention must not overwrite a terminal record with a
            # transient one: that would make a settled image a backfill
            # candidate forever.
            assert image._pgthumbor_source_info["reason"] == "not_needed"

    def test_the_semaphore_is_released_after_a_run(self, monkeypatch):
        from plone.pgthumbor import subscribers
        from tests.conftest import namedfile_storables

        _configured(monkeypatch, PGTHUMBOR_SOURCE_MAX_EDGE="1000")
        with namedfile_storables():
            _pin_schema(monkeypatch, _fake_schema())
            content = _Content(image=_named_image(big_jpeg_bytes()), title="x")

            subscribers.generate_source_derivatives(content, None)

            assert subscribers._DECODE_SEMAPHORE.acquire(timeout=0) is True
            subscribers._DECODE_SEMAPHORE.release()

    def test_only_one_decode_runs_at_a_time(self, monkeypatch):
        from plone.pgthumbor import subscribers
        from tests.conftest import namedfile_storables

        import threading

        _configured(monkeypatch, PGTHUMBOR_SOURCE_MAX_EDGE="1000")
        concurrent = []
        started = threading.Event()

        def slow(image, max_edge):
            concurrent.append(len(concurrent) + 1)
            started.set()
            # Long enough that the second thread's zero timeout expires
            # while this one is still inside, which is the whole point.
            threading.Event().wait(0.05)

        monkeypatch.setattr(subscribers, "set_source_derivative", slow)
        with namedfile_storables():
            _pin_schema(monkeypatch, _fake_schema())
            first = _Content(image=_named_image(), title="x")
            second = _Content(image=_named_image(), title="x")
            monkeypatch.setattr(subscribers, "DECODE_TIMEOUT", 0)

            worker = threading.Thread(
                target=subscribers.generate_source_derivatives, args=(first, None)
            )
            worker.start()
            started.wait(1.0)
            subscribers.generate_source_derivatives(second, None)
            worker.join(2.0)

            assert concurrent == [1]
            assert second.image._pgthumbor_source_info["reason"] == "retry"


class TestPerFieldIsolation:
    """One broken image field must not deny the others their derivative."""

    def test_a_failing_field_does_not_stop_the_next_one(self, monkeypatch, caplog):
        from plone.pgthumbor import subscribers
        from tests.conftest import namedfile_storables

        _configured(monkeypatch)
        seen = []

        def explode_on_first(image, max_edge):
            if not seen:
                seen.append(image)
                raise RuntimeError("this blob is unreadable")
            seen.append(image)

        monkeypatch.setattr(subscribers, "set_source_derivative", explode_on_first)
        with namedfile_storables():
            first = _named_image()
            second = _named_image(filename="lead.jpg")
            _pin_schema(monkeypatch, _fake_schema(second_image=True))
            content = _Content(image=first, lead_image=second, title="x")

            with caplog.at_level("WARNING"):
                subscribers.generate_source_derivatives(content, None)

        # An object-level guard would have abandoned the lead image, and
        # silently: it carries no outcome record for a field never reached,
        # so nothing would ever say it was skipped.
        assert seen == [first, second]
        assert "continuing with the rest" in caplog.text
