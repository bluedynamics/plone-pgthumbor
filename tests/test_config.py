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
