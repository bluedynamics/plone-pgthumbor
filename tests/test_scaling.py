"""Tests for Thumbor image scale view and @@images override."""

from __future__ import annotations

from tests.conftest import env_override
from unittest.mock import MagicMock

import struct


SERVER = "http://thumbor:8888"
KEY = "test-secret-key"
_MISSING = object()


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
        """SVG images must use the original field URL — uid URLs never
        resolve under the volatile ThumborScaleStorage (issue #17)."""
        from plone.pgthumbor.scaling import ThumborImageScale

        _setup_env(monkeypatch)
        ctx = MagicMock()
        ctx.absolute_url.return_value = "http://plone:8080/doc"
        request = MagicMock()
        data = _mock_image_data(content_type="image/svg+xml")
        data.modified = None
        ctx._p_mtime = None

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

        assert SERVER not in scale.url
        assert scale.url == "http://plone:8080/doc/@@images/image"
        assert "abc123" not in scale.url

    def test_svg_fallback_cache_buster_from_field_modified(self, monkeypatch):
        """When the field carries a modification time, append ?v=<millis>."""
        from plone.pgthumbor.scaling import ThumborImageScale

        _setup_env(monkeypatch)
        ctx = MagicMock()
        ctx.absolute_url.return_value = "http://plone:8080/doc"
        data = _mock_image_data(content_type="image/svg+xml")
        data.modified = 1700000000.0

        scale = ThumborImageScale(
            ctx,
            MagicMock(),
            data=data,
            fieldname="image",
            width=400,
            height=300,
            uid="image-400-abc123",
            mimetype="image/svg+xml",
        )

        assert scale.url == "http://plone:8080/doc/@@images/image?v=1700000000000"

    def test_svg_fallback_cache_buster_from_p_mtime(self, monkeypatch):
        """Without field.modified, fall back to context._p_mtime."""
        from plone.pgthumbor.scaling import ThumborImageScale

        _setup_env(monkeypatch)
        ctx = MagicMock()
        ctx.absolute_url.return_value = "http://plone:8080/doc"
        ctx._p_mtime = 1600000000.0
        data = _mock_image_data(content_type="image/svg+xml")
        data.modified = None

        scale = ThumborImageScale(
            ctx,
            MagicMock(),
            data=data,
            fieldname="image",
            width=400,
            height=300,
            uid="image-400-abc123",
            mimetype="image/svg+xml",
        )

        assert scale.url == "http://plone:8080/doc/@@images/image?v=1600000000000"

    def test_svg_fallback_legacy_namedfile_path(self, monkeypatch):
        """The legacy (<8.0.0a2) __init__ branch must also emit the field
        URL — production runs plone.namedfile 7.x."""
        from plone.pgthumbor.scaling import ThumborImageScale

        import plone.pgthumbor.scaling as scaling_mod

        _setup_env(monkeypatch)
        monkeypatch.setattr(scaling_mod, "_HAS_SCALE_URL", False)

        from plone.pgthumbor.scaling import _default_scale_url

        monkeypatch.setattr(
            ThumborImageScale,
            "_scale_url",
            lambda self, uid, ext, base_url=None, scale_info=None: _default_scale_url(
                self.context, uid, ext, base_url
            ),
        )
        ctx = MagicMock()
        ctx.absolute_url.return_value = "http://plone:8080/doc"
        ctx._p_mtime = None
        data = _mock_image_data(content_type="image/svg+xml")
        data.modified = None

        scale = ThumborImageScale(
            ctx,
            MagicMock(),
            data=data,
            fieldname="image",
            width=400,
            height=300,
            uid="image-400-abc123",
            mimetype="image/svg+xml",
        )

        assert scale.url == "http://plone:8080/doc/@@images/image"

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

    def test_srcset_attribute_uses_thumbor_urls(self, monkeypatch):
        """HiDPI srcset entries must be Thumbor URLs, not dead uid URLs."""
        from plone.pgthumbor.scaling import ThumborImageScale

        _setup_env(monkeypatch)
        ctx = MagicMock()
        ctx.absolute_url.return_value = "http://plone:8080/doc"
        data = _mock_image_data()

        scale = ThumborImageScale(
            ctx,
            MagicMock(),
            data=data,
            fieldname="image",
            width=400,
            height=300,
            uid="image-400-abc123",
            mimetype="image/jpeg",
            srcset=[
                {"uid": "image-800-def456", "width": 800, "height": 600, "scale": 2},
            ],
        )

        attr = scale.srcset_attribute()
        assert attr.endswith(" 2x")
        assert attr.startswith(SERVER)
        assert "def456" not in attr

    def test_srcset_attribute_empty_for_svg(self, monkeypatch):
        """Vector images need no srcset — one URL fits all densities."""
        from plone.pgthumbor.scaling import ThumborImageScale

        _setup_env(monkeypatch)
        ctx = MagicMock()
        ctx.absolute_url.return_value = "http://plone:8080/doc"
        ctx._p_mtime = None
        data = _mock_image_data(content_type="image/svg+xml")
        data.modified = None

        scale = ThumborImageScale(
            ctx,
            MagicMock(),
            data=data,
            fieldname="image",
            width=400,
            height=300,
            uid="image-400-abc123",
            mimetype="image/svg+xml",
            srcset=[
                {"uid": "image-800-def456", "width": 800, "height": 600, "scale": 2},
            ],
        )

        assert scale.srcset_attribute() == ""

    def test_srcset_attribute_drops_unresolvable_entries(self, monkeypatch):
        """No Thumbor config → entries are dropped, never emitted as uid URLs."""
        from plone.pgthumbor.scaling import ThumborImageScale

        env_override(monkeypatch)
        ctx = MagicMock()
        ctx.absolute_url.return_value = "http://plone:8080/doc"
        data = _mock_image_data()

        scale = ThumborImageScale(
            ctx,
            MagicMock(),
            data=data,
            fieldname="image",
            width=400,
            height=300,
            uid="image-400-abc123",
            mimetype="image/jpeg",
            srcset=[
                {"uid": "image-800-def456", "width": 800, "height": 600, "scale": 2},
            ],
        )

        assert scale.srcset_attribute() == ""

    def test_srcset_attribute_skips_entry_without_scale_factor(self, monkeypatch):
        """A srcset entry lacking the 'scale' factor is skipped, not a KeyError."""
        from plone.pgthumbor.scaling import ThumborImageScale

        _setup_env(monkeypatch)
        ctx = MagicMock()
        ctx.absolute_url.return_value = "http://plone:8080/doc"
        data = _mock_image_data()

        scale = ThumborImageScale(
            ctx,
            MagicMock(),
            data=data,
            fieldname="image",
            width=400,
            height=300,
            uid="image-400-abc123",
            mimetype="image/jpeg",
            srcset=[{"uid": "image-800-def456", "width": 800, "height": 600}],
        )

        assert scale.srcset_attribute() == ""


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
        from plone.pgthumbor import url as url_mod
        from plone.pgthumbor.scaling import ThumborImageScale

        _setup_env(monkeypatch)
        monkeypatch.setattr(url_mod, "PLONE_SCALE_VERSION", "6.0.0")
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
        from plone.pgthumbor import url as url_mod

        monkeypatch.setattr(url_mod, "PLONE_SCALE_VERSION", "6.0.0")

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

    def test_paranoid_mode_always_returns_true(self, monkeypatch):
        """Paranoid mode → always True, even for Anonymous-readable content."""
        from plone.pgthumbor import scaling as scaling_mod

        monkeypatch.setattr(
            scaling_mod, "rolesForPermissionOn", lambda p, c: ("Anonymous", "Manager")
        )
        result = scaling_mod._needs_auth_url(MagicMock(), 0x42, paranoid_mode=True)
        assert result is True

    def test_anonymous_has_view_returns_false(self, monkeypatch):
        """'Anonymous' in View roles → content is publicly readable → False."""
        from plone.pgthumbor import scaling as scaling_mod

        monkeypatch.setattr(
            scaling_mod,
            "rolesForPermissionOn",
            lambda p, c: ("Anonymous", "Manager", "Authenticated"),
        )
        result = scaling_mod._needs_auth_url(MagicMock(), 0x42)
        assert result is False

    def test_anonymous_not_in_view_returns_true(self, monkeypatch):
        """'Anonymous' absent from View roles → restricted → True."""
        from plone.pgthumbor import scaling as scaling_mod

        monkeypatch.setattr(
            scaling_mod,
            "rolesForPermissionOn",
            lambda p, c: ("Manager", "Reviewer", "Editor"),
        )
        result = scaling_mod._needs_auth_url(MagicMock(), 0x42)
        assert result is True

    def test_private_content_returns_true(self, monkeypatch):
        """Private content (empty role set from ``_what_not_even_god_should_do``) → True."""
        from plone.pgthumbor import scaling as scaling_mod

        monkeypatch.setattr(scaling_mod, "rolesForPermissionOn", lambda p, c: [])
        result = scaling_mod._needs_auth_url(MagicMock(), 0x42)
        assert result is True

    def test_zoid_argument_is_unused(self, monkeypatch):
        """zoid is kept for back-compat; default None must work."""
        from plone.pgthumbor import scaling as scaling_mod

        monkeypatch.setattr(
            scaling_mod, "rolesForPermissionOn", lambda p, c: ("Anonymous",)
        )
        assert scaling_mod._needs_auth_url(MagicMock()) is False
        assert scaling_mod._needs_auth_url(MagicMock(), None) is False

    def test_does_not_touch_pg_pool(self, monkeypatch):
        """No DB/pool imports are invoked on the happy path (regression for issue #8)."""
        from plone.pgthumbor import scaling as scaling_mod

        monkeypatch.setattr(
            scaling_mod, "rolesForPermissionOn", lambda p, c: ("Anonymous",)
        )
        # If the function tries to reach plone.pgcatalog.pool, scaling_mod
        # doesn't import it anymore — confirm by attribute absence.
        assert not hasattr(scaling_mod, "get_pool")
        assert not hasattr(scaling_mod, "get_request_connection")

        scaling_mod._needs_auth_url(MagicMock(), 0x42)  # should not raise


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

    def test_permission_lookup_exception_returns_true(self, monkeypatch):
        """rolesForPermissionOn failure → fail safe → True."""
        from plone.pgthumbor import scaling as scaling_mod

        def _boom(_perm, _ctx):
            raise Exception("broken acquisition chain")

        monkeypatch.setattr(scaling_mod, "rolesForPermissionOn", _boom)

        result = scaling_mod._needs_auth_url(MagicMock(), 0x42)
        assert result is True


