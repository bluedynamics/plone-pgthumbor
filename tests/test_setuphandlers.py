"""Tests for GenericSetup install handler and @@thumbor-setup diagnostics view."""

from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import patch


class TestPostInstall:
    """Test post_install() GenericSetup handler."""

    def test_registers_interface_in_registry(self):
        from plone.pgthumbor.interfaces import IThumborSettings

        mock_registry = MagicMock()

        with patch(
            "plone.pgthumbor.setuphandlers.getUtility", return_value=mock_registry
        ):
            from plone.pgthumbor.setuphandlers import post_install

            post_install(context=MagicMock())

        mock_registry.registerInterface.assert_called_once_with(
            IThumborSettings, prefix="plone.pgthumbor.settings"
        )


class TestSetupView:
    """Test SetupView (@@thumbor-setup) diagnostics."""

    def _make_view(self, registry=None):
        from plone.pgthumbor.setuphandlers import SetupView

        context = MagicMock()
        request = MagicMock()
        request.response = MagicMock()
        view = SetupView(context, request)

        if registry is None:
            registry = self._make_registry()

        return view, registry

    def _make_registry(self, *, key_present=True, for_interface_ok=True):
        registry = MagicMock()
        registry.__contains__ = MagicMock(return_value=key_present)

        records = MagicMock()
        records.__contains__ = MagicMock(return_value=key_present)

        values = MagicMock()
        values.__contains__ = MagicMock(return_value=key_present)
        values.__iter__ = MagicMock(
            return_value=iter(
                [
                    "plone.pgthumbor.settings.server_url",
                    "plone.pgthumbor.settings.security_key",
                ]
                if key_present
                else []
            )
        )
        values._p_oid = b"\x00" * 8
        values._p_serial = b"\x00" * 8
        records._values = values
        registry.records = records

        if for_interface_ok:
            proxy = MagicMock()
            proxy.server_url = "http://thumbor:8888"
            registry.forInterface.return_value = proxy
        else:
            registry.forInterface.side_effect = KeyError("not registered")

        return registry

    def test_returns_plain_text(self):
        view, registry = self._make_view()

        with patch("plone.pgthumbor.setuphandlers.getUtility", return_value=registry):
            result = view()

        view.request.response.setHeader.assert_called_once_with(
            "Content-Type", "text/plain"
        )
        assert isinstance(result, str)

    def test_reports_key_presence(self):
        view, registry = self._make_view()

        with patch("plone.pgthumbor.setuphandlers.getUtility", return_value=registry):
            result = view()

        assert "key in registry: True" in result
        assert "key in registry.records: True" in result

    def test_reports_values_btree_info(self):
        view, registry = self._make_view()

        with patch("plone.pgthumbor.setuphandlers.getUtility", return_value=registry):
            result = view()

        assert "key in _values: True" in result
        assert "_p_oid:" in result
        assert "_p_serial:" in result

    def test_reports_matching_keys(self):
        view, registry = self._make_view()

        with patch("plone.pgthumbor.setuphandlers.getUtility", return_value=registry):
            result = view()

        assert "pgthumbor keys in _values:" in result
        assert "server_url" in result

    def test_for_interface_check_false_success(self):
        view, registry = self._make_view()

        with patch("plone.pgthumbor.setuphandlers.getUtility", return_value=registry):
            result = view()

        assert "forInterface(check=False) OK" in result
        assert "server_url=" in result

    def test_for_interface_check_true_success(self):
        view, registry = self._make_view()

        with patch("plone.pgthumbor.setuphandlers.getUtility", return_value=registry):
            result = view()

        assert "forInterface(check=True) OK" in result

    def test_for_interface_check_false_failure(self):
        registry = self._make_registry()

        def fail_on_check_false(*_args, **kwargs):
            if kwargs.get("check") is False:
                raise Exception("broken registry")
            proxy = MagicMock()
            proxy.server_url = "http://thumbor:8888"
            return proxy

        registry.forInterface.side_effect = fail_on_check_false
        view, _ = self._make_view(registry=registry)

        with patch("plone.pgthumbor.setuphandlers.getUtility", return_value=registry):
            result = view()

        assert "forInterface(check=False) FAILED" in result

    def test_for_interface_check_true_failure(self):
        registry = self._make_registry()

        def fail_on_check_true(*_args, **kwargs):
            if kwargs.get("check", True) is not False:
                raise KeyError("not registered")
            proxy = MagicMock()
            proxy.server_url = "http://thumbor:8888"
            return proxy

        registry.forInterface.side_effect = fail_on_check_true
        view, _ = self._make_view(registry=registry)

        with patch("plone.pgthumbor.setuphandlers.getUtility", return_value=registry):
            result = view()

        assert "forInterface(check=True) FAILED" in result
