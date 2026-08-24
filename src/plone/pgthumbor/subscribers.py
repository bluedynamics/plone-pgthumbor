"""Dexterity subscribers that keep Thumbor source derivatives current.

``derivative.py`` owns the pixels.  This module owns only the wiring:
which objects, which fields, and how much of it may run at once.

Registered for ``IDexterityContent`` and never for ``*``.  This package
declares a ``z3c.autoinclude`` plugin, so its ZCML is loaded for the whole
instance — an unqualified registration would fire in every site in the
process, including the ones that never installed the add-on and whose
images nothing here should touch.
"""

from __future__ import annotations

from plone.dexterity.utils import iterSchemata
from plone.namedfile.interfaces import INamedBlobImage
from plone.namedfile.interfaces import INamedBlobImageField
from plone.pgthumbor.config import get_thumbor_config
from plone.pgthumbor.derivative import needs_processing
from plone.pgthumbor.derivative import REASON_RETRY
from plone.pgthumbor.derivative import set_source_derivative
from zope.schema import getFieldsInOrder

import logging
import threading


logger = logging.getLogger(__name__)

# One decode at a time, process-wide.
#
# The mass-upload path (``@@fileUpload`` -> ``DXFileFactory``) already
# serialises on its own module-global ``upload_lock``, but the edit path
# holds no lock at all: ``IObjectModifiedEvent`` can fan out across every
# worker thread in the process, at roughly 79-105 MB of RGB buffer per
# concurrent decode of a print-resolution original.  Against a pod memory
# limit that is an OOM kill, not a slowdown.
_DECODE_SEMAPHORE = threading.BoundedSemaphore(1)

# Short on purpose.  A thread that cannot get in promptly records a retry
# and gives up rather than queueing: queueing would hold a request thread
# for the length of somebody else's 100 MP decode, and the work is never
# urgent — without a derivative the original is served exactly as before.
DECODE_TIMEOUT = 2.0

_MISSING = object()


def _configured_max_edge() -> int:
    """The cap in force, or 0 when derivative generation is off."""
    config = get_thumbor_config()
    if config is None:
        # No Thumbor configured: nothing would ever read a derivative, so
        # producing one is pure cost.  Note that nothing is *recorded*
        # either, so the objects stay backfill candidates and configuring
        # Thumbor later picks them all up.
        return 0
    return config.source_max_edge


def _field_value(obj, schema, name: str):
    """The value of *name* on *obj*, looking through a behaviour adapter."""
    value = getattr(obj, name, _MISSING)
    if value is not _MISSING:
        return value
    # A behaviour's fields do not live on the content object at all, so
    # getattr misses them outright and the adapter is the only place the
    # value exists.  ``schema(obj, None)`` is the cheap form: a marker-only
    # behaviour adapts to nothing and None is then the right answer.
    return getattr(schema(obj, None), name, None)


def _image_fields(obj, schema):
    """Yield every ``NamedBlobImage`` value *obj* holds for *schema*."""
    for name, field in getFieldsInOrder(schema):
        if not INamedBlobImageField.providedBy(field):
            continue
        value = _field_value(obj, schema, name)
        # Not "is not None": a plain string left behind by a half-finished
        # migration is a broken field value, not a reason to raise inside
        # somebody's upload.
        if INamedBlobImage.providedBy(value):
            yield value


def iter_image_fields(obj):
    """Yield every image value on *obj*, across its schema and behaviours.

    Public because ``modifiers.py`` asks the same question when it decides
    which derivatives to keep out of a version snapshot.  A second walk
    would be a second answer.
    """
    try:
        schemata = tuple(iterSchemata(obj))
    except Exception:
        # A missing or broken FTI is somebody else's bug.  Losing the
        # derivative over it is acceptable; losing the upload is not.
        logger.warning(
            "Could not read the schemata of %r; no Thumbor source "
            "derivatives will be generated for it",
            obj,
            exc_info=True,
        )
        return
    for schema in schemata:
        yield from _image_fields(obj, schema)


