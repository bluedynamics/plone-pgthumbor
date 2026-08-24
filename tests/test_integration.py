"""Integration tests for plone.pgthumbor.

These tests verify ZCML registrations and end-to-end behavior
without requiring a full Plone site.
"""

from __future__ import annotations

from tests.conftest import env_override
from unittest.mock import MagicMock

import struct


SERVER = "http://thumbor:8888"
KEY = "test-secret-key"


def _mock_blob(oid_int=0x42, serial_int=0xFF):
    blob = MagicMock()
    blob._p_oid = struct.pack(">Q", oid_int)
    blob._p_serial = struct.pack(">Q", serial_int)
    return blob


def _mock_image_data(
    content_type="image/jpeg",
    width=800,
    height=600,
    derivative=None,
    source_ids=None,
):
    data = MagicMock()
    data.contentType = content_type
    data._width = width
    data._height = height
    data.getImageSize.return_value = (width, height)
    data._blob = _mock_blob()
    # Both attributes are set explicitly, and that is load-bearing.  getattr
    # on a MagicMock auto-creates a child mock rather than returning the
    # default, so source selection would find a Mock instead of None, u64()
    # would be handed one, and roughly forty green tests would fail at once.
    data._pgthumbor_source = derivative
    data._pgthumbor_source_info = {"source_ids": source_ids} if source_ids else None
    return data


class TestClassHierarchy:
    """Verify class relationships are correct."""

    def test_thumbor_image_scale_extends_image_scale(self):
        from plone.namedfile.scaling import ImageScale
        from plone.pgthumbor.scaling import ThumborImageScale

        assert issubclass(ThumborImageScale, ImageScale)

    def test_thumbor_image_scaling_extends_image_scaling(self):
        from plone.namedfile.scaling import ImageScaling
        from plone.pgthumbor.scaling import ThumborImageScaling

        assert issubclass(ThumborImageScaling, ImageScaling)

    def test_thumbor_scale_storage_extends_annotation_storage(self):
        from plone.pgthumbor.storage import ThumborScaleStorage
        from plone.scale.storage import AnnotationStorage

        assert issubclass(ThumborScaleStorage, AnnotationStorage)


class TestEndToEndScaleRedirect:
    """Test full flow: create scale → verify URL → redirect."""

    def test_scale_creates_thumbor_url_and_redirects(self, monkeypatch):
        from plone.pgthumbor.scaling import ThumborImageScale

        env_override(
            monkeypatch,
            PGTHUMBOR_SERVER_URL=SERVER,
            PGTHUMBOR_SECURITY_KEY=KEY,
        )

        ctx = MagicMock()
        ctx.absolute_url.return_value = "http://plone:8080/doc"
        request = MagicMock()
        data = _mock_image_data()

        scale = ThumborImageScale(
            ctx,
            request,
            data=data,
            fieldname="image",
            width=400,
            height=300,
            uid="image-400-abc123",
            mimetype="image/jpeg",
            mode="scale",
        )

        # URL should be a Thumbor URL
        assert scale.url.startswith(SERVER)
        assert "/42/ff" in scale.url
        assert "fit-in" in scale.url  # 'scale' mode → fit-in

        # index_html should redirect
        scale.index_html()
        request.response.redirect.assert_called_once_with(scale.url)

    def test_svg_bypasses_thumbor(self, monkeypatch):
        from plone.pgthumbor.scaling import ThumborImageScale

        env_override(
            monkeypatch,
            PGTHUMBOR_SERVER_URL=SERVER,
            PGTHUMBOR_SECURITY_KEY=KEY,
        )

        ctx = MagicMock()
        ctx.absolute_url.return_value = "http://plone:8080/doc"
        request = MagicMock()
        data = _mock_image_data(content_type="image/svg+xml")

        scale = ThumborImageScale(
            ctx,
            request,
            data=data,
            fieldname="image",
            width=400,
            height=300,
            uid="image-400-abc123",
            mimetype="image/svg+xml",
        )

        assert "@@images" in scale.url
        assert SERVER not in scale.url

    def test_tag_output_is_valid_html(self, monkeypatch):
        from plone.pgthumbor.scaling import ThumborImageScale

        env_override(
            monkeypatch,
            PGTHUMBOR_SERVER_URL=SERVER,
            PGTHUMBOR_SECURITY_KEY=KEY,
        )

        ctx = MagicMock()
        ctx.absolute_url.return_value = "http://plone:8080/doc"
        ctx.Title.return_value = "Test"
        request = MagicMock()
        data = _mock_image_data()

        scale = ThumborImageScale(
            ctx,
            request,
            data=data,
            fieldname="image",
            width=400,
            height=300,
            uid="image-400-abc123",
            mimetype="image/jpeg",
        )

        tag = scale.tag()
        assert tag.startswith("<img")
        assert 'src="' in tag
        assert SERVER in tag


class TestConfigInterface:
    """Test IThumborSettings interface."""

    def test_interface_has_expected_fields(self):
        from plone.pgthumbor.interfaces import IThumborSettings

        names = list(IThumborSettings.names())
        assert "smart_cropping" in names
        assert "paranoid_mode" in names
        assert "server_url" not in names
        assert "security_key" not in names
        assert "unsafe" not in names

    def test_config_dataclass_matches_interface(self):
        """Config dataclass should have matching fields."""
        from dataclasses import fields
        from plone.pgthumbor.config import ThumborConfig
        from plone.pgthumbor.interfaces import IThumborSettings

        dc_fields = {f.name for f in fields(ThumborConfig)}
        for name in IThumborSettings.names():
            assert name in dc_fields, f"ThumborConfig missing field: {name}"


class TestSubscriberRegistrations:
    """The derivative subscribers are wired, and never for every object.

    Parsing the ZCML is cheap and needs no Zope, but parsing alone would
    happily accept a handler that does not exist — so the dotted names are
    resolved here too.
    """

    @staticmethod
    def _subscribers():
        from pathlib import Path

        import plone.pgthumbor
        import xml.etree.ElementTree as ElementTree

        zcml = Path(plone.pgthumbor.__file__).parent / "configure.zcml"
        root = ElementTree.parse(zcml).getroot()
        return root.findall("{http://namespaces.zope.org/zope}subscriber")

    @staticmethod
    def _normalise(value):
        return " ".join(value.split())

    def test_both_lifecycle_events_are_registered(self):
        registrations = {
            self._normalise(element.get("for")): element.get("handler")
            for element in self._subscribers()
        }

        content = "plone.dexterity.interfaces.IDexterityContent"
        added = "zope.lifecycleevent.interfaces.IObjectAddedEvent"
        modified = "zope.lifecycleevent.interfaces.IObjectModifiedEvent"
        handler = ".subscribers.generate_source_derivatives"

        assert registrations[f"{content} {added}"] == handler
        assert registrations[f"{content} {modified}"] == handler

    def test_nothing_is_registered_for_every_object(self):
        # z3c.autoinclude loads this package for the whole instance, so a
        # "*" registration would fire in every site in the process,
        # including ones that never installed the add-on.
        for element in self._subscribers():
            assert "*" not in element.get("for")

    def test_the_handler_resolves(self):
        from plone.pgthumbor.subscribers import generate_source_derivatives

        handlers = {element.get("handler") for element in self._subscribers()}

        assert handlers == {".subscribers.generate_source_derivatives"}
        assert callable(generate_source_derivatives)

    def test_the_registered_interfaces_resolve(self):
        import importlib

        for element in self._subscribers():
            for dotted in self._normalise(element.get("for")).split():
                module, name = dotted.rsplit(".", 1)
                assert getattr(importlib.import_module(module), name) is not None
