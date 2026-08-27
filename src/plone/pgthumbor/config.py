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


# Only these count as "on".  Deliberately not widened while fixing #38 —
# the set of accepted spellings is a separate decision from whether an
# explicit "off" is honoured.
_TRUTHY = frozenset({"true", "1", "yes"})


def _env_bool(name: str) -> bool | None:
    """A boolean from the environment, or None when the variable is unset.

    The None is the whole point (#38).  Reading these by membership alone
    made ``"false"``, ``"0"``, ``"no"`` and *unset* the same value, so an
    explicit "off" could not be told apart from "no answer" — the registry
    was consulted for both, and a registry "on" overrode the operator.  The
    reference documentation promised the opposite, and these are the two
    settings someone reaches for under pressure.

    An empty value counts as **set**: ``VAR=`` in a compose file or a
    ConfigMap is somebody saying off, not asking someone else.
    """
    raw = os.environ.get(name)
    if raw is None:
        return None
    return raw.strip().lower() in _TRUTHY


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


def _registry_fallback(smart_cropping, paranoid_mode, source_max_edge):
    """Fill in whatever the environment did not answer, from the registry.

    Only ``None`` reaches the registry.  An explicit environment value —
    including an explicit "off" — is never overridden, which is #38 and is
    what the reference documentation always promised.
    """
    if None not in (smart_cropping, paranoid_mode, source_max_edge):
        return smart_cropping, paranoid_mode, source_max_edge
    try:
        from plone.pgthumbor.interfaces import IThumborSettings
        from plone.registry.interfaces import IRegistry
        from zope.component import queryUtility

        registry = queryUtility(IRegistry)
        if registry is None:
            return smart_cropping, paranoid_mode, source_max_edge
        settings = registry.forInterface(
            IThumborSettings, prefix="plone.pgthumbor.settings", check=False
        )
        if smart_cropping is None:
            smart_cropping = bool(getattr(settings, "smart_cropping", False))
        if paranoid_mode is None:
            paranoid_mode = bool(getattr(settings, "paranoid_mode", False))
        if source_max_edge is None:
            stored = getattr(settings, "source_max_edge", None)
            # isinstance, not truthiness: a stored 0 is meaningful.  bool is
            # excluded because it is an int subclass, and a test double
            # would otherwise int() its way to 1.  Clamp here too — the
            # schema's max= guards writes through the control panel, but a
            # record written before the bound existed never revalidates.
            if isinstance(stored, int) and not isinstance(stored, bool):
                source_max_edge = _clamp_source_max_edge(stored)
    except Exception:
        pass
    return smart_cropping, paranoid_mode, source_max_edge


def get_thumbor_config() -> ThumborConfig | None:
    """Get Thumbor configuration from environment variables.

    Returns None if required configuration is missing.
    Registry fallback will be added in Phase 5 (ZCML integration).
    """
    server_url = os.environ.get("PGTHUMBOR_SERVER_URL", "").strip()
    if not server_url:
        return None

    # No registry fallback for this one — upgrade_to_3 removed the record —
    # so an unset value is simply off.
    unsafe = bool(_env_bool("PGTHUMBOR_UNSAFE"))
    security_key = os.environ.get("PGTHUMBOR_SECURITY_KEY", "").strip()

    if not security_key and not unsafe:
        logger.warning(
            "PGTHUMBOR_SECURITY_KEY not set and unsafe mode disabled — Thumbor URLs unavailable"
        )
        return None

    # Every setting with a registry fallback is read through a None
    # sentinel, never through falsiness.  For the booleans that is #38: an
    # explicit "false" has to beat a registry "true", which membership
    # testing alone cannot express.  For the cap it is the documented kill
    # switch: 0 means off, and reading it as "unset" would send us to the
    # registry, which hands back 4000 and silently switches generation back
    # on during exactly the bulk import or incident you reached for it in.
    smart_cropping = _env_bool("PGTHUMBOR_SMART_CROPPING")
    paranoid_mode = _env_bool("PGTHUMBOR_PARANOID_MODE")

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

    smart_cropping, paranoid_mode, source_max_edge = _registry_fallback(
        smart_cropping, paranoid_mode, source_max_edge
    )

    # Neither environment nor registry had an answer.
    if smart_cropping is None:
        smart_cropping = False
    if paranoid_mode is None:
        paranoid_mode = False
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
