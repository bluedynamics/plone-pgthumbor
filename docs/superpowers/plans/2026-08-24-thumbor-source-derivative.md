# Thumbor Source Derivative — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Design:** [`../specs/2026-08-21-thumbor-source-derivative-design.md`](../specs/2026-08-21-thumbor-source-derivative-design.md) — read it first, including **both** 2026-08-24 revision notes — the second pass corrects four claims the first pass left standing. Tracking issue: [#25](https://github.com/bluedynamics/plone-pgthumbor/issues/25). Adjacent open issues: [#14](https://github.com/bluedynamics/plone-pgthumbor/issues/14) and [#15](https://github.com/bluedynamics/plone-pgthumbor/issues/15) (out of scope), [#16](https://github.com/bluedynamics/plone-pgthumbor/issues/16) (fixed by Task 13).

**Prerequisite — satisfied, with two chores outstanding.** Issue [#21](https://github.com/bluedynamics/plone-pgthumbor/issues/21) is fixed and merged: PR #29, merge commit `14a0de1`. Spec §5 keeps the reasoning for why it had to come first.

1. **Merge `main` into this branch before reading anything else.** `docs/thumbor-source-derivative` is **30 commits behind** and does not contain `src/plone/pgthumbor/uid_healing.py`. Every line reference below is against `main` at `14a0de1`; on the unmerged branch they point at the wrong code.
2. **A release is still owed.** Newest tag is `v0.6.5`, `CHANGES.md` reads `0.6.6 (unreleased)`. Spec §9 step 1 requires it *released*, not merely merged, before the backfill runs. Not a blocker for Tasks 1–14.

**Goal:** Store a capped, colour-normalised second `NamedBlobImage` on the original field value and point Thumbor URLs at it, so Thumbor never sees a print-resolution original and never fetches more source data than a web rendition needs.

**Architecture:** `derivative.py` (new) owns all pixel work and is pure — bytes in, bytes out, no ZODB, no ZCA. `subscribers.py` (new) walks Dexterity schemata on add/modify and calls into it. `scaling.py::_build_thumbor_url` — the package's single URL funnel, holding the only `get_blob_ids()` call — gains source selection, crop rescaling and dimension clamping, all three inline and inseparable. The Thumbor loader is not touched: the URL already addresses one blob by `(zoid, tid)`.

**Tech Stack:** Python 3.12/3.13, Pillow (new direct dependency), `plone.namedfile`, `plone.dexterity`, `libthumbor.CryptoURL`, `Products.CMFEditions` clone modifiers, `psycopg` for the backfill, pure `pytest` + `unittest.mock`.

## Context for the implementing engineer

- **`_build_thumbor_url` (`src/plone/pgthumbor/scaling.py:105`) is the only funnel.** One `get_blob_ids()` call at :122, one `thumbor_url()` call at :143, four callers (`ThumborImageScale.__init__` :228, `._scale_url` :248, `.srcset_attribute` :286, `ThumborImageScaling._scale_url` :317). Changing that one function covers every URL the package emits. *(Line numbers are post-#21; the file grew by ~44 lines. The pre-#21 numbers in earlier revisions of this plan are stale.)*
- `_HAS_SCALE_URL` (:208) forks on the installed `plone.namedfile`: 7.x makes `__init__` the live path, 8.x makes `_scale_url` live. Both funnel into `_build_thumbor_url`, so **no fork handling is needed in the implementation** — but see the pin below, because it does affect what the tests actually exercise. `tests/test_scaling.py::TestModeReachesTheUrlOnTheLegacyPath` shows the double-monkeypatch needed to reach the 7.x path from an 8.x environment.
- **`mode` did not reach the URL at all before #21, and now does.** `plone.scale`'s `pre_scale` builds `info` as `dict(uid, key, modified, mimetype, data, width, height)` — no `mode`. Every call site read `scale_info.get("mode", "scale")`, so **every** Thumbor URL was emitted as `fit_in`, and a `contain` scale rendered letterboxed inside a tag claiming the cropped box. `main` adds `_scale_param()` (:35) and `_scale_mode()` (:71), which read the parameter back out of `info["key"]` where `hash_key` left it. Use `_scale_mode(scale_info)` — never `scale_info.get("mode", ...)` — and note that "the mode default" Task 7 restores is now a real value rather than a constant `"scale"`.
- **New module `plone.pgthumbor.uid_healing`**, if the backfill needs it: `parse_legacy_uid(uid)`, `registered_scales()` (registry order, duplicates kept), `candidate_parameters(fieldname, dimension, scales, original_size=None)` where `original_size` is a lazily-consulted zero-argument callable. `ThumborScaleStorage` gained `_mint_time`, `_original_size`, `_match_candidate`, `_fallback_parameters`. **`_allowed_scale_sizes` no longer exists** — nothing may reference it.
- Baseline on `main`: **235 tests, under a second.** That is the number Task 6's "every pre-existing test stays green" is measured against, not the 174 of the pre-#21 tree.
- **The package touches zero image bytes today** and does not declare Pillow. This work makes it the first byte manipulation in the package.
- **Tests are pure pytest with `MagicMock`.** No Zope, no Plone test layer, no fixtures — `tests/conftest.py` holds one plain function (`env_override`) that tests import by name. Coverage gate is `fail_under = 90` over `plone.pgthumbor` only (`scripts/` and `tests/` excluded).
- Dimensions reported to Plone (`<img width/height>`, `image_scales`) come from the **original** via `pre_scale` and `ImageFieldScales`, independently of source selection. That is correct and must stay: only the fetched bytes change, never the rendered geometry.
- `plone.dexterity` is a hard dependency of `plone.namedfile`, so `iterSchemata` imports in the pure-pytest environment without a Zope bootstrap.
- Constructing a real `NamedBlobImage` in pure pytest needs an `IStorage` utility registered under the name `"builtins.bytes"`; without it you get `ComponentLookupError`.
- **Pin `plone.namedfile < 8` for this work.** There is no lockfile in the repository and the dependency is unbounded, so a fresh environment resolves 8.1.1 while the target deployment runs 7.x. The version flips `_HAS_SCALE_URL` (`scaling.py:208`) and with it whether `ThumborImageScale.__init__` or `._scale_url` is the live path. `_build_thumbor_url` is shared, so no implementation fork is needed — but a test suite written under one resolution silently never exercises the other. Assert the §4 behaviours through both entry points regardless of which is live.
- **`Products.CMFEditions` always imports** (hard dependency of `Products.CMFPlone`) **but its site tool may not exist.** `portal_modifier` appears only once the CMFEditions GenericSetup profile has been applied, and `plone.app.versioningbehavior` reaches a site via `plone.app.contenttypes`, not via Plone core. Task 10 must degrade quietly when either is absent.
- **PR [#23](https://github.com/bluedynamics/plone-pgthumbor/pull/23) is merged — branch from `main` after it.** It rewrote `scale_mode_to_thumbor` to swap `cover`/`contain` for `plone.scale < 6`, and 5.0.0a3 is what is installed, so **the swap is active**: `mode="cover"` no longer yields the `fit_in` value the older tests assumed. Task 7 restores "the mode default" after dropping a degenerate crop — read `url.py` for what that default now is rather than inferring it. The PR also edited `tests/test_scaling.py` in three places, the file Tasks 6–8 rewrite. Its tests pin `PLONE_SCALE_VERSION` via monkeypatch; new tests touching mode semantics must do the same or they encode the installed version by accident.

## Attributes written on the field value

| Attribute | Meaning |
|---|---|
| `_pgthumbor_source` | the derivative `NamedBlobImage`, or `None` when none is needed |
| `_pgthumbor_source_info` | `{"reason": str, "max_edge": int, "source_ids": (zoid, tid) \| None}` — outcome record |

"Neither attribute present" — **or** a recorded reason that is non-terminal, **or** a recorded `max_edge` that differs from the configured cap — is what makes an image a backfill candidate. Recording the cap is what turns tuning it from a migration into a setting change: `plone.pgthumbor` is generic, every deployment will pick its own value at least once, and a re-run that silently does nothing without `force` is a trap. Terminal reasons (`"generated"`, `"not_needed"`, `"skipped_type"`) mean a re-run would change nothing. Non-terminal reasons (`"retry"` from a semaphore timeout, `"error"` from a failed decode) are transient and must be re-selected by an ordinary backfill run, without `force`. Getting this wrong lets one contended upload exclude its image permanently while the terminal verification still reports success. `_pgthumbor_source_info["source_ids"]` is compared against `get_blob_ids(original)` at URL-build time so an in-place `image.data = ...` mutation cannot leave a stale derivative in service.

---

## Task list

### Task 1 — Test-support foundations
**PARALLEL-SAFE · no dependencies**

Files: `tests/conftest.py`, `tests/test_image_fixtures.py` *(new)*, `pyproject.toml`

- [ ] Write `tests/test_image_fixtures.py` first, pinning each factory's observable properties through `PIL.Image.open`: mode, size, format, `is_animated`, presence of `transparency`/`exif`, and that the corrupt/truncated bytes raise. This file guards against a Pillow upgrade silently changing what the factories produce.
- [ ] Add plain module-level factories to `conftest.py`, matching `env_override`'s style (functions, not fixtures): `jpeg_bytes`, `big_jpeg_bytes(size=(2400,1800))` (draw diagonal lines so DCT reduction is measurable), `cmyk_jpeg_bytes`, `png_bytes(mode="RGBA")`, `palette_png_bytes(transparency=True|False)`, `animated_gif_bytes`, `exif_jpeg_bytes`, `truncated_jpeg_bytes`, `CORRUPT_BYTES`, `SVG_BYTES`.
- [ ] Add a `zodb_db()` helper returning `ZODB.DB(DemoStorage())` with a temp blob dir, plus a `namedfile_storables()` context manager registering the `IStorage` utility under `"builtins.bytes"` and **unregistering it on teardown** — a leaked global utility contaminates every later test.
- [ ] Add `"PGTHUMBOR_SOURCE_MAX_EDGE"` to `env_override`'s hardcoded `all_vars` list, or a developer's shell environment leaks into every config test.
- [ ] Add `Pillow>=10` to `[project.optional-dependencies].test` (the extra currently has neither Pillow nor Plone).
- [ ] Verify: `uv run pytest tests/test_image_fixtures.py -q`; full suite still green.

### Task 2 — `PGTHUMBOR_SOURCE_MAX_EDGE` setting
**PARALLEL-SAFE with 3 and 7 · depends on 1**

Files: `interfaces.py`, `config.py`, `setuphandlers.py`, `configure.zcml`, `profiles/default/metadata.xml`, `tests/test_config.py`, `tests/test_upgrade.py`, `docs/sources/reference/configuration.md`

- [ ] Tests first: default 4000; env override; **env `"0"` disables and is not overwritten by the registry** (the case the existing falsiness idiom gets wrong); non-integer falls back with a warning; negative clamps to 0; **above 8000 clamps to 8000, from env and from the registry alike**; env beats registry; registry fallback when env absent. Mirror `test_registry_fallback_for_smart_cropping`'s mocking shape.
- [ ] `IThumborSettings.source_max_edge = schema.Int(default=4000, min=0, max=8000)`; `ThumborConfig.source_max_edge: int = 4000`.
- [ ] Clamp in `get_thumbor_config()` as well, do not rely on the schema. The bound is load-bearing, not cosmetic: an edge of 4000 caps the derivative at 16 MP, well under Thumbor's 75 MP `MAX_PIXELS`, and that guarantee dies above √75e6 ≈ 8660. A registry value written before the bound existed, or an env var, bypasses schema validation entirely. Add a comment saying so, and a test asserting `12000 → 8000`.
- [ ] In `get_thumbor_config()`, read the env var through a `None` sentinel — `raw = os.environ.get(...)`, `if raw is not None:` — never through falsiness. Restructure the surrounding registry-fallback guard accordingly.
- [ ] `metadata.xml` → `<version>4</version>`; `upgrade_to_4` calling `registry.registerInterface(IThumborSettings, prefix="plone.pgthumbor.settings")`; `<genericsetup:upgradeStep source="3" destination="4">`.
- [ ] Note: `tests/test_integration.py::test_config_dataclass_matches_interface` will fail the moment the interface field lands and pass once the dataclass field does — a free parity check, no edit needed.
- [ ] `docs/sources/reference/configuration.md` — Task 2 owns this file end to end. Add `PGTHUMBOR_SOURCE_MAX_EDGE` to both the environment-variable and the registry table, documenting `0` as the kill switch and `8000` as the clamp. Reference only: say what the setting is, and link to the how-to from Task 14 for how to choose a value. While here, fix the pre-existing drift — the page still documents `server_url`/`security_key`/`unsafe` as registry fields, which `upgrade_to_3` removed.
- [ ] Verify: `uv run pytest tests/test_config.py tests/test_upgrade.py tests/test_integration.py -q`; `vale --config=docs/.vale.ini docs/sources/reference/configuration.md`

### Task 3 — `derivative.py`: decode, ceiling, trigger detection
**PARALLEL-SAFE with 2 and 7 · depends on 1**

Files: `src/plone/pgthumbor/derivative.py` *(new)*, `tests/test_derivative.py` *(new)*, `pyproject.toml`

- [ ] Tests first. `_needs_derivative`: oversized triggers; exactly at cap does **not** (boundary is `>`); CMYK under cap triggers; palette-with-transparency triggers; palette-without does not; RGBA under cap does **not** (alpha only picks the encoder, it is not a trigger).
- [ ] `_draft_target(size, max_edge)`: assert the divisor is the largest power of two keeping the result ≥ cap — `(11811, 8858)` at 4000 → decoded **`(5906, 4429)`**; `(7000, 5000)` at 4000 → unchanged, no reduction available. Do **not** call `draft(None, (cap, cap))`; compute the target explicitly. (The first draft of the spec said `4430`; Pillow rounds up per axis, `(8858 + 1) // 2 = 4429`. Verified against Pillow 12.1.1 — assert the measured value, not the arithmetic you expect.)
- [ ] `_open_and_draft`: draft engages for a large JPEG (assert decoded size); no-op for PNG; **mode unchanged for CMYK**, proving `mode=None`.
- [ ] Ceiling: reading `im.size` from the header rejects above `MAX_SOURCE_PIXELS` **before** `load()`. The constant is **`175_000_000`**, not the 500 MP of the first draft. Pillow's `_decompression_bomb_check` runs *inside* `Image.open` and raises `DecompressionBombError` above `2 × Image.MAX_IMAGE_PIXELS` = 178,956,970 px, so 500 MP is unreachable and a test for it unwritable. The constant must land **above** the 164.6 MP largest image in the field (that image has to get a derivative — it is one of the broken ones) and **below** Pillow's 178,956,970. Test both edges: 175 MP + 1 px is refused by us; 164.6 MP is accepted.
- [ ] Assert `Image.MAX_IMAGE_PIXELS` is never assigned — it is a process global consulted by every `Image.open` in the process, including `plone.namedfile`'s own EXIF handling.
- [ ] Test that a source above Pillow's own limit surfaces as a recorded failure, not a crash: `DecompressionBombError` is caught like any other exception and produces the `"error"` outcome.
- [ ] Suppress `DecompressionBombWarning` around the decode with `warnings.catch_warnings()` and assert it: under `-W error` the warning would otherwise become an exception the blanket handler swallows into a silent "no derivative". Note in a comment that the filter is process-global for the duration, bounded by the decode semaphore.
- [ ] Assert `derivative._SKIP_CONTENT_TYPES == scaling._SKIP_THUMBOR_TYPES` so the duplicated `"image/svg+xml"` cannot drift.
- [ ] Add `Pillow>=10` to `[project].dependencies`.
- [ ] Verify: `uv run pytest tests/test_derivative.py -q`; `uvx ruff check src tests`

### Task 4 — `derivative.py`: convert, encode, `build_derivative_bytes`
**SEQUENTIAL · depends on 3**

Files: `derivative.py`, `tests/test_derivative.py`

`build_derivative_bytes(source, max_edge) -> (bytes, content_type, extension) | None`, accepting bytes or a seekable file object, never raising.

- [ ] Tests first: small clean JPEG → `None`; oversized JPEG → capped JPEG at the expected decoded size; **CMYK under cap → RGB with dimensions unchanged** (the regression guard for the next bullet); oversized RGBA → PNG; palette+transparency → PNG; animated GIF → `None`; SVG → `None`; corrupt and truncated bytes → `None` plus a WARNING; file object accepted; `max_edge=0` → `None`; EXIF dropped; JPEG subsampling is 0.
- [ ] Use `img.thumbnail((max_edge, max_edge), LANCZOS)`, **not** `ImageOps.contain` — `contain` upscales, which would enlarge a small CMYK image that triggered on colour space alone.
- [ ] sRGB conversion via `ImageCms.profileToProfile` when `img.info["icc_profile"]` is present, else `convert("RGB")`. Test both by spying on the `ImageCms` boundary — generating a valid CMYK ICC profile in-test is not reliable across littlecms builds, and the assertion here is about *which path was chosen*, not about pixels. Verify `PIL.ImageCms` imports at module load; littlecms is optional in some builds.
- [ ] Skip the derivative when the encoded result is not meaningfully smaller than the source — a photographic 4000 px RGBA PNG can exceed the original it replaces.
- [ ] Whole body in `try/except Exception` → log warning, return `None`.
- [ ] Verify: `uv run pytest tests/test_derivative.py --cov -q`; `derivative.py` ≥ 90 %.

### Task 5 — `set_source_derivative()` and outcome records
**SEQUENTIAL · depends on 2 and 4**

Files: `derivative.py`, `tests/test_derivative.py`

`set_source_derivative(named_image, max_edge=None, force=False) -> bool`

- [ ] Tests first, using `namedfile_storables()`: oversized gets a derivative; small clean gets `_pgthumbor_source = None` plus an outcome record; already-processed is skipped (spy asserts `build_derivative_bytes` not called); `force=True` reprocesses; SVG records without decoding; `max_edge=0` writes **no attribute at all**; cap read from config when omitted; no config is a no-op; a raising generator returns `False` and writes no derivative; **`get_blob_ids(img._pgthumbor_source) is None`** immediately after creation, pinning §5's "backfill phase 1 → uid fallback" row.
- [ ] Read source bytes via `named_image.open("r")`, never `.data` — the latter materialises a 40 MB blob as `bytes` before Pillow sees it, defeating the lazy draft.
- [ ] Record `_pgthumbor_source_info` with the reason, the `max_edge` actually used, and `get_blob_ids(named_image)` at generation time. Test that a record written under one cap is treated as stale under another — this is the hook the backfill uses so a cap change needs no `force`. Fix the reason vocabulary here, in one module constant, and have the backfill import it rather than re-spell the strings: terminal = `"generated"`, `"not_needed"`, `"skipped_type"`; non-terminal = `"retry"`, `"error"`. Export a `TERMINAL_REASONS` frozenset.
- [ ] Test that a non-terminal record is reprocessed by a plain call **without** `force`, while a terminal one is skipped. This is the invariant Task 11's candidate SQL mirrors; if the two drift, a transient failure becomes permanent and the backfill still reports success.
- [ ] Verify: `uv run pytest tests/test_derivative.py -q`

### Task 6 — Source selection in `_build_thumbor_url`
**PARALLEL-SAFE with 2–5 · depends on 1**

Files: `scaling.py`, `tests/test_scaling.py`, `tests/test_integration.py`

- [ ] **Do this before touching `scaling.py`:** add `_pgthumbor_source = None` to `_mock_image_data()` in **both** `tests/test_scaling.py:23` and `tests/test_integration.py:26` (post-#21 line numbers; both still return a bare `MagicMock`), with a `derivative=None` keyword. Otherwise `getattr(data, "_pgthumbor_source", None)` auto-creates a child mock, `u64()` receives a `MagicMock`, and roughly forty green tests fail at once.
- [ ] Tests first: derivative's ids used when present; original used when the sentinel is `None`; original used when the attribute is absent (plain object, not a mock); **no fallback when the derivative lacks a TID** — URL is `None` and the original's ids appear nowhere; recorded `source_ids` mismatching `get_blob_ids(original)` → treated as no derivative; SVG skip still reads the *original's* `contentType`; `content_zoid` still from context; selection reaches `srcset_attribute` and `ThumborImageScaling._scale_url`.
- [ ] Implementation is three lines plus a comment block stating that the absent fallback **is** the feature — substituting the original in the no-TID window would freeze a direct Thumbor URL to an over-`MAX_PIXELS` image into catalog metadata with no path to recovery. Prefer the explicit `if source is None:` form over `or`, immune to a future `__bool__` on `NamedBlobFile`.
- [ ] Verify: `uv run pytest tests/test_scaling.py tests/test_integration.py -q` — every pre-existing test stays green.

### Task 7 — Crop rescaling and dimension clamping
**SEQUENTIAL · depends on 6**

Files: `scaling.py`, `tests/test_scaling.py`

- [ ] Tests first, asserting on the plaintext box `libthumbor` emits (`"100x200:1000x1200"`), as the existing crop tests already do: rescaled to the derivative; direction-aware rounding; clamped to bounds; degenerate box dropped **and** `fit_in`/`smart` restored to the mode default; untouched without a derivative; dropped safely when the original width is unknown; translation works through `_scale_url` too.
- [ ] Clamping: no emitted URL ever requests a width or height exceeding the selected source's dimensions.
- [ ] Place the translation **before** the `if crop is not None:` block that forces `fit_in=True, smart=False`, so a dropped crop does not leave crop semantics active without a crop.
- [ ] **"The mode default" is a real value now — read it correctly.** Before #21 every URL was built as `fit_in` because `pre_scale` never copies `mode` into the info dict; `main` resolves it through `_scale_mode(scale_info)`, which reads `info["key"]`. Restore *that* value when a degenerate box is dropped, never a hardcoded `"scale"`. Two things have moved underneath this since the plan was first written — PR #23's `cover`/`contain` swap for `plone.scale < 6` (active at 5.0.0a3) and the #21 mode fix — so derive the expected `fit_in`/`smart` from `scale_mode_to_thumbor` in the test rather than writing literals, and pin `PLONE_SCALE_VERSION` by monkeypatch as the existing tests do.
- [ ] Gate on `source is not data`, never on "a crop exists" — a skipped or failed derivative with a crop must pass the box through untouched. Two factors, from the derivative's actual size; floor left/top, ceil right/bottom.
- [ ] Verify: `uv run pytest tests/test_scaling.py -q`

### Task 8 — `srcset` upscale clamp
**SEQUENTIAL · depends on 7**

Files: `scaling.py`, `tests/test_scaling.py`

- [ ] Tests first: with a derivative present, `srcset` emits no candidate wider than the derivative, and the original-width back-fill entry is dropped rather than upscaled; without a derivative the current behaviour is unchanged.
- [ ] `ThumborImageScaling.srcset` (`scaling.py:382`, post-#21) back-fills an entry at the original's dimensions whenever no configured scale covers them. Compare its guards against the **effective source** width instead.
- [ ] **Know what this clamp hides.** A healed `0:H` uid also arrives asking for the original's dimensions (issue #21, defect 3). Today that returns a loud 400; clamped, it returns a valid image of the wrong size. That is why #21 is a prerequisite rather than adjacent work. Add a comment at the clamp saying the clamp is not a fix for #21 and must not be treated as one, plus a test pinning that a request above the source size is clamped rather than dropped, so a later reader cannot mistake silence for correctness.
- [ ] Verify: `uv run pytest tests/test_scaling.py -q`

### Task 9 — Subscriber
**SEQUENTIAL · depends on 5**

Files: `subscribers.py` *(new)*, `configure.zcml`, `pyproject.toml`, `tests/test_subscribers.py` *(new)*, `tests/test_integration.py`

- [ ] Tests first, with `monkeypatch.setattr(subscribers, "iterSchemata", lambda obj: [IFakeSchema])` over a hand-built interface carrying one `NamedBlobImage` field and one `TextLine`: yields only image fields; skips `None` and non-`NamedBlobImage` values; falls back to the behavior adapter when the attribute is missing; an `iterSchemata` failure yields nothing; every field processed; no config and cap `0` short-circuit **before any blob read**; never raises; **firing twice is idempotent**; replacing the image regenerates.
- [ ] Register both events for `IDexterityContent` — `z3c.autoinclude` loads this package for the whole instance, so an unqualified `for="*"` would fire in every site in the process. `IObjectAddedEvent` also fires on rename, move, paste and content import; idempotence is what makes those free.
- [ ] Guard the decode with a process-wide `BoundedSemaphore(1)` and a short acquisition timeout. The `@@fileUpload` path is serialised by `DXFileFactory`'s `upload_lock` (`plone/app/dexterity/factories.py:14`), but the edit path holds no lock and can fan out across worker threads at ~79–105 MB each. On timeout, record the **non-terminal** `"retry"` outcome and skip rather than queue — and add a test asserting that an image carrying that outcome is still a backfill candidate. A terminal marker here is the difference between a deferred image and a permanently lost one.
- [ ] Assert both `<subscriber>` registrations exist by parsing `configure.zcml` with `xml.etree` in `test_integration.py` — cheap, catches typos, needs no Zope.
- [ ] Add `plone.dexterity` to `[project].dependencies` (currently transitive).
- [ ] Verify: `uv run pytest tests/test_subscribers.py tests/test_integration.py -q`

### Task 10 — Keep derivatives out of version snapshots
**SEQUENTIAL · depends on 5 and 9** — shares `configure.zcml` with Task 9 and `setuphandlers.py` / `metadata.xml` with Task 2, so it runs beside neither.

Files: `modifiers.py` *(new)*, `configure.zcml`, `setuphandlers.py`, `profiles/default/metadata.xml`, `tests/test_modifiers.py` *(new)*, `tests/test_upgrade.py`

This is larger than "write a modifier". CMFEditions clone modifiers are not ZCML registrations: `plone.app.versioningbehavior/setuphandlers.py::install_modifiers` constructs each one, wraps it in a `ConditionalTalesModifier` and calls `portal_modifier.register(id, wrapper)` from a GenericSetup import step. Read that file and `plone/app/versioningbehavior/modifiers.py` before starting.

- [ ] Test first with the ZODB helper from Task 1: pickle a `NamedBlobImage` carrying a derivative through `persistent_id` callbacks shaped like `CloneNamedFileBlobs`, and assert the clone arrives **without** a derivative. Add the negative test proving why: without the modifier, the nested blob pickles to an empty `Blob` (`Blob.__getstate__` returns `None`).
- [ ] Match `getCallbacks`' shape: `persistent_id` returns a value from an `id()`-keyed mapping, `persistent_load` returns `None`. `ModifierRegistryTool.getOnCloneModifiers` chains every registered `ICloneModifier` and prefixes each pid with the modifier's id, so this **composes with** `CloneNamedFileBlobs` rather than replacing it. Assert that in a test with both active — the top-level blob must survive by reference while the nested one is dropped.
- [ ] The modifier maps `_pgthumbor_source` and `_pgthumbor_source_info` to `None` on clone, so the repository never carries a derivative and a revert yields a bare field value that regenerates on the next modification.
- [ ] Zope 2 scaffolding matching its peers: `InitializeClass`, an add form and a factory, because the wrapper expects a registerable object.
- [ ] **Registration needs an upgrade step, not just `post_install`.** `post_install` is the profile post-handler and runs on install only, so an already-installed site — the one this design exists to repair — would take Task 2's registry upgrade and never receive the modifier, leaving exactly the empty-blob-in-a-snapshot failure this task prevents. Bump `metadata.xml` to `<version>5</version>` (Task 2 leaves it at 4), add `upgrade_to_5` and a `<genericsetup:upgradeStep source="4" destination="5">`, both calling a shared `install_clone_modifier(site)` helper that `post_install` also calls. Idempotent: skip when the id is already in `portal_modifier.objectIds()`, because upgrade steps get re-run.
- [ ] **Degrade quietly when CMFEditions is not set up.** `Products.CMFEditions` always imports (hard dependency of `Products.CMFPlone`), but `portal_modifier` exists only once its GenericSetup profile has been applied, and `plone.app.versioningbehavior` reaches a site through `plone.app.contenttypes`, not through Plone core — a deployment with custom types and no `plone.app.contenttypes` has neither. Look the tool up as `getToolByName(site, "portal_modifier", None)` and return without error when it is `None`; guard ZCML that imports CMFEditions interfaces with `zcml:condition="installed Products.CMFEditions"`. Test both branches: tool present → registered once; tool absent → no exception, nothing registered, no traceback in the log.
- [ ] Audit `plone.app.iterate` working copies (`ObjectCopiedEvent`) for the same path and cover whichever hook applies. If `plone.app.iterate` is absent the path does not exist — do not add a dependency for it.
- [ ] Verify: `uv run pytest tests/test_modifiers.py tests/test_upgrade.py -q`

### Task 11 — Backfill: skeleton, candidate SQL, progress
**PARALLEL-SAFE with 6–10 · depends on 5**

Files: `scripts/backfill_thumbor_sources.py` *(new)*, `tests/test_backfill.py` *(new)*

- [ ] Tests first against a fake cursor recording `execute(sql, params)` and `tmp_path` for the progress file: SQL filters on class; excludes **terminal** outcomes only — an image whose recorded reason is `"retry"` or `"error"` stays a candidate in an ordinary run, and only terminal reasons need `force` to revisit; a recorded `max_edge` differing from the configured cap also keeps it a candidate; import `TERMINAL_REASONS` from `derivative.py` instead of re-spelling the strings; size-only mode adds the dimension predicate; keyset pagination present. `Progress` round-trips, starts at zero on a missing or corrupt file, and records the phase per chunk.
- [ ] Follow `scripts/purge_legacy_scales.py` for structure, but put the zconsole bootstrap under `if "app" in dir(): main(app)` instead of `sys.exit(1)`, so the module imports cleanly under pytest and its pure parts get tested.
- [ ] Keyset pagination on `zoid` (`WHERE zoid > :last ORDER BY zoid LIMIT :chunk`), not `OFFSET`, and not the catalog.
- [ ] Verify: `uv run pytest tests/test_backfill.py -q`

### Task 12 — Backfill: phase 1 runner
**SEQUENTIAL · depends on 11**

Files: `scripts/backfill_thumbor_sources.py`, `tests/test_backfill.py`

- [ ] Tests first with a fake connection and monkeypatched `transaction`: each candidate processed; commit per chunk; resumes from progress; a per-object error is skipped, not fatal; stops when no candidates remain; dry run writes nothing and reports four numbers: candidate count, median encoded derivative size on a sample, and how many candidate field values carry no `_modified` attribute. The third one matters — for those, writing the derivative changes `_p_mtime`, and with it every scale uid for that image, because `hash_key` folds `modified_time` and `ModifiedPropertyMixin.modified` falls back to `_p_mtime` (spec §1). It sizes the cache-invalidation blast radius before anything is written. The fourth is a histogram of scale names that actually carry crops, read from the `plone.app.imagecropping` annotation's `{fieldname}_{scalename}` keys — that is the binding *S* in spec §4's `X ≥ S / cap` threshold, and therefore the entire input to choosing a cap. Emit it even when `plone.app.imagecropping` is absent (empty histogram, not a crash).
- [ ] Fetch the `NamedBlobImage` by oid directly — never load the content object — then `_p_deactivate()`. Lift `_invalidate_cache` and `_release_memory` (`malloc_trim`) verbatim from `purge_legacy_scales.py`.
- [ ] Verify: `uv run pytest tests/test_backfill.py -q`

### Task 13 — Backfill: phase 2 reindex, request context, verification
**SEQUENTIAL · depends on 12**

Files: `scripts/backfill_thumbor_sources.py`, `src/plone/pgthumbor/purge_scales.py`, `tests/test_backfill.py`, `tests/test_purge_scales.py`

- [ ] Test first, and make it the gate: with `getRequest()` returning `None`, the entry point must **abort** before writing anything. Assert the abort, not a specific exception type from deeper down.
- [ ] Get the mechanism right — the spec's first draft described it wrongly and a test written against that description asserts nothing. Verified chain: `Products.CMFPlone.image_scales.indexer.image_scales` calls `queryMultiAdapter((obj, getRequest()), IImageScalesAdapter)`; with `None` the lookup misses and the indexer raises `AttributeError`, plone.indexer's "do not index" signal. `plone.pgcatalog`'s `extraction.extract_idx` then reads it as `getattr(wrapper, meta_name, None)` — the default **swallows** that signal — and writes `idx["image_scales"] = None` through `set_partial_pending`, a JSONB merge that puts an explicit `null` where the scales were. It is not `Missing.Value` and the column is not skipped: it is **overwritten**. `ImageFieldScales.__call__` never runs, so its own `ComponentLookupError` guard is not what fires.
- [ ] Entry point does `makerequest` → `alsoProvides(req, IPlonePgthumborLayer)` → `setRequest(req)`, then asserts the `@@images` lookup resolves to `ThumborImageScaling` before doing any work.
- [ ] Apply the same fix to `purge_scales.main()` (`src/plone/pgthumbor/purge_scales.py`), which calls `makerequest` but neither `setRequest` nor `alsoProvides` today. This is already filed as **issue #16** — reference it in the changelog entry and close it with this change.
- [ ] Phase 2 reindexes with `idxs=["image_scales"]` — an empty `idxs` calls `notifyModified()` and would bump the modification date of every object touched.
- [ ] Terminal verification: zero remaining candidates **and** no catalog row carrying a Thumbor URL whose blob zoid belongs to an original that now has a derivative.
- [ ] Verify: `uv run pytest tests/test_backfill.py tests/test_purge_scales.py -q`

### Task 14 — Documentation and changelog
**SEQUENTIAL · depends on 8, 10 and 13**

Files: `docs/sources/explanation/architecture.md`, `docs/sources/llms.txt`, `docs/sources/explanation/why-thumbor.md`, `README.md`, `storage.py` docstrings, `docs/sources/how-to/backfill-source-derivatives.md` *(new)*, `docs/sources/how-to/choose-source-max-edge.md` *(new)*, `docs/sources/how-to/index.md`, `CHANGES.md`

`docs/sources/reference/configuration.md` belongs to Task 2 — do not reopen it. Link to it instead.

- [ ] Scope the "Pillow is never imported, never invoked" claim to the request path, where it stays true — `ThumborScaleStorage` still never calls `IImageScaleFactory`, and `tests/test_storage.py::test_no_pillow_invoked` needs no change. `llms.txt`'s "Zero Pillow dependency in Plone process" becomes flatly wrong and must go.
- [ ] `architecture.md` gets a "Source derivatives" section: field-value placement, structural invalidation plus its versioning caveat, the loader's non-involvement, the draft decode, and the two attributes. This is the *explanation* quadrant — the reasoning, including why crops below `S / cap` soften.
- [ ] **New how-to: `choose-source-max-edge.md`.** This is the piece that keeps the package honest as a generic distribution. `plone.pgthumbor` ships 4000 as a starting point, not as an answer; every deployment has a different largest cropped scale. The guide walks an operator through it: run the backfill dry run, read the crop histogram, apply `X ≥ S / cap` from spec §4, set the value, re-run. State plainly that changing the cap later is an ordinary backfill run — the recorded `max_edge` makes stale derivatives candidates on their own, no `force` needed — so the decision is revisitable rather than permanent. Include the cap-4000/5000 threshold table and the cost of going up (~1.56× storage and resize at 5000, 8000 hard bound). Diataxis header, one sentence per line, toctree entry under **Setup**.
- [ ] New how-to for the backfill mirroring `how-to/purge-legacy-scales.md`, toctree entry under **Migration**, cross-linking the cap guide for the dry-run step.
- [ ] `CHANGES.md`: the feature, plus the `purge_scales` zconsole fix closing issue #16.
- [ ] Verify: `uvx ruff format --check .` and the vale pre-commit hook (`files: ^docs/sources/.*\.md$` — `docs/superpowers/` is not linted, `docs/sources/` is).

### Task 15 — Final verification
**SEQUENTIAL · depends on all**

- [ ] `uv run pytest --cov` passes the `fail_under = 90` gate; `derivative.py`, `subscribers.py` and `modifiers.py` individually clear 90 %.
- [ ] `uvx ruff check .` and `uvx ruff format --check .`.
- [ ] `pyproject.toml` declares both new direct imports (`Pillow`, `plone.dexterity`). While there: `packaging`, introduced by PR #23's `import packaging.version`, is undeclared too and currently arrives only transitively.
- [ ] Run the suite against **both** `plone.namedfile` 7.x and 8.x. The version flips `_HAS_SCALE_URL` and therefore which of `ThumborImageScale.__init__` / `._scale_url` is live; a suite green under one resolution can be silently untested under the other. Production runs 7.x.
- [ ] Assert the cap's ceiling holds end to end: `PGTHUMBOR_SOURCE_MAX_EDGE=12000` produces derivatives no larger than 8000 px on the long edge, so the ≤16 MP / 75 MP `MAX_PIXELS` guarantee cannot be configured away.
- [ ] Staging checklist from spec §9: the four IA images from `bda/aaf/deployment#6`; a crop set on one image above and one below the cap, comparing rendered regions — **expecting** that only the `src` is cropped, since `srcset`/HiDPI/`image_scales` never forward a scale name (out of scope, spec Non-goals); dry-run counts before the production backfill; Varnish ban after deploy.

---

## Wave map

| Wave | Tasks | Agents | Notes |
|---|---|---|---|
| A | 1 | 1 | Byte factories, ZODB helper and the env-var list gate everything downstream |
| B | **2, 3, 6** | 3 | Disjoint: settings/profiles · `derivative.py` · `scaling.py` |
| C | **4** (after 3), **7** (after 6) | 2 | |
| D | **5** (after 2+4), **8** (after 7) | 2 | 5 is the first join point |
| E | **9, 11** (both after 5) | 2 | 10 moved out — it shares `configure.zcml` with 9 |
| F | **10** (after 9), **12** (after 11) | 2 | |
| G | **13** (after 12) | 1 | |
| H | **14** (after 8+10+13) | 1 | |
| I | **15** | 1 | |

**File-collision check across parallel waves.** Re-derived after the first version of this plan asserted "`configure.zcml` … never in the same wave" while the wave map put 9 and 10 in wave E together. Single-owner chains: `scaling.py` → 6 → 7 → 8; `derivative.py` → 3 → 4 → 5; `scripts/backfill_thumbor_sources.py` → 11 → 12 → 13. Shared files, with the wave that owns them:

| File | Tasks (wave) | Concurrent? |
|---|---|---|
| `configure.zcml` | 2 (B), 9 (E), 10 (F) | no |
| `setuphandlers.py`, `profiles/default/metadata.xml` | 2 (B, → v4), 10 (F, → v5) | no — 10 must build on the version 2 left |
| `pyproject.toml` | 1 (A), 3 (B), 9 (E) | no |
| `tests/test_integration.py` | 6 (B), 9 (E) | no |
| `tests/test_upgrade.py` | 2 (B), 10 (F) | no |
| `docs/sources/reference/configuration.md` | 2 (B) only — including the pre-existing `upgrade_to_3` drift | Task 14 must not reopen it |
| `docs/sources/how-to/*`, `explanation/*`, `llms.txt`, `README.md` | 14 (H) only | |
