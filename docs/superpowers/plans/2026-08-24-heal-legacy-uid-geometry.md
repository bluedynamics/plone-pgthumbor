# Legacy uid Healing Geometry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `ThumborScaleStorage.get_or_generate` recover the exact scale a legacy uid was minted for, instead of guessing one from its width.

**Architecture:** A legacy uid is `{fieldname}-{width}-{md5hex32}`, where the md5 covers the full parameter set plus the field's modification time. The parameters cannot be read back out, but they can be enumerated and re-hashed. A new pure module produces the candidate parameter sets; the storage reconstructs the modification time, hashes each candidate with `plone.scale`'s own `hash_key`, and takes the one that matches. A separate change makes the recovered `mode` actually reach the Thumbor URL, which it does not today.

**Tech Stack:** Python 3.12+, `plone.scale` 5.x, `plone.namedfile` (production runs 7.x; dev and CI, with no lockfile, resolve 8.x), pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-08-24-heal-legacy-uid-geometry-design.md`

## Global Constraints

- Run every command from the worktree root: `/home/jensens/ws/cdev/cloudbrine/sources/plone-pgthumbor/.worktrees/fix-21-heal-legacy-uid-geometry`. This is a nested independent git repository; never `cd` to the outer `cloudbrine` checkout.
- Tests run as `uv run pytest`. The whole suite is fast (~2 s) and must stay green after every task. Baseline before this plan: 179 passed.
- Lint is `uvx ruff check .` and `uvx ruff format --check .`, both run in CI over the whole repo including markdown code fences. `uvx` fetches the latest ruff, so a clean local `.venv` ruff is not proof.
- Ruff isort is configured `force-single-line = true`, `from-first = true`, `no-sections = true`, `order-by-type = false`. Write one import per line and let `uvx ruff check --fix .` settle the order rather than hand-sorting.
- Both `plone.namedfile` paths must keep working: the 7.x path through `ThumborImageScale.__init__` and the 8.x path through `_scale_url`. `_HAS_SCALE_URL` in `scaling.py` selects between them. There is no lockfile pin, so dev/CI may resolve either.
- Every change needs a `CHANGES.md` entry in the same PR (Task 6).
- No ZODB writes anywhere on this path. `ThumborScaleStorage.storage` is a volatile dict and must stay one.

---

### Task 1: `uid_healing` module — uid parsing and registered scales

Two pure functions with no storage instance and no ZODB. `storage.py` keeps its own copies for now and is rewired in Task 3, so the suite stays green.

**Files:**
- Create: `src/plone/pgthumbor/uid_healing.py`
- Create: `tests/test_uid_healing.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `parse_legacy_uid(uid: str) -> tuple[str, int] | None` returning `(fieldname, dimension)`
  - `registered_scales() -> tuple[tuple[str, int, int], ...]` of `(name, width, height)` in registry order, duplicates kept
  - `SCALE_MODES: tuple[str | None, ...]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_uid_healing.py`:

```python
"""Tests for legacy uid parsing and candidate parameter recovery."""

from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import patch


class TestParseLegacyUid:
    """The deterministic uid shape from plone.scale >= 4."""

    def test_parses_fieldname_and_width(self):
        from plone.pgthumbor.uid_healing import parse_legacy_uid

        assert parse_legacy_uid("image-400-" + "a" * 32) == ("image", 400)

    def test_fieldname_may_contain_dashes(self):
        from plone.pgthumbor.uid_healing import parse_legacy_uid

        assert parse_legacy_uid("my-logo-field-200-" + "b" * 32) == (
            "my-logo-field",
            200,
        )

    def test_width_zero_is_kept_as_zero(self):
        from plone.pgthumbor.uid_healing import parse_legacy_uid

        assert parse_legacy_uid("image-0-" + "c" * 32) == ("image", 0)

    def test_rejects_non_hex_tail(self):
        from plone.pgthumbor.uid_healing import parse_legacy_uid

        assert parse_legacy_uid("image-400-nothex") is None

    def test_rejects_missing_hash(self):
        from plone.pgthumbor.uid_healing import parse_legacy_uid

        assert parse_legacy_uid("image-400") is None

    def test_rejects_oversized_width_without_raising(self):
        from plone.pgthumbor.uid_healing import parse_legacy_uid

        assert parse_legacy_uid("image-" + "9" * 5000 + "-" + "a" * 32) is None


class TestRegisteredScales:
    """Registry parsing. Order matters and duplicates must survive."""

    def _registry_with(self, lines):
        registry = MagicMock()
        registry.get.return_value = lines
        return registry

    def test_parses_registered_sizes(self):
        from plone.pgthumbor.uid_healing import registered_scales

        registry = self._registry_with(["preview 400:400", "large 800:65536"])
        with patch("plone.pgthumbor.uid_healing.queryUtility", return_value=registry):
            scales = registered_scales()

        assert scales == (("preview", 400, 400), ("large", 800, 65536))
        registry.get.assert_called_once_with("plone.allowed_sizes")

    def test_keeps_both_scales_sharing_a_width(self):
        """Issue #21 defect 2: collapsing these made Haeuser heal as preview."""
        from plone.pgthumbor.uid_healing import registered_scales

        registry = self._registry_with(["preview 400:0", "Haeuser 400:200"])
        with patch("plone.pgthumbor.uid_healing.queryUtility", return_value=registry):
            assert registered_scales() == (
                ("preview", 400, 0),
                ("Haeuser", 400, 200),
            )

    def test_keeps_height_driven_scales(self):
        from plone.pgthumbor.uid_healing import registered_scales

        registry = self._registry_with(["Header 0:460", "Bottom 0:270"])
        with patch("plone.pgthumbor.uid_healing.queryUtility", return_value=registry):
            assert registered_scales() == (("Header", 0, 460), ("Bottom", 0, 270))

    def test_skips_malformed_lines(self):
        from plone.pgthumbor.uid_healing import registered_scales

        registry = self._registry_with(
            ["broken", "no-dims 400", "preview 400:400", "bad 400x300"]
        )
        with patch("plone.pgthumbor.uid_healing.queryUtility", return_value=registry):
            assert registered_scales() == (("preview", 400, 400),)

    def test_no_registry_returns_empty(self):
        from plone.pgthumbor.uid_healing import registered_scales

        with patch("plone.pgthumbor.uid_healing.queryUtility", return_value=None):
            assert registered_scales() == ()

    def test_none_record_returns_empty(self):
        from plone.pgthumbor.uid_healing import registered_scales

        registry = self._registry_with(None)
        with patch("plone.pgthumbor.uid_healing.queryUtility", return_value=registry):
            assert registered_scales() == ()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_uid_healing.py -q`
