"""Thumbor configuration — reads from env vars with registry fallback."""

from __future__ import annotations

from dataclasses import dataclass

import logging
import os


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ThumborConfig:
    """Immutable Thumbor configuration."""

    server_url: str
    security_key: str = ""
    unsafe: bool = False
    smart_cropping: bool = False

    def __post_init__(self):
        # Strip trailing slash from server_url
        if self.server_url.endswith("/"):
            object.__setattr__(self, "server_url", self.server_url.rstrip("/"))


def get_thumbor_config() -> ThumborConfig | None:
    """Get Thumbor configuration from environment variables.

    Returns None if required configuration is missing.
    Registry fallback will be added in Phase 5 (ZCML integration).
    """
    server_url = os.environ.get("PGTHUMBOR_SERVER_URL", "").strip()
    if not server_url:
        return None

    unsafe = os.environ.get("PGTHUMBOR_UNSAFE", "false").lower() in ("true", "1", "yes")
    security_key = os.environ.get("PGTHUMBOR_SECURITY_KEY", "").strip()

    if not security_key and not unsafe:
        logger.warning("PGTHUMBOR_SECURITY_KEY not set and unsafe mode disabled — Thumbor URLs unavailable")
        return None

    return ThumborConfig(
        server_url=server_url,
        security_key=security_key,
        unsafe=unsafe,
    )
