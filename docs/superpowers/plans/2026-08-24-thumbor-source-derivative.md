# Thumbor Source Derivative — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Design:** [`../specs/2026-08-21-thumbor-source-derivative-design.md`](../specs/2026-08-21-thumbor-source-derivative-design.md) — read it first, in the 2026-08-24 revision. Tracking issue: [#25](https://github.com/bluedynamics/plone-pgthumbor/issues/25).

**Goal:** Store a capped, colour-normalised second `NamedBlobImage` on the original field value and point Thumbor URLs at it, so Thumbor never sees a print-resolution original and never fetches more source data than a web rendition needs.

**Architecture:** `derivative.py` (new) owns all pixel work and is pure — bytes in, bytes out, no ZODB, no ZCA. `subscribers.py` (new) walks Dexterity schemata on add/modify and calls into it. `scaling.py::_build_thumbor_url` — the package's single URL funnel, holding the only `get_blob_ids()` call — gains source selection, crop rescaling and dimension clamping, all three inline and inseparable. The Thumbor loader is not touched: the URL already addresses one blob by `(zoid, tid)`.

**Tech Stack:** Python 3.12/3.13, Pillow (new direct dependency), `plone.namedfile`, `plone.dexterity`, `libthumbor.CryptoURL`, `Products.CMFEditions` clone modifiers, `psycopg` for the backfill, pure `pytest` + `unittest.mock`.

## Context for the implementing engineer

- **`_build_thumbor_url` (`src/plone/pgthumbor/scaling.py:61`) is the only funnel.** One `get_blob_ids()` call at :78, one `thumbor_url()` call at :99, four callers (`ThumborImageScale.__init__` :190, `._scale_url` :210, `.srcset_attribute` :248, `ThumborImageScaling._scale_url` :279). Changing that one function covers every URL the package emits.
- `_HAS_SCALE_URL` (:170) forks on the installed `plone.namedfile`: 7.x makes `__init__` the live path, 8.x makes `_scale_url` live. Both funnel into `_build_thumbor_url`, so **no fork handling is needed** for this change.
- **The package touches zero image bytes today** and does not declare Pillow. This work makes it the first byte manipulation in the package.
- **Tests are pure pytest with `MagicMock`.** No Zope, no Plone test layer, no fixtures — `tests/conftest.py` holds one plain function (`env_override`) that tests import by name. Coverage gate is `fail_under = 90` over `plone.pgthumbor` only (`scripts/` and `tests/` excluded).
- Dimensions reported to Plone (`<img width/height>`, `image_scales`) come from the **original** via `pre_scale` and `ImageFieldScales`, independently of source selection. That is correct and must stay: only the fetched bytes change, never the rendered geometry.
- `plone.dexterity` is a hard dependency of `plone.namedfile`, so `iterSchemata` imports in the pure-pytest environment without a Zope bootstrap.
- Constructing a real `NamedBlobImage` in pure pytest needs an `IStorage` utility registered under the name `"builtins.bytes"`; without it you get `ComponentLookupError`.

## Attributes written on the field value

| Attribute | Meaning |
|---|---|
| `_pgthumbor_source` | the derivative `NamedBlobImage`, or `None` when none is needed |
| `_pgthumbor_source_info` | `{"reason": str, "source_ids": (zoid, tid) \| None}` — outcome record |

"Neither attribute present" is what makes an image a backfill candidate. `_pgthumbor_source_info["source_ids"]` is compared against `get_blob_ids(original)` at URL-build time so an in-place `image.data = ...` mutation cannot leave a stale derivative in service.

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

- [ ] Tests first: default 4000; env override; **env `"0"` disables and is not overwritten by the registry** (the case the existing falsiness idiom gets wrong); non-integer falls back with a warning; negative clamps to 0; env beats registry; registry fallback when env absent. Mirror `test_registry_fallback_for_smart_cropping`'s mocking shape.
- [ ] `IThumborSettings.source_max_edge = schema.Int(default=4000, min=0)`; `ThumborConfig.source_max_edge: int = 4000`.
- [ ] In `get_thumbor_config()`, read the env var through a `None` sentinel — `raw = os.environ.get(...)`, `if raw is not None:` — never through falsiness. Restructure the surrounding registry-fallback guard accordingly.
- [ ] `metadata.xml` → `<version>4</version>`; `upgrade_to_4` calling `registry.registerInterface(IThumborSettings, prefix="plone.pgthumbor.settings")`; `<genericsetup:upgradeStep source="3" destination="4">`.
- [ ] Note: `tests/test_integration.py::test_config_dataclass_matches_interface` will fail the moment the interface field lands and pass once the dataclass field does — a free parity check, no edit needed.
- [ ] Verify: `uv run pytest tests/test_config.py tests/test_upgrade.py tests/test_integration.py -q`