Expected: collection errors, `ModuleNotFoundError: No module named 'plone.pgthumbor.uid_healing'`

- [ ] **Step 3: Write the module**

Create `src/plone/pgthumbor/uid_healing.py`:

```python
"""Recover the parameters a legacy scale uid was minted from.

plone.scale >= 4 mints deterministic uids of the form
``{fieldname}-{width}-{md5hex32}``.  The md5 covers the whole parameter set
plus the field's modification time, so the parameters cannot be read back out
of the uid.  They can, however, be enumerated and re-hashed: the registered
scales are known and the set of call shapes is small.

This module produces the candidates.  ``ThumborScaleStorage`` does the hashing
and the matching, because only it holds the context and the modification time.
"""

from __future__ import annotations

from plone.registry.interfaces import IRegistry
from zope.component import queryUtility

import re


# plone.scale >= 4 deterministic uid: {fieldname}-{width}-{md5hex}
_LEGACY_UID_RE = re.compile(
    r"^(?P<fieldname>.+)-(?P<width>\d{1,9})-(?P<hash>[0-9a-f]{32})$"
)

# ``hash_key`` hashes the raw mode string the caller passed, not the value
# ``get_scale_mode`` normalises it to, so "keep" and "scale" produce different
# uids for the same scale.  An alias that is not enumerated is a silent miss.
SCALE_MODES = (
    "scale",
    "cover",
    "contain",
    "keep",
    "thumbnail",
    "down",
    "up",
    "scale-crop-to-fit",
    "scale-crop-to-fill",
    None,
)


def parse_legacy_uid(uid):
    """Split a deterministic uid into ``(fieldname, dimension)``.

    Returns None when *uid* is not shaped like one.  ``dimension`` is the
    number plone.scale put in the uid: the requested width, or 0 when the call
    had no width at all.  The width group is length-limited so a multi-thousand
    digit uid cannot turn into an expensive int.
    """
    match = _LEGACY_UID_RE.match(uid)
    if match is None:
        return None
    return match.group("fieldname"), int(match.group("width"))


def registered_scales():
    """Return ``(name, width, height)`` for every ``plone.allowed_sizes`` entry.

    Registry order is preserved and duplicates are kept.  Two scales may share
    a width (``preview 400:0`` and ``Haeuser 400:200``), and collapsing them
    into a width-keyed mapping is what made healing pick the wrong one.
    Malformed lines are skipped rather than raising: the registry is editable
    through the control panel.
    """
    registry = queryUtility(IRegistry)
    if registry is None:
        return ()
    scales = []
    for line in registry.get("plone.allowed_sizes") or ():
        try:
            name, dims = line.split()
            width, height = dims.split(":")
            scales.append((name, int(width), int(height)))
        except ValueError:
            continue
    return tuple(scales)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_uid_healing.py -q`
Expected: 12 passed

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest -q && uvx ruff check --fix . && uvx ruff format .`
Expected: 191 passed, ruff clean

- [ ] **Step 6: Commit**

```bash
git add src/plone/pgthumbor/uid_healing.py tests/test_uid_healing.py
git commit -m "feat: add uid_healing module with uid parsing and registered scales

