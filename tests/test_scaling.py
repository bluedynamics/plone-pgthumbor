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


class TestNeedsAuthUrl:
    """Test _needs_auth_url() helper."""

    def _make_context(self, oid_int=0x42):
        ctx = MagicMock()
        ctx._p_oid = struct.pack(">Q", oid_int)
        return ctx

    def test_paranoid_mode_always_returns_true(self):
        """Paranoid mode → always return True regardless of allowedRolesAndUsers."""
        from plone.pgthumbor import scaling as scaling_mod

        ctx = self._make_context()
        # Even if Anonymous is in allowedRolesAndUsers, paranoid → True
        result = scaling_mod._needs_auth_url(ctx, 0x42, paranoid_mode=True)
        assert result is True

    def test_anonymous_in_allowedroles_returns_false(self, monkeypatch):
        """Anonymous in allowedRolesAndUsers → no auth needed → False."""
        from plone.pgthumbor import scaling as scaling_mod

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = {
            "is_anon": True
        }  # Anonymous present

        mock_pool = MagicMock()

        monkeypatch.setattr(
            "plone.pgthumbor.scaling.get_pool", lambda ctx: mock_pool, raising=False
        )
        monkeypatch.setattr(
            "plone.pgthumbor.scaling.get_request_connection",
            lambda pool: mock_conn,
            raising=False,
        )

        ctx = MagicMock()
        result = scaling_mod._needs_auth_url(ctx, 0x42)
        assert result is False

    def test_no_anonymous_in_allowedroles_returns_true(self, monkeypatch):
        """Anonymous NOT in allowedRolesAndUsers → auth needed → True."""
        from plone.pgthumbor import scaling as scaling_mod

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = {
            "is_anon": False
        }  # Anonymous absent

        mock_pool = MagicMock()

        monkeypatch.setattr(
            "plone.pgthumbor.scaling.get_pool", lambda ctx: mock_pool, raising=False
        )
        monkeypatch.setattr(
            "plone.pgthumbor.scaling.get_request_connection",
            lambda pool: mock_conn,
            raising=False,
        )

        ctx = MagicMock()
        result = scaling_mod._needs_auth_url(ctx, 0x42)
        assert result is True

    def test_not_in_catalog_returns_true(self, monkeypatch):
        """Object not in catalog (None row) → conservative → True."""
        from plone.pgthumbor import scaling as scaling_mod

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None  # not found

        mock_pool = MagicMock()

        monkeypatch.setattr(
            "plone.pgthumbor.scaling.get_pool", lambda ctx: mock_pool, raising=False
        )
        monkeypatch.setattr(
            "plone.pgthumbor.scaling.get_request_connection",
            lambda pool: mock_conn,
            raising=False,
        )

        ctx = MagicMock()
        result = scaling_mod._needs_auth_url(ctx, 0x42)
        assert result is True


class TestThumborImageScaleAuthUrl:
    """Test that ThumborImageScale passes content_zoid for restricted content."""

    def test_public_content_no_content_zoid(self, monkeypatch):
        """When _needs_auth_url returns False, URL is 2-segment."""
        from plone.pgthumbor import scaling as scaling_mod
        from plone.pgthumbor.scaling import ThumborImageScale

        _setup_env(monkeypatch)
        monkeypatch.setattr(
            scaling_mod, "_needs_auth_url", lambda ctx, zoid, paranoid_mode=False: False
        )

        ctx = MagicMock()
        ctx._p_oid = struct.pack(">Q", 0x42)
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
        assert not scale.url.endswith("/42/ff/" + format(0x42, "x"))
        # 2-segment: ends with blob_zoid/tid
        parts = scale.url.rstrip("/").split("/")
        # last two segments are hex (no 3rd segment for content_zoid)
        assert parts[-1] == "ff"

    def test_restricted_content_has_content_zoid(self, monkeypatch):
        """When _needs_auth_url returns True, URL is 3-segment."""
        from plone.pgthumbor import scaling as scaling_mod
        from plone.pgthumbor.scaling import ThumborImageScale

        _setup_env(monkeypatch)
        content_oid_int = 0x99
        monkeypatch.setattr(
            scaling_mod, "_needs_auth_url", lambda ctx, zoid, paranoid_mode=False: True
        )

        ctx = MagicMock()
        ctx._p_oid = struct.pack(">Q", content_oid_int)
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
        # 3-segment: ends with blob_zoid/tid/content_zoid
        assert scale.url.endswith(f"/42/ff/{content_oid_int:x}")

    def test_no_p_oid_skips_auth(self, monkeypatch):
        """Context without valid _p_oid → no content_zoid appended."""
        from plone.pgthumbor.scaling import ThumborImageScale

        _setup_env(monkeypatch)

        ctx = MagicMock()
        ctx._p_oid = None  # not persisted
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
        # Falls back to 2-segment URL
        parts = scale.url.rstrip("/").split("/")
        assert parts[-1] == "ff"

    def test_blob_ids_none_falls_back(self, monkeypatch):
        """When get_blob_ids returns None, use standard Plone URL."""
        from plone.pgthumbor import scaling as scaling_mod
        from plone.pgthumbor.scaling import ThumborImageScale

        _setup_env(monkeypatch)
        monkeypatch.setattr(scaling_mod, "get_blob_ids", lambda data: None)

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

        assert "@@images" in scale.url
        assert SERVER not in scale.url

    def test_index_html_fallback_without_thumbor(self, monkeypatch):
        """index_html() delegates to super when no Thumbor URL set."""
        from plone.pgthumbor.scaling import ThumborImageScale
        from unittest.mock import patch

        env_override(monkeypatch)  # no Thumbor config
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

        assert scale._thumbor_url is None
        # index_html delegates to super — don't redirect
        with patch.object(type(scale).__mro__[1], "index_html", return_value=b"image"):
            result = scale.index_html()
        request.response.redirect.assert_not_called()
        assert result == b"image"


class TestNeedsAuthUrlExceptionPaths:
    """Test _needs_auth_url() exception handling paths."""

    def test_registry_exception_falls_through_to_pg(self, monkeypatch):
        """Registry access failure → falls through to PG query."""
        from plone.pgthumbor import scaling as scaling_mod

        def broken_registry(iface):
            raise Exception("component architecture not loaded")

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = {"is_anon": True}
        mock_pool = MagicMock()

        monkeypatch.setattr(
            "plone.pgthumbor.scaling.queryUtility", broken_registry, raising=False
        )
        monkeypatch.setattr(
            "plone.pgthumbor.scaling.get_pool", lambda ctx: mock_pool, raising=False
        )
        monkeypatch.setattr(
            "plone.pgthumbor.scaling.get_request_connection",
            lambda pool: mock_conn,
            raising=False,
        )

        ctx = MagicMock()
        result = scaling_mod._needs_auth_url(ctx, 0x42)
        # Registry failed but PG says Anonymous → False
        assert result is False

    def test_pg_exception_returns_true(self, monkeypatch):
        """PG query failure → fail safe → True."""
        from plone.pgthumbor import scaling as scaling_mod

        monkeypatch.setattr(
            "plone.pgthumbor.scaling.queryUtility", lambda iface: None, raising=False
        )
        monkeypatch.setattr(
            "plone.pgthumbor.scaling.get_pool",
            lambda ctx: (_ for _ in ()).throw(Exception("no pool")),
            raising=False,
        )

        ctx = MagicMock()
        result = scaling_mod._needs_auth_url(ctx, 0x42)
        assert result is True