class TestDefaultScaleUrl:
    """Test _default_scale_url helper."""

    def test_with_base_url(self):
        from plone.pgthumbor.scaling import _default_scale_url

        ctx = MagicMock()
        url = _default_scale_url(ctx, "uid123", "jpeg", base_url="http://plone/doc")
        assert url == "http://plone/doc/@@images/uid123.jpeg"
        ctx.absolute_url.assert_not_called()

    def test_without_base_url(self):
        from plone.pgthumbor.scaling import _default_scale_url

        ctx = MagicMock()
        ctx.absolute_url.return_value = "http://plone/doc"
        url = _default_scale_url(ctx, "uid123", "jpeg")
        assert url == "http://plone/doc/@@images/uid123.jpeg"


class TestThumborImageScaleScaleUrl:
    """Test ThumborImageScale._scale_url method."""

    def test_scale_url_with_scale_info(self, monkeypatch):
        """_scale_url with scale_info generates a Thumbor URL."""
        from plone.pgthumbor.scaling import ThumborImageScale

        _setup_env(monkeypatch)
        ctx = MagicMock()
        ctx._p_oid = struct.pack(">Q", 0x42)
        ctx.absolute_url.return_value = "http://plone:8080/doc"
        request = MagicMock()
        data = _mock_image_data()

        monkeypatch.setattr(
            "plone.pgthumbor.scaling._needs_auth_url",
            lambda ctx, zoid, paranoid_mode=False: False,
        )

        scale = ThumborImageScale(ctx, request, data=data, fieldname="image")
        scale.data = data

        result = scale._scale_url(
            "uid123",
            "jpeg",
            scale_info={"uid": "uid123", "width": 400, "height": 300, "mode": "scale"},
        )
        assert result.startswith(SERVER)
        assert scale._thumbor_url == result

    def test_scale_url_fallback_no_scale_info(self, monkeypatch):
        """_scale_url without scale_info falls back to default URL."""
        from plone.pgthumbor.scaling import ThumborImageScale

        _setup_env(monkeypatch)
        ctx = MagicMock()
        ctx.absolute_url.return_value = "http://plone:8080/doc"
        request = MagicMock()
        data = _mock_image_data()

        scale = ThumborImageScale(ctx, request, data=data, fieldname="image")
        scale.data = data

        result = scale._scale_url("uid123", "jpeg")
        assert "@@images/uid123.jpeg" in result


