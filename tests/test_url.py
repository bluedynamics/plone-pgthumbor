"""Tests for Thumbor URL generation."""

from __future__ import annotations


SERVER = "http://thumbor:8888"
KEY = "test-secret-key"


class TestThumborUrl:
    """Test thumbor_url() function."""

    def test_basic_signed_url(self):
        from plone.pgthumbor.url import thumbor_url

        url = thumbor_url(
            server_url=SERVER,
            security_key=KEY,
            zoid=0x42,
            tid=0xFF,
            width=400,
            height=0,
        )
        assert url.startswith(SERVER + "/")
        assert "/400x0/" in url
        assert url.endswith("/42/ff")

    def test_unsafe_url(self):
        from plone.pgthumbor.url import thumbor_url

        url = thumbor_url(
            server_url=SERVER,
            security_key=KEY,
            zoid=0x42,
            tid=0xFF,
            width=400,
            height=0,
            unsafe=True,
        )
        assert "/unsafe/" in url
        assert url.endswith("/42/ff")

    def test_fit_in(self):
        from plone.pgthumbor.url import thumbor_url

        url = thumbor_url(
            server_url=SERVER,
            security_key=KEY,
            zoid=0x42,
            tid=0xFF,
            width=400,
            height=300,
            fit_in=True,
        )
        assert "/fit-in/" in url

    def test_smart(self):
        from plone.pgthumbor.url import thumbor_url

        url = thumbor_url(
            server_url=SERVER,
            security_key=KEY,
            zoid=0x42,
            tid=0xFF,
            width=400,
            height=300,
            smart=True,
        )
        assert "/smart/" in url

    def test_no_dimensions(self):
        """width=0, height=0 → libthumbor omits dimensions, passes original."""
        from plone.pgthumbor.url import thumbor_url

        url = thumbor_url(
            server_url=SERVER,
            security_key=KEY,
            zoid=0x42,
            tid=0xFF,
            width=0,
            height=0,
        )
        # libthumbor omits 0x0 from the path
        assert url.endswith("/42/ff")
        assert "x" not in url.split("/")[-3]  # no WxH segment before image_url

    def test_cover_mode(self):
        """Cover mode: no fit_in, with smart."""
        from plone.pgthumbor.url import thumbor_url

        url = thumbor_url(
            server_url=SERVER,
            security_key=KEY,
            zoid=0x42,
            tid=0xFF,
            width=400,
            height=300,
            smart=True,
            fit_in=False,
        )
        assert "/smart/" in url
        assert "fit-in" not in url

    def test_hex_format_no_padding(self):
        """ZOID/TID appear as lowercase hex without leading zeros."""
        from plone.pgthumbor.url import thumbor_url

        url = thumbor_url(
            server_url=SERVER,
            security_key=KEY,
            zoid=0xDEAD,
            tid=0xBEEF,
            width=400,
            height=0,
        )
        assert url.endswith("/dead/beef")

    def test_signature_valid(self):
        """Verify HMAC matches what libthumbor produces."""
        from libthumbor import CryptoURL
        from plone.pgthumbor.url import thumbor_url

        url = thumbor_url(
            server_url=SERVER,
            security_key=KEY,
            zoid=0x42,
            tid=0xFF,
            width=400,
            height=300,
            smart=True,
        )

        # Extract path after server URL
        path = url[len(SERVER):]

        # libthumbor should produce the same result
        crypto = CryptoURL(key=KEY)
        expected_path = crypto.generate(
            image_url="42/ff",
            width=400,
            height=300,
            smart=True,
        )
        assert path == expected_path

    def test_fit_in_and_smart_combined(self):
        """Plone 'scale' mode: fit_in + smart."""
        from plone.pgthumbor.url import thumbor_url

        url = thumbor_url(
            server_url=SERVER,
            security_key=KEY,
            zoid=0x42,
            tid=0xFF,
            width=400,
            height=300,
            fit_in=True,
            smart=True,
        )
        assert "/fit-in/" in url
        assert "/smart/" in url

    def test_with_filters(self):
        from plone.pgthumbor.url import thumbor_url

        url = thumbor_url(
            server_url=SERVER,
            security_key=KEY,
            zoid=0x42,
            tid=0xFF,
            width=400,
            height=0,
            filters=["quality(85)"],
        )
        assert "quality(85)" in url


class TestScaleModeToThumbor:
    """Test mapping Plone scale modes to Thumbor parameters."""

    def test_scale_mode(self):
        """Default 'scale' mode: fit_in + smart."""
        from plone.pgthumbor.url import scale_mode_to_thumbor

        params = scale_mode_to_thumbor("scale", smart_cropping=True)
        assert params == {"fit_in": True, "smart": True}

    def test_scale_mode_no_smart(self):
        """Default 'scale' mode without smart cropping."""
        from plone.pgthumbor.url import scale_mode_to_thumbor

        params = scale_mode_to_thumbor("scale", smart_cropping=False)
        assert params == {"fit_in": True, "smart": False}

    def test_cover_mode(self):
        """Cover mode: no fit_in, smart crop."""
        from plone.pgthumbor.url import scale_mode_to_thumbor

        params = scale_mode_to_thumbor("cover", smart_cropping=True)
        assert params == {"fit_in": False, "smart": True}

    def test_contain_mode(self):
        """Contain mode: fit_in, no smart."""
        from plone.pgthumbor.url import scale_mode_to_thumbor

        params = scale_mode_to_thumbor("contain", smart_cropping=True)
        assert params == {"fit_in": True, "smart": False}

    def test_unknown_mode_defaults_to_scale(self):
        from plone.pgthumbor.url import scale_mode_to_thumbor

        params = scale_mode_to_thumbor("unknown", smart_cropping=True)
        assert params == {"fit_in": True, "smart": True}
