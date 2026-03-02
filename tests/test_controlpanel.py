"""Tests for the Thumbor control panel."""

from __future__ import annotations


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
    """Test IThumborSettings interface has the paranoid_mode field."""

    def test_paranoid_mode_field_exists(self):
        from plone.pgthumbor.interfaces import IThumborSettings

        assert "paranoid_mode" in IThumborSettings

    def test_paranoid_mode_default_false(self):
        from plone.pgthumbor.interfaces import IThumborSettings
        from zope import schema

        field = IThumborSettings["paranoid_mode"]
        assert isinstance(field, schema.Bool)
        assert field.default is False