class TestThumborImageScalingScaleUrl:
    """Test ThumborImageScaling._scale_url method."""

    def test_scale_url_with_fieldname(self, monkeypatch):
        """_scale_url with fieldname in scale_info generates Thumbor URL."""
        from plone.pgthumbor.scaling import ThumborImageScaling

        _setup_env(monkeypatch)
        ctx = MagicMock()
        ctx._p_oid = struct.pack(">Q", 0x42)
        ctx.absolute_url.return_value = "http://plone:8080/doc"
        ctx.image = _mock_image_data()
        request = MagicMock()

        monkeypatch.setattr(
            "plone.pgthumbor.scaling._needs_auth_url",
            lambda ctx, zoid, paranoid_mode=False: False,
        )

        scaling = ThumborImageScaling(ctx, request)
        result = scaling._scale_url(
            "uid123",
            "jpeg",
            scale_info={
                "fieldname": "image",
                "uid": "uid123",
                "width": 400,
                "height": 300,
                "mode": "scale",
            },
        )
        assert result.startswith(SERVER)

    def test_scale_url_fallback_no_fieldname(self, monkeypatch):
        """_scale_url without fieldname falls back to default URL."""
        from plone.pgthumbor.scaling import ThumborImageScaling

        _setup_env(monkeypatch)
        ctx = MagicMock()
        ctx.absolute_url.return_value = "http://plone:8080/doc"
        request = MagicMock()

        scaling = ThumborImageScaling(ctx, request)
        result = scaling._scale_url("uid123", "jpeg")
        assert "@@images/uid123.jpeg" in result

    def test_scale_url_svg_returns_field_url(self, monkeypatch):
        """ThumborImageScaling._scale_url must not emit uid URLs for SVG."""
        from plone.pgthumbor.scaling import ThumborImageScaling

        _setup_env(monkeypatch)
        ctx = MagicMock()
        ctx.absolute_url.return_value = "http://plone:8080/doc"
        ctx._p_mtime = None
        svg = _mock_image_data(content_type="image/svg+xml")
        svg.modified = None
        ctx.logo = svg
        view = ThumborImageScaling(ctx, MagicMock())

        uid = "logo-200-" + "a" * 32
        url = view._scale_url(
            uid,
            "svg",
            scale_info={"fieldname": "logo", "width": 200, "height": 200, "uid": uid},
        )

        assert url == "http://plone:8080/doc/@@images/logo"


class TestGetCrop:
    """Test _get_crop() helper."""

    def test_no_provider_returns_none(self, monkeypatch):
        """No ICropProvider adapter → None."""
        from plone.pgthumbor import scaling as scaling_mod

        monkeypatch.setattr(scaling_mod, "queryAdapter", lambda ctx, iface: None)
        result = scaling_mod._get_crop(
            MagicMock(), "image", {"key": (("scale", "preview"),)}
        )
        assert result is None

    def test_with_provider_returns_crop(self, monkeypatch):
        """ICropProvider returns a 4-tuple → converted to nested tuple."""
        from plone.pgthumbor import scaling as scaling_mod

        provider = MagicMock()
        provider.get_crop.return_value = (10, 20, 300, 400)
        monkeypatch.setattr(scaling_mod, "queryAdapter", lambda ctx, iface: provider)

        result = scaling_mod._get_crop(
            MagicMock(),
            "image",
            {"key": (("scale", "preview"),)},
        )
        assert result == ((10, 20), (300, 400))
        provider.get_crop.assert_called_once_with("image", "preview")

    def test_no_scale_name_returns_none(self, monkeypatch):
        """scale_info without 'key' → no scale name → None."""
        from plone.pgthumbor import scaling as scaling_mod

        provider = MagicMock()
        monkeypatch.setattr(scaling_mod, "queryAdapter", lambda ctx, iface: provider)

        result = scaling_mod._get_crop(MagicMock(), "image", {})
        assert result is None
        provider.get_crop.assert_not_called()

    def test_provider_returns_none(self, monkeypatch):
        """ICropProvider returns None → None."""
        from plone.pgthumbor import scaling as scaling_mod

        provider = MagicMock()
        provider.get_crop.return_value = None
        monkeypatch.setattr(scaling_mod, "queryAdapter", lambda ctx, iface: provider)

        result = scaling_mod._get_crop(
            MagicMock(),
            "image",
            {"key": (("scale", "preview"),)},
        )
        assert result is None

    def test_no_fieldname_returns_none(self, monkeypatch):
        """Empty fieldname → None."""
        from plone.pgthumbor import scaling as scaling_mod

        provider = MagicMock()
        monkeypatch.setattr(scaling_mod, "queryAdapter", lambda ctx, iface: provider)

        result = scaling_mod._get_crop(
            MagicMock(),
            "",
            {"key": (("scale", "preview"),)},
        )
        assert result is None

    def test_srcset_density_factor_is_not_read_as_a_scale_name(self, monkeypatch):
        """A srcset entry's dict-level "scale" is the HiDPI density factor
        (plone.namedfile's calculate_srcset does
        ``scale_src["scale"] = hdScale["scale"]`` and never forwards a scale
        name into it), not a scale name. The key carries no "scale" at all
        for such entries, so the crop lookup must not fall back to reading
        the density factor as one — it must yield no crop rather than call
        the provider with an integer."""
        from plone.pgthumbor import scaling as scaling_mod

        provider = MagicMock()
        monkeypatch.setattr(scaling_mod, "queryAdapter", lambda ctx, iface: provider)

        result = scaling_mod._get_crop(
            MagicMock(),
            "image",
            {"key": (("fieldname", "image"), ("width", 800)), "scale": 2},
        )
        assert result is None
        provider.get_crop.assert_not_called()


class TestBuildThumborUrlWithCrop:
    """Test _build_thumbor_url() crop behavior."""

    def test_crop_forces_fit_in_and_disables_smart(self, monkeypatch):
        """When crop is set, fit_in=True and smart=False regardless of mode."""
        from plone.pgthumbor import scaling as scaling_mod

        _setup_env(monkeypatch)
        monkeypatch.setattr(
            scaling_mod, "_needs_auth_url", lambda ctx, zoid, paranoid_mode=False: False
        )

        ctx = MagicMock()
        ctx._p_oid = struct.pack(">Q", 0x42)
        data = _mock_image_data()
        crop = ((10, 20), (300, 400))

        url = scaling_mod._build_thumbor_url(ctx, data, 400, 300, "cover", crop=crop)
        assert url is not None
        # With crop, URL should have crop coords and fit-in, no smart
        assert "10x20:300x400" in url
        assert "fit-in" in url
        assert "/smart/" not in url

    def test_no_crop_keeps_mode_behavior(self, monkeypatch):
        """Without crop, mode params unchanged."""
        from plone.pgthumbor import scaling as scaling_mod
        from plone.pgthumbor import url as url_mod

        _setup_env(monkeypatch)
        monkeypatch.setattr(url_mod, "PLONE_SCALE_VERSION", "6.0.0")
        monkeypatch.setattr(
            scaling_mod, "_needs_auth_url", lambda ctx, zoid, paranoid_mode=False: False
        )

        ctx = MagicMock()
        ctx._p_oid = struct.pack(">Q", 0x42)
        data = _mock_image_data()

        url = scaling_mod._build_thumbor_url(ctx, data, 400, 300, "cover")
        assert url is not None
        assert "fit-in" not in url


