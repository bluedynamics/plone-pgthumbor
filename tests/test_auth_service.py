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

    def _patch_dependencies(self, service, catalog, row, storage_conn=True):
        """Patch REST service dependencies.

        By default (``storage_conn=True``) the storage connection is used
        — matching the production path where ZODB holds a request-scoped
        connection.  Set ``storage_conn=False`` to simulate the pool
        fallback (tests, scripts, non-ZODB contexts).
        """
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
            patch(
                "plone.pgthumbor.restapi.get_storage_connection",
                return_value=mock_conn if storage_conn else None,
            ),
        ]
        return patches

    def test_allowed_user_returns_200(self):
        """User principals overlap with allowedRolesAndUsers → 200 {}."""
        service = self._make_service("000000000000001a")
        catalog = self._mock_catalog(["user:john", "Authenticated", "Anonymous"])
        patches = self._patch_dependencies(service, catalog, {"allowed": True})

        with patches[0], patches[1] as mock_sm, patches[2], patches[3], patches[4]:
            mock_sm.return_value.getUser.return_value = MagicMock()
            result = service.render()

        assert json.loads(result) == {}
        assert service.request.response.status == 200

    def test_denied_user_returns_401(self):
        """No overlap between user principals and allowedRolesAndUsers → 401."""
        service = self._make_service("000000000000001a")
        catalog = self._mock_catalog(["user:john", "Authenticated"])
        patches = self._patch_dependencies(service, catalog, {"allowed": False})

        with patches[0], patches[1] as mock_sm, patches[2], patches[3], patches[4]:
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

        with patches[0], patches[1] as mock_sm, patches[2], patches[3], patches[4]:
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
            patch(
                "plone.pgthumbor.restapi.get_storage_connection",
                side_effect=Exception("DB down"),
            ),
        ):
            mock_sm.return_value.getUser.return_value = MagicMock()
            result = service.render()

        assert service.request.response.status == 503
        assert "error" in json.loads(result)

    def test_prefers_storage_connection_over_pool(self):
        """Storage conn is used when available; pool is not touched (regression for #8)."""
        service = self._make_service("000000000000001a")
        catalog = self._mock_catalog(["user:john", "Authenticated", "Anonymous"])

        storage_conn = MagicMock()
        storage_conn.execute.return_value.fetchone.return_value = {"allowed": True}

        with (
            patch("plone.pgthumbor.restapi.getToolByName", return_value=catalog),
            patch("plone.pgthumbor.restapi.getSecurityManager") as mock_sm,
            patch(
                "plone.pgthumbor.restapi.get_storage_connection",
                return_value=storage_conn,
            ) as mock_storage,
            patch("plone.pgthumbor.restapi.get_pool") as mock_pool,
            patch("plone.pgthumbor.restapi.get_request_connection") as mock_req_conn,
        ):
            mock_sm.return_value.getUser.return_value = MagicMock()
            service.render()

        mock_storage.assert_called_once_with(service.context)
        # Pool fallback must not be entered when storage conn is available.
        mock_pool.assert_not_called()
        mock_req_conn.assert_not_called()
        storage_conn.execute.assert_called_once()

    def test_falls_back_to_pool_when_no_storage_connection(self):
        """When storage conn is unavailable (tests, scripts), fall back to pool."""
        service = self._make_service("000000000000001a")
        catalog = self._mock_catalog(["user:john", "Authenticated", "Anonymous"])

        pool_conn = MagicMock()
        pool_conn.execute.return_value.fetchone.return_value = {"allowed": True}

        with (
            patch("plone.pgthumbor.restapi.getToolByName", return_value=catalog),
            patch("plone.pgthumbor.restapi.getSecurityManager") as mock_sm,
            patch(
                "plone.pgthumbor.restapi.get_storage_connection",
                return_value=None,
            ),
            patch("plone.pgthumbor.restapi.get_pool") as mock_pool,
            patch(
                "plone.pgthumbor.restapi.get_request_connection",
                return_value=pool_conn,
            ) as mock_req_conn,
        ):
            mock_sm.return_value.getUser.return_value = MagicMock()
            service.render()

        mock_pool.assert_called_once_with(service.context)
        mock_req_conn.assert_called_once()
        pool_conn.execute.assert_called_once()
