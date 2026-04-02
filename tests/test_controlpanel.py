"""Tests for the Thumbor control panel."""

from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import patch


class TestThumborSettingsForm:
    """Test ThumborSettingsForm."""

    def test_form_schema_is_ithumborsettings(self):
        from plone.pgthumbor.controlpanel import ThumborSettingsForm
        from plone.pgthumbor.interfaces import IThumborSettings

        assert ThumborSettingsForm.schema is IThumborSettings

    def test_form_label(self):
        from plone.pgthumbor.controlpanel import ThumborSettingsForm

        assert "Thumbor" in ThumborSettingsForm.label

    def test_thumbor_settings_is_wrapped_form(self):
        """ThumborSettings should be the wrapped view class."""
        from plone.pgthumbor.controlpanel import ThumborSettings

        # ThumborSettings is ControlPanelFormWrapper(ThumborSettingsForm)
        # It's callable (a class/view factory)
        assert callable(ThumborSettings)


class TestIThumborSettings:
    """Test IThumborSettings interface has only registry-backed fields."""

    def test_smart_cropping_field_exists(self):
        from plone.pgthumbor.interfaces import IThumborSettings

        assert "smart_cropping" in IThumborSettings

    def test_paranoid_mode_field_exists(self):
        from plone.pgthumbor.interfaces import IThumborSettings

        assert "paranoid_mode" in IThumborSettings

    def test_server_url_removed(self):
        from plone.pgthumbor.interfaces import IThumborSettings

        assert "server_url" not in IThumborSettings

    def test_security_key_removed(self):
        from plone.pgthumbor.interfaces import IThumborSettings

        assert "security_key" not in IThumborSettings

    def test_unsafe_removed(self):
        from plone.pgthumbor.interfaces import IThumborSettings

        assert "unsafe" not in IThumborSettings


class TestGetContent:
    """Test ThumborSettingsForm.getContent() method."""

    def test_get_content_returns_proxy(self):
        from plone.pgthumbor.controlpanel import ThumborSettingsForm

        mock_proxy = MagicMock()
        mock_registry = MagicMock()
        mock_registry.forInterface.return_value = mock_proxy

        form = ThumborSettingsForm.__new__(ThumborSettingsForm)
        form.schema = ThumborSettingsForm.schema

        with patch(
            "plone.pgthumbor.controlpanel.getUtility", return_value=mock_registry
        ):
            result = form.getContent()

        assert result is mock_proxy

    def test_get_content_auto_registers_on_key_error(self):
        from plone.pgthumbor.controlpanel import ThumborSettingsForm
        from plone.pgthumbor.interfaces import IThumborSettings

        mock_proxy = MagicMock()
        mock_registry = MagicMock()
        # First call raises KeyError, second succeeds
        mock_registry.forInterface.side_effect = [KeyError("missing"), mock_proxy]

        form = ThumborSettingsForm.__new__(ThumborSettingsForm)
        form.schema = ThumborSettingsForm.schema

        with patch(
            "plone.pgthumbor.controlpanel.getUtility", return_value=mock_registry
        ):
            result = form.getContent()

        assert result is mock_proxy
        mock_registry.registerInterface.assert_called_once_with(
            IThumborSettings, prefix="plone.pgthumbor.settings"
        )


class TestHandleSave:
    """Test ThumborSettingsForm.handleSave() method."""

    def test_save_applies_changes(self):
        from plone.pgthumbor.controlpanel import ThumborSettingsForm

        form = ThumborSettingsForm.__new__(ThumborSettingsForm)
        form.extractData = MagicMock(return_value=({"server_url": "http://t:8888"}, []))
        form.applyChanges = MagicMock()
        form.formErrorsMessage = "Errors"

        form.handleSave(form, action=MagicMock())

        form.applyChanges.assert_called_once_with({"server_url": "http://t:8888"})
        assert form.status == "Changes saved."

    def test_save_with_errors_does_not_apply(self):
        from plone.pgthumbor.controlpanel import ThumborSettingsForm

        form = ThumborSettingsForm.__new__(ThumborSettingsForm)
        form.extractData = MagicMock(return_value=(None, ["some error"]))
        form.applyChanges = MagicMock()
        form.formErrorsMessage = "There were errors."

        form.handleSave(form, action=MagicMock())

        form.applyChanges.assert_not_called()
        assert form.status == "There were errors."


class TestHandleCancel:
    """Test ThumborSettingsForm.handleCancel() method."""

    def test_cancel_redirects_to_overview(self):
        from plone.pgthumbor.controlpanel import ThumborSettingsForm

        form = ThumborSettingsForm.__new__(ThumborSettingsForm)
        form.context = MagicMock()
        form.context.absolute_url.return_value = "http://localhost:8080/Plone"
        form.request = MagicMock()

        form.handleCancel(form, action=MagicMock())

        form.request.response.redirect.assert_called_once_with(
            "http://localhost:8080/Plone/@@overview-controlpanel"
        )


class TestHandlePurgeScales:
    """Test ThumborSettingsForm.handlePurgeScales() method."""

    @patch("plone.pgthumbor.controlpanel.purge_scales")
    def test_purge_sets_status_message(self, mock_purge):
        from plone.pgthumbor.controlpanel import ThumborSettingsForm

        mock_purge.return_value = (10, 45, 2, 50)

        form = ThumborSettingsForm.__new__(ThumborSettingsForm)
        form.context = MagicMock()

        form.handlePurgeScales(form, action=MagicMock())

        mock_purge.assert_called_once_with(form.context)
        assert "10" in form.status
        assert "45" in form.status
        assert "2 skipped" in form.status
        assert "50 total" in form.status

    @patch("plone.pgthumbor.controlpanel.purge_scales")
    def test_purge_zero_results(self, mock_purge):
        from plone.pgthumbor.controlpanel import ThumborSettingsForm

        mock_purge.return_value = (0, 0, 0, 100)

        form = ThumborSettingsForm.__new__(ThumborSettingsForm)
        form.context = MagicMock()

        form.handlePurgeScales(form, action=MagicMock())

        assert "Purged 0" in form.status


class TestUpdateActions:
    """Test that updateActions configures the purge button styling."""

    def test_purge_button_has_destructive_class(self):
        from plone.pgthumbor.controlpanel import _PURGE_CONFIRM_JS
        from plone.pgthumbor.controlpanel import ThumborSettingsForm

        form = ThumborSettingsForm.__new__(ThumborSettingsForm)
        # Simple object that allows attr assignment (z3c.form actions are
        # heavily schema-validated, so we bypass with a plain namespace)
        action_obj = type("Action", (), {"klass": "", "onclick": ""})()
        form.__dict__["actions"] = {"purge_scales": action_obj}

        with patch.object(
            ThumborSettingsForm.__mro__[1], "updateActions", return_value=None
        ):
            form.updateActions()

        assert "destructive" in action_obj.klass
        assert action_obj.onclick == _PURGE_CONFIRM_JS