class TestCropInScaleUrl:
    """Test crop integration in _scale_url methods."""

    def test_scale_url_with_crop(self, monkeypatch):
        """ThumborImageScale._scale_url includes crop coordinates."""
        from plone.pgthumbor import scaling as scaling_mod
        from plone.pgthumbor.scaling import ThumborImageScale

        _setup_env(monkeypatch)
        monkeypatch.setattr(
            scaling_mod, "_needs_auth_url", lambda ctx, zoid, paranoid_mode=False: False
        )
        # Mock crop provider
        provider = MagicMock()
        provider.get_crop.return_value = (10, 20, 300, 400)
        monkeypatch.setattr(scaling_mod, "queryAdapter", lambda ctx, iface: provider)

        ctx = MagicMock()
        ctx._p_oid = struct.pack(">Q", 0x42)
        ctx.absolute_url.return_value = "http://plone:8080/doc"
        request = MagicMock()
        data = _mock_image_data()

        scale = ThumborImageScale(ctx, request, data=data, fieldname="image")
        scale.data = data

        result = scale._scale_url(
            "uid123",
            "jpeg",
            scale_info={
                "uid": "uid123",
                "width": 400,
                "height": 300,
                "mode": "scale",
                "fieldname": "image",
                "key": (("scale", "preview"),),
            },
        )
        assert result.startswith(SERVER)
        assert "10x20:300x400" in result

    def test_scaling_scale_url_with_crop(self, monkeypatch):
        """ThumborImageScaling._scale_url includes crop coordinates."""
        from plone.pgthumbor import scaling as scaling_mod
        from plone.pgthumbor.scaling import ThumborImageScaling

        _setup_env(monkeypatch)
        monkeypatch.setattr(
            scaling_mod, "_needs_auth_url", lambda ctx, zoid, paranoid_mode=False: False
        )
        provider = MagicMock()
        provider.get_crop.return_value = (10, 20, 300, 400)
        monkeypatch.setattr(scaling_mod, "queryAdapter", lambda ctx, iface: provider)

        ctx = MagicMock()
        ctx._p_oid = struct.pack(">Q", 0x42)
        ctx.absolute_url.return_value = "http://plone:8080/doc"
        ctx.image = _mock_image_data()
        request = MagicMock()

        scaling = ThumborImageScaling(ctx, request)
        result = scaling._scale_url(
            "uid123",
            "jpeg",
            scale_info={
                "fieldname": "image",
                "uid": "uid123",
                "width": 400,
                "height": 300,
                "mode": "scale",
                "key": (("scale", "preview"),),
            },
        )
        assert result.startswith(SERVER)
        assert "10x20:300x400" in result


class TestThumborImageScalingSrcset:
    """srcset() must never emit uid URLs (issue #17)."""

    def _make_view(self, monkeypatch, content_type="image/jpeg"):
        from plone.namedfile.scaling import ImageScaling
        from plone.pgthumbor.scaling import ThumborImageScaling

        _setup_env(monkeypatch)
        ctx = MagicMock()
        ctx.absolute_url.return_value = "http://plone:8080/doc"
        ctx.Title.return_value = "Doc"
        ctx.image = _mock_image_data(content_type=content_type)
        view = ThumborImageScaling(ctx, MagicMock())
        monkeypatch.setattr(
            ImageScaling,
            "available_sizes",
            {"preview": (400, 400), "large": (800, 800)},
            raising=False,
        )
        view.getImageSize = lambda fieldname: (800, 600)
        return view

    def test_srcset_uses_scale_view_urls(self, monkeypatch):
        """Each configured size must be requested — and rendered — via its
        own scale view, not a single mocked return_value that would mask
        per-size behavior."""
        view = self._make_view(monkeypatch)
        calls = []

        def fake_scale(fieldname=None, width=None, height=None, scale=None, **kw):
            calls.append(
                {
                    "fieldname": fieldname,
                    "width": width,
                    "height": height,
                    "scale": scale,
                }
            )
            fake = MagicMock()
            resolved_width = width or {"preview": 400, "large": 800}.get(scale, 400)
            fake.url = f"{SERVER}/signed/{resolved_width}x0/42/ff"
            fake.width = resolved_width
            fake.height = resolved_width * 3 // 4
            return fake

        view.scale = MagicMock(side_effect=fake_scale)

        tag = view.srcset(fieldname="image", scale_in_src="preview")

        assert SERVER in tag
        assert "@@images/image-" not in tag
        assert "400w" in tag
        assert "800w" in tag
        requested_widths = {c["width"] for c in calls if c["width"] is not None}
        assert requested_widths == {400, 800}
        assert any(c["scale"] == "preview" for c in calls)

    def test_srcset_svg_delegates_to_tag(self, monkeypatch):
        from plone.namedfile.scaling import _marker

        view = self._make_view(monkeypatch, content_type="image/svg+xml")
        view.tag = MagicMock(return_value="<img/>")

        result = view.srcset(fieldname="image")

        assert result == "<img/>"
        view.tag.assert_called_once_with(
            fieldname="image", alt=_marker, css_class=None, title=_marker
        )

    def test_srcset_unresolvable_src_scale_returns_none(self, monkeypatch):
        """An unresolvable scale_in_src must not raise AttributeError on
        ``None.url`` — mirrors the parent's ``if scale is None: return
        None`` guard (issue #17 fix round 1, Finding 1)."""
        view = self._make_view(monkeypatch)
        view.scale = MagicMock(return_value=None)

        assert view.srcset(fieldname="image", scale_in_src="nonexistent") is None

    def test_srcset_undersized_original_backfills_original_entry(self, monkeypatch):
        """An original smaller than every configured scale must still get a
        non-empty srcset, back-filled with an original-size scale-view URL
        (issue #17 fix round 1, Finding 2)."""
        view = self._make_view(monkeypatch)
        view.getImageSize = lambda fieldname: (100, 80)
        calls = []

        def fake_scale(fieldname=None, scale=None, height=None, width=None, **kw):
            calls.append({"scale": scale, "width": width, "height": height})
            fake = MagicMock()
            fake.url = f"{SERVER}/signed/{width or scale}/42/ff"
            fake.width = width or 100
            fake.height = height or 80
            return fake

        view.scale = MagicMock(side_effect=fake_scale)

        tag = view.srcset(fieldname="image", scale_in_src="preview")

        assert "srcset=" in tag
        assert f"{SERVER}/signed/100/42/ff 100w" in tag


class TestScaleParam:
    """Reading call parameters back out of a plone.scale info dict."""

    def _key(self, **parameters):
        """A key tuple shaped the way plone.scale's hash() builds it."""
        return tuple(sorted(parameters.items()))

    def test_reads_from_the_key_tuple(self):
        from plone.pgthumbor.scaling import _scale_param

        info = {"key": self._key(mode="cover", width=400)}
        assert _scale_param(info, "mode", "scale") == "cover"

    def test_key_wins_over_the_info_dict(self):
        """plone/plone.scale#156 will add mode to the dict; the key holds the
        raw value hash_key hashed, so it stays authoritative."""
        from plone.pgthumbor.scaling import _scale_param

        info = {"key": self._key(mode="contain"), "mode": "scale"}
        assert _scale_param(info, "mode", "scale") == "contain"

    def test_falls_back_to_the_info_dict(self):
        from plone.pgthumbor.scaling import _scale_param

        assert _scale_param({"mode": "cover"}, "mode", "scale") == "cover"

    def test_default_when_absent_everywhere(self):
        from plone.pgthumbor.scaling import _scale_param

        assert _scale_param({"key": self._key(width=400)}, "mode", "scale") == "scale"

    def test_none_info_returns_the_default(self):
        from plone.pgthumbor.scaling import _scale_param

        assert _scale_param(None, "mode", "scale") == "scale"

    def test_malformed_key_does_not_raise(self):
        from plone.pgthumbor.scaling import _scale_param

        assert _scale_param({"key": ("hash",)}, "mode", "scale") == "scale"

    def test_explicit_none_in_the_key_is_returned(self):
        from plone.pgthumbor.scaling import _scale_param

        info = {"key": self._key(mode=None)}
        assert _scale_param(info, "mode", "scale") is None