### Task 3 — `derivative.py`: decode, ceiling, trigger detection
**PARALLEL-SAFE with 2 and 7 · depends on 1**

Files: `src/plone/pgthumbor/derivative.py` *(new)*, `tests/test_derivative.py` *(new)*, `pyproject.toml`

- [ ] Tests first. `_needs_derivative`: oversized triggers; exactly at cap does **not** (boundary is `>`); CMYK under cap triggers; palette-with-transparency triggers; palette-without does not; RGBA under cap does **not** (alpha only picks the encoder, it is not a trigger).
- [ ] `_draft_target(size, max_edge)`: assert the divisor is the largest power of two keeping the result ≥ cap — `(11811, 8858)` at 4000 → decoded `(5906, 4430)`; `(7000, 5000)` at 4000 → unchanged, no reduction available. Do **not** call `draft(None, (cap, cap))`; compute the target explicitly.
- [ ] `_open_and_draft`: draft engages for a large JPEG (assert decoded size); no-op for PNG; **mode unchanged for CMYK**, proving `mode=None`.
- [ ] Ceiling: reading `im.size` from the header rejects above `MAX_SOURCE_PIXELS` (module constant, 500 MP) **before** `load()`. Assert `Image.MAX_IMAGE_PIXELS` is never assigned — it is a process global consulted by every `Image.open` in the process, including `plone.namedfile`'s own EXIF handling.
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
- [ ] Record `_pgthumbor_source_info` with the reason and `get_blob_ids(named_image)` at generation time.
- [ ] Verify: `uv run pytest tests/test_derivative.py -q`

### Task 6 — Source selection in `_build_thumbor_url`
**PARALLEL-SAFE with 2–5 · depends on 1**

Files: `scaling.py`, `tests/test_scaling.py`, `tests/test_integration.py`

- [ ] **Do this before touching `scaling.py`:** add `_pgthumbor_source = None` to `_mock_image_data()` in **both** `tests/test_scaling.py:22` and `tests/test_integration.py:26`, with a `derivative=None` keyword. Otherwise `getattr(data, "_pgthumbor_source", None)` auto-creates a child mock, `u64()` receives a `MagicMock`, and roughly forty green tests fail at once.
- [ ] Tests first: derivative's ids used when present; original used when the sentinel is `None`; original used when the attribute is absent (plain object, not a mock); **no fallback when the derivative lacks a TID** — URL is `None` and the original's ids appear nowhere; recorded `source_ids` mismatching `get_blob_ids(original)` → treated as no derivative; SVG skip still reads the *original's* `contentType`; `content_zoid` still from context; selection reaches `srcset_attribute` and `ThumborImageScaling._scale_url`.
- [ ] Implementation is three lines plus a comment block stating that the absent fallback **is** the feature — substituting the original in the no-TID window would freeze a direct Thumbor URL to an over-`MAX_PIXELS` image into catalog metadata with no path to recovery. Prefer the explicit `if source is None:` form over `or`, immune to a future `__bool__` on `NamedBlobFile`.
- [ ] Verify: `uv run pytest tests/test_scaling.py tests/test_integration.py -q` — every pre-existing test stays green.

### Task 7 — Crop rescaling and dimension clamping
**SEQUENTIAL · depends on 6**

Files: `scaling.py`, `tests/test_scaling.py`

- [ ] Tests first, asserting on the plaintext box `libthumbor` emits (`"100x200:1000x1200"`), as the existing crop tests already do: rescaled to the derivative; direction-aware rounding; clamped to bounds; degenerate box dropped **and** `fit_in`/`smart` restored to the mode default; untouched without a derivative; dropped safely when the original width is unknown; translation works through `_scale_url` too.
- [ ] Clamping: no emitted URL ever requests a width or height exceeding the selected source's dimensions.
- [ ] Place the translation **before** the `if crop is not None:` block that forces `fit_in=True, smart=False`, so a dropped crop does not leave crop semantics active without a crop.
- [ ] Gate on `source is not data`, never on "a crop exists" — a skipped or failed derivative with a crop must pass the box through untouched. Two factors, from the derivative's actual size; floor left/top, ceil right/bottom.
- [ ] Verify: `uv run pytest tests/test_scaling.py -q`

### Task 8 — `srcset` upscale clamp
**SEQUENTIAL · depends on 7**

Files: `scaling.py`, `tests/test_scaling.py`

- [ ] Tests first: with a derivative present, `srcset` emits no candidate wider than the derivative, and the original-width back-fill entry is dropped rather than upscaled; without a derivative the current behaviour is unchanged.
- [ ] `ThumborImageScaling.srcset` (`scaling.py:344`) back-fills an entry at the original's dimensions whenever no configured scale covers them. Compare its guards against the **effective source** width instead.
- [ ] Verify: `uv run pytest tests/test_scaling.py -q`

