# Hosting-aware Thumbor URLs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Thumbor URLs hosting-agnostic: stored catalog metadata contains only the Thumbor-signed path, and the final `src` attribute is composed at render time against the current host / navigation root, so one `PGTHUMBOR_SERVER_URL` value covers absolute-host, root-relative-path, and nav-root-relative-path deployments.

**Architecture:** Introduce `_resolve_thumbor_prefix()` as the single source of truth for turning `PGTHUMBOR_SERVER_URL` + context into a URL prefix. Three syntactic forms: `http(s)://…` (absolute), `/…` (root-relative), `…` without scheme/leading-slash (nav-root-relative). `ThumborImageScale._scale_url()` uses the helper to produce direct URLs; a new `ThumborImageScalesFieldAdapter` stores only the signed path (prefixed with `thumbor:` to make Thumbor entries self-identifying) in `image_scales` brain metadata; a new `ThumborNavigationRootScaling` view recognises the marker at render time and composes the final `src` using the helper again. A config validator rejects ambiguous values at load time.

**Tech Stack:** Python 3.12, `plone.namedfile` 8, `libthumbor.CryptoURL`, `zope.component` adapters, `Products.Five` browser views, `pytest` with `monkeypatch`-based env overrides.

**Context for the implementing engineer:**

- `plone-pgthumbor` is a Plone 6 add-on that replaces ZODB image scaling with Thumbor URLs.
- The existing [`ThumborImageScale._scale_url()`](../../../src/plone/pgthumbor/scaling.py) only handled fully-qualified Thumbor hosts — the codebase never had to care about whether the URL was absolute or relative, because `libthumbor` was always pointed at an external Thumbor host.
- `image_scales` is a Plone catalog metadata column (a dict keyed by field name) that holds pre-computed image-tag data so listings don't have to load the original objects. The write path goes through `Products.CMFPlone.image_scales.adapters.ImageScales` → `plone.namedfile.adapters.ImageFieldScales` → its `_scale_view_from_url()`. The read path goes through `plone.namedfile.scaling.NavigationRootScaling._tag_from_brain_image_scales()`.
- A **navigation root** in Plone is an `INavigationRoot`-providing container — a Plone site or a subsite. In the `@@image_scale` view its `self.context` is *always* the navigation root (that's why the class is called `NavigationRootScaling`).
- A `ThumborConfig` is produced by `get_thumbor_config()` reading env vars with Plone registry fallback for the two boolean toggles. Return `None` = "not configured" and all Thumbor code paths fall back to the standard Plone behaviour.
- Existing tests live under `tests/` and are pure pytest — no Plone test layer. Env overrides happen via the `env_override()` helper in `tests/conftest.py`. New tests follow the same pattern: mock `context`, mock `request`, never start Zope.
- The package is pre-1.0 — we break storage format and migrate once. No backwards-compatible reads, no feature flags.
- Tests are run with `.venv/bin/pytest` from the package root (`/home/jensens/ws/cdev/z3blobs/sources/plone-pgthumbor`).

---

## File Structure

**Files to create**

| Path | Responsibility |
|---|---|
| `src/plone/pgthumbor/prefix.py` | `_resolve_thumbor_prefix(server_url, nav_root_url)` pure function. Single place that turns the three config forms into a prefix. Input validation raises `ValueError`. |
| `src/plone/pgthumbor/field_adapter.py` | `ThumborImageScalesFieldAdapter(ImageFieldScales)` — the write-side override. Replaces the inherited `_scale_view_from_url()` so that Thumbor URLs (recognised by being returned from `ThumborImageScale._scale_url()`) are stored as `thumbor:<signed_path>` and legacy `@@images/<uid>` URLs still pass through the default minus-leading-slash trick. |
| `tests/test_prefix.py` | Unit tests for `_resolve_thumbor_prefix`: absolute / root-relative / nav-root-relative / invalid inputs. |
| `tests/test_field_adapter.py` | Unit tests for the write-side adapter: Thumbor URL → stored as `thumbor:<signed_path>`; legacy URL → stored relative path as today. |
| `tests/test_nav_root_scaling.py` | Unit tests for the read-side view: stored `thumbor:<signed_path>` + each config form → correct `src`; legacy stored entry → unchanged behaviour. |

**Files to modify**

| Path | Change |
|---|---|
| `src/plone/pgthumbor/config.py` | Add syntactic validation for `server_url` in `ThumborConfig.__post_init__`: either `http(s)://…`, `/…` with no scheme, or a non-empty path with no scheme and no leading slash. Reject whitespace, query strings, fragments. |
| `src/plone/pgthumbor/url.py` | `thumbor_url()` stops composing `server_url + path` and returns the signed path only (no `server_url` argument). Prefix composition moves to the call sites via `_resolve_thumbor_prefix()`. |
| `src/plone/pgthumbor/scaling.py` | `_build_thumbor_url()` drops the `server_url` kwarg to `thumbor_url()` and instead composes the final URL via `_resolve_thumbor_prefix(cfg.server_url, nav_root_url)`. Add `ThumborNavigationRootScaling(NavigationRootScaling)` with an overridden `_tag_from_brain_image_scales()` that recognises the `thumbor:` marker and composes the final `src` against the current navigation root. |
| `src/plone/pgthumbor/overrides.zcml` | Register `ThumborImageScalesFieldAdapter` as `IImageScalesFieldAdapter` for `(INamedImageField, IDexterityContent, IPlonePgthumborLayer)`. Register `ThumborNavigationRootScaling` as the `@@image_scale` view on `INavigationRoot` in our layer. |
| `tests/test_scaling.py` | Update `test_url_is_thumbor_url` and friends to the new nav-root-resolved URL shape. Add tests for the three config forms through the direct-access (`@@images/<uid>`) path. |
| `tests/test_config.py` | Add validation tests for the three accepted config forms + rejection cases. (File may need to be created if it doesn't exist yet.) |
| `CHANGES.md` | Add a `## 0.7.0 (unreleased)` entry (breaking storage change → minor version bump below 1.0). |
| `RELEASE.md` | Add a "⚠️ run `@@thumbor-purge-scales` after upgrade" note to the hotfix section. |
| `docs/sources/how-to/configure-plone.md` | Document the three `PGTHUMBOR_SERVER_URL` forms with the three corresponding hosting scenarios. |

---

## Task 1: Prefix resolver (pure function)

**Files:**
- Create: `src/plone/pgthumbor/prefix.py`
- Test: `tests/test_prefix.py`

- [ ] **Step 1: Write the failing test**

Write `tests/test_prefix.py`:

```python
"""Tests for the Thumbor URL prefix resolver."""

from __future__ import annotations

import pytest

from plone.pgthumbor.prefix import resolve_thumbor_prefix


class TestAbsoluteForm:
    """PGTHUMBOR_SERVER_URL=https://cdn.example/thumbor — ignores nav root."""

    def test_https_scheme(self):
        assert (
            resolve_thumbor_prefix(
                "https://cdn.example/thumbor",
                "https://site-a.example/2019",
            )
            == "https://cdn.example/thumbor"
        )

    def test_http_scheme(self):
        assert (
            resolve_thumbor_prefix("http://thumbor:8888", "https://plone.example")
            == "http://thumbor:8888"
        )

    def test_ignores_nav_root_completely(self):
        assert (
            resolve_thumbor_prefix(
                "https://cdn.example/thumbor",
                "https://entirely-different.example/deep/path",
            )
            == "https://cdn.example/thumbor"
        )


class TestRootRelativeForm:
    """PGTHUMBOR_SERVER_URL=/thumbor — anchors at host root, drops nav path."""

    def test_single_segment(self):
        assert (
            resolve_thumbor_prefix("/thumbor", "https://site-a.example/2019")
            == "https://site-a.example/thumbor"
        )

    def test_nested_path(self):
        assert (
            resolve_thumbor_prefix(
                "/img/thumbor", "https://site-a.example/subsite"
            )
            == "https://site-a.example/img/thumbor"
        )

    def test_host_only_nav_root(self):
        assert (
            resolve_thumbor_prefix("/thumbor", "https://plone.example")
            == "https://plone.example/thumbor"
        )


class TestNavRootRelativeForm:
    """PGTHUMBOR_SERVER_URL=thumbor — appended to full nav-root URL."""

    def test_single_segment(self):
        assert (
            resolve_thumbor_prefix("thumbor", "https://arch.example/2019")
            == "https://arch.example/2019/thumbor"
        )

    def test_parallel_mounts(self):
        # scenario 6 from issue #7
        assert (
            resolve_thumbor_prefix("thumbor", "https://arch.example/2017")
            == "https://arch.example/2017/thumbor"
        )
        assert (
            resolve_thumbor_prefix("thumbor", "https://arch.example/2021")
            == "https://arch.example/2021/thumbor"
        )

    def test_strips_trailing_slash_on_nav_root(self):
        assert (
            resolve_thumbor_prefix("thumbor", "https://arch.example/2019/")
            == "https://arch.example/2019/thumbor"
        )


class TestInvalidForms:
    """Ambiguous / malformed configurations are rejected at resolve time."""

    def test_empty(self):
        with pytest.raises(ValueError):
            resolve_thumbor_prefix("", "https://plone.example")

    def test_whitespace(self):
        with pytest.raises(ValueError):
            resolve_thumbor_prefix("   ", "https://plone.example")

    def test_scheme_but_no_host(self):
        with pytest.raises(ValueError):
            resolve_thumbor_prefix("http://", "https://plone.example")

    def test_contains_query_string(self):
        with pytest.raises(ValueError):
            resolve_thumbor_prefix("/thumbor?x=1", "https://plone.example")

    def test_contains_fragment(self):
        with pytest.raises(ValueError):
            resolve_thumbor_prefix("/thumbor#frag", "https://plone.example")


class TestOutputShape:
    """Guarantees independent of input form."""

    @pytest.mark.parametrize(
        "server_url",
        ["https://cdn.example/thumbor", "/thumbor", "thumbor"],
    )
    def test_no_trailing_slash(self, server_url):
        assert not resolve_thumbor_prefix(
            server_url, "https://plone.example"
        ).endswith("/")

    @pytest.mark.parametrize(
        "server_url,expected_prefix",
        [
            ("https://cdn.example/thumbor", "https://"),
            ("/thumbor", "https://"),
            ("thumbor", "https://"),
        ],
    )
    def test_output_always_absolute(self, server_url, expected_prefix):
        result = resolve_thumbor_prefix(server_url, "https://plone.example")
        assert result.startswith(expected_prefix)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_prefix.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'plone.pgthumbor.prefix'`

- [ ] **Step 3: Write minimal implementation**

Write `src/plone/pgthumbor/prefix.py`:

```python
"""Resolve PGTHUMBOR_SERVER_URL into a concrete URL prefix.

Three forms are accepted, distinguished syntactically:

1. **Absolute URL** (``http://…`` / ``https://…``): used as-is.
   Ignores the navigation-root URL.  For deployments where Thumbor
   lives on a dedicated host / CDN.

2. **Root-relative path** (starts with ``/``): anchored at the
   scheme + host of the navigation-root URL, ignoring any path
   portion of it.  For deployments where Thumbor shares the host
   with Plone and multiple subsites live under different hostnames.

3. **Nav-root-relative path** (no scheme, no leading slash):
   appended to the full navigation-root URL.  For deployments where
   Plone itself lives under a subpath (``/2019``) and Thumbor is
   mounted under the same subpath (``/2019/thumbor``), or where
   several parallel Plone subpath mounts in one Zope each have their
   own Thumbor mount.
"""

from __future__ import annotations

from urllib.parse import urlsplit


_ALLOWED_SCHEMES = ("http://", "https://")


def resolve_thumbor_prefix(server_url: str, nav_root_url: str) -> str:
    """Compose the absolute URL prefix for a Thumbor signed path.

    Args:
        server_url: The ``PGTHUMBOR_SERVER_URL`` configuration value.
        nav_root_url: ``navigation_root.absolute_url()`` — what the
            current request sees as the site or subsite URL.

    Returns:
        An absolute URL with no trailing slash, suitable for
        ``f"{prefix}/{signed_path}"`` composition.

    Raises:
        ValueError: if ``server_url`` is empty, contains whitespace,
            has a query / fragment, or is otherwise malformed.
    """
    if not server_url or server_url.strip() != server_url:
        raise ValueError(
            "PGTHUMBOR_SERVER_URL is empty or has surrounding whitespace"
        )
    if "?" in server_url or "#" in server_url:
        raise ValueError(
            "PGTHUMBOR_SERVER_URL must not contain a query string or fragment"
        )

    if server_url.startswith(_ALLOWED_SCHEMES):
        parts = urlsplit(server_url)
        if not parts.netloc:
            raise ValueError(
                f"PGTHUMBOR_SERVER_URL {server_url!r} has a scheme but no host"
            )
        return server_url.rstrip("/")

    nav_root = nav_root_url.rstrip("/")

    if server_url.startswith("/"):
        host_root = _host_root(nav_root)
        return f"{host_root}{server_url.rstrip('/')}"

    return f"{nav_root}/{server_url.rstrip('/')}"


def _host_root(absolute_url: str) -> str:
    """Return scheme + host portion of an absolute URL, no path, no slash."""
    parts = urlsplit(absolute_url)
    return f"{parts.scheme}://{parts.netloc}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_prefix.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/plone/pgthumbor/prefix.py tests/test_prefix.py
git commit -m "feat: add resolve_thumbor_prefix helper for hosting-aware URLs"
```

---

## Task 2: Strict validation in `ThumborConfig`

**Files:**
- Modify: `src/plone/pgthumbor/config.py:24-27` (the `__post_init__`)
- Test: `tests/test_config.py` (create if missing)

**Why:** Today a broken `PGTHUMBOR_SERVER_URL` silently slips through and only fails at render time with a confusing stack trace. With three accepted forms and new semantics, we reject malformed values at config load.

- [ ] **Step 1: Check whether `tests/test_config.py` exists**

Run: `ls tests/test_config.py 2>/dev/null || echo MISSING`

If it prints `MISSING`, create it with this header:

```python
"""Tests for Thumbor configuration loading."""

from __future__ import annotations

from plone.pgthumbor.config import get_thumbor_config
from tests.conftest import env_override

import pytest
```

Otherwise, keep its existing content and append the new tests.

- [ ] **Step 2: Write the failing tests (append)**

Append to `tests/test_config.py`:

```python
class TestServerUrlValidation:
    """Syntactic validation of PGTHUMBOR_SERVER_URL in get_thumbor_config()."""

    def test_absolute_url_accepted(self, monkeypatch):
        env_override(
            monkeypatch,
            PGTHUMBOR_SERVER_URL="https://cdn.example/thumbor",
            PGTHUMBOR_SECURITY_KEY="key",
        )
        cfg = get_thumbor_config()
        assert cfg is not None
        assert cfg.server_url == "https://cdn.example/thumbor"

    def test_root_relative_accepted(self, monkeypatch):
        env_override(
            monkeypatch,
            PGTHUMBOR_SERVER_URL="/thumbor",
            PGTHUMBOR_SECURITY_KEY="key",
        )
        cfg = get_thumbor_config()
        assert cfg is not None
        assert cfg.server_url == "/thumbor"

    def test_nav_root_relative_accepted(self, monkeypatch):
        env_override(
            monkeypatch,
            PGTHUMBOR_SERVER_URL="thumbor",
            PGTHUMBOR_SECURITY_KEY="key",
        )
        cfg = get_thumbor_config()
        assert cfg is not None
        assert cfg.server_url == "thumbor"

    def test_trailing_slash_stripped(self, monkeypatch):
        env_override(
            monkeypatch,
            PGTHUMBOR_SERVER_URL="/thumbor/",
            PGTHUMBOR_SECURITY_KEY="key",
        )
        cfg = get_thumbor_config()
        assert cfg.server_url == "/thumbor"

    def test_query_string_rejected(self, monkeypatch):
        env_override(
            monkeypatch,
            PGTHUMBOR_SERVER_URL="/thumbor?x=1",
            PGTHUMBOR_SECURITY_KEY="key",
        )
        with pytest.raises(ValueError):
            get_thumbor_config()

    def test_fragment_rejected(self, monkeypatch):
        env_override(
            monkeypatch,
            PGTHUMBOR_SERVER_URL="/thumbor#x",
            PGTHUMBOR_SECURITY_KEY="key",
        )
        with pytest.raises(ValueError):
            get_thumbor_config()

    def test_scheme_without_host_rejected(self, monkeypatch):
        env_override(
            monkeypatch,
            PGTHUMBOR_SERVER_URL="https://",
            PGTHUMBOR_SECURITY_KEY="key",
        )
        with pytest.raises(ValueError):
            get_thumbor_config()
```

- [ ] **Step 3: Run tests — the rejection tests must fail**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: the three `*_rejected` tests FAIL (no `ValueError` raised), the accepted tests PASS.

- [ ] **Step 4: Add validation**

Modify [`src/plone/pgthumbor/config.py`](../../../src/plone/pgthumbor/config.py) — replace `__post_init__` with:

```python
    def __post_init__(self):
        from plone.pgthumbor.prefix import _ALLOWED_SCHEMES

        server_url = self.server_url
        if not server_url or server_url.strip() != server_url:
            raise ValueError(
                "PGTHUMBOR_SERVER_URL is empty or has surrounding whitespace"
            )
        if "?" in server_url or "#" in server_url:
            raise ValueError(
                "PGTHUMBOR_SERVER_URL must not contain a query string or "
                "fragment"
            )
        if server_url.startswith(_ALLOWED_SCHEMES):
            from urllib.parse import urlsplit

            parts = urlsplit(server_url)
            if not parts.netloc:
                raise ValueError(
                    f"PGTHUMBOR_SERVER_URL {server_url!r} has a scheme "
                    "but no host"
                )
        # Normalise: strip trailing slash
        object.__setattr__(self, "server_url", server_url.rstrip("/"))
```

- [ ] **Step 5: Run tests — all pass now**

Run: `.venv/bin/pytest tests/test_config.py tests/test_prefix.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/plone/pgthumbor/config.py tests/test_config.py
git commit -m "feat: validate PGTHUMBOR_SERVER_URL syntactic forms up front"
```

---

## Task 3: `thumbor_url()` returns the signed path only

**Files:**
- Modify: `src/plone/pgthumbor/url.py` (the whole `thumbor_url` function)
- Modify: `src/plone/pgthumbor/scaling.py:99-110` (`_build_thumbor_url` call site)

**Why:** Prefix composition moves to the call sites. `thumbor_url()` becomes a pure path-builder so it's easier to test and has no hosting knowledge.

- [ ] **Step 1: Update the existing url.py tests to expect a leading-slash path**

Open `tests/test_url.py` (it exists — verify with `ls tests/test_url.py`). Find every test that calls `thumbor_url(server_url=...)` and update them as follows:

For each test, replace `thumbor_url(server_url="http://thumbor:8888", security_key=KEY, ...)` with `thumbor_url(security_key=KEY, ...)` and replace the assertion `assert result.startswith("http://thumbor:8888")` with `assert result.startswith("/")`.

If a test currently asserts the full composed URL, replace it with:

```python
assert result.startswith("/")
assert "/42/ff" in result  # or whatever the specific path assertion is
```

If `tests/test_url.py` has assertions about the server_url portion itself, delete those tests — that concern now lives in `test_prefix.py`.

- [ ] **Step 2: Run the tests — they should fail**

Run: `.venv/bin/pytest tests/test_url.py -v`
Expected: FAIL — existing `thumbor_url()` still requires `server_url`.

- [ ] **Step 3: Update `url.py`**

Replace `src/plone/pgthumbor/url.py`'s `thumbor_url` signature and body:

```python
"""Thumbor URL generation using libthumbor."""

from __future__ import annotations

from libthumbor import CryptoURL


def thumbor_url(
    security_key: str,
    zoid: int,
    tid: int,
    width: int = 0,
    height: int = 0,
    smart: bool = False,
    fit_in: bool = False,
    unsafe: bool = False,
    filters: list[str] | None = None,
    content_zoid: int | None = None,
    crop: tuple[tuple[int, int], tuple[int, int]] | None = None,
) -> str:
    """Build the signed Thumbor path portion for a blob.

    The returned string always starts with ``/`` and does NOT include
    a server URL, host, or scheme.  The caller is responsible for
    prefixing the result with whatever
    :func:`plone.pgthumbor.prefix.resolve_thumbor_prefix` produces for
    the current request context.

    Args:
        security_key: HMAC-SHA1 signing key.
        zoid: Blob ZOID as integer.
        tid: Blob TID as integer.
        width: Target width (0 = proportional).
        height: Target height (0 = proportional).
        smart: Enable smart cropping.
        fit_in: Enable fit-in mode (no crop).
        unsafe: Generate unsigned ``/unsafe/`` URL.
        filters: Optional list of Thumbor filter strings.
        content_zoid: Content object ZOID for authenticated URLs
            (3-segment format).  When set, appends
            ``/{content_zoid:x}`` so the Thumbor auth handler can
            verify Plone access.
        crop: Optional crop box ``((left, top), (right, bottom))`` in
            pixels.

    Returns:
        The Thumbor-signed path starting with ``/`` — no host, no
        scheme.
    """
    image_url = f"{zoid:x}/{tid:x}"
    if content_zoid is not None:
        image_url += f"/{content_zoid:x}"

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
    if crop is not None:
        kwargs["crop"] = crop

    path = crypto.generate(**kwargs)
    if not path.startswith("/"):
        path = f"/{path}"
    return path


def scale_mode_to_thumbor(mode: str, smart_cropping: bool = False) -> dict:
    """Map a Plone scale mode to Thumbor parameters."""
    if mode == "cover":
        return {"fit_in": False, "smart": smart_cropping}
    if mode == "contain":
        return {"fit_in": True, "smart": False}
    return {"fit_in": True, "smart": smart_cropping}
```

- [ ] **Step 4: Update `_build_thumbor_url()` in `scaling.py`**

In [`src/plone/pgthumbor/scaling.py`](../../../src/plone/pgthumbor/scaling.py), change the imports near the top:

```python
from plone.api.portal import get_navigation_root
from plone.pgthumbor.prefix import resolve_thumbor_prefix
```

Replace the body of `_build_thumbor_url()` (lines 61-110) with:

```python
def _build_thumbor_url(context, data, width, height, mode, crop=None):
    """Build a Thumbor URL for the given image data and dimensions.

    Returns None if Thumbor is not applicable (SVG, no config, no blob).
    """
    content_type = getattr(data, "contentType", "") if data else ""
    if content_type in _SKIP_THUMBOR_TYPES:
        return None

    cfg = get_thumbor_config()
    if cfg is None:
        return None

    blob_ids = get_blob_ids(data)
    if blob_ids is None:
        return None

    zoid, tid = blob_ids

    # Determine whether to append content_zoid for access control
    content_zoid = None
    oid = getattr(context, "_p_oid", None)
    if isinstance(oid, bytes) and len(oid) == 8:
        content_zoid_int = u64(oid)
        if _needs_auth_url(context, content_zoid_int, cfg.paranoid_mode):
            content_zoid = content_zoid_int

    thumbor_params = scale_mode_to_thumbor(mode, smart_cropping=cfg.smart_cropping)
    if crop is not None:
        thumbor_params["fit_in"] = True
        thumbor_params["smart"] = False

    signed_path = thumbor_url(
        security_key=cfg.security_key,
        zoid=zoid,
        tid=tid,
        width=width,
        height=height,
        unsafe=cfg.unsafe,
        content_zoid=content_zoid,
        crop=crop,
        **thumbor_params,
    )

    nav_root = get_navigation_root(context)
    prefix = resolve_thumbor_prefix(cfg.server_url, nav_root.absolute_url())
    return f"{prefix}{signed_path}"
```

- [ ] **Step 5: Update `tests/test_scaling.py` to accept the new URL shapes**

In [`tests/test_scaling.py`](../../../tests/test_scaling.py), find the `_setup_env` helper and note that tests use `SERVER = "http://thumbor:8888"`. Make sure the context mock in `TestThumborImageScale.test_url_is_thumbor_url` has a navigation-root patchable entry point. Monkeypatch `plone.pgthumbor.scaling.get_navigation_root` to return a `MagicMock` whose `absolute_url()` returns `"http://plone:8080"`.

Change the test:

```python
def test_url_is_thumbor_url(self, monkeypatch):
    from plone.pgthumbor.scaling import ThumborImageScale
    from plone.pgthumbor import scaling as scaling_mod

    _setup_env(monkeypatch)
    ctx = MagicMock()
    ctx.absolute_url.return_value = "http://plone:8080/doc"
    nav_root = MagicMock()
    nav_root.absolute_url.return_value = "http://plone:8080"
    monkeypatch.setattr(scaling_mod, "get_navigation_root", lambda c: nav_root)
    request = MagicMock()
    data = _mock_image_data()

    scale = ThumborImageScale(
        ctx,
        request,
        data=data,
        fieldname="image",
        width=400,
        height=300,
        uid="image-400-abc123",
        mimetype="image/jpeg",
    )

    # Absolute PGTHUMBOR_SERVER_URL → URL starts with that host
    assert scale.url.startswith(SERVER)
    assert "/42/ff" in scale.url
```

Apply the same `get_navigation_root` monkeypatch to any other test in `TestThumborImageScale` / `TestSmartCropping` that calls `ThumborImageScale(...)` or `_build_thumbor_url(...)`.

- [ ] **Step 6: Run the whole test suite**

Run: `.venv/bin/pytest -v`
Expected: all PASS (tests updated in previous step).

- [ ] **Step 7: Commit**

```bash
git add src/plone/pgthumbor/url.py src/plone/pgthumbor/scaling.py tests/test_url.py tests/test_scaling.py
git commit -m "refactor: thumbor_url returns signed path only; prefix resolved at call site"
```

---

## Task 4: Direct-URL tests for all three config forms

**Files:**
- Modify: `tests/test_scaling.py`

Prove that direct `@@images/<uid>` URLs (the path that doesn't go through the catalog) work for all three `PGTHUMBOR_SERVER_URL` forms.

- [ ] **Step 1: Add a parametrized test**

Append to `tests/test_scaling.py` (inside `TestThumborImageScale`):

```python
import pytest


@pytest.mark.parametrize(
    "server_url, nav_root, expected_prefix",
    [
        ("https://cdn.example/thumbor", "https://site-a.example/2019",
         "https://cdn.example/thumbor"),
        ("/thumbor", "https://site-a.example/2019",
         "https://site-a.example/thumbor"),
        ("thumbor", "https://arch.example/2019",
         "https://arch.example/2019/thumbor"),
    ],
)
def test_url_uses_configured_prefix(
    monkeypatch, server_url, nav_root, expected_prefix
):
    from plone.pgthumbor import scaling as scaling_mod
    from plone.pgthumbor.scaling import ThumborImageScale

    env_override(
        monkeypatch,
        PGTHUMBOR_SERVER_URL=server_url,
        PGTHUMBOR_SECURITY_KEY=KEY,
    )
    nav_root_mock = MagicMock()
    nav_root_mock.absolute_url.return_value = nav_root
    monkeypatch.setattr(
        scaling_mod, "get_navigation_root", lambda c: nav_root_mock
    )

    ctx = MagicMock()
    ctx.absolute_url.return_value = f"{nav_root}/doc"
    request = MagicMock()
    data = _mock_image_data()

    scale = ThumborImageScale(
        ctx,
        request,
        data=data,
        fieldname="image",
        width=400,
        height=300,
        uid="image-400-abc123",
        mimetype="image/jpeg",
    )

    assert scale.url.startswith(expected_prefix + "/")
    assert "/42/ff" in scale.url
```

(Check that `env_override` is already imported at the top of the file — if not, add `from tests.conftest import env_override`.)

- [ ] **Step 2: Run tests**

Run: `.venv/bin/pytest tests/test_scaling.py -v -k prefix`
Expected: all three parametrized variants PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_scaling.py
git commit -m "test: cover absolute / root-relative / nav-root-relative URL forms"
```

---

## Task 5: Write-side field adapter stores signed paths

**Files:**
- Create: `src/plone/pgthumbor/field_adapter.py`
- Create: `tests/test_field_adapter.py`

**Why:** The inherited `_scale_view_from_url()` in `plone.namedfile.adapters.ImageFieldScales` mangles Thumbor URLs (`.lstrip("/")` eats our leading slash; host-prefixed URLs get stored verbatim, baking the host into catalog rows). We override it to recognise Thumbor URLs — whose final form came through `ThumborImageScale._scale_url` — and strip them back to the stable, hosting-agnostic signed path, marked as `thumbor:<path>`.

**Design note:** We recognise a Thumbor URL as one that includes a known signature shape, but the simpler reliable signal is: on our layer, the `@@images` view is our `ThumborImageScaling`, whose `scale.url` is always a Thumbor URL when `get_thumbor_config()` returns non-None. So we resolve the prefix for the current context, and if the URL starts with that prefix, strip it. Otherwise fall back to the default behaviour (handles non-image fields and Thumbor-skipped content types like SVG).

- [ ] **Step 1: Write the failing test**

Write `tests/test_field_adapter.py`:

```python
"""Tests for the write-side image_scales field adapter."""

from __future__ import annotations

from tests.conftest import env_override
from unittest.mock import MagicMock


KEY = "test-secret-key"


def _make_adapter(monkeypatch, server_url, nav_root):
    from plone.pgthumbor import field_adapter as fa_mod
    from plone.pgthumbor.field_adapter import ThumborImageScalesFieldAdapter

    env_override(
        monkeypatch,
        PGTHUMBOR_SERVER_URL=server_url,
        PGTHUMBOR_SECURITY_KEY=KEY,
    )
    nav_root_mock = MagicMock()
    nav_root_mock.absolute_url.return_value = nav_root
    monkeypatch.setattr(
        fa_mod, "get_navigation_root", lambda c: nav_root_mock
    )

    field = MagicMock()
    field.__name__ = "image"
    context = MagicMock()
    context.absolute_url.return_value = f"{nav_root}/folder/doc"
    request = MagicMock()

    return ThumborImageScalesFieldAdapter(field, context, request)


class TestScaleViewFromUrl:
    """The download value stored in image_scales metadata."""

    def test_absolute_thumbor_url_stored_as_signed_path_marker(
        self, monkeypatch
    ):
        adapter = _make_adapter(
            monkeypatch, "https://cdn.example/thumbor", "https://plone.example"
        )
        stored = adapter._scale_view_from_url(
            "https://cdn.example/thumbor/abc/300x200/fit-in/42/ff"
        )
        assert stored == "thumbor:/abc/300x200/fit-in/42/ff"

    def test_root_relative_thumbor_url_stored_as_signed_path_marker(
        self, monkeypatch
    ):
        adapter = _make_adapter(
            monkeypatch, "/thumbor", "https://plone.example"
        )
        stored = adapter._scale_view_from_url(
            "https://plone.example/thumbor/abc/300x200/fit-in/42/ff"
        )
        assert stored == "thumbor:/abc/300x200/fit-in/42/ff"

    def test_nav_root_relative_thumbor_url_stored_as_signed_path_marker(
        self, monkeypatch
    ):
        adapter = _make_adapter(
            monkeypatch, "thumbor", "https://arch.example/2019"
        )
        stored = adapter._scale_view_from_url(
            "https://arch.example/2019/thumbor/abc/300x200/fit-in/42/ff"
        )
        assert stored == "thumbor:/abc/300x200/fit-in/42/ff"

    def test_legacy_images_url_falls_back_to_default(self, monkeypatch):
        # Non-Thumbor URL (e.g. SVG — ThumborImageScale returns default)
        # must retain the default behaviour: strip context URL + leading "/"
        adapter = _make_adapter(
            monkeypatch, "https://cdn.example/thumbor", "https://plone.example"
        )
        stored = adapter._scale_view_from_url(
            "https://plone.example/folder/doc/@@images/uid-xyz.svg"
        )
        assert stored == "@@images/uid-xyz.svg"

    def test_no_config_falls_back_to_default(self, monkeypatch):
        # When Thumbor isn't configured the adapter must not crash.
        from plone.pgthumbor import field_adapter as fa_mod
        from plone.pgthumbor.field_adapter import (
            ThumborImageScalesFieldAdapter,
        )

        env_override(monkeypatch)  # all vars cleared → cfg = None

        field = MagicMock()
        field.__name__ = "image"
        context = MagicMock()
        context.absolute_url.return_value = "https://plone.example/folder/doc"
        request = MagicMock()
        adapter = ThumborImageScalesFieldAdapter(field, context, request)

        stored = adapter._scale_view_from_url(
            "https://plone.example/folder/doc/@@images/uid-xyz.jpeg"
        )
        assert stored == "@@images/uid-xyz.jpeg"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_field_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'plone.pgthumbor.field_adapter'`

- [ ] **Step 3: Write the implementation**

Write `src/plone/pgthumbor/field_adapter.py`:

```python
"""Write-side override of image_scales field adapter.

Stores Thumbor URLs as hosting-agnostic signed paths (prefixed with
``thumbor:``) instead of letting the inherited behaviour bake the
current Thumbor host into the catalog metadata.
"""

from __future__ import annotations

from plone.api.portal import get_navigation_root
from plone.namedfile.adapters import ImageFieldScales
from plone.pgthumbor.config import get_thumbor_config
from plone.pgthumbor.prefix import resolve_thumbor_prefix


THUMBOR_MARKER = "thumbor:"


class ThumborImageScalesFieldAdapter(ImageFieldScales):
    """Write the Thumbor signed path (not the full URL) into metadata.

    Recognises a Thumbor URL by checking whether the incoming ``url``
    starts with the prefix that :func:`resolve_thumbor_prefix` would
    produce for the current request context. If it does, strip the
    prefix and prepend ``thumbor:`` so the read side can identify
    entries that need render-time prefix re-composition.

    Non-Thumbor URLs (SVGs, non-image fields) fall back to the
    inherited behaviour.
    """

    def _scale_view_from_url(self, url):
        cfg = get_thumbor_config()
        if cfg is not None:
            nav_root = get_navigation_root(self.context)
            prefix = resolve_thumbor_prefix(
                cfg.server_url, nav_root.absolute_url()
            )
            if url.startswith(prefix + "/") or url == prefix:
                signed_path = url[len(prefix):] or "/"
                return f"{THUMBOR_MARKER}{signed_path}"
        return super()._scale_view_from_url(url)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_field_adapter.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/plone/pgthumbor/field_adapter.py tests/test_field_adapter.py
git commit -m "feat: write-side adapter stores Thumbor signed paths with thumbor: marker"
```

---

## Task 6: Read-side `ThumborNavigationRootScaling`

**Files:**
- Modify: `src/plone/pgthumbor/scaling.py` — add `ThumborNavigationRootScaling` class at the bottom
- Create: `tests/test_nav_root_scaling.py`

**Why:** At render time, turn `thumbor:<signed_path>` back into a full URL using the current navigation root.

- [ ] **Step 1: Write the failing test**

Write `tests/test_nav_root_scaling.py`:

```python
"""Tests for the @@image_scale view read path."""

from __future__ import annotations

from tests.conftest import env_override
from unittest.mock import MagicMock

import pytest


KEY = "test-secret-key"


def _make_brain(download, field="image", width=300, height=200):
    brain = MagicMock()
    brain.image_scales = {
        field: [{"scales": {"preview": {
            "download": download,
            "width": width,
            "height": height,
        }}}]
    }
    brain.getURL.return_value = "https://plone.example/folder/doc"
    brain.Title = "An Image"
    return brain


def _make_view(monkeypatch, nav_root_url, server_url=None):
    from plone.pgthumbor import scaling as scaling_mod
    from plone.pgthumbor.scaling import ThumborNavigationRootScaling

    if server_url is not None:
        env_override(
            monkeypatch,
            PGTHUMBOR_SERVER_URL=server_url,
            PGTHUMBOR_SECURITY_KEY=KEY,
        )
    else:
        env_override(monkeypatch)

    context = MagicMock()
    context.absolute_url.return_value = nav_root_url
    request = MagicMock()
    view = ThumborNavigationRootScaling(context, request)
    # Avoid ram-cache side effects in unit tests.
    monkeypatch.setattr(
        scaling_mod.ThumborNavigationRootScaling,
        "_supports_hidpi",
        False,
    )
    return view


class TestThumborMarkerRendering:
    @pytest.mark.parametrize(
        "server_url, nav_root, expected_prefix",
        [
            ("https://cdn.example/thumbor", "https://plone.example",
             "https://cdn.example/thumbor"),
            ("/thumbor", "https://site-a.example/2019",
             "https://site-a.example/thumbor"),
            ("thumbor", "https://arch.example/2019",
             "https://arch.example/2019/thumbor"),
        ],
    )
    def test_marker_gets_resolved_with_current_prefix(
        self, monkeypatch, server_url, nav_root, expected_prefix
    ):
        view = _make_view(monkeypatch, nav_root, server_url)
        brain = _make_brain("thumbor:/abc/300x200/fit-in/42/ff")

        tag = view._tag_from_brain_image_scales(
            brain, "image", scale="preview"
        )

        assert tag is not None
        assert f'src="{expected_prefix}/abc/300x200/fit-in/42/ff"' in tag


class TestLegacyDownloadRendering:
    def test_http_download_used_verbatim(self, monkeypatch):
        # Backwards compat: a legacy absolute URL still renders directly.
        view = _make_view(
            monkeypatch, "https://plone.example", "https://cdn.example/thumbor"
        )
        brain = _make_brain("https://legacy.example/foo/bar.jpg")

        tag = view._tag_from_brain_image_scales(
            brain, "image", scale="preview"
        )
        assert 'src="https://legacy.example/foo/bar.jpg"' in tag

    def test_legacy_relative_download_concatenated_to_brain_url(
        self, monkeypatch
    ):
        view = _make_view(
            monkeypatch, "https://plone.example", "https://cdn.example/thumbor"
        )
        brain = _make_brain("@@images/uid-xyz.jpeg")

        tag = view._tag_from_brain_image_scales(
            brain, "image", scale="preview"
        )
        assert (
            'src="https://plone.example/folder/doc/@@images/uid-xyz.jpeg"'
            in tag
        )


class TestMissingOrInvalidMetadata:
    def test_no_scale_returns_none(self, monkeypatch):
        view = _make_view(
            monkeypatch, "https://plone.example", "https://cdn.example/thumbor"
        )
        brain = MagicMock()
        brain.image_scales = None
        assert (
            view._tag_from_brain_image_scales(brain, "image", scale="preview")
            is None
        )

    def test_thumbor_marker_without_config_falls_back(self, monkeypatch):
        # If someone removed PGTHUMBOR_SERVER_URL but stored marker
        # entries are still present, we must not crash — return None
        # so the caller falls back to object-based rendering.
        view = _make_view(monkeypatch, "https://plone.example", server_url=None)
        brain = _make_brain("thumbor:/abc/300x200/42/ff")
        assert (
            view._tag_from_brain_image_scales(brain, "image", scale="preview")
            is None
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_nav_root_scaling.py -v`
Expected: FAIL — `ThumborNavigationRootScaling` doesn't exist yet.

- [ ] **Step 3: Add `ThumborNavigationRootScaling`**

Append to [`src/plone/pgthumbor/scaling.py`](../../../src/plone/pgthumbor/scaling.py):

```python
from plone.namedfile.scaling import NavigationRootScaling
from plone.pgthumbor.field_adapter import THUMBOR_MARKER


class ThumborNavigationRootScaling(NavigationRootScaling):
    """@@image_scale view that resolves Thumbor markers at render time."""

    def _tag_from_brain_image_scales(
        self, brain, fieldname, scale=None, **kwargs
    ):
        # Reuse the parent's validation / hidpi / lookup logic by
        # pulling the relevant stored dict ourselves — but we need to
        # handle our custom "thumbor:" marker before the parent's
        # plain "startswith http" branch runs.
        if self._supports_hidpi:
            return None
        if not (brain and fieldname and scale):
            return None
        if not getattr(brain, "image_scales", None):
            return None
        if fieldname not in brain.image_scales:
            return None
        try:
            data = brain.image_scales[fieldname][0]["scales"][scale]
        except (KeyError, IndexError):
            return None

        download = data["download"]
        if download.startswith(THUMBOR_MARKER):
            cfg = get_thumbor_config()
            if cfg is None:
                # Thumbor not configured anymore; caller falls back
                # to object-based rendering.
                return None
            signed_path = download[len(THUMBOR_MARKER):]
            prefix = resolve_thumbor_prefix(
                cfg.server_url, self.context.absolute_url()
            )
            src = f"{prefix}{signed_path}"
            return self._render_image_tag(brain, data, src, **kwargs)

        # No Thumbor marker → delegate to the inherited implementation.
        return super()._tag_from_brain_image_scales(
            brain, fieldname, scale=scale, **kwargs
        )

    def _render_image_tag(self, brain, data, src, alt=_marker,
                          css_class=None, title=_marker, **kwargs):
        from plone.namedfile.scaling import _image_tag_from_values, _marker

        if title is _marker:
            title = brain.Title
            if callable(title):
                title = title()
        if alt is _marker:
            alt = title
        values = [
            ("src", src),
            ("alt", alt),
            ("title", title),
            ("height", data["height"]),
            ("width", data["width"]),
        ]
        if css_class:
            values.append(("class", css_class))
        return _image_tag_from_values(*values)
```

At the top of `scaling.py`, ensure these imports exist (add as needed):

```python
from plone.namedfile.scaling import NavigationRootScaling
from plone.namedfile.scaling import _image_tag_from_values
from plone.namedfile.scaling import _marker
from plone.pgthumbor.field_adapter import THUMBOR_MARKER
```

Move the `_image_tag_from_values`/`_marker` imports to module level (don't re-import them inside `_render_image_tag`) — that inner import was just to keep the snippet above self-contained. The final version has the imports at the top.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_nav_root_scaling.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/plone/pgthumbor/scaling.py tests/test_nav_root_scaling.py
git commit -m "feat: ThumborNavigationRootScaling resolves thumbor: markers at render time"
```

---

## Task 7: Wire new adapters and view into ZCML

**Files:**
- Modify: `src/plone/pgthumbor/overrides.zcml`

- [ ] **Step 1: Update ZCML**

Replace `src/plone/pgthumbor/overrides.zcml` with:

```xml
<configure
    xmlns="http://namespaces.zope.org/zope"
    xmlns:browser="http://namespaces.zope.org/browser">

  <!-- Override @@images view (on content objects) to use Thumbor URL generation -->
  <browser:page
      name="images"
      for="plone.namedfile.interfaces.IImageScaleTraversable"
      class=".scaling.ThumborImageScaling"
      allowed_attributes="scale tag"
      permission="zope2.View"
      layer=".interfaces.IPlonePgthumborLayer"
      />

  <!-- Override @@image_scale view (on the navigation root) to resolve
       thumbor: markers stored in image_scales brain metadata -->
  <browser:page
      name="image_scale"
      for="plone.base.interfaces.INavigationRoot"
      class=".scaling.ThumborNavigationRootScaling"
      allowed_attributes="scale tag"
      permission="zope2.View"
      layer=".interfaces.IPlonePgthumborLayer"
      />

  <!-- Override plone.namedfile's IImageScalesFieldAdapter so the
       write side stores hosting-agnostic signed paths instead of
       full absolute URLs. -->
  <adapter
      for="plone.namedfile.interfaces.INamedImageField
           plone.dexterity.interfaces.IDexterityContent
           .interfaces.IPlonePgthumborLayer"
      factory=".field_adapter.ThumborImageScalesFieldAdapter"
      provides="plone.base.interfaces.IImageScalesFieldAdapter"
      />

  <!-- Override scale storage to prevent Pillow invocation and ZODB writes. -->
  <adapter
      for="plone.namedfile.interfaces.IImageScaleTraversable *"
      factory=".storage.thumbor_scale_storage_factory"
      provides="plone.scale.storage.IImageScaleStorage"
      />

</configure>
```

- [ ] **Step 2: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all PASS (ZCML is loaded by Plone at startup; the unit tests don't exercise it, but we want to make sure we haven't broken anything in the Python imports).

- [ ] **Step 3: Commit**

```bash
git add src/plone/pgthumbor/overrides.zcml
git commit -m "feat: register ThumborNavigationRootScaling and field adapter"
```

---

## Task 8: Changelog + release + docs

**Files:**
- Modify: `CHANGES.md`
- Modify: `docs/sources/how-to/configure-plone.md`
- Modify: `README.md` (if there's a configuration-quickstart section)

- [ ] **Step 1: Update `CHANGES.md`**

Replace the current `## 0.6.3 (unreleased)` header with `## 0.7.0 (unreleased)` and fill in the body:

```markdown
## 0.7.0 (unreleased)

- **Breaking / storage change**: `image_scales` brain metadata now
  stores Thumbor URLs as hosting-agnostic signed paths (prefixed
  with `thumbor:`).  At render time the path is composed against
  the current navigation root.

  Three forms of `PGTHUMBOR_SERVER_URL` are now accepted:

  - **Absolute URL** (e.g. `https://cdn.example/thumbor`): used
    verbatim. For dedicated Thumbor hosts / CDNs.
  - **Root-relative path** (e.g. `/thumbor`): anchored at the
    current request host. For shared-host setups, including
    multiple subsites on different hostnames.
  - **Nav-root-relative path** (e.g. `thumbor`): appended to the
    full navigation-root URL. For Plone-under-subpath deployments
    and for one Zope serving several parallel Plone mounts, each
    with its own Thumbor subpath.

  Malformed values (query strings, fragments, schemes without a
  host) are now rejected at configuration load rather than at
  render time.

  **Upgrade**: run `@@thumbor-purge-scales` once so existing brain
  metadata gets rewritten into the new shape.  Closes
  [#7](https://github.com/bluedynamics/plone-pgthumbor/issues/7).
```

- [ ] **Step 2: Update `docs/sources/how-to/configure-plone.md`**

Find the section describing `PGTHUMBOR_SERVER_URL`. Replace it with a table of the three forms and which scenarios they fit, mirroring the matrix from issue #7.

Keep the existing language plain and the table the same shape as the other env-var docs in that file.

- [ ] **Step 3: Run the vale / ruff hook via a dry-run commit**

```bash
.venv/bin/pytest -v
git add CHANGES.md docs/sources/how-to/configure-plone.md
git commit -m "docs: document three PGTHUMBOR_SERVER_URL forms for issue #7 fix"
```

Expected: vale passes; if it flags wording, adjust and recommit.

- [ ] **Step 4: Follow the release process in `RELEASE.md`**

For the actual release, follow `RELEASE.md` step-by-step — **do not run it automatically from inside this plan's execution**. The human driving the plan decides when to cut 0.7.0.

---

## Task 9: Verify end-to-end with the sample Plone instance

**Files:** none — this is a smoke test.

**Why:** The unit tests cover the logic. This task exercises the actual ZCML + Plone indexer integration.

- [ ] **Step 1: Install 0.7.0-dev locally**

In the dev Plone instance (outside this repo), `uv pip install -e /path/to/sources/plone-pgthumbor`.

- [ ] **Step 2: Set each config form in turn**

For each of the three forms, restart the instance with:

- `PGTHUMBOR_SERVER_URL=https://localhost:8888` (absolute)
- `PGTHUMBOR_SERVER_URL=/thumbor` (root-relative)
- `PGTHUMBOR_SERVER_URL=thumbor` (nav-root-relative)

Upload an image, view a folder listing that renders it from the brain, inspect the rendered HTML (`view-source:` in the browser), and confirm the `src` attribute matches the expected prefix for that form.

- [ ] **Step 3: Run `@@thumbor-purge-scales`**

Visit `/@@thumbor-purge-scales` once after the upgrade. Confirm that existing image_scales metadata is rewritten and that the listing still renders correctly.

- [ ] **Step 4: Record findings**

If anything doesn't match expectations, drop back into the plan at the relevant task. Otherwise, we're done.

---

## Self-Review

**Spec coverage** (against issue #7 comment's scenario matrix and fix proposal):

- Scenario 1 (absolute URL) — Task 1 test `TestAbsoluteForm`, Task 4 parametrized, Task 6 `TestThumborMarkerRendering`.
- Scenario 2 (root-relative, single-host) — same test classes.
- Scenario 3 (root-relative, multi-subsite) — Task 1 covers the resolution; by construction, the same prefix resolver runs per request, producing different hosts naturally.
- Scenario 4 (Plone subpath + Thumbor at host root) — covered by the root-relative form tests (Task 1 `TestRootRelativeForm.test_host_only_nav_root` and the parametrized Task 6 test).
- Scenario 5 (Plone subpath + Thumbor under same subpath) — Task 1 `TestNavRootRelativeForm.test_single_segment`, Task 6 `TestThumborMarkerRendering` nav-root-relative variant.
- Scenario 6 (multiple parallel Plone subpaths) — Task 1 `TestNavRootRelativeForm.test_parallel_mounts`, plus the Task 6 view test produces different prefixes for different nav roots via pure-function input changes.
- Scenario 7 (subpath Plone + Thumbor on different host) — absolute-URL form tests.
- Scenario 8 (multi-nav-root in one site) — same mechanism as 3; the view gets a different `self.context` per nav root.
- Proposal item "storage shape: `thumbor:<signed_path>`" — Task 5 implements, Task 6 reads.
- Proposal item "syntactic form validation up front" — Task 2.
- Proposal item "one-time `@@thumbor-purge-scales` migration" — called out in CHANGES.md (Task 8) and the smoke test (Task 9).
- "No new env var" — configuration layer unchanged except for validation.
- "Direct-access path still works" — Task 4.

**Placeholder scan:** searched the document for "TBD", "TODO", "implement later", "similar to task" — none present.

**Type / signature consistency:**

- `resolve_thumbor_prefix(server_url: str, nav_root_url: str) -> str` used identically in Tasks 1, 3, 5, 6.
- `THUMBOR_MARKER = "thumbor:"` defined in Task 5, consumed in Task 6 and in the render tests (marker string is quoted inline).
- `thumbor_url()` new signature (no `server_url` arg) introduced in Task 3 is the same everywhere it's called afterwards.
- Test file names: `test_prefix.py`, `test_config.py`, `test_url.py` (pre-existing), `test_scaling.py` (pre-existing), `test_field_adapter.py`, `test_nav_root_scaling.py` — no collisions, no renames mid-plan.
- `ThumborImageScalesFieldAdapter`, `ThumborNavigationRootScaling` — spelled the same in Python, ZCML, and tests.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-04-13-hosting-aware-thumbor-urls.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
