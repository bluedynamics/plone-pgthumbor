"""Tests for the @thumbor-auth REST service."""

from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import patch

import json


class TestThumborAuthService:
    """Test ThumborAuthService.render()."""

    def _make_service(self, zoid_param="000000000000001a"):
        from plone.pgthumbor.restapi import ThumborAuthService

        service = object.__new__(ThumborAuthService)
        service.context = MagicMock()
        service.request = MagicMock()
        service.request.form = {}
        service.request.response.status = 200
        service.request.response.setStatus.side_effect = lambda code: setattr(
            service.request.response, "status", code
        )
        if zoid_param is not None:
            service.request.form["zoid"] = zoid_param
        return service

    def _mock_catalog(self, principals=None):
        if principals is None:
            principals = ["user:john", "Member", "Authenticated", "Anonymous"]
        catalog = MagicMock()
        catalog._listAllowedRolesAndUsers.return_value = principals
        return catalog

    def _patch_dependencies(self, service, catalog, row):
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = row
        mock_pool = MagicMock()
        patches = [
            patch("plone.pgthumbor.restapi.getToolByName", return_value=catalog),
            patch("plone.pgthumbor.restapi.getSecurityManager"),
            patch("plone.pgthumbor.restapi.get_pool", return_value=mock_pool),
            patch(
                "plone.pgthumbor.restapi.get_request_connection", return_value=mock_conn
            ),
        ]
        return patches

    def test_allowed_user_returns_200(self):
        """User principals overlap with allowedRolesAndUsers → 200 {}."""
        service = self._make_service("000000000000001a")
        catalog = self._mock_catalog(["user:john", "Authenticated", "Anonymous"])
        patches = self._patch_dependencies(service, catalog, {"allowed": True})

        with patches[0], patches[1] as mock_sm, patches[2], patches[3]:
            mock_sm.return_value.getUser.return_value = MagicMock()
            result = service.render()

        assert json.loads(result) == {}
        assert service.request.response.status == 200

    def test_denied_user_returns_401(self):
        """No overlap between user principals and allowedRolesAndUsers → 401."""
        service = self._make_service("000000000000001a")
        catalog = self._mock_catalog(["user:john", "Authenticated"])
        patches = self._patch_dependencies(service, catalog, {"allowed": False})

        with patches[0], patches[1] as mock_sm, patches[2], patches[3]:
            mock_sm.return_value.getUser.return_value = MagicMock()
            result = service.render()

        assert service.request.response.status == 401
        assert "error" in json.loads(result)

    def test_missing_zoid_returns_400(self):
        """No zoid param → 400."""
        service = self._make_service(zoid_param=None)
        result = service.render()

        assert service.request.response.status == 400
        assert "Missing zoid" in json.loads(result)["error"]

    def test_invalid_hex_zoid_returns_400(self):
        """Non-hex zoid → 400."""
        service = self._make_service(zoid_param="not-hex!")
        result = service.render()

        assert service.request.response.status == 400
        assert "Invalid zoid" in json.loads(result)["error"]

    def test_zoid_not_in_catalog_returns_404(self):
        """zoid not found in object_state → 404."""
        service = self._make_service("000000000000001a")
        catalog = self._mock_catalog(["user:john", "Authenticated"])
        patches = self._patch_dependencies(service, catalog, None)

        with patches[0], patches[1] as mock_sm, patches[2], patches[3]:
            mock_sm.return_value.getUser.return_value = MagicMock()
            result = service.render()

        assert service.request.response.status == 404
        assert "error" in json.loads(result)

    def test_db_error_returns_503(self):
        """DB error → 503 (fail closed)."""
        service = self._make_service("000000000000001a")
        catalog = self._mock_catalog()

        with (
            patch("plone.pgthumbor.restapi.getToolByName", return_value=catalog),
            patch("plone.pgthumbor.restapi.getSecurityManager") as mock_sm,
            patch("plone.pgthumbor.restapi.get_pool", side_effect=Exception("DB down")),
        ):
            mock_sm.return_value.getUser.return_value = MagicMock()
            result = service.render()

        assert service.request.response.status == 503
        assert "error" in json.loads(result)