### Task 9 — Subscriber
**SEQUENTIAL · depends on 5**

Files: `subscribers.py` *(new)*, `configure.zcml`, `pyproject.toml`, `tests/test_subscribers.py` *(new)*, `tests/test_integration.py`

- [ ] Tests first, with `monkeypatch.setattr(subscribers, "iterSchemata", lambda obj: [IFakeSchema])` over a hand-built interface carrying one `NamedBlobImage` field and one `TextLine`: yields only image fields; skips `None` and non-`NamedBlobImage` values; falls back to the behavior adapter when the attribute is missing; an `iterSchemata` failure yields nothing; every field processed; no config and cap `0` short-circuit **before any blob read**; never raises; **firing twice is idempotent**; replacing the image regenerates.
- [ ] Register both events for `IDexterityContent` — `z3c.autoinclude` loads this package for the whole instance, so an unqualified `for="*"` would fire in every site in the process. `IObjectAddedEvent` also fires on rename, move, paste and content import; idempotence is what makes those free.
- [ ] Guard the decode with a process-wide `BoundedSemaphore(1)` and a short acquisition timeout. The `@@fileUpload` path is serialised by `DXFileFactory`'s `upload_lock`, but the edit path holds no lock and can fan out across worker threads at ~79–105 MB each. On timeout, record a retry outcome and skip rather than queue.
- [ ] Assert both `<subscriber>` registrations exist by parsing `configure.zcml` with `xml.etree` in `test_integration.py` — cheap, catches typos, needs no Zope.
- [ ] Add `plone.dexterity` to `[project].dependencies` (currently transitive).
- [ ] Verify: `uv run pytest tests/test_subscribers.py tests/test_integration.py -q`

### Task 10 — Keep derivatives out of version snapshots
**PARALLEL-SAFE with 9 · depends on 5**

Files: `modifiers.py` *(new)*, `configure.zcml`, `profiles/default/`, `tests/test_modifiers.py` *(new)*

- [ ] Test first with the ZODB helper from Task 1: pickle a `NamedBlobImage` carrying a derivative through `persistent_id` callbacks shaped like `CloneNamedFileBlobs`, and assert the clone arrives **without** a derivative. Add the negative test proving why: without the modifier, the nested blob pickles to an empty `Blob` (`Blob.__getstate__` returns `None`).
- [ ] Register an `ICloneModifier` mapping `_pgthumbor_source` and `_pgthumbor_source_info` to `None` on clone, so the repository never carries a derivative and a revert yields a bare field value that regenerates.
- [ ] Audit `plone.app.iterate` working copies (`ObjectCopiedEvent`) for the same path and cover whichever hook applies.
- [ ] Verify: `uv run pytest tests/test_modifiers.py -q`

### Task 11 — Backfill: skeleton, candidate SQL, progress
**PARALLEL-SAFE with 6–10 · depends on 5**

Files: `scripts/backfill_thumbor_sources.py` *(new)*, `tests/test_backfill.py` *(new)*

- [ ] Tests first against a fake cursor recording `execute(sql, params)` and `tmp_path` for the progress file: SQL filters on class; excludes already-recorded outcomes unless in recheck mode; size-only mode adds the dimension predicate; keyset pagination present. `Progress` round-trips, starts at zero on a missing or corrupt file, and records the phase per chunk.
- [ ] Follow `scripts/purge_legacy_scales.py` for structure, but put the zconsole bootstrap under `if "app" in dir(): main(app)` instead of `sys.exit(1)`, so the module imports cleanly under pytest and its pure parts get tested.
- [ ] Keyset pagination on `zoid` (`WHERE zoid > :last ORDER BY zoid LIMIT :chunk`), not `OFFSET`, and not the catalog.
- [ ] Verify: `uv run pytest tests/test_backfill.py -q`

### Task 12 — Backfill: phase 1 runner
**SEQUENTIAL · depends on 11**

Files: `scripts/backfill_thumbor_sources.py`, `tests/test_backfill.py`

- [ ] Tests first with a fake connection and monkeypatched `transaction`: each candidate processed; commit per chunk; resumes from progress; a per-object error is skipped, not fatal; stops when no candidates remain; dry run writes nothing and reports the count plus median encoded size on a sample.
- [ ] Fetch the `NamedBlobImage` by oid directly — never load the content object — then `_p_deactivate()`. Lift `_invalidate_cache` and `_release_memory` (`malloc_trim`) verbatim from `purge_legacy_scales.py`.
- [ ] Verify: `uv run pytest tests/test_backfill.py -q`

### Task 13 — Backfill: phase 2 reindex, request context, verification
**SEQUENTIAL · depends on 12**