def _record_retry(named_image, max_edge: int) -> None:
    """Record a deferred decode, leaving any existing derivative in place.

    Deliberately not ``derivative._record``: that one writes
    ``_pgthumbor_source`` as well, and here there is nothing to write it
    *from*.  The image that reaches this function under contention is
    usually one whose recorded cap no longer matches — it still carries a
    perfectly serviceable derivative at the old cap, and replacing it with
    ``None`` would put the print-resolution original back into service, the
    exact HTTP 400 this package exists to remove, until the next backfill.

    ``source_ids`` is carried over rather than re-read for the same reason:
    it is the provenance of the derivative that is *there*, not of the run
    that failed to produce a new one.  Re-reading it would paper over an
    in-place ``image.data = ...`` and keep a stale derivative in service.

    The reason is ``"retry"``, which is **not** terminal.  That is the whole
    point of this path: the image has to be re-selected by an ordinary
    backfill run, with no ``force`` flag for anyone to forget.  A terminal
    marker here is the difference between a deferred image and a
    permanently lost one, and the backfill's verification would still
    report success.
    """
    info = getattr(named_image, "_pgthumbor_source_info", None)
    named_image._pgthumbor_source_info = {
        "reason": REASON_RETRY,
        "max_edge": max_edge,
        "source_ids": info.get("source_ids") if isinstance(info, dict) else None,
    }


def _generate(named_image, max_edge: int) -> None:
    """Generate one derivative, under the process-wide decode semaphore."""
    if not needs_processing(named_image, max_edge):
        # Imported from derivative.py rather than re-derived here: this is
        # the same "does this still need work" rule the backfill's
        # candidate SQL mirrors, and a second spelling of it would be a
        # place for the two to drift.
        #
        # Asking before taking the semaphore is load-bearing twice over.
        # Every edit of an already-processed object would otherwise queue
        # behind an unrelated decode for nothing, and a timeout there would
        # overwrite a terminal record with a transient one — turning a
        # settled image into a backfill candidate forever.
        return
    if not _DECODE_SEMAPHORE.acquire(timeout=DECODE_TIMEOUT):
        _record_retry(named_image, max_edge)
        return
    try:
        set_source_derivative(named_image, max_edge=max_edge)
    finally:
        # A raising generator must not wedge every later request in the
        # process.  ``set_source_derivative`` promises not to raise; this
        # does not depend on the promise.
        _DECODE_SEMAPHORE.release()


def generate_source_derivatives(obj, event) -> None:
    """Give every image field on *obj* a Thumbor source derivative.

    Registered for both ``IObjectAddedEvent`` and ``IObjectModifiedEvent``.
    The added event also fires on rename, move, paste and content import,
    none of which change a byte of the image — idempotence, enforced by
    ``derivative.needs_processing``, is what makes those free rather than a
    repeated decode.

    **Never raises.**  Derivative generation is an optimisation over
    serving the original, and an optimisation must not be able to fail an
    upload.
    """
    try:
        max_edge = _configured_max_edge()
        if max_edge <= 0:
            # Before any schema walk and long before any blob is opened.
            # 0 is the documented kill switch, the thing an operator
            # reaches for during a bulk import or an incident.
            return
        for named_image in iter_image_fields(obj):
            try:
                _generate(named_image, max_edge)
            except Exception:
                # Per field, not per object.  A content type with both a
                # content image and a lead image must not lose the second
                # one's derivative because the first has a broken blob —
                # that failure would be silent and permanent, since the
                # object carries no outcome record for the field never
                # reached.
                logger.warning(
                    "Thumbor source derivative generation failed for one "
                    "field of %r; continuing with the rest",
                    obj,
                    exc_info=True,
                )
    except Exception:
        logger.warning(
            "Thumbor source derivative generation failed for %r; the "
            "original will be used unchanged",
            obj,
            exc_info=True,
        )