class TestModeReachesTheUrl:
    """Regression for the gap that made every URL fit-in (issue #21).

    These assert that the mode is *threaded through*, not what the mapping
    does with it. Which mode implies fit-in is pinned separately in
    tests/test_url.py, where it is derived from Pillow rather than restated,
    so these stay correct if plone/plone.scale#78 changes the mapping.
    """

    def _url(self, monkeypatch, key_mode=_MISSING, info_mode=_MISSING):
        from plone.pgthumbor.scaling import ThumborImageScale

        _setup_env(monkeypatch)
        ctx = MagicMock()
        ctx.absolute_url.return_value = "http://plone:8080/doc"
        info = {
            "data": _mock_image_data(),
            "fieldname": "image",
            "width": 400,
            "height": 200,
            "uid": "image-400-abc123",
            "mimetype": "image/jpeg",
        }
        if key_mode is not _MISSING:
            info["key"] = tuple(
                sorted(
                    {
                        "fieldname": "image",
                        "width": 400,
                        "height": 200,
                        "mode": key_mode,
                    }.items()
                )
            )
        if info_mode is not _MISSING:
            info["mode"] = info_mode
        return ThumborImageScale(ctx, MagicMock(), **info).url

    def test_mode_in_the_key_changes_the_url(self, monkeypatch):
        """The defect: pre_scale puts mode only in the key, so this used to
        make no difference at all and every URL came out fit-in."""
        contain = self._url(monkeypatch, key_mode="contain")
        plain = self._url(monkeypatch, key_mode="scale")

        assert contain != plain

    def test_key_mode_agrees_with_an_explicit_mode(self, monkeypatch):
        for mode in ("scale", "cover", "contain"):
            from_key = self._url(monkeypatch, key_mode=mode)
            explicit = self._url(monkeypatch, info_mode=mode)
            assert from_key == explicit, mode

    def test_missing_mode_everywhere_defaults_to_scale(self, monkeypatch):
        assert self._url(monkeypatch) == self._url(monkeypatch, info_mode="scale")

    def test_mode_alias_is_normalised(self, monkeypatch):
        """hash_key hashes the raw alias the caller passed, so the URL for
        "scale-crop-to-fit" must equal the one for "contain"."""
        alias = self._url(monkeypatch, key_mode="scale-crop-to-fit")
        canonical = self._url(monkeypatch, key_mode="contain")

        assert alias == canonical


class TestModeReachesTheUrlOnTheLegacyPath:
    """Same regression as TestModeReachesTheUrl, forced onto the legacy
    (<8.0.0a2) ``__init__`` branch of ``ThumborImageScale`` — the
    ``if not _HAS_SCALE_URL`` block in ``scaling.py``.

    This repo has no lockfile, and this dev venv resolves plone.namedfile
    8.x, where ``_HAS_SCALE_URL`` is True: ``ImageScale.__init__`` already
    calls ``self._scale_url(...)``, so TestModeReachesTheUrl only ever
    exercises that 8.x call site. Production runs plone.namedfile 7.x,
    where ``_scale_url`` does not exist on the parent at all and the legacy
    ``__init__`` block is the *only* call site that runs. Without a test
    that forces this branch, the mode fix has zero executed coverage on the
    path production actually uses. (Follows the pattern already used by
    test_svg_fallback_legacy_namedfile_path above.)
    """

    def _url(self, monkeypatch, key_mode=_MISSING, info_mode=_MISSING):
        from plone.pgthumbor.scaling import ThumborImageScale

        import plone.pgthumbor.scaling as scaling_mod

        _setup_env(monkeypatch)
        monkeypatch.setattr(scaling_mod, "_HAS_SCALE_URL", False)
        # Stub out the override so the 8.x call site (already covered by
        # TestModeReachesTheUrl) cannot itself supply the URL — only the
        # manual legacy-path block in __init__ may do so here.
        monkeypatch.setattr(
            ThumborImageScale,
            "_scale_url",
            lambda self, uid, ext, base_url=None, scale_info=None: None,
        )
        ctx = MagicMock()
        ctx.absolute_url.return_value = "http://plone:8080/doc"
        info = {
            "data": _mock_image_data(),
            "fieldname": "image",
            "width": 400,
            "height": 200,
            "uid": "image-400-abc123",
            "mimetype": "image/jpeg",
        }
        if key_mode is not _MISSING:
            info["key"] = tuple(
                sorted(
                    {
                        "fieldname": "image",
                        "width": 400,
                        "height": 200,
                        "mode": key_mode,
                    }.items()
                )
            )
        if info_mode is not _MISSING:
            info["mode"] = info_mode
        return ThumborImageScale(ctx, MagicMock(), **info).url

    def test_mode_in_the_key_changes_the_url(self, monkeypatch):
        contain = self._url(monkeypatch, key_mode="contain")
        plain = self._url(monkeypatch, key_mode="scale")

        assert contain != plain

    def test_key_mode_agrees_with_an_explicit_mode(self, monkeypatch):
        for mode in ("scale", "cover", "contain"):
            from_key = self._url(monkeypatch, key_mode=mode)
            explicit = self._url(monkeypatch, info_mode=mode)
            assert from_key == explicit, mode


class TestModeReachesTheUrlInSrcsetAttribute:
    """Same regression as TestModeReachesTheUrl, on srcset_attribute's own
    call site (scaling.py:291).

    Its argument is a ``calculate_srcset`` entry, not a full info dict --
    that shape difference is exactly what the bug commit 7faa284 had to fix
    for ``_get_crop`` (a srcset entry's top-level ``"scale"`` is the HiDPI
    density factor, not a scale name), so this call site needs its own
    coverage rather than borrowing TestModeReachesTheUrl's.
    """

    def _attr(self, monkeypatch, key_mode):
        from plone.pgthumbor.scaling import ThumborImageScale

        _setup_env(monkeypatch)
        ctx = MagicMock()
        ctx.absolute_url.return_value = "http://plone:8080/doc"
        data = _mock_image_data()

        key = tuple(
            sorted(
                {
                    "fieldname": "image",
                    "width": 800,
                    "height": 600,
                    "mode": key_mode,
                }.items()
            )
        )
        scale = ThumborImageScale(
            ctx,
            MagicMock(),
            data=data,
            fieldname="image",
            width=400,
            height=300,
            uid="image-400-abc123",
            mimetype="image/jpeg",
            srcset=[
                {
                    "uid": "image-800-def456",
                    "width": 800,
                    "height": 600,
                    "scale": 2,
                    "key": key,
                },
            ],
        )
        return scale.srcset_attribute()

    def test_mode_in_the_entry_key_changes_the_url(self, monkeypatch):
        contain = self._attr(monkeypatch, "contain")
        plain = self._attr(monkeypatch, "scale")

        assert contain != plain