registered_scales() keeps registry order and duplicates, unlike the
width-keyed _allowed_scale_sizes() it will replace. Two scales sharing a
width made the later one unreachable (issue #21 defect 2)."
```

---

### Task 2: Candidate parameter enumeration

The part the issue underestimates. `hash_key` drops the `scale` key only when width *and* height are truthy, so a scale with a zero component is reachable under three different parameter sets that all hash differently.

**Files:**
- Modify: `src/plone/pgthumbor/uid_healing.py`
- Modify: `tests/test_uid_healing.py`

**Interfaces:**
- Consumes: `SCALE_MODES` from Task 1.
- Produces: `candidate_parameters(fieldname: str, dimension: int, scales: tuple, original_size: tuple[int, int] | None = None) -> Iterator[dict]`. Each yielded dict has exactly the keys `fieldname`, `width`, `height`, `mode`, and optionally `scale`, ready to splat into `hash_key(**parameters)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_uid_healing.py`:

```python
class TestCandidateParameters:
    """Every parameter set that could have minted a given uid."""

    def _candidates(self, fieldname, dimension, scales, original_size=None):
        from plone.pgthumbor.uid_healing import candidate_parameters

        return list(candidate_parameters(fieldname, dimension, scales, original_size))

    def test_both_dimensions_truthy_yields_one_shape_per_mode(self):
        """hash_key deletes the scale key when width and height are truthy,
        so the name path and the explicit-dimensions path collapse to one."""
        from plone.pgthumbor.uid_healing import SCALE_MODES

        candidates = self._candidates("image", 400, (("Haeuser", 400, 200),))

        assert len(candidates) == len(SCALE_MODES)
        assert all(c["scale"] is None for c in candidates)
        assert {c["mode"] for c in candidates} == set(SCALE_MODES)
        assert all((c["width"], c["height"]) == (400, 200) for c in candidates)

    def test_zero_height_yields_three_shapes_per_mode(self):
        """width 400, height 0: the scale key survives, so scale=None,
        scale="preview" and no scale key at all are three distinct uids."""
        from plone.pgthumbor.uid_healing import SCALE_MODES

        candidates = self._candidates("image", 400, (("preview", 400, 0),))

        assert len(candidates) == 3 * len(SCALE_MODES)

        shapes = [
            c.get("scale", "<absent>") for c in candidates if c["mode"] == "scale"
        ]
        assert len(shapes) == 3
        assert set(shapes) == {None, "preview", "<absent>"}

    def test_zero_width_scale_is_reachable(self):
        """Issue #21 defect 3: Header 0:460 must be a candidate, not a
        request for the original dimensions."""
        candidates = self._candidates("image", 0, (("Header", 0, 460),))

        assert any(
            (c["width"], c["height"]) == (0, 460) and c["scale"] == "Header"
            for c in candidates
        )

    def test_dimension_zero_also_offers_the_original(self):
        """A bare tag() without a width mints image-0-... too."""
        candidates = self._candidates("image", 0, (("Header", 0, 460),))

        no_dims = [c for c in candidates if c["width"] is None]
        assert no_dims
        assert all(c["height"] is None for c in no_dims)
        assert all("scale" not in c or c["scale"] is None for c in no_dims)

    def test_non_zero_dimension_never_offers_the_original(self):
        candidates = self._candidates("image", 400, (("preview", 400, 0),))

        assert all(c["width"] is not None for c in candidates)

    def test_scales_with_other_widths_are_skipped(self):
        candidates = self._candidates(
            "image", 400, (("preview", 400, 0), ("large", 800, 800))
        )

        assert all(c["width"] in (400, None) for c in candidates)

    def test_original_size_candidate_when_width_matches(self):
        """The image_scales "download" entry is minted at the original size."""
        candidates = self._candidates("image", 900, (), original_size=(900, 600))

        assert candidates
        assert all((c["width"], c["height"]) == (900, 600) for c in candidates)

    def test_original_size_ignored_when_width_differs(self):
        candidates = self._candidates("image", 400, (), original_size=(900, 600))

        assert candidates == []

    def test_no_original_size_is_tolerated(self):
        """Empty field or unreadable image: every other candidate survives."""
        candidates = self._candidates(
            "image", 400, (("Haeuser", 400, 200),), original_size=None
        )

        assert candidates

    def test_fieldname_is_threaded_through(self):
        candidates = self._candidates("my-logo-field", 400, (("Haeuser", 400, 200),))

        assert all(c["fieldname"] == "my-logo-field" for c in candidates)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_uid_healing.py::TestCandidateParameters -q`
Expected: FAIL with `ImportError: cannot import name 'candidate_parameters'`

- [ ] **Step 3: Write the implementation**

Append to `src/plone/pgthumbor/uid_healing.py`:

```python
def candidate_parameters(fieldname, dimension, scales, original_size=None):
    """Yield every parameter set that could have minted a uid.

    ``hash_key`` deletes the ``scale`` key only when width *and* height are
    truthy.  A scale with a zero component is therefore reachable under three
    parameter sets that hash differently: ``@@images/{field}/{name}`` passes
    the scale name, the ``image_scales`` indexer passes ``scale=None``, and
    plone.namedfile's own ``srcset()`` passes no ``scale`` key at all.  Missing
    that splits exactly the ``0:H`` and ``W:0`` scales this fix exists for.

    *original_size* is the field value's ``getImageSize()`` or None.  It covers
    the "download" entry, which is minted at the original's dimensions.
    """
    for name, width, height in scales:
        if width != dimension:
            continue
        for mode in SCALE_MODES:
            base = {
                "fieldname": fieldname,
                "width": width,
                "height": height,
                "mode": mode,
            }
            if width and height:
                # hash_key drops "scale" here, so all three call shapes
                # collapse into one hash.
                yield {**base, "scale": None}
            else:
                yield {**base, "scale": None}
                yield {**base, "scale": name}
                yield dict(base)

    if dimension == 0:
        # A bare tag() with no width at all mints {fieldname}-0-{md5} too.
        # Such a call carries no scale name, so there are only two shapes.
        for mode in SCALE_MODES:
            base = {
                "fieldname": fieldname,
                "width": None,
                "height": None,
                "mode": mode,
            }
            yield {**base, "scale": None}
            yield dict(base)

    if original_size and original_size[0] == dimension:
        orig_width, orig_height = original_size
        for mode in SCALE_MODES:
            yield {
                "fieldname": fieldname,
                "width": orig_width,
                "height": orig_height,
                "mode": mode,
                "scale": None,
            }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_uid_healing.py -q`
Expected: 22 passed

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest -q && uvx ruff check --fix . && uvx ruff format .`
Expected: 201 passed, ruff clean

- [ ] **Step 6: Commit**

```bash
git add src/plone/pgthumbor/uid_healing.py tests/test_uid_healing.py
git commit -m "feat: enumerate candidate parameter sets for a legacy uid

hash_key keeps the scale key when width or height is falsy, so a 0:H or
W:0 scale corresponds to three distinct uids depending on the call path.
All three are enumerated, plus the no-width and original-size shapes."
```

---

### Task 3: Reconstruct the mint time and match candidates

Replaces the guesswork in `_heal_legacy_uid` with an identification. Deletes `_allowed_scale_sizes` and the duplicated regex from `storage.py`.

**Files:**
- Modify: `src/plone/pgthumbor/storage.py`
- Modify: `tests/test_storage.py`

**Interfaces:**
- Consumes: `parse_legacy_uid`, `registered_scales`, `candidate_parameters` from Tasks 1 and 2.
- Produces:
  - `ThumborScaleStorage._mint_time(fieldname) -> int` (milliseconds)
  - `ThumborScaleStorage._original_size(fieldname) -> tuple[int, int] | None`
  - `ThumborScaleStorage._match_candidate(uid, fieldname, dimension, scales) -> dict | None`
  - `_allowed_scale_sizes` and `_LEGACY_UID_RE` no longer exist in `storage.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_storage.py`, delete the whole `TestAllowedScaleSizes` class (it moved to `tests/test_uid_healing.py` in Task 1), delete `test_get_or_generate_heals_legacy_uid`, `test_get_or_generate_heals_fieldname_with_dashes`, `test_get_or_generate_rejects_unregistered_width`, `test_get_or_generate_width_zero_means_original` and `test_get_or_generate_pre_scale_none_returns_none`, and add:

```python
class TestMintTime:
    """The modification time a uid was hashed against."""

    def test_uses_the_field_value_modification_time(self):
        storage = _make_storage()
        storage.context._p_mtime = 1700000000.0
        storage.context.image.modified = 1755000000.0

        assert storage._mint_time("image") == 1755000000000

    def test_falls_back_to_the_context_mtime(self):
        from plone.pgthumbor.storage import ThumborScaleStorage

        ctx = MagicMock(spec=["_p_mtime"])
        ctx._p_mtime = 1700000000.0
        storage = ThumborScaleStorage(ctx, modified=None)

        assert storage._mint_time("image") == 1700000000000


class TestHealByHashMatch:
    """Mint a uid, then heal it: the recovered parameters must be identical.

    This is the whole point of the fix. A storage built the way traversal
    builds it (modified=None) cannot reproduce a mint-time hash, so every
    test here also proves the mint time was reconstructed.
    """

    MINT_TIME = 1755000000000

    def _storages(self):
        """Return (minting storage, healing storage) over the same context."""
        from plone.pgthumbor.storage import ThumborScaleStorage

        ctx = MagicMock()
        minting = ThumborScaleStorage(ctx, modified=lambda: self.MINT_TIME)
        healing = ThumborScaleStorage(ctx, modified=None)
        return minting, healing

    def _heal(self, healing, uid, scales, original_size=None):
        """Run the healing path with the mint time and registry stubbed out."""
        recorded = {}

        def fake_pre_scale(**parameters):
            recorded.update(parameters)
            return {"uid": uid, "data": None}

        with (
            patch.object(healing, "pre_scale", side_effect=fake_pre_scale),
            patch.object(healing, "_mint_time", return_value=self.MINT_TIME),
            patch.object(healing, "_original_size", return_value=original_size),
            patch("plone.pgthumbor.storage.registered_scales", return_value=scales),
        ):
            result = healing.get_or_generate(uid)
        return result, recorded

    def test_recovers_cover_mode(self):
        """Issue #21 defect 1: mode was hardcoded to "scale"."""
        minting, healing = self._storages()
        uid = minting.hash_key(
            fieldname="image", width=400, height=200, mode="cover", scale=None
        )

        result, recorded = self._heal(healing, uid, (("Haeuser", 400, 200),))

        assert result is not None
        assert recorded["mode"] == "cover"
        assert (recorded["width"], recorded["height"]) == (400, 200)

    def test_disambiguates_two_scales_sharing_a_width(self):
        """Issue #21 defect 2: Haeuser 400:200 healed as preview 400:0."""
        minting, healing = self._storages()
        uid = minting.hash_key(
            fieldname="image", width=400, height=200, mode="scale", scale=None
        )

        _result, recorded = self._heal(
            healing, uid, (("preview", 400, 0), ("Haeuser", 400, 200))
        )

        assert (recorded["width"], recorded["height"]) == (400, 200)

    def test_recovers_a_height_driven_scale(self):
        """Issue #21 defect 3: Header 0:460 healed to the original size."""
        minting, healing = self._storages()
        uid = minting.hash_key(
            fieldname="image", width=0, height=460, mode="scale", scale="Header"
        )

        _result, recorded = self._heal(healing, uid, (("Header", 0, 460),))

        assert (recorded["width"], recorded["height"]) == (0, 460)
        assert recorded["scale"] == "Header"

    def test_recovers_the_explicit_dimensions_shape_of_a_zero_height_scale(self):
        """The image_scales indexer passes scale=None, not the name."""
        minting, healing = self._storages()
        uid = minting.hash_key(
            fieldname="image", width=400, height=0, mode="scale", scale=None
        )

        _result, recorded = self._heal(healing, uid, (("preview", 400, 0),))

        assert recorded["scale"] is None

    def test_recovers_the_no_scale_key_shape(self):
        """plone.namedfile's own srcset() passes no scale key at all."""
        minting, healing = self._storages()
        uid = minting.hash_key(fieldname="image", width=400, height=0, mode="scale")

        _result, recorded = self._heal(healing, uid, (("preview", 400, 0),))

        assert "scale" not in recorded

    def test_recovers_the_original_size_download_entry(self):
        minting, healing = self._storages()
        uid = minting.hash_key(
            fieldname="image", width=900, height=600, mode="scale", scale=None
        )

        _result, recorded = self._heal(healing, uid, (), original_size=(900, 600))

        assert (recorded["width"], recorded["height"]) == (900, 600)

    def test_fieldname_with_dashes_round_trips(self):
        minting, healing = self._storages()
        uid = minting.hash_key(
            fieldname="my-logo-field",
            width=400,
            height=200,
            mode="scale",
            scale=None,
        )

        _result, recorded = self._heal(healing, uid, (("Haeuser", 400, 200),))

        assert recorded["fieldname"] == "my-logo-field"

    def test_unregistered_width_stays_a_404(self):
        """The #17 gate: arbitrary dimensions must not be signed on demand."""
        _minting, healing = self._storages()

        result, recorded = self._heal(
            healing, "image-999-" + "b" * 32, (("Haeuser", 400, 200),)
        )

        assert result is None
        assert recorded == {}

    def test_malformed_uid_stays_a_404(self):
        storage = _make_storage()

        with patch.object(storage, "pre_scale") as mock_pre:
            assert storage.get_or_generate("image-400-nothex") is None
            assert storage.get_or_generate("image-400") is None

        mock_pre.assert_not_called()

    def test_oversized_width_stays_a_404(self):
        storage = _make_storage()

        with patch.object(storage, "pre_scale") as mock_pre:
            assert (
                storage.get_or_generate("image-" + "9" * 5000 + "-" + "a" * 32) is None
            )

        mock_pre.assert_not_called()

    def test_pre_scale_returning_none_stays_a_404(self):
        minting, healing = self._storages()
        uid = minting.hash_key(
            fieldname="image", width=400, height=200, mode="scale", scale=None
        )

        with (
            patch.object(healing, "pre_scale", return_value=None),
            patch.object(healing, "_mint_time", return_value=self.MINT_TIME),
            patch.object(healing, "_original_size", return_value=None),
            patch(
                "plone.pgthumbor.storage.registered_scales",
                return_value=(("Haeuser", 400, 200),),
            ),
        ):
            assert healing.get_or_generate(uid) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_storage.py -q`
Expected: FAIL, `AttributeError: <ThumborScaleStorage> does not have the attribute '_mint_time'`

- [ ] **Step 3: Rewrite the healing path**

In `src/plone/pgthumbor/storage.py`, replace the imports, delete `_LEGACY_UID_RE` and `_allowed_scale_sizes`, and replace `_heal_legacy_uid`:

```python
from __future__ import annotations

from Acquisition import aq_base
from plone.namedfile.scaling import ImageScaling
from plone.pgthumbor.interfaces import IPlonePgthumborLayer
from plone.pgthumbor.uid_healing import candidate_parameters
from plone.pgthumbor.uid_healing import parse_legacy_uid
from plone.pgthumbor.uid_healing import registered_scales
from plone.scale.storage import AnnotationStorage
from zope.globalrequest import getRequest

import logging


logger = logging.getLogger(__name__)
```

Methods on `ThumborScaleStorage`:

```python
def _mint_time(self, fieldname):
    """The modification time the requested uid was hashed against.

    ``ImageScaling.modified`` is what minted it.  ``ImageScaling`` is a
    BrowserView whose ``__init__`` only assigns, so instantiating it here
    is a plain constructor call: no component lookup, no ZCML, and no copy
    of that logic to drift.  The method is byte-identical in
    plone.namedfile 7.3.0 and 8.0.0a3.
    """
    return ImageScaling(self.context, getRequest()).modified(fieldname)


def _original_size(self, fieldname):
    """The original image's ``(width, height)``, or None.

    Only used to offer the "download" candidate, so an empty field or a
    corrupt image simply removes that one candidate.
    """
    value = getattr(aq_base(self.context), fieldname, None)
    get_size = getattr(value, "getImageSize", None)
    if get_size is None:
        return None
    try:
        return get_size()
    except Exception:
        logger.warning(
            "Could not read image size for %r field %s",
            self.context,
            fieldname,
            exc_info=True,
        )
        return None


def _match_candidate(self, uid, fieldname, dimension, scales):
    """Return the parameters whose hash_key equals *uid*, or None."""
    original_size = self._original_size(fieldname)
    for parameters in candidate_parameters(fieldname, dimension, scales, original_size):
        if self.hash_key(**parameters) == uid:
            return parameters
    return None


def _heal_legacy_uid(self, uid):
    """Rebuild scale info for a ``{fieldname}-{width}-{md5hex}`` uid.

    The parameters are not readable out of the uid, so they are enumerated
    and re-hashed until one matches.  That identifies the mode, tells two
    scales sharing a width apart, and resolves ``0:H`` scales, all of which
    the previous width-only heuristic got wrong (issue #21).
    """
    parsed = parse_legacy_uid(uid)
    if parsed is None:
        return None
    fieldname, dimension = parsed

    # publishTraverse adapts (context, None), so ``modified_time`` is None
    # here while the uid was hashed against the field's modification time.
    # Without restoring it, no candidate can ever match.
    mint_time = self._mint_time(fieldname)

    def minted():
        return mint_time

    self.modified = minted

    scales = registered_scales()
    parameters = self._match_candidate(uid, fieldname, dimension, scales)
    if parameters is None:
        return None
    info = self.pre_scale(**parameters)
    if info is not None:
        info.setdefault("fieldname", fieldname)
    return info
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_storage.py -q`
Expected: all pass

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest -q && uvx ruff check --fix . && uvx ruff format .`
Expected: green, no reference to `_allowed_scale_sizes` remains

Run: `grep -rn "_allowed_scale_sizes" src tests`
Expected: no output

- [ ] **Step 6: Commit**

```bash
git add src/plone/pgthumbor/storage.py tests/test_storage.py
git commit -m "fix: identify the scale a legacy uid was minted for (#21)

Enumerate the candidate parameter sets and re-hash them with plone.scale's
own hash_key instead of guessing from the uid's width. That recovers the
mode, tells two scales sharing a width apart, and resolves 0:H scales.

The mint time has to be reconstructed first: publishTraverse builds the
storage as (context, None), so modified_time is None at healing time while
the uid was hashed against the field's modification time. The issue's
suggested fix assumed otherwise and could not have matched anything."
```

---

### Task 4: Fallback when no candidate matches

A uid minted before the image was last modified matches nothing, because `modified` is part of the hash. Serve the current image rather than a hole.

**Files:**
- Modify: `src/plone/pgthumbor/storage.py`
- Modify: `tests/test_storage.py`

**Interfaces:**
- Consumes: `_match_candidate` from Task 3.
- Produces: `ThumborScaleStorage._fallback_parameters(fieldname, dimension, scales) -> dict | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_storage.py`:

```python
class TestStaleUidFallback:
    """A uid older than the image's last modification cannot be identified."""

    def _fallback(self, dimension, scales):
        storage = _make_storage()
        return storage._fallback_parameters("image", dimension, scales)

    def test_picks_the_first_registered_scale_with_that_width(self):
        parameters = self._fallback(400, (("preview", 400, 0), ("Haeuser", 400, 200)))

        assert (parameters["width"], parameters["height"]) == (400, 0)
        assert parameters["mode"] == "scale"
        assert parameters["scale"] is None

    def test_zero_width_prefers_a_registered_height_driven_scale(self):
        """Never request the original's dimensions while a 0:H scale exists:
        that is the variant that can push Thumbor past MAX_PIXELS."""
        parameters = self._fallback(0, (("Header", 0, 460),))

        assert (parameters["width"], parameters["height"]) == (0, 460)

    def test_zero_width_without_a_registered_scale_means_the_original(self):
        """The genuine tag()-without-a-width case, and the only reading left."""
        parameters = self._fallback(0, (("preview", 400, 0),))

        assert (parameters["width"], parameters["height"]) == (None, None)

    def test_unregistered_width_has_no_fallback(self):
        assert self._fallback(999, (("preview", 400, 0),)) is None

    def test_stale_uid_reaches_the_fallback(self):
        """End to end: a uid whose hash matches nothing still renders."""
        storage = _make_storage()
        recorded = {}

        def fake_pre_scale(**parameters):
            recorded.update(parameters)
            return {"uid": "x", "data": None}

        with (
            patch.object(storage, "pre_scale", side_effect=fake_pre_scale),
            patch.object(storage, "_mint_time", return_value=1755000000000),
            patch.object(storage, "_original_size", return_value=None),
            patch(
                "plone.pgthumbor.storage.registered_scales",
                return_value=(("Haeuser", 400, 200),),
            ),
        ):
            result = storage.get_or_generate("image-400-" + "f" * 32)

        assert result is not None
        assert (recorded["width"], recorded["height"]) == (400, 200)
        assert recorded["mode"] == "scale"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_storage.py::TestStaleUidFallback -q`
Expected: FAIL, `AttributeError: ... '_fallback_parameters'`

- [ ] **Step 3: Add the fallback**

Add to `ThumborScaleStorage` in `src/plone/pgthumbor/storage.py`:

```python
    def _fallback_parameters(self, fieldname, dimension, scales):
        """Parameters to use when no candidate hashes to the requested uid.

        The uid predates the image's last modification, so its parameters are
        unrecoverable: ``modified`` is part of the hash.  Serve the current
        image anyway.  plone.scale deliberately returns outdated scales at this
        point, and a cached page whose image was replaced should show the new
        image rather than a hole.

        The original's dimensions are only requested when the uid carries no
        width and no ``0:H`` scale is registered, which is the genuine
        ``tag()``-without-a-width case and the only reading left.  Requesting
        them speculatively is what can push Thumbor past ``MAX_PIXELS``.

        An unregistered width returns None and traversal raises NotFound,
        keeping the issue #17 gate that stops arbitrary dimensions from being
        signed on demand.
        """
        for _name, width, height in scales:
            if width == dimension:
                return {
                    "fieldname": fieldname,
                    "width": width,
                    "height": height,
                    "mode": "scale",
                    "scale": None,
                }
        if dimension == 0:
            return {
                "fieldname": fieldname,
                "width": None,
                "height": None,
                "mode": "scale",
                "scale": None,
            }
        return None
```

And wire it into `_heal_legacy_uid`, replacing the early return:

```python
        parameters = self._match_candidate(uid, fieldname, dimension, scales)
        if parameters is None:
            parameters = self._fallback_parameters(fieldname, dimension, scales)
        if parameters is None:
            return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_storage.py -q`
Expected: all pass

Note: `test_unregistered_width_stays_a_404` from Task 3 must still pass, because width 999 has neither a candidate nor a fallback.

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest -q && uvx ruff check --fix . && uvx ruff format .`
Expected: green

- [ ] **Step 6: Commit**

```bash
git add src/plone/pgthumbor/storage.py tests/test_storage.py
git commit -m "fix: define the fallback for a uid older than the image (#21)

modified is part of the hash, so a uid minted before the last image change
matches no candidate. Fall back to the first registered scale of that
width rather than 404, and request the original's dimensions only when the
uid carries no width and no 0:H scale is registered."
```

---

### Task 5: Make the recovered mode reach the Thumbor URL

Without this, Task 3's mode recovery is inert. `plone.scale` keeps `mode` in `info["key"]` and never copies it into the info dict, so all four call sites in `scaling.py` read `"scale"`.

**Files:**
- Modify: `src/plone/pgthumbor/scaling.py`
- Modify: `tests/test_scaling.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `_scale_param(scale_info: dict | None, name: str, default=None)` in `scaling.py`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scaling.py`:

```python
class TestScaleParam:
    """Reading call parameters back out of a plone.scale info dict."""

    def _key(self, **parameters):
        """A key tuple shaped the way plone.scale's hash() builds it."""
        return tuple(sorted(parameters.items()))

    def test_reads_from_the_key_tuple(self):
        from plone.pgthumbor.scaling import _scale_param

        info = {"key": self._key(mode="cover", width=400)}
        assert _scale_param(info, "mode", "scale") == "cover"

    def test_key_wins_over_the_info_dict(self):
        """plone/plone.scale#156 will add mode to the dict; the key holds the
        raw value hash_key hashed, so it stays authoritative."""
        from plone.pgthumbor.scaling import _scale_param

        info = {"key": self._key(mode="contain"), "mode": "scale"}
        assert _scale_param(info, "mode", "scale") == "contain"

    def test_falls_back_to_the_info_dict(self):
        from plone.pgthumbor.scaling import _scale_param

        assert _scale_param({"mode": "cover"}, "mode", "scale") == "cover"

    def test_default_when_absent_everywhere(self):
        from plone.pgthumbor.scaling import _scale_param

        assert _scale_param({"key": self._key(width=400)}, "mode", "scale") == ("scale")

    def test_none_info_returns_the_default(self):
        from plone.pgthumbor.scaling import _scale_param

        assert _scale_param(None, "mode", "scale") == "scale"

    def test_malformed_key_does_not_raise(self):
        from plone.pgthumbor.scaling import _scale_param

        assert _scale_param({"key": ("hash",)}, "mode", "scale") == "scale"

    def test_explicit_none_in_the_key_is_returned(self):
        from plone.pgthumbor.scaling import _scale_param

        info = {"key": self._key(mode=None)}
        assert _scale_param(info, "mode", "scale") is None


class TestModeReachesTheUrl:
    """Regression for the gap that made every URL fit-in (issue #21).

    These assert that the mode is *threaded through*, not what the mapping
    does with it. Which mode implies fit-in is pinned separately in
    tests/test_url.py, where it is derived from Pillow rather than restated,
    so these stay correct if plone/plone.scale#78 changes the mapping.
    """

    def _url(self, monkeypatch, key_mode=_MISSING, info_mode=_MISSING):
        from plone.pgthumbor.scaling import ThumborImageScale

        _setup_env(monkeypatch)
        ctx = MagicMock()
        ctx.absolute_url.return_value = "http://plone:8080/doc"
        info = {
            "data": _mock_image_data(),
            "fieldname": "image",
            "width": 400,
            "height": 200,
            "uid": "image-400-abc123",
            "mimetype": "image/jpeg",
        }
        if key_mode is not _MISSING:
            info["key"] = tuple(
                sorted(
                    {
                        "fieldname": "image",
                        "width": 400,
                        "height": 200,
                        "mode": key_mode,
                    }.items()
                )
            )
        if info_mode is not _MISSING:
            info["mode"] = info_mode
        return ThumborImageScale(ctx, MagicMock(), **info).url

    def test_mode_in_the_key_changes_the_url(self, monkeypatch):
        """The defect: pre_scale puts mode only in the key, so this used to
        make no difference at all and every URL came out fit-in."""
        contain = self._url(monkeypatch, key_mode="contain")
        plain = self._url(monkeypatch, key_mode="scale")

        assert contain != plain

    def test_key_mode_agrees_with_an_explicit_mode(self, monkeypatch):
        for mode in ("scale", "cover", "contain"):
            from_key = self._url(monkeypatch, key_mode=mode)
            explicit = self._url(monkeypatch, info_mode=mode)
            assert from_key == explicit, mode

    def test_missing_mode_everywhere_defaults_to_scale(self, monkeypatch):
        assert self._url(monkeypatch) == self._url(monkeypatch, info_mode="scale")

    def test_mode_alias_is_normalised(self, monkeypatch):
        """hash_key hashes the raw alias the caller passed, so the URL for
        "scale-crop-to-fit" must equal the one for "contain"."""
        alias = self._url(monkeypatch, key_mode="scale-crop-to-fit")
        canonical = self._url(monkeypatch, key_mode="contain")

        assert alias == canonical
```

`_MISSING` is a module-level sentinel; add it next to the other helpers near the top of `tests/test_scaling.py`:

```python
_MISSING = object()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_scaling.py::TestScaleParam -q`
Expected: FAIL with `ImportError: cannot import name '_scale_param'`

- [ ] **Step 3: Add the helper and route all four call sites through it**

In `src/plone/pgthumbor/scaling.py`, add the import `from plone.scale.scale import get_scale_mode`, then:

```python
_UNSET = object()


def _scale_param(scale_info, name, default=None):
    """Read a call parameter back out of a plone.scale info dict.

    ``pre_scale`` keeps the parameters in ``info["key"]`` and copies only a few
    of them into the info dict itself.  ``info.get("mode")`` is therefore
    always absent, and reading it that way builds every URL as if the mode were
    "scale" — the whole reason a "contain" scale renders letterboxed today.

    The key is consulted first: it exists in every plone.scale version and
    holds the raw value ``hash_key`` hashed.  plone/plone.scale#156 will add
    ``mode`` to the dict as well, and reading the key first keeps this correct
    either way.
    """
    if not scale_info:
        return default
    key = scale_info.get("key")
    if key:
        try:
            parameters = dict(key)
        except (TypeError, ValueError):
            parameters = {}
        value = parameters.get(name, _UNSET)
        if value is not _UNSET:
            return value
    return scale_info.get(name, default)
```

Rewrite `_get_crop` to use it, replacing the manual key parsing:

```python
def _get_crop(context, fieldname, scale_info):
    """Look up crop coordinates via an ICropProvider adapter.

    Returns ``((left, top), (right, bottom))`` or None.
    """
    provider = queryAdapter(context, ICropProvider)
    if provider is None:
        return None

    scale_name = _scale_param(scale_info, "scale")
    if not fieldname or not scale_name:
        return None

    box = provider.get_crop(fieldname, scale_name)
    if box is None:
        return None
    # Convert (left, top, right, bottom) to ((left, top), (right, bottom))
    if len(box) == 4:
        return ((box[0], box[1]), (box[2], box[3]))
    return box
```

Then replace the mode argument at all four `_build_thumbor_url` call sites. Each currently reads `info.get("mode", "scale")`, `scale_info.get("mode", "scale")` or `entry.get("mode", "scale")`; each becomes the same expression over the matching variable:

```python
(get_scale_mode(_scale_param(info, "mode", "scale")),)
```

```python
(get_scale_mode(_scale_param(scale_info, "mode", "scale")),)
```

```python
(get_scale_mode(_scale_param(entry, "mode", "scale")),)
```

```python
(get_scale_mode(_scale_param(scale_info, "mode", "scale")),)
```

Run this to confirm none was missed:

```bash
grep -n 'get("mode"' src/plone/pgthumbor/scaling.py
```

Expected: no output.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_scaling.py -q`
Expected: all pass, including the pre-existing `TestScaleModeMapping` tests which pass `mode=` directly in the info dict and exercise the fallback branch of `_scale_param`

- [ ] **Step 5: Run the full suite and lint**

Run: `uv run pytest -q && uvx ruff check --fix . && uvx ruff format .`
Expected: green

- [ ] **Step 6: Commit**

```bash
git add src/plone/pgthumbor/scaling.py tests/test_scaling.py
git commit -m "fix: let the scale mode reach the Thumbor URL

plone.scale keeps mode in info[\"key\"] and never copies it into the info
dict, so info.get(\"mode\", \"scale\") always returned \"scale\" and every
URL was built as fit-in — healed or not. A contain scale got a tag
claiming the cropped box and an image fitted inside it.

_scale_param reads the key first and the dict second, which also makes
this forward-compatible with plone/plone.scale#156. _get_crop moves onto
the same helper; it was already doing this by hand for the scale name."
```

---

### Task 6: Pin the mode mapping, changelog, and final verification

`scale_mode_to_thumbor` is correct but untested, and Task 5 is what makes that matter: until now it was only ever reached with `"scale"`.

**Files:**
- Modify: `tests/test_url.py`
- Modify: `CHANGES.md`

**Interfaces:**
- Consumes: `_scale_param` from Task 5 (indirectly, as the reason the mapping is now load-bearing).
- Produces: nothing further tasks depend on.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_url.py`:

```python
class TestModeMappingMatchesPloneScale:
    """Derive the expectation from Pillow instead of restating the table.

    scale_mode_to_thumbor compensates for plone.scale's inverted mode names
    behind a `PLONE_SCALE_VERSION < 6` gate, on the expectation that
    plone/plone.scale#78 lands in 6. If 6 ships without that fix, or if the
    floor moves before anyone checks, the swap disappears and every cropping
    scale silently becomes a fit-in. This test notices.
    """

    BOX = (400, 200)
    # Aspect ratios deliberately unlike the box, so "cropped" is detectable
    # as a changed aspect ratio rather than as a filled box. An original that
    # already matches the box would make cover's upscale look like a crop.
    ORIGINALS = ((1000, 400), (400, 1000))

    def _crops(self, original, mode):
        from PIL import Image
        from plone.scale.scale import scalePILImage

        scaled = scalePILImage(
            Image.new("RGB", original), self.BOX[0], self.BOX[1], mode=mode
        )
        original_ratio = original[0] / original[1]
        scaled_ratio = scaled.size[0] / scaled.size[1]
        return abs(scaled_ratio - original_ratio) > 0.01

    def test_fit_in_says_the_same_as_plone_scale(self):
        from plone.pgthumbor.url import scale_mode_to_thumbor

        for original in self.ORIGINALS:
            for mode in ("scale", "contain", "cover"):
                params = scale_mode_to_thumbor(mode, smart_cropping=True)
                message = f"{mode} on {original[0]}x{original[1]}"
                assert params["fit_in"] is not self._crops(original, mode), message

    def test_smart_is_enabled_on_the_cropping_path(self):
        from plone.pgthumbor.url import scale_mode_to_thumbor

        for original in self.ORIGINALS:
            for mode in ("scale", "contain", "cover"):
                if not self._crops(original, mode):
                    continue
                params = scale_mode_to_thumbor(mode, smart_cropping=True)
                assert params["smart"] is True, mode

    def test_smart_stays_off_when_the_setting_is_off(self):
        from plone.pgthumbor.url import scale_mode_to_thumbor

        for mode in ("scale", "contain", "cover"):
            params = scale_mode_to_thumbor(mode, smart_cropping=False)
            assert params["smart"] is False, mode
```

- [ ] **Step 2: Run the test to verify it passes as written**

Run: `uv run pytest tests/test_url.py::TestModeMappingMatchesPloneScale -q`
Expected: 3 passed. This is a characterisation test for behaviour that is already correct, so it passes immediately.

To confirm it actually bites, temporarily edit `src/plone/pgthumbor/url.py` and delete the `if PLONE_SCALE_VERSION and ...` swap block, re-run, and check that `test_fit_in_says_the_same_as_plone_scale` fails with `contain on 1000x400`. Then restore the block with `git checkout src/plone/pgthumbor/url.py`.

- [ ] **Step 3: Write the changelog entry**

In `CHANGES.md`, under `## 0.6.6 (unreleased)`, add:

```markdown
- Fix: `_heal_legacy_uid` now recovers the scale a uid was minted for instead
  of guessing one from its width. The uid's md5 covers the whole parameter set
  plus the field's modification time, so the candidates are enumerated and
  re-hashed with `plone.scale`'s own `hash_key` until one matches. That
  identifies the mode rather than assuming `"scale"`, tells two registered
  scales sharing a width apart (`Haeuser 400:200` used to heal as
  `preview 400:0`), and resolves height-driven `0:H` scales, which used to
  heal into a request at the original's dimensions and could push Thumbor past
  `MAX_PIXELS`.

  Matching only works once the modification time is reconstructed:
  `publishTraverse` adapts `(context, None)`, so the storage's `modified_time`
  is `None` at healing time while the uid was hashed against the field's
  modification time. A uid older than the image's last modification cannot be
  identified at all and falls back to the first registered scale of that width,
  never speculatively to the original's dimensions.

  Closes [#21](https://github.com/bluedynamics/plone-pgthumbor/issues/21).

- Fix: the scale mode now reaches the generated Thumbor URL. `plone.scale`
  keeps `mode` in `info["key"]` and never copies it into the info dict, so
  `info.get("mode", "scale")` always read `"scale"` and every URL was built
  with `fit_in` — a `contain` scale got an `<img>` tag claiming the cropped
  box and an image fitted inside it instead. Both `plone.namedfile` code paths
  and the HiDPI `srcset` attribute are fixed. Forward-compatible with
  [plone/plone.scale#156](https://github.com/plone/plone.scale/pull/156),
  which adds `mode` to the info dict upstream.

- Tests: pin `scale_mode_to_thumbor` against real `scalePILImage` output. The
  mapping compensates for `plone.scale`'s inverted mode names behind a
  `plone.scale < 6` gate, and until the fix above it was only ever reached
  with `"scale"`, so the other two branches were unobservable.
```

- [ ] **Step 4: Run the full suite, lint, and check the diff**

Run: `uv run pytest -q`
Expected: all pass, no failures, no errors

Run: `uvx ruff check . && uvx ruff format --check .`
Expected: `All checks passed!` and `N files already formatted`

Run: `git diff origin/main --stat`
Expected: changes limited to `CHANGES.md`, `src/plone/pgthumbor/storage.py`, `src/plone/pgthumbor/scaling.py`, `src/plone/pgthumbor/uid_healing.py`, `tests/test_storage.py`, `tests/test_scaling.py`, `tests/test_uid_healing.py`, `tests/test_url.py`, and the two docs files

Run: `grep -rn 'get("mode"' src/plone/pgthumbor/`
Expected: no output

- [ ] **Step 5: Commit**

```bash
git add tests/test_url.py CHANGES.md
git commit -m "test: pin the cover/contain mapping against scalePILImage

The mapping is correct, but only because commit 45af38a compensates for
plone.scale's inverted mode names behind a version gate. Now that mode
actually reaches the URL, that gate is load-bearing: if plone.scale 6
ships without plone/plone.scale#78, the swap disappears and every
cropping scale becomes a fit-in. The test derives the expectation from
Pillow rather than restating the table, so it fails on that day and needs
no edit on the day the upstream fix lands."
```

---

## Verification before opening the PR

- [ ] `uv run pytest -q` passes with no skips introduced by this branch
- [ ] `uvx ruff check .` and `uvx ruff format --check .` both clean
- [ ] `grep -rn "_allowed_scale_sizes" src tests` returns nothing
- [ ] `grep -rn 'get("mode"' src/plone/pgthumbor/` returns nothing
- [ ] `CHANGES.md` has the 0.6.6 entries
- [ ] PR title and body in English, referencing `Closes #21`

## Follow-up, not part of this PR

After this merges and releases, `docs/superpowers/specs/2026-08-21-thumbor-source-derivative-design.md` needs two corrections in §5: the `self.modified_time` assumption in its #21 paragraph, and the `bda/aaf/deployment#5` hypothesis, which now has a route through the mode plumbing gap that does not involve healing at all. That design's §9 then continues from its step 2.
