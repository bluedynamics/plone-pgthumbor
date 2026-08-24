"""Tests for Thumbor configuration — env vars and defaults."""

from __future__ import annotations

from tests.conftest import env_override


class TestGetThumborConfig:
    """Test get_thumbor_config() reads env vars correctly."""

    def test_config_from_env_vars(self, monkeypatch):
        from plone.pgthumbor.config import get_thumbor_config

        env_override(
            monkeypatch,
            PGTHUMBOR_SERVER_URL="http://thumbor:8888",
            PGTHUMBOR_SECURITY_KEY="my-secret",
        )
        cfg = get_thumbor_config()

        assert cfg.server_url == "http://thumbor:8888"
        assert cfg.security_key == "my-secret"
        assert cfg.unsafe is False

    def test_config_missing_server_url(self, monkeypatch):
        from plone.pgthumbor.config import get_thumbor_config

        env_override(monkeypatch, PGTHUMBOR_SECURITY_KEY="key")
        cfg = get_thumbor_config()

        assert cfg is None

    def test_config_unsafe_mode(self, monkeypatch):
        from plone.pgthumbor.config import get_thumbor_config

        env_override(
            monkeypatch,
            PGTHUMBOR_SERVER_URL="http://thumbor:8888",
            PGTHUMBOR_SECURITY_KEY="key",
            PGTHUMBOR_UNSAFE="true",
        )
        cfg = get_thumbor_config()

        assert cfg.unsafe is True

    def test_config_defaults(self, monkeypatch):
        from plone.pgthumbor.config import get_thumbor_config

        env_override(
            monkeypatch,
            PGTHUMBOR_SERVER_URL="http://thumbor:8888",
            PGTHUMBOR_SECURITY_KEY="key",
        )
        cfg = get_thumbor_config()

        assert cfg.unsafe is False
        assert cfg.smart_cropping is False

    def test_config_env_overrides_registry(self, monkeypatch):
        """Env vars take precedence (registry fallback tested in integration)."""
        from plone.pgthumbor.config import get_thumbor_config

        env_override(
            monkeypatch,
            PGTHUMBOR_SERVER_URL="http://env-thumbor:8888",
            PGTHUMBOR_SECURITY_KEY="env-key",
        )
        cfg = get_thumbor_config()

        assert cfg.server_url == "http://env-thumbor:8888"
        assert cfg.security_key == "env-key"

    def test_config_missing_security_key_with_unsafe(self, monkeypatch):
        """When unsafe=true, security_key is not required."""
        from plone.pgthumbor.config import get_thumbor_config

        env_override(
            monkeypatch,
            PGTHUMBOR_SERVER_URL="http://thumbor:8888",
            PGTHUMBOR_UNSAFE="true",
        )
        cfg = get_thumbor_config()

        assert cfg is not None
        assert cfg.unsafe is True
        assert cfg.security_key == ""

    def test_config_missing_security_key_without_unsafe(self, monkeypatch):
        """When unsafe=false, security_key is required."""
        from plone.pgthumbor.config import get_thumbor_config

        env_override(monkeypatch, PGTHUMBOR_SERVER_URL="http://thumbor:8888")
        cfg = get_thumbor_config()

        assert cfg is None

    def test_smart_cropping_from_env(self, monkeypatch):
        from plone.pgthumbor.config import get_thumbor_config

        env_override(
            monkeypatch,
            PGTHUMBOR_SERVER_URL="http://thumbor:8888",
            PGTHUMBOR_SECURITY_KEY="key",
            PGTHUMBOR_SMART_CROPPING="true",
        )
        cfg = get_thumbor_config()
        assert cfg.smart_cropping is True

    def test_paranoid_mode_from_env(self, monkeypatch):
        from plone.pgthumbor.config import get_thumbor_config

        env_override(
            monkeypatch,
            PGTHUMBOR_SERVER_URL="http://thumbor:8888",
            PGTHUMBOR_SECURITY_KEY="key",
            PGTHUMBOR_PARANOID_MODE="yes",
        )
        cfg = get_thumbor_config()
        assert cfg.paranoid_mode is True

    def test_registry_fallback_for_smart_cropping(self, monkeypatch):
        from plone.pgthumbor.config import get_thumbor_config
        from unittest.mock import MagicMock

        env_override(
            monkeypatch,
            PGTHUMBOR_SERVER_URL="http://thumbor:8888",
            PGTHUMBOR_SECURITY_KEY="key",
        )
        mock_registry = MagicMock()
        mock_settings = MagicMock()
        mock_settings.smart_cropping = True
        mock_settings.paranoid_mode = False
        mock_registry.forInterface.return_value = mock_settings

        monkeypatch.setattr(
            "zope.component.queryUtility",
            lambda iface: mock_registry,
        )
        cfg = get_thumbor_config()
        assert cfg.smart_cropping is True
        assert cfg.paranoid_mode is False

    def test_registry_fallback_for_paranoid_mode(self, monkeypatch):
        from plone.pgthumbor.config import get_thumbor_config
        from unittest.mock import MagicMock

        env_override(
            monkeypatch,
            PGTHUMBOR_SERVER_URL="http://thumbor:8888",
            PGTHUMBOR_SECURITY_KEY="key",
        )
        mock_registry = MagicMock()
        mock_settings = MagicMock()
        mock_settings.smart_cropping = False
        mock_settings.paranoid_mode = True
        mock_registry.forInterface.return_value = mock_settings

        monkeypatch.setattr(
            "zope.component.queryUtility",
            lambda iface: mock_registry,
        )
        cfg = get_thumbor_config()
        assert cfg.paranoid_mode is True