class TestModeReachesTheUrlInScalingScaleUrl:
    """Same regression as TestModeReachesTheUrl, on
    ThumborImageScaling._scale_url (scaling.py:322) -- the @@images-level
    override used for srcset-minted URLs, a different call site than
    ThumborImageScale._scale_url covered by TestModeReachesTheUrl above.
    """

    def _url(self, monkeypatch, key_mode):
        from plone.pgthumbor.scaling import ThumborImageScaling

        _setup_env(monkeypatch)
        ctx = MagicMock()
        ctx._p_oid = struct.pack(">Q", 0x42)
        ctx.absolute_url.return_value = "http://plone:8080/doc"
        ctx.image = _mock_image_data()
        request = MagicMock()

        monkeypatch.setattr(
            "plone.pgthumbor.scaling._needs_auth_url",
            lambda ctx, zoid, paranoid_mode=False: False,
        )

        scaling = ThumborImageScaling(ctx, request)
        key = tuple(
            sorted(
                {
                    "fieldname": "image",
                    "width": 400,
                    "height": 200,
                    "mode": key_mode,
                }.items()
            )
        )
        return scaling._scale_url(
            "uid123",
            "jpeg",
            scale_info={
                "fieldname": "image",
                "uid": "uid123",
                "width": 400,
                "height": 200,
                "key": key,
            },
        )

    def test_mode_in_the_key_changes_the_url(self, monkeypatch):
        contain = self._url(monkeypatch, "contain")
        plain = self._url(monkeypatch, "scale")

        assert contain != plain


def _mock_derivative(oid_int=0x99, serial_int=0xAA, size=(800, 600)):
    """A stand-in for the NamedBlobImage stored as _pgthumbor_source."""
    derivative = MagicMock()
    derivative.contentType = "image/jpeg"
    derivative._blob = _mock_blob(oid_int, serial_int)
    derivative.getImageSize.return_value = size
    return derivative


class _PlainImage:
    """A field value that is not a mock and has no derivative attribute.

    Guards the ``getattr(..., None)`` default itself: a MagicMock would
    return a child mock and hide a missing default.
    """

    contentType = "image/jpeg"

    def __init__(self):
        self._blob = _mock_blob()

    def getImageSize(self):
        return (800, 600)


class TestSourceSelection:
    """Which blob the Thumbor URL names: the derivative, or the original."""

    def _url(self, monkeypatch, data, **kwargs):
        from plone.pgthumbor import scaling as scaling_mod

        _setup_env(monkeypatch)
        monkeypatch.setattr(
            scaling_mod, "_needs_auth_url", lambda ctx, zoid, paranoid_mode=False: False
        )
        ctx = MagicMock()
        ctx._p_oid = struct.pack(">Q", 0x42)
        return scaling_mod._build_thumbor_url(ctx, data, 400, 300, "scale", **kwargs)

    def test_the_derivative_is_used_when_there_is_one(self, monkeypatch):
        data = _mock_image_data(derivative=_mock_derivative())

        url = self._url(monkeypatch, data)

        assert "/99/aa" in url
        assert "/42/ff" not in url

    def test_the_original_is_used_when_the_sentinel_is_none(self, monkeypatch):
        url = self._url(monkeypatch, _mock_image_data())

        assert "/42/ff" in url

    def test_the_original_is_used_when_the_attribute_is_absent(self, monkeypatch):
        url = self._url(monkeypatch, _PlainImage())

        assert "/42/ff" in url

    def test_no_fallback_when_the_derivative_has_no_committed_tid(self, monkeypatch):
        """The absent fallback IS the feature.  Do not "fix" this.

        A derivative created in the current transaction has no TID yet.
        Substituting the original here would bake a permanent, direct,
        signed Thumbor URL to an image that may exceed MAX_PIXELS into
        catalog metadata — and a browser fetches those without Plone in the
        path, so the uid-healing route can never repair it.  Emitting no
        URL yields a uid fallback instead, which heals on the next render
        and is corrected for good by the backfill's second phase.
        """
        uncommitted = _mock_derivative(serial_int=0)
        data = _mock_image_data(derivative=uncommitted)

        url = self._url(monkeypatch, data)

        assert url is None

    def test_recorded_ids_matching_the_original_keep_the_derivative(self, monkeypatch):
        data = _mock_image_data(derivative=_mock_derivative(), source_ids=(0x42, 0xFF))

        assert "/99/aa" in self._url(monkeypatch, data)

    def test_recorded_ids_mismatching_the_original_drop_the_derivative(
        self, monkeypatch
    ):
        """Catches an in-place ``image.data = ...``.

        NamedBlobImage.data is a settable property, and migration scripts
        and transmogrifier blueprints use it.  That replaces the bytes
        without replacing the object, so the structural-invalidation
        argument does not apply and only the recorded ids notice.
        """
        data = _mock_image_data(
            derivative=_mock_derivative(), source_ids=(0x1234, 0x5678)
        )

        url = self._url(monkeypatch, data)

        assert "/42/ff" in url
        assert "/99/aa" not in url

    def test_recorded_ids_of_none_keep_the_derivative(self, monkeypatch):
        """None means "not comparable", not "mismatched".

        A derivative generated before its original was committed records
        None.  Reading that as a mismatch would make every freshly
        uploaded image permanently refuse the derivative it just made.
        """
        data = _mock_image_data(derivative=_mock_derivative(), source_ids=None)

        assert "/99/aa" in self._url(monkeypatch, data)

    def test_the_skip_type_decision_reads_the_originals_content_type(self, monkeypatch):
        # The derivative is a JPEG; the original is the SVG.  Deciding on
        # the derivative would send vector images through Thumbor.
        data = _mock_image_data(
            content_type="image/svg+xml", derivative=_mock_derivative()
        )

        assert self._url(monkeypatch, data) is None

    def test_content_zoid_still_comes_from_the_context(self, monkeypatch):
        from plone.pgthumbor import scaling as scaling_mod

        _setup_env(monkeypatch)
        monkeypatch.setattr(
            scaling_mod, "_needs_auth_url", lambda ctx, zoid, paranoid_mode=False: True
        )
        ctx = MagicMock()
        ctx._p_oid = struct.pack(">Q", 0x7B)
        data = _mock_image_data(derivative=_mock_derivative())

        url = scaling_mod._build_thumbor_url(ctx, data, 400, 300, "scale")

        # Blob ids from the derivative, content zoid still from the context.
        assert "/99/aa/7b" in url

    def test_selection_reaches_srcset_attribute(self, monkeypatch):
        from plone.pgthumbor.scaling import ThumborImageScale

        _setup_env(monkeypatch)
        ctx = MagicMock()
        ctx.absolute_url.return_value = "http://plone:8080/doc"
        data = _mock_image_data(derivative=_mock_derivative())

        scale = ThumborImageScale(
            ctx,
            MagicMock(),
            data=data,
            fieldname="image",
            width=400,
            height=300,
            uid="image-400-abc123",
            mimetype="image/jpeg",
            srcset=[
                {"uid": "image-800-def456", "width": 800, "height": 600, "scale": 2},
            ],
        )

        assert "/99/aa" in scale.srcset_attribute()

    def test_selection_reaches_the_scaling_scale_url(self, monkeypatch):
        from plone.pgthumbor.scaling import ThumborImageScaling

        _setup_env(monkeypatch)
        ctx = MagicMock()
        ctx.image = _mock_image_data(derivative=_mock_derivative())
        view = ThumborImageScaling(ctx, MagicMock())

        url = view._scale_url(
            "image-400-abc",
            "jpeg",
            scale_info={"fieldname": "image", "width": 400, "height": 300},
        )

        assert "/99/aa" in url


