"""The ``image_scales`` metadata adapter.

Issue #15, the top-level half.  ``ImageFieldScales`` builds the metadata's
top-level ``download`` from a scale at *the original's own dimensions*, and
under pgthumbor that came back as a Thumbor URL.  Two things follow from
that, and both are wrong.

The value is meant to be the **original file** — a "download original" link
should hand over the bytes that were uploaded.  Since 0.7.0 a Thumbor URL
names the source derivative, so it was not even a render of the original
any more.

And it is meant to be **context-relative**: ``_scale_view_from_url`` strips
the context URL off, and the renderer puts it back.  A Thumbor URL has no
context prefix to strip, so with ``PGTHUMBOR_SERVER_URL=/thumbor`` the
stored value was ``thumbor/<signed>`` and the renderer produced
``{image_url}/thumbor/<signed>`` — broken for every consumer of the
metadata, including plone.namedfile's own ``tag()``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


SERVER = "http://thumbor:8888"
CONTEXT_URL = "http://plone:8080/site/doc"


def _adapter(fieldname="image", modified=1700000000.0):
    from plone.pgthumbor.adapters import ThumborImageFieldScales

    context = MagicMock()
    context.absolute_url.return_value = CONTEXT_URL
    value = MagicMock()
    value.modified = modified
    field = MagicMock()
    field.__name__ = fieldname
    field.get.return_value = value
    return ThumborImageFieldScales(field, context, MagicMock())


class TestOriginalDownload:
    """The top-level download is the original, served by Plone."""

    def test_it_is_the_field_url_not_a_thumbor_url(self):
        adapter = _adapter()

        url = adapter.get_original_image_url("image", 11811, 8858)

        assert url.startswith(f"{CONTEXT_URL}/@@images/image")
        assert SERVER not in url
        assert "thumbor" not in url

    def test_the_requested_dimensions_are_ignored(self):
        adapter = _adapter()

        # They are the original's own size, so there is nothing to scale
        # to; passing them through to Thumbor is what produced a
        # smart-cropped render where the original belonged.
        assert adapter.get_original_image_url(
            "image", 11811, 8858
        ) == adapter.get_original_image_url("image", 100, 100)

    def test_it_survives_the_context_strip_intact(self):
        from plone.namedfile.adapters import ImageFieldScales

        adapter = _adapter()

        url = adapter.get_original_image_url("image", 11811, 8858)
        stored = ImageFieldScales._scale_view_from_url(adapter, url)

        # This is the property the metadata contract rests on: what is
        # stored must be relative to the context, so the renderer's
        # f"{brain.getURL()}/{download}" comes out right.
        assert stored.startswith("@@images/image")
        assert not stored.startswith("http")
        assert f"{CONTEXT_URL}/{stored}".startswith(CONTEXT_URL + "/@@images/")

    def test_it_carries_a_cache_buster(self):
        adapter = _adapter(modified=1700000000.0)

        # The field URL is not unique per scale, so it caches worse than a
        # uid URL; the modification time compensates, exactly as the SVG
        # fallback does.
        assert "?v=" in adapter.get_original_image_url("image", 800, 600)

    def test_a_value_without_a_modification_time_still_yields_a_url(self):
        adapter = _adapter(modified=None)
        adapter.context._p_mtime = None

        url = adapter.get_original_image_url("image", 800, 600)

        assert url.endswith("/@@images/image")

    def test_the_fieldname_comes_from_the_argument(self):
        adapter = _adapter(fieldname="lead_image")

        assert "/@@images/lead_image" in adapter.get_original_image_url(
            "lead_image", 800, 600
        )


class TestPerScaleDownloadsAreUntouched:
    """Only the top-level field changes; scales still go through Thumbor."""

    def test_get_scales_is_not_overridden(self):
        from plone.namedfile.adapters import ImageFieldScales
        from plone.pgthumbor.adapters import ThumborImageFieldScales

        # Per-scale downloads *should* be Thumbor URLs. Their own problem —
        # a host-root path the renderer mangles — is issue #15's second
        # half and is tangled with #7, so it is deliberately not touched
        # here.
        assert ThumborImageFieldScales.get_scales is ImageFieldScales.get_scales

    def test_only_the_original_url_method_is_overridden(self):
        from plone.namedfile.adapters import ImageFieldScales
        from plone.pgthumbor.adapters import ThumborImageFieldScales

        overridden = {
            name
            for name, value in vars(ThumborImageFieldScales).items()
            if not name.startswith("__") and hasattr(ImageFieldScales, name)
        }

        assert overridden == {"get_original_image_url"}


class TestRegistration:
    """It has to win against plone.namedfile's own adapter."""

    def _zcml(self):
        from xml.etree import ElementTree

        import pathlib
        import plone.pgthumbor

        path = pathlib.Path(plone.pgthumbor.__file__).parent / "configure.zcml"
        return ElementTree.parse(path).getroot()

    def test_it_is_registered_for_the_browser_layer(self):
        adapters = [
            element
            for element in self._zcml().iter()
            if element.tag.endswith("adapter")
            and element.get("factory") == ".adapters.ThumborImageFieldScales"
        ]

        assert len(adapters) == 1
        # plone.namedfile registers for (field, content, Interface). Naming
        # the layer makes ours strictly more specific, so it wins on
        # lookup without needing an overrides.zcml entry.
        assert "IPlonePgthumborLayer" in adapters[0].get("for")

    def test_it_adapts_what_the_parent_adapts(self):
        from plone.namedfile.adapters import ImageFieldScales
        from plone.pgthumbor.adapters import ThumborImageFieldScales

        assert issubclass(ThumborImageFieldScales, ImageFieldScales)

    def test_it_provides_the_same_interface(self):
        from plone.base.interfaces import IImageScalesFieldAdapter
        from plone.pgthumbor.adapters import ThumborImageFieldScales

        assert IImageScalesFieldAdapter.implementedBy(ThumborImageFieldScales)


def test_the_field_url_helper_is_shared_with_the_svg_fallback():
    """One spelling of "the original's own URL", not two."""
    from plone.pgthumbor import adapters

    import inspect

    source = inspect.getsource(adapters.ThumborImageFieldScales)

    # SVG already needs exactly this URL (issue #17), and a second
    # construction of it would be a second thing to keep in step.
    assert "_skip_type_fallback_url" in source


@pytest.mark.parametrize("width,height", [(0, 0), (None, None)])
def test_degenerate_dimensions_do_not_raise(width, height):
    adapter = _adapter()

    assert adapter.get_original_image_url("image", width, height)
