"""Tests for Thumbor image scale view and @@images override."""

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


def _mock_image_data(content_type="image/jpeg", width=800, height=600):
    data = MagicMock()
    data.contentType = content_type
    data._width = width
    data._height = height
    data.getImageSize.return_value = (width, height)
    data._blob = _mock_blob()
    return data


def _setup_env(monkeypatch):
    env_override(
        monkeypatch,
        PGTHUMBOR_SERVER_URL=SERVER,
        PGTHUMBOR_SECURITY_KEY=KEY,
    )


class TestThumborImageScale:
    """Test ThumborImageScale view."""

    def test_url_is_thumbor_url(self, monkeypatch):
        from plone.pgthumbor.scaling import ThumborImageScale

        _setup_env(monkeypatch)
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
        )

        assert scale.url.startswith(SERVER)
        assert "/42/ff" in scale.url

    def test_index_html_redirects_302(self, monkeypatch):
        from plone.pgthumbor.scaling import ThumborImageScale

        _setup_env(monkeypatch)
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
        )

        scale.index_html()
        request.response.redirect.assert_called_once()
        redirect_url = request.response.redirect.call_args[0][0]
        assert redirect_url.startswith(SERVER)
        # 302 is the default for redirect()

    def test_tag_has_thumbor_src(self, monkeypatch):
        from plone.pgthumbor.scaling import ThumborImageScale

        _setup_env(monkeypatch)
        ctx = MagicMock()
        ctx.absolute_url.return_value = "http://plone:8080/doc"
        ctx.Title.return_value = "Test Doc"
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
        assert f'src="{scale.url}"' in tag

    def test_svg_fallback(self, monkeypatch):
        """SVG images should use standard Plone URLs, not Thumbor."""
        from plone.pgthumbor.scaling import ThumborImageScale

        _setup_env(monkeypatch)
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

        # SVG should use standard Plone URL
        assert SERVER not in scale.url
        assert "@@images" in scale.url

    def test_not_configured_falls_back(self, monkeypatch):
        """When Thumbor not configured, use standard Plone URL."""
        from plone.pgthumbor.scaling import ThumborImageScale

        env_override(monkeypatch)  # clear all env vars
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
        )

        # Falls back to standard URL
        assert "@@images" in scale.url

    def test_original_image_no_thumbor(self, monkeypatch):
        """Original image (no uid/scale) should use standard Plone URL."""
        from plone.pgthumbor.scaling import ThumborImageScale

        _setup_env(monkeypatch)
        ctx = MagicMock()
        ctx.absolute_url.return_value = "http://plone:8080/doc"
        request = MagicMock()
        data = _mock_image_data()

        # Original image: no uid, no width/height override
        scale = ThumborImageScale(
            ctx,
            request,
            data=data,
            fieldname="image",
        )

        # Original images served by Plone
        assert "@@images" in scale.url
        assert SERVER not in scale.url


class TestThumborImageScaling:
    """Test ThumborImageScaling (@@images view override)."""

    def test_uses_thumbor_scale_class(self):
        from plone.pgthumbor.scaling import ThumborImageScale
        from plone.pgthumbor.scaling import ThumborImageScaling

        assert ThumborImageScaling._scale_view_class is ThumborImageScale


class TestScaleModeMapping:
    """Test that Plone scale modes map correctly to Thumbor params."""

    def test_scale_mode_fit_in(self, monkeypatch):
        """Default 'scale' mode → fit-in in Thumbor URL."""
        from plone.pgthumbor.scaling import ThumborImageScale

        _setup_env(monkeypatch)
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

        assert "fit-in" in scale.url

    def test_cover_mode(self, monkeypatch):
        """Cover mode → no fit-in in Thumbor URL."""
        from plone.pgthumbor.scaling import ThumborImageScale

        _setup_env(monkeypatch)
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
            mode="cover",
        )

        assert "fit-in" not in scale.url

    def test_smart_cropping_enabled(self, monkeypatch):
        """When smart_cropping is on, URL should contain /smart/."""
        from plone.pgthumbor.scaling import ThumborImageScale

        env_override(
            monkeypatch,
            PGTHUMBOR_SERVER_URL=SERVER,
            PGTHUMBOR_SECURITY_KEY=KEY,
        )
        # Patch config in the scaling module (where it's imported)
        from plone.pgthumbor import config as config_mod
        from plone.pgthumbor import scaling as scaling_mod

        original_fn = config_mod.get_thumbor_config

        def mock_config():
            cfg = original_fn()
            if cfg:
                object.__setattr__(cfg, "smart_cropping", True)
            return cfg

        monkeypatch.setattr(scaling_mod, "get_thumbor_config", mock_config)

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
            mode="cover",
        )

        assert "/smart/" in scale.url
