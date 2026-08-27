"""``image_scales`` catalog metadata under Thumbor.

Plone 6 stores an ``image_scales`` metadata column so listings and tiles can
render an ``<img>`` from a brain without waking the object.  Each entry
carries a ``download`` value, and ``plone.namedfile`` treats that as a
**context-relative** path: ``_scale_view_from_url`` strips the context URL
off on the way in, and the renderer puts it back on the way out.

The top-level ``download`` of that structure is meant to be the original
file.  ``ImageFieldScales.get_original_image_url`` produces it by asking for
a scale at the original's *own* dimensions — which under Thumbor came back
as a Thumbor URL, and that is wrong twice over.

It is not the original.  A 1:1 request through an image processor is at
best a re-encode, and since source derivatives landed a Thumbor URL names
the **derivative**, so a "download original" link handed over a capped,
colour-converted rendition rather than the bytes that were uploaded — on a
press site, exactly the asset the link exists for.

And it is not context-relative.  With ``PGTHUMBOR_SERVER_URL=/thumbor`` the
URL is host-root, so there is no context prefix to strip; the value stored
is ``thumbor/<signed>`` and the renderer emits
``{image_url}/thumbor/<signed>``, which resolves nowhere.  That breaks every
consumer of the metadata, including ``plone.namedfile``'s own ``tag()``.

Only the top-level field is changed here.  The per-scale ``download``
entries *should* be Thumbor URLs; their own version of the host-root
problem is the rest of issue #15 and is tangled with issue #7, so it wants
answering in one piece rather than half here.
"""

from __future__ import annotations

from plone.base.interfaces import IImageScalesFieldAdapter
from plone.dexterity.interfaces import IDexterityContent
from plone.namedfile.adapters import ImageFieldScales
from plone.namedfile.interfaces import INamedImageField
from plone.pgthumbor.interfaces import IPlonePgthumborLayer
from plone.pgthumbor.scaling import _skip_type_fallback_url
from zope.component import adapter
from zope.interface import implementer


@implementer(IImageScalesFieldAdapter)
@adapter(INamedImageField, IDexterityContent, IPlonePgthumborLayer)
class ThumborImageFieldScales(ImageFieldScales):
    """Keep the original's ``download`` pointing at the original.

    Registered for ``IPlonePgthumborLayer`` rather than ``Interface``, which
    makes it strictly more specific than ``plone.namedfile``'s own
    registration and so wins the lookup without an ``overrides.zcml`` entry.
    Sites without the add-on installed keep the stock adapter.
    """

    def get_original_image_url(self, fieldname, width, height):
        """The field's own URL, which Plone serves from the original blob.

        *width* and *height* are ignored on purpose.  They are the
        original's own dimensions, so there is nothing to scale to, and
        passing them on is what produced a Thumbor render where the
        original belonged.

        ``@@images/{fieldname}`` reaches the original rather than a
        rendition: the field branch of ``ImageScaling.publishTraverse``
        builds the scale view without a ``uid``, and
        ``ThumborImageScale.__init__`` only produces a Thumbor URL when one
        is present — so ``index_html()`` falls through to the parent and
        streams the stored bytes.

        Shares ``_skip_type_fallback_url`` with the SVG path (issue #17),
        which needs the same URL for the same reason.  Two spellings of
        "the original's own URL" would be two things to keep in step.
        """
        return _skip_type_fallback_url(
            self.context, self.field.get(self.context), fieldname
        )