class TestRescaleCrop:
    """Mapping a crop box from original pixels into derivative pixels.

    plone.app.imagecropping stores boxes in the original's coordinates and
    passes them through untouched.  Against a 4000 px derivative of an
    11811 px original an unscaled box cuts into empty space.
    """

    def test_both_axes_get_their_own_factor(self):
        from plone.pgthumbor.scaling import _rescale_crop

        # Deliberately non-proportional so a single shared factor cannot
        # produce this answer: x halves, y quarters.
        box = _rescale_crop(((1000, 1000), (2000, 2000)), (8000, 4000), (4000, 1000))

        assert box == ((500, 250), (1000, 500))

    def test_rounding_is_direction_aware(self):
        from plone.pgthumbor.scaling import _rescale_crop

        # Floor the near edges, ceil the far ones, so the mapped region
        # covers the original selection rather than cutting into it.
        # 1000 * 4000 / 11811 = 338.67 -> 338; 2000 * ... = 677.34 -> 678.
        box = _rescale_crop(((1000, 1000), (2000, 2000)), (11811, 8858), (4000, 2999))

        assert box == ((338, 338), (678, 678))

    def test_the_box_is_clamped_to_the_derivative(self):
        from plone.pgthumbor.scaling import _rescale_crop

        box = _rescale_crop(((-50, -50), (9000, 9000)), (8000, 6000), (4000, 3000))

        assert box == ((0, 0), (4000, 3000))

    def test_a_box_outside_the_image_degenerates_to_none(self):
        from plone.pgthumbor.scaling import _rescale_crop

        # A crop left over from a larger image that has since been
        # replaced: everything clamps to the right edge and the region
        # collapses.
        assert (
            _rescale_crop(((9000, 9000), (9500, 9500)), (8000, 6000), (4000, 3000))
            is None
        )

    def test_a_zero_area_box_degenerates_to_none(self):
        from plone.pgthumbor.scaling import _rescale_crop

        assert (
            _rescale_crop(((100, 100), (100, 100)), (8000, 6000), (4000, 3000)) is None
        )

    def test_an_unknown_original_size_drops_the_crop(self):
        from plone.pgthumbor.scaling import _rescale_crop

        # Better no crop than a box in the wrong coordinate system.
        assert _rescale_crop(((10, 10), (20, 20)), None, (4000, 3000)) is None
        assert _rescale_crop(((10, 10), (20, 20)), (0, 0), (4000, 3000)) is None


class TestCropTranslationInTheUrl:
    """The translation as it reaches libthumbor's plaintext box."""

    def _url(self, monkeypatch, data, crop, mode="cover", width=400, height=300):
        from plone.pgthumbor import scaling as scaling_mod

        _setup_env(monkeypatch)
        monkeypatch.setattr(
            scaling_mod, "_needs_auth_url", lambda ctx, zoid, paranoid_mode=False: False
        )
        ctx = MagicMock()
        ctx._p_oid = struct.pack(">Q", 0x42)
        return scaling_mod._build_thumbor_url(ctx, data, width, height, mode, crop=crop)

    def test_the_box_is_rescaled_to_the_derivative(self, monkeypatch):
        data = _mock_image_data(
            width=8000, height=6000, derivative=_mock_derivative(size=(4000, 3000))
        )

        url = self._url(monkeypatch, data, ((1000, 2000), (3000, 4000)))

        assert "500x1000:1500x2000" in url

    def test_the_box_is_untouched_without_a_derivative(self, monkeypatch):
        # Gated on "a derivative was selected", never on "a crop exists".
        # A skipped or failed derivative carrying a crop must pass the box
        # through unchanged.
        data = _mock_image_data(width=8000, height=6000)

        url = self._url(monkeypatch, data, ((1000, 2000), (3000, 4000)))

        assert "1000x2000:3000x4000" in url

    def test_a_degenerate_box_is_dropped_and_the_mode_default_restored(
        self, monkeypatch
    ):
        from plone.pgthumbor.url import scale_mode_to_thumbor

        data = _mock_image_data(
            width=8000, height=6000, derivative=_mock_derivative(size=(4000, 3000))
        )

        url = self._url(monkeypatch, data, ((9000, 9000), (9500, 9500)), mode="cover")

        # libthumbor silently ignores an all-zero box, which would render a
        # full uncropped image under crop semantics rather than erroring.
        assert ":" not in url.rsplit("/", 3)[-3]
        # The mode's own values come back.  Derived, not written as a
        # literal: PR #23's cover/contain swap for plone.scale < 6 and the
        # #21 mode fix both moved what "the default" means.
        expected = scale_mode_to_thumbor("cover", smart_cropping=False)
        assert ("fit-in" in url) is expected["fit_in"]
        assert ("/smart/" in url) is expected["smart"]

    def test_the_translation_reaches_scale_url(self, monkeypatch):
        from plone.pgthumbor import scaling as scaling_mod
        from plone.pgthumbor.scaling import ThumborImageScaling

        _setup_env(monkeypatch)
        monkeypatch.setattr(
            scaling_mod, "_needs_auth_url", lambda ctx, zoid, paranoid_mode=False: False
        )
        monkeypatch.setattr(
            scaling_mod,
            "_get_crop",
            lambda ctx, fieldname, info: ((1000, 2000), (3000, 4000)),
        )
        ctx = MagicMock()
        ctx._p_oid = struct.pack(">Q", 0x42)
        ctx.image = _mock_image_data(
            width=8000, height=6000, derivative=_mock_derivative(size=(4000, 3000))
        )
        view = ThumborImageScaling(ctx, MagicMock())

        url = view._scale_url(
            "image-400-abc",
            "jpeg",
            scale_info={"fieldname": "image", "width": 400, "height": 300},
        )

        assert "500x1000:1500x2000" in url