class TestThumborConfig:
    """Test ThumborConfig dataclass."""

    def test_dataclass_fields(self):
        from plone.pgthumbor.config import ThumborConfig

        cfg = ThumborConfig(
            server_url="http://thumbor:8888",
            security_key="key",
            unsafe=False,
            smart_cropping=True,
        )
        assert cfg.server_url == "http://thumbor:8888"
        assert cfg.security_key == "key"
        assert cfg.unsafe is False
        assert cfg.smart_cropping is True

    def test_server_url_trailing_slash_stripped(self):
        from plone.pgthumbor.config import ThumborConfig

        cfg = ThumborConfig(
            server_url="http://thumbor:8888/",
            security_key="key",
        )
        assert cfg.server_url == "http://thumbor:8888"


class TestSourceMaxEdge:
    """PGTHUMBOR_SOURCE_MAX_EDGE — the derivative generation cap.

    This setting is an integer whose meaningful value includes ``0``, so it
    deliberately does *not* copy the falsiness-as-unset idiom the boolean
    settings use.  ``0`` is the documented kill switch; reading it as "unset"
    would let the registry silently switch generation back on during exactly
    the bulk import or incident you reached for it in.
    """

    def _registry(self, monkeypatch, **settings):
        from unittest.mock import MagicMock

        mock_registry = MagicMock()
        mock_settings = MagicMock()
        mock_settings.smart_cropping = False
        mock_settings.paranoid_mode = False
        for name, value in settings.items():
            setattr(mock_settings, name, value)
        mock_registry.forInterface.return_value = mock_settings
        monkeypatch.setattr(
            "zope.component.queryUtility",
            lambda iface: mock_registry,
        )

    def _env(self, monkeypatch, **kwargs):
        env_override(
            monkeypatch,
            PGTHUMBOR_SERVER_URL="http://thumbor:8888",
            PGTHUMBOR_SECURITY_KEY="key",
            **kwargs,
        )

    def test_defaults_to_4000(self, monkeypatch):
        from plone.pgthumbor.config import get_thumbor_config

        self._env(monkeypatch)

        assert get_thumbor_config().source_max_edge == 4000

    def test_env_override(self, monkeypatch):
        from plone.pgthumbor.config import get_thumbor_config

        self._env(monkeypatch, PGTHUMBOR_SOURCE_MAX_EDGE="5000")

        assert get_thumbor_config().source_max_edge == 5000

    def test_env_zero_disables_and_the_registry_does_not_win(self, monkeypatch):
        """The case the falsiness idiom gets wrong.

        Reading ``"0"`` as unset would reach for the registry and come back
        with 4000 — silently re-enabling the thing the operator just turned
        off.
        """
        from plone.pgthumbor.config import get_thumbor_config

        self._env(monkeypatch, PGTHUMBOR_SOURCE_MAX_EDGE="0")
        self._registry(monkeypatch, source_max_edge=4000)

        assert get_thumbor_config().source_max_edge == 0

    def test_env_beats_registry(self, monkeypatch):
        from plone.pgthumbor.config import get_thumbor_config

        self._env(monkeypatch, PGTHUMBOR_SOURCE_MAX_EDGE="5000")
        self._registry(monkeypatch, source_max_edge=2000)

        assert get_thumbor_config().source_max_edge == 5000

    def test_registry_fallback_when_env_absent(self, monkeypatch):
        from plone.pgthumbor.config import get_thumbor_config

        self._env(monkeypatch)
        self._registry(monkeypatch, source_max_edge=6000)

        assert get_thumbor_config().source_max_edge == 6000

    def test_registry_zero_is_honoured(self, monkeypatch):
        from plone.pgthumbor.config import get_thumbor_config

        self._env(monkeypatch)
        self._registry(monkeypatch, source_max_edge=0)

        assert get_thumbor_config().source_max_edge == 0

    def test_non_integer_env_falls_back_with_a_warning(self, monkeypatch, caplog):
        from plone.pgthumbor.config import get_thumbor_config

        import logging

        self._env(monkeypatch, PGTHUMBOR_SOURCE_MAX_EDGE="not-a-number")

        with caplog.at_level(logging.WARNING, logger="plone.pgthumbor.config"):
            cfg = get_thumbor_config()

        assert cfg.source_max_edge == 4000
        assert "PGTHUMBOR_SOURCE_MAX_EDGE" in caplog.text

    def test_negative_clamps_to_zero(self, monkeypatch):
        from plone.pgthumbor.config import get_thumbor_config

        self._env(monkeypatch, PGTHUMBOR_SOURCE_MAX_EDGE="-100")

        assert get_thumbor_config().source_max_edge == 0

    def test_env_above_the_ceiling_clamps(self, monkeypatch):
        """The ceiling is load-bearing, not cosmetic.

        An edge of 4000 bounds the derivative at 16 MP, well under Thumbor's
        75 MP MAX_PIXELS.  That guarantee dies above sqrt(75e6) ~= 8660, so a
        cap of 12000 would quietly reproduce the very 400 this feature exists
        to remove.
        """
        from plone.pgthumbor.config import get_thumbor_config

        self._env(monkeypatch, PGTHUMBOR_SOURCE_MAX_EDGE="12000")

        assert get_thumbor_config().source_max_edge == 8000

    def test_registry_above_the_ceiling_clamps(self, monkeypatch):
        """A value written before the bound existed must not slip through.

        The schema's max= only guards writes through the control panel; a
        record already in the registry never revalidates.
        """
        from plone.pgthumbor.config import get_thumbor_config

        self._env(monkeypatch)
        self._registry(monkeypatch, source_max_edge=12000)

        assert get_thumbor_config().source_max_edge == 8000

    def test_derivative_cap_bounds_pixels_below_thumbor_max(self, monkeypatch):
        """The arithmetic the whole design rests on, pinned as a test."""
        from plone.pgthumbor.config import SOURCE_MAX_EDGE_CEILING

        assert SOURCE_MAX_EDGE_CEILING**2 < 75_000_000
