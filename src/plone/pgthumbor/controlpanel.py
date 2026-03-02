"""Control panel for Thumbor settings."""

from __future__ import annotations

from plone.app.registry.browser.controlpanel import ControlPanelFormWrapper
from plone.app.registry.browser.controlpanel import RegistryEditForm
from plone.pgthumbor.interfaces import IThumborSettings
from plone.registry.interfaces import IRegistry
from plone.z3cform import layout
from z3c.form import button
from zope.component import getUtility


class ThumborSettingsForm(RegistryEditForm):
    """Edit form for Thumbor integration settings."""

    schema = IThumborSettings
    label = "Thumbor image scaling"
    description = "Configure Thumbor image scaling and access control."

    def getContent(self):
        registry = getUtility(IRegistry)
        try:
            return registry.forInterface(self.schema, prefix="plone.pgthumbor.settings")
        except KeyError:
            # Auto-register on first access (handles missing GS import)
            registry.registerInterface(self.schema, prefix="plone.pgthumbor.settings")
            return registry.forInterface(self.schema, prefix="plone.pgthumbor.settings")

    @button.buttonAndHandler("Save", name="save")
    def handleSave(self, action):
        data, errors = self.extractData()
        if errors:
            self.status = self.formErrorsMessage
            return
        self.applyChanges(data)
        self.status = "Changes saved."

    @button.buttonAndHandler("Cancel", name="cancel")
    def handleCancel(self, action):
        self.request.response.redirect(
            f"{self.context.absolute_url()}/@@overview-controlpanel"
        )


ThumborSettings = layout.wrap_form(ThumborSettingsForm, ControlPanelFormWrapper)
