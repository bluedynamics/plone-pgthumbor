"""Thumbor configuration — reads from env vars with registry fallback."""

from __future__ import annotations

from dataclasses import dataclass

import logging
import os


logger = logging.getLogger(__name__)

SOURCE_MAX_EDGE_DEFAULT = 4000

# Hard ceiling on the cap.  Load-bearing arithmetic, not taste: a longest
# edge of E bounds the derivative at E**2 pixels, and Thumbor refuses to
# process anything above MAX_PIXELS (75e6).  Past sqrt(75e6) ~= 8660 a
# derivative could reproduce the exact HTTP 400 this feature exists to
# remove — and silently, because generation would succeed and only Thumbor
# would object.
SOURCE_MAX_EDGE_CEILING = 8000


def _clamp_source_max_edge(value: int) -> int:
    """Bound a cap to [0, SOURCE_MAX_EDGE_CEILING]."""
    return max(0, min(int(value), SOURCE_MAX_EDGE_CEILING))


@dataclass(frozen=True)
class ThumborConfig:
    """Immutable Thumbor configuration."""

    server_url: str
    security_key: str = ""
    unsafe: bool = False
    smart_cropping: bool = False
    paranoid_mode: bool = False
    source_max_edge: int = SOURCE_MAX_EDGE_DEFAULT

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
        logger.warning(
            "PGTHUMBOR_SECURITY_KEY not set and unsafe mode disabled — Thumbor URLs unavailable"
        )
        return None

    smart_cropping = os.environ.get("PGTHUMBOR_SMART_CROPPING", "").lower() in (
        "true",
        "1",
        "yes",
    )
    paranoid_mode = os.environ.get("PGTHUMBOR_PARANOID_MODE", "").lower() in (
        "true",
        "1",
        "yes",
    )

    # Read the cap through a None sentinel, never through falsiness.  The
    # booleans above can get away with "falsy means unset" because their
    # default is False either way.  Here 0 is the documented kill switch for
    # derivative generation — the thing you reach for during a bulk import or
    # an incident — and reading it as "unset" would send us to the registry,
    # which hands back 4000 and silently switches generation back on.
    source_max_edge = None
    raw_max_edge = os.environ.get("PGTHUMBOR_SOURCE_MAX_EDGE")
    if raw_max_edge is not None:
        try:
            source_max_edge = _clamp_source_max_edge(int(raw_max_edge.strip()))
        except ValueError:
            logger.warning(
                "PGTHUMBOR_SOURCE_MAX_EDGE=%r is not an integer — ignoring it",
                raw_max_edge,
            )

    # Registry fallback for settings not in env
    if not smart_cropping or not paranoid_mode or source_max_edge is None:
        try:
            from plone.pgthumbor.interfaces import IThumborSettings
            from plone.registry.interfaces import IRegistry
            from zope.component import queryUtility

            registry = queryUtility(IRegistry)
            if registry is not None:
                settings = registry.forInterface(
                    IThumborSettings, prefix="plone.pgthumbor.settings", check=False
                )
                if not smart_cropping:
                    smart_cropping = getattr(settings, "smart_cropping", False)
                if not paranoid_mode:
                    paranoid_mode = getattr(settings, "paranoid_mode", False)
                if source_max_edge is None:
                    stored = getattr(settings, "source_max_edge", None)
                    # isinstance, not truthiness: a stored 0 is meaningful.
                    # bool is excluded because it is an int subclass, and a
                    # test double would otherwise int() its way to 1.
                    if isinstance(stored, int) and not isinstance(stored, bool):
                        # Clamp here too.  The schema's max= guards writes
                        # through the control panel; a record written before
                        # the bound existed never revalidates.
                        source_max_edge = _clamp_source_max_edge(stored)
        except Exception:
            pass

    if source_max_edge is None:
        source_max_edge = SOURCE_MAX_EDGE_DEFAULT

    return ThumborConfig(
        server_url=server_url,
        security_key=security_key,
        unsafe=unsafe,
        smart_cropping=smart_cropping,
        paranoid_mode=paranoid_mode,
        source_max_edge=source_max_edge,
    )
