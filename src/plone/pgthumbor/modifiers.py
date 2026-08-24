"""Keep Thumbor source derivatives out of CMFEditions version snapshots.

``Products.CMFEditions`` snapshots content by deep-pickling it, and
``plone.app.versioningbehavior``'s ``CloneNamedFileBlobs`` protects blobs
from that pickle by collecting them — but it walks **top-level field values
only**, returning ``field_value._blob`` per ``INamedBlobImageField``.  A
derivative lives one level deeper, as an attribute of the field value, so
its ``_blob`` is not in that mapping.  It goes through the pickle, and
``ZODB.blob.Blob.__getstate__`` returns ``None``.

The result is not an error, which is what makes it worth a module.  The
snapshot ends up holding a ``NamedBlobImage`` that looks entirely valid and
reads back zero bytes.  On revert, ``get_blob_ids`` resolves it to a real
``(zoid, tid)``, Thumbor is handed nothing and answers 400, and §1's
structural-invalidation argument does not save us because the attribute
*is* present.

This module is imported only where ``Products.CMFEditions`` exists;
``setuphandlers.install_clone_modifier`` catches the ImportError.
"""

from __future__ import annotations

from AccessControl.class_init import InitializeClass
from plone.pgthumbor.derivative import INFO_ATTRIBUTE
from plone.pgthumbor.derivative import SOURCE_ATTRIBUTE
from plone.pgthumbor.setuphandlers import MODIFIER_ID
from plone.pgthumbor.setuphandlers import MODIFIER_TITLE
from plone.pgthumbor.subscribers import iter_image_fields
from Products.CMFEditions.interfaces.IModifier import ICloneModifier
from Products.CMFEditions.Modifiers import ConditionalTalesModifier
from zope.interface import implementer

import logging


logger = logging.getLogger(__name__)


def _callbacks(values):
    """Pickle callbacks that replace *values* with ``None``.

    The same shape as ``plone.app.versioningbehavior.modifiers.getCallbacks``,
    reimplemented rather than imported: that package reaches a site through
    ``plone.app.contenttypes`` and is not a dependency of this one, so
    importing it would trade a graceful absence for an ImportError.

    Keeping a reference to each value matters — some are freshly created and
    must not be garbage collected, because a reused ``id()`` would make the
    mapping answer for the wrong object.
    """
    mapping = {id(value): value for value in values}

    def persistent_id(obj):
        return mapping.get(id(obj))

    def persistent_load(reference):
        return None

    return persistent_id, persistent_load


@implementer(ICloneModifier)
class SkipThumborSourceDerivatives:
    """Drop derivatives and their outcome records on clone."""

    def __init__(self, id_, title):
        self.id = str(id_)
        self.title = str(title)

    def getOnCloneModifiers(self, obj):
        """See ``ICloneModifier``.

        Both attributes go, not just the derivative.  Dropping only the
        derivative would be worse than dropping neither: a reverted object
        would carry a *terminal* outcome record and no derivative,
        ``derivative.needs_processing`` would answer False, and nothing
        would ever regenerate it — the print-resolution original would go
        to Thumbor forever, silently.

        Returning ``None`` for an object with nothing to protect is the
        documented "no callbacks" answer, and it lets CMFEditions skip the
        ``persistent_id`` hook altogether for that clone.
        """
        values = []
        for field_value in iter_image_fields(obj):
            for attribute in (SOURCE_ATTRIBUTE, INFO_ATTRIBUTE):
                found = getattr(field_value, attribute, None)
                if found is not None:
                    values.append(found)
        if not values:
            return None
        persistent_id, persistent_load = _callbacks(values)
        # ModifierRegistryTool chains every registered ICloneModifier and
        # prefixes each pid with the modifier's id, so this composes with
        # CloneNamedFileBlobs rather than replacing it: the top-level blob
        # still survives by reference, the nested one still goes away.
        return persistent_id, persistent_load, [], []


InitializeClass(SkipThumborSourceDerivatives)


def make_clone_modifier():
    """The registerable object ``portal_modifier`` expects."""
    modifier = SkipThumborSourceDerivatives(MODIFIER_ID, MODIFIER_TITLE)
    wrapper = ConditionalTalesModifier(MODIFIER_ID, modifier, MODIFIER_TITLE)
    wrapper.edit(enabled=True, condition="python:True")
    return wrapper
