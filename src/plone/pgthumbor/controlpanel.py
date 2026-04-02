"""Control panel for Thumbor settings."""

from __future__ import annotations

from plone.app.registry.browser.controlpanel import ControlPanelFormWrapper
from plone.app.registry.browser.controlpanel import RegistryEditForm
from plone.pgthumbor.interfaces import IThumborSettings
from plone.pgthumbor.purge_scales import purge_scales
from plone.registry.interfaces import IRegistry
from plone.z3cform import layout
from z3c.form import button
from zope.component import getUtility

import logging


logger = logging.getLogger(__name__)

# JS confirm before submitting the purge button
_PURGE_CONFIRM_JS = (
    "return confirm("
    "'This will permanently delete all legacy ZODB image scales "
    "and reindex image_scales metadata.\\n\\n"
    "This is a one-time operation intended for use after installing "
    "plone.pgthumbor.\\n\\nProceed?'"
    ");"
)


class ThumborSettingsForm(RegistryEditForm):
    """Edit form for Thumbor integration settings."""

    schema = IThumborSettings
    label = "Thumbor image scaling"
    description = (
        "Server connection (PGTHUMBOR_SERVER_URL, PGTHUMBOR_SECURITY_KEY) "
        "is configured via environment variables. "
        "The settings below can additionally be managed here."
    )

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

    @button.buttonAndHandler(
        "Purge Legacy Scales",
        name="purge_scales",
        condition=lambda form: True,
    )
    def handlePurgeScales(self, action):
        purged, reindexed, skipped, total = purge_scales(self.context)
        self.status = (
            f"Purged {purged}, reindexed {reindexed} "
            f"({skipped} skipped, {total} total)."
        )

    def updateActions(self):
        super().updateActions()
        if "purge_scales" in self.actions:
            self.actions["purge_scales"].klass = "destructive"
            self.actions["purge_scales"].onclick = _PURGE_CONFIRM_JS


ThumborSettings = layout.wrap_form(ThumborSettingsForm, ControlPanelFormWrapper)
