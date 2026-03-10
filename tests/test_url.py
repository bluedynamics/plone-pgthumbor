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
        path = url[len(SERVER) :]

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


class TestThumborUrlContentZoid:
    """Test thumbor_url() with content_zoid parameter for authenticated URLs."""

    def test_content_zoid_none_gives_two_segment_url(self):
        """content_zoid=None → 2-segment image URL (blob_zoid/tid)."""
        from plone.pgthumbor.url import thumbor_url

        url = thumbor_url(
            server_url=SERVER,
            security_key=KEY,
            zoid=0x42,
            tid=0xFF,
            width=400,
            height=0,
            content_zoid=None,
        )
        assert url.endswith("/42/ff")

    def test_content_zoid_set_gives_three_segment_url(self):
        """content_zoid=0x1A → 3-segment image URL (blob_zoid/tid/content_zoid)."""
        from plone.pgthumbor.url import thumbor_url

        url = thumbor_url(
            server_url=SERVER,
            security_key=KEY,
            zoid=0x42,
            tid=0xFF,
            width=400,
            height=0,
            content_zoid=0x1A,
        )
        assert url.endswith("/42/ff/1a")

    def test_content_zoid_is_hex_lowercase(self):
        """content_zoid appears as lowercase hex."""
        from plone.pgthumbor.url import thumbor_url

        url = thumbor_url(
            server_url=SERVER,
            security_key=KEY,
            zoid=0x42,
            tid=0xFF,
            width=0,
            height=0,
            content_zoid=0xDEAD,
        )
        assert url.endswith("/42/ff/dead")

    def test_content_zoid_included_in_signature(self):
        """3-segment URL signature covers the content_zoid segment."""
        from libthumbor import CryptoURL
        from plone.pgthumbor.url import thumbor_url

        url = thumbor_url(
            server_url=SERVER,
            security_key=KEY,
            zoid=0x42,
            tid=0xFF,
            width=400,
            height=0,
            content_zoid=0x1A,
        )
        path = url[len(SERVER) :]
        crypto = CryptoURL(key=KEY)
        expected_path = crypto.generate(image_url="42/ff/1a", width=400, height=0)
        assert path == expected_path


class TestThumborUrlCrop:
    """Test thumbor_url() with crop parameter."""

    def test_crop_in_url(self):
        """Crop coordinates appear in the URL path."""
        from plone.pgthumbor.url import thumbor_url

        url = thumbor_url(
            server_url=SERVER,
            security_key=KEY,
            zoid=0x42,
            tid=0xFF,
            width=400,
            height=300,
            crop=((10, 20), (300, 400)),
        )
        assert "10x20:300x400" in url

    def test_crop_none_no_crop_in_url(self):
        """crop=None → no crop coordinates in URL."""
        from plone.pgthumbor.url import thumbor_url

        url = thumbor_url(
            server_url=SERVER,
            security_key=KEY,
            zoid=0x42,
            tid=0xFF,
            width=400,
            height=300,
        )
        # No crop coordinates pattern
        assert "x20:" not in url

    def test_crop_signature_matches_libthumbor(self):
        """Crop URL signature matches direct libthumbor generation."""
        from libthumbor import CryptoURL
        from plone.pgthumbor.url import thumbor_url

        crop = ((10, 20), (300, 400))
        url = thumbor_url(
            server_url=SERVER,
            security_key=KEY,
            zoid=0x42,
            tid=0xFF,
            width=400,
            height=300,
            crop=crop,
        )
        path = url[len(SERVER) :]
        crypto = CryptoURL(key=KEY)
        expected = crypto.generate(image_url="42/ff", width=400, height=300, crop=crop)
        assert path == expected