class TestDimensionClamping:
    """No URL ever asks for more pixels than the selected source holds."""

    def _url(self, monkeypatch, data, width, height):
        from plone.pgthumbor import scaling as scaling_mod

        _setup_env(monkeypatch)
        monkeypatch.setattr(
            scaling_mod, "_needs_auth_url", lambda ctx, zoid, paranoid_mode=False: False
        )
        ctx = MagicMock()
        ctx._p_oid = struct.pack(">Q", 0x42)
        return scaling_mod._build_thumbor_url(ctx, data, width, height, "scale")

    def test_a_request_above_the_derivative_is_clamped(self, monkeypatch):
        """Thumbor's MAX_PIXELS guards the source, not the output.

        Asking an 11811 px rendition of a 4000 px derivative would succeed
        by upscaling, allocating hundreds of megabytes in a pod limited to
        1536Mi and handing browsers a multi-megabyte candidate.
        """
        data = _mock_image_data(
            width=11811, height=8858, derivative=_mock_derivative(size=(4000, 2999))
        )

        url = self._url(monkeypatch, data, 11811, 8858)

        assert "/4000x2999/" in url

    def test_a_request_below_the_derivative_is_left_alone(self, monkeypatch):
        data = _mock_image_data(
            width=11811, height=8858, derivative=_mock_derivative(size=(4000, 2999))
        )

        url = self._url(monkeypatch, data, 400, 300)

        assert "/400x300/" in url

    def test_zero_means_auto_and_is_not_clamped(self, monkeypatch):
        data = _mock_image_data(
            width=11811, height=8858, derivative=_mock_derivative(size=(4000, 2999))
        )

        url = self._url(monkeypatch, data, 400, 0)

        assert "/400x0/" in url

    def test_the_original_is_clamped_too_when_no_derivative_exists(self, monkeypatch):
        data = _mock_image_data(width=800, height=600)

        url = self._url(monkeypatch, data, 4000, 3000)

        assert "/800x600/" in url


class TestImageSize:
    """A field value that cannot state its size must drop the crop.

    Better no crop than a box interpreted in the wrong coordinate system,
    which is what an unscaled original box against a derivative would be.
    """

    def test_reads_a_normal_size(self):
        from plone.pgthumbor.scaling import _image_size

        assert _image_size(_mock_image_data(width=800, height=600)) == (800, 600)

    def test_a_raising_field_value_is_unknowable(self):
        from plone.pgthumbor.scaling import _image_size

        image = MagicMock()
        image.getImageSize.side_effect = ValueError("corrupt header")

        assert _image_size(image) is None

    def test_a_missing_method_is_unknowable(self):
        from plone.pgthumbor.scaling import _image_size

        assert _image_size(object()) is None

    def test_a_zero_dimension_is_unknowable(self):
        from plone.pgthumbor.scaling import _image_size

        # plone.namedfile reports (0, 0) for an image it could not sniff.
        assert _image_size(_mock_image_data(width=0, height=0)) is None

    def test_an_unknowable_original_drops_the_crop_in_the_url(self, monkeypatch):
        from plone.pgthumbor import scaling as scaling_mod

        _setup_env(monkeypatch)
        monkeypatch.setattr(
            scaling_mod, "_needs_auth_url", lambda ctx, zoid, paranoid_mode=False: False
        )
        ctx = MagicMock()
        ctx._p_oid = struct.pack(">Q", 0x42)
        data = _mock_image_data(
            width=0, height=0, derivative=_mock_derivative(size=(4000, 3000))
        )

        url = scaling_mod._build_thumbor_url(
            ctx, data, 400, 300, "cover", crop=((10, 10), (20, 20))
        )

        assert "10x10:20x20" not in url


class TestSrcsetUpscaleClamp:
    """srcset must not offer a candidate the source cannot satisfy.

    The back-fill entry is the sharp case.  ``srcset`` adds an entry at the
    original's dimensions whenever no configured scale covers them, which
    for an 11811 px print original is always.  Today that entry fails
    loudly with a Thumbor 400.  Once the source is a 4000 px derivative it
    would *succeed*, because MAX_PIXELS guards the source and not the
    output: Thumbor would upscale to 11811 px, allocate hundreds of
    megabytes in a pod limited to 1536Mi, and hand browsers a
    multi-megabyte candidate.  Turning a visible failure into an invisible
    one is worse than leaving it broken.
    """

    def _make_view(
        self, monkeypatch, derivative=None, sizes=None, original=(11811, 8858)
    ):
        from plone.namedfile.scaling import ImageScaling
        from plone.pgthumbor.scaling import ThumborImageScaling

        _setup_env(monkeypatch)
        ctx = MagicMock()
        ctx.absolute_url.return_value = "http://plone:8080/doc"
        ctx.Title.return_value = "Doc"
        ctx.image = _mock_image_data(
            width=original[0], height=original[1], derivative=derivative
        )
        view = ThumborImageScaling(ctx, MagicMock())
        monkeypatch.setattr(
            ImageScaling,
            "available_sizes",
            sizes
            if sizes is not None
            else {"preview": (400, 400), "large": (800, 800)},
            raising=False,
        )
        view.getImageSize = lambda fieldname: original
        return view

    def _requested_widths(self, view):
        widths = []

        def fake_scale(fieldname=None, scale=None, height=None, width=None, **kw):
            if width is not None:
                widths.append(width)
            fake = MagicMock()
            fake.url = f"{SERVER}/signed/{width or scale}/42/ff"
            fake.width = width or 400
            fake.height = height or 300
            return fake

        view.scale = MagicMock(side_effect=fake_scale)
        return widths

    def test_the_backfill_entry_is_dropped_rather_than_upscaled(self, monkeypatch):
        view = self._make_view(
            monkeypatch, derivative=_mock_derivative(size=(4000, 2999))
        )
        widths = self._requested_widths(view)

        view.srcset(fieldname="image", scale_in_src="preview")

        assert 11811 not in widths

    def test_the_backfill_entry_survives_without_a_derivative(self, monkeypatch):
        # Behaviour for an unprocessed image must be exactly as before.
        view = self._make_view(monkeypatch, original=(100, 80))
        widths = self._requested_widths(view)

        view.srcset(fieldname="image", scale_in_src="preview")

        assert 100 in widths

    def test_the_backfill_entry_survives_when_the_derivative_matches(self, monkeypatch):
        # A colour-space-only trigger leaves the dimensions alone, so the
        # original-size entry is still satisfiable.
        view = self._make_view(
            monkeypatch,
            original=(100, 80),
            derivative=_mock_derivative(size=(100, 80)),
        )
        widths = self._requested_widths(view)

        view.srcset(fieldname="image", scale_in_src="preview")

        assert 100 in widths

    def test_no_configured_scale_wider_than_the_derivative_is_offered(
        self, monkeypatch
    ):
        view = self._make_view(
            monkeypatch,
            derivative=_mock_derivative(size=(4000, 2999)),
            sizes={"preview": (400, 400), "huge": (6000, 6000)},
        )
        widths = self._requested_widths(view)

        view.srcset(fieldname="image", scale_in_src="preview")

        assert 6000 not in widths
        assert 400 in widths

    def test_scales_within_the_derivative_are_still_offered(self, monkeypatch):
        view = self._make_view(
            monkeypatch, derivative=_mock_derivative(size=(4000, 2999))
        )
        widths = self._requested_widths(view)

        view.srcset(fieldname="image", scale_in_src="preview")

        assert 400 in widths
        assert 800 in widths
