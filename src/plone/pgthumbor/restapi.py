"""Plone REST API service: @thumbor-auth

Verifies whether the current user may view a content object identified
by its ZODB OID.  Used by the Thumbor auth handler to check access before
delivering images for 3-segment (authenticated) URLs.

Uses a direct PostgreSQL query against object_state.allowed_roles
(plone-pgcatalog) — no ZODB object loading, no security manager
switching.

Registration (configure.zcml):
    <plone:service
        method="GET"
        name="@thumbor-auth"
        factory=".restapi.ThumborAuthService"
        permission="zope2.Public"
        for="plone.base.interfaces.INavigationRoot"
        />
"""

from __future__ import annotations

from AccessControl import getSecurityManager
from plone.pgcatalog.pool import get_pool
from plone.pgcatalog.pool import get_request_connection
from plone.rest.service import Service
from Products.CMFCore.utils import getToolByName

import json
import logging


logger = logging.getLogger(__name__)


class ThumborAuthService(Service):
    """Check if the current user may view a content object by ZODB OID.

    GET /@thumbor-auth?zoid=<hex_oid>

    Returns:
        200 {}          — user is allowed
        400 Bad Request — missing or invalid zoid param
        401 Unauthorized — user is not allowed
        404 Not Found   — object not in catalog
        503 Service Unavailable — DB error
    """

    def render(self):
        self.request.response.setHeader("Content-Type", "application/json")
        zoid_hex = self.request.form.get("zoid", "")
        if not zoid_hex:
            self.request.response.setStatus(400)
            return json.dumps({"error": "Missing zoid parameter"})
        try:
            zoid = int(zoid_hex, 16)
        except ValueError:
            self.request.response.setStatus(400)
            return json.dumps({"error": "Invalid zoid parameter"})

        # Compute the current user's effective principals
        user = getSecurityManager().getUser()
        catalog = getToolByName(self.context, "portal_catalog")
        user_principals = catalog._listAllowedRolesAndUsers(user)

        # Single PG query: does the object's allowed_roles overlap with
        # user principals?  plone-pgcatalog stores allowedRolesAndUsers in
        # a dedicated TEXT[] column (with GIN index), not inside idx JSONB.
        try:
            pool = get_pool(self.context)
            conn = get_request_connection(pool)
            row = conn.execute(
                "SELECT (allowed_roles && %s::text[]) AS allowed "
                "FROM object_state WHERE zoid = %s",
                (list(user_principals), zoid),
            ).fetchone()
        except Exception as exc:
            logger.error("@thumbor-auth DB query failed for zoid=%s: %s", zoid_hex, exc)
            self.request.response.setStatus(503)
            return json.dumps({"error": "Service unavailable"})

        if row is None:
            self.request.response.setStatus(404)
            return json.dumps({"error": "Not found"})
        if not row["allowed"]:
            self.request.response.setStatus(401)
            return json.dumps({"error": "Unauthorized"})

        return json.dumps({})