Files: `scripts/backfill_thumbor_sources.py`, `src/plone/pgthumbor/purge_scales.py`, `tests/test_backfill.py`, `tests/test_purge_scales.py`

- [ ] Test first, and make it the gate: calling the indexer with `getRequest()` returning `None` must make the entry point **abort**, not proceed. Without `setRequest()`, `ImageFieldScales`' `getMultiAdapter((context, None), name="images")` fails, the indexer raises, and ZCatalog stores `Missing.Value` — emptying `image_scales` for every object touched.
- [ ] Entry point does `makerequest` → `alsoProvides(req, IPlonePgthumborLayer)` → `setRequest(req)`, then asserts the `@@images` lookup resolves to `ThumborImageScaling` before doing any work.
- [ ] Apply the same fix to `purge_scales.main()`, which has the defect latently today.
- [ ] Phase 2 reindexes with `idxs=["image_scales"]` — an empty `idxs` calls `notifyModified()` and would bump the modification date of every object touched.
- [ ] Terminal verification: zero remaining candidates **and** no catalog row carrying a Thumbor URL whose blob zoid belongs to an original that now has a derivative.
- [ ] Verify: `uv run pytest tests/test_backfill.py tests/test_purge_scales.py -q`

### Task 14 — Documentation and changelog
**SEQUENTIAL · depends on 8, 10 and 13**

Files: `docs/sources/explanation/architecture.md`, `docs/sources/llms.txt`, `docs/sources/explanation/why-thumbor.md`, `README.md`, `storage.py` docstrings, `docs/sources/how-to/backfill-source-derivatives.md` *(new)*, `docs/sources/how-to/index.md`, `CHANGES.md`

- [ ] Scope the "Pillow is never imported, never invoked" claim to the request path, where it stays true — `ThumborScaleStorage` still never calls `IImageScaleFactory`, and `tests/test_storage.py::test_no_pillow_invoked` needs no change. `llms.txt`'s "Zero Pillow dependency in Plone process" becomes flatly wrong and must go.
- [ ] Add a "Source derivatives" section: field-value placement, structural invalidation plus its versioning caveat, the loader's non-involvement, the draft decode, and the two attributes.
- [ ] New how-to for the backfill mirroring `how-to/purge-legacy-scales.md`, with the diataxis header, one sentence per line, and a toctree entry.
- [ ] While here: `docs/sources/reference/configuration.md` still documents `server_url`/`security_key`/`unsafe` as registry fields, which `upgrade_to_3` removed. Pre-existing drift, cheap to fix.
- [ ] Verify: `uvx ruff format --check .` and the vale pre-commit hook.

### Task 15 — Final verification
**SEQUENTIAL · depends on all**

- [ ] `uv run pytest --cov` passes the `fail_under = 90` gate; `derivative.py`, `subscribers.py` and `modifiers.py` individually clear 90 %.
- [ ] `uvx ruff check .` and `uvx ruff format --check .`.
- [ ] `pyproject.toml` declares both new direct imports (`Pillow`, `plone.dexterity`).
- [ ] Staging checklist from spec §9: the four IA images from `bda/aaf/deployment#6`; a crop set on one image above and one below the cap, comparing rendered regions — **expecting** that only the `src` is cropped, since `srcset`/HiDPI/`image_scales` never forward a scale name (out of scope, spec Non-goals); dry-run counts before the production backfill; Varnish ban after deploy.

---

## Wave map

| Wave | Tasks | Agents | Notes |
|---|---|---|---|
| A | 1 | 1 | Byte factories, ZODB helper and the env-var list gate everything downstream |
| B | **2, 3, 6** | 3 | Disjoint: settings/profiles · `derivative.py` · `scaling.py` |
| C | **4** (after 3), **7** (after 6) | 2 | |
| D | **5** (after 2+4), **8** (after 7) | 2 | 5 is the first join point |
| E | **9, 10, 11** (all after 5) | 3 | |
| F | **12** (after 11) | 1 | |
| G | **13** (after 12) | 1 | |
| H | **14** (after 8+10+13) | 1 | |
| I | **15** | 1 | |

**File-collision check across parallel waves.** `scaling.py` belongs to tasks 6 → 7 → 8, which are strictly sequential. `derivative.py` to 3 → 4 → 5. `configure.zcml` is touched by 2 (upgrade step), 9 (subscribers) and 10 (clone modifier) — never in the same wave. `pyproject.toml` by 1 (test extra), 3 (Pillow) and 9 (plone.dexterity) — never concurrent. `tests/test_integration.py` by 6 (`_mock_image_data`) and 9 (ZCML assertion) — never concurrent. `docs/sources/reference/configuration.md` belongs entirely to Task 2; Task 14 must not reopen it.
