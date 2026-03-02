"""Thumbor URL generation using libthumbor."""

from __future__ import annotations

from libthumbor import CryptoURL


def thumbor_url(
    server_url: str,
    security_key: str,
    zoid: int,
    tid: int,
    width: int = 0,
    height: int = 0,
    smart: bool = False,
    fit_in: bool = False,
    unsafe: bool = False,
    filters: list[str] | None = None,
) -> str:
    """Generate a signed (or unsafe) Thumbor URL for a blob.

    Args:
        server_url: Base Thumbor server URL (e.g., "http://thumbor:8888").
        security_key: HMAC-SHA1 signing key.
        zoid: Blob ZOID as integer.
        tid: Blob TID as integer.
        width: Target width (0 = proportional).
        height: Target height (0 = proportional).
        smart: Enable smart cropping.
        fit_in: Enable fit-in mode (no crop).
        unsafe: Generate unsigned /unsafe/ URL.
        filters: Optional list of Thumbor filter strings.

    Returns:
        Full Thumbor URL string.
    """
    image_url = f"{zoid:x}/{tid:x}"

    crypto = CryptoURL(key=security_key)
    kwargs = {
        "image_url": image_url,
        "width": width,
        "height": height,
        "smart": smart,
        "fit_in": fit_in,
        "unsafe": unsafe,
    }
    if filters:
        kwargs["filters"] = filters

    path = crypto.generate(**kwargs)
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{server_url}{path}"


def scale_mode_to_thumbor(mode: str, smart_cropping: bool = False) -> dict:
    """Map a Plone scale mode to Thumbor parameters.

    Args:
        mode: Plone scale mode ("scale", "cover", "contain").
        smart_cropping: Whether smart cropping is enabled in settings.

    Returns:
        Dict with "fit_in" and "smart" keys.
    """
    if mode == "cover":
        return {"fit_in": False, "smart": smart_cropping}
    if mode == "contain":
        return {"fit_in": True, "smart": False}
    # Default "scale" mode (and any unknown mode)
    return {"fit_in": True, "smart": smart_cropping}
