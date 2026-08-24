# Thumbor Source Derivative — Design

**Date:** 2026-08-21, revised 2026-08-24
**Status:** approved
**Driver:** `bda/aaf/deployment#6` — Thumbor returns HTTP 400 for source images above its `MAX_PIXELS` limit

> **Revision 2026-08-24.** A pre-implementation review verified several claims of the first draft against the installed sources and found four of them wrong. Corrected here: the `draft()` reduction factor and the memory figures that followed from it (§3); the crop-rescale pseudocode, which could not run as written (§4); the characterisation of the two-phase backfill as an optimisation (§5); and the assertion that a stale derivative is structurally impossible, which versioning defeats (§1). Three adjacent defects that this feature would turn from loud failures into silent ones are now in scope: the srcset original-width upscale (§4), the `zconsole` request/layer gap that empties `image_scales` (§5), and the falsiness-as-unset config idiom that would break the `0` kill switch (§6).

## Problem

Thumbor refuses to process images beyond `MAX_PIXELS` (default `75e6`). Because `thumbor/server.py` promotes `DecompressionBombWarning` to an error, `PILEngine.create_image` returns `None` and the request ends as HTTP 400 after several seconds of work.

On the deployment that motivated this, a scan of `object_state` found **120 images above 75 MP** and a further **143 between 50 and 75 MP**, the largest being 164.6 MP. These are print originals — 100×75 cm at 300 dpi — held deliberately at that resolution for press downloads.

Two obvious remedies are both wrong:

- **Downscale the originals.** Destroys the asset the press area exists to serve.
- **Raise Thumbor's `MAX_PIXELS`.** Requires an env passthrough in `thumborblobloader` and leaves Thumbor decoding 100+ MP images on every cache miss, inside a pod limited to 1.5 GiB.

There is also a second, larger problem hiding behind the first: Thumbor fetches the **full original blob** for every cache miss, including a 60×60 listing thumbnail. A 40 MB original crosses the network and the decoder to produce a 3 KB tile.

## Goals

1. Thumbor never sees an image it cannot process.
2. Thumbor never fetches more source data than a web-sized rendition needs.
3. Colour-space and format oddities (CMYK press material in particular) are normalised before Thumbor sees them.
4. The original remains untouched and continues to serve `@@download`.

## Non-goals

- Validating or rejecting uploads by pixel count. This design makes such a limit unnecessary.
- Changing Thumbor's `MAX_PIXELS` in either direction.
- Any change to `zodb-pgjsonb-thumborblobloader`.
- Periodic `image_scales` maintenance — tracked separately as issue #20.
- Fixing `_heal_legacy_uid`'s geometry defects — issue #21. The two-phase backfill keeps the site off that path.
- Closing the gaps where crops never reach the URL at all: `srcset`, HiDPI and `image_scales` do not forward a scale name, so `_get_crop` finds nothing and only the `src` is ever cropped. Pre-existing and out of scope, but the §9 crop verification must expect it rather than treat it as a regression of this change.

## Design

### 1. The derivative lives on the field value

A **thumbor source derivative** is a second `NamedBlobImage`, stored as an attribute on the original `NamedBlobImage` instance:

```
content.image                    → NamedBlobImage (original, print resolution)
content.image._pgthumbor_source  → NamedBlobImage (derivative, capped + normalised)
```

Because it is a distinct `NamedBlobImage`, it carries its own blob and therefore its own `(zoid, tid)` — which is exactly what a Thumbor URL addresses.

**Why on the field value rather than in an annotation.** Invalidation becomes structural. Replacing an image produces a *new* `NamedBlobImage`, which carries no derivative attribute, so a fresh derivative is generated. A stale derivative cannot exist. An annotation keyed by fieldname would need explicit invalidation, and a missed invalidation means serving the wrong image indefinitely — the failure mode this project has already met twice with `image_scales` and legacy scales.

Secondary benefits: no fieldname plumbing (the choke point receives the field value, not the field name), and uniform coverage of every image field — content image, lead-image behavior, sponsor logos, custom fields.

The cost is an undeclared attribute on a `plone.namedfile` class. `NamedBlobFile` is a plain `Persistent` subclass without `__slots__`, and this package is the only writer.

**Nesting has one consequence that must be handled explicitly: versioning.** `Products.CMFEditions` snapshots content by deep-pickling it, and `plone.app.versioningbehavior`'s `CloneNamedFileBlobs.getReferencedAttributes` protects blobs from that pickle by collecting them — but it walks **top-level field values only**, returning `field_value._blob` per `INamedBlobImageField`. A nested `_pgthumbor_source._blob` is not in that mapping, so it goes through the pickle, and `ZODB.blob.Blob.__getstate__` returns `None`: the file does not travel.

Every version of an image-bearing versioned type would therefore store a derivative whose blob is empty, and a **revert** would produce a field value carrying a derivative that `get_blob_ids()` resolves to a perfectly valid `(zoid, tid)` pointing at nothing. Thumbor 400s, and the structural-invalidation argument above does not save us, because the attribute *is* present. This affects any versioned type with a lead image, which is the common case.

The fix is to keep derivatives out of the repository entirely: register an `ICloneModifier` that maps `_pgthumbor_source` to `None` on clone, so a reverted object arrives with a bare field value and regenerates on the next modification. `plone.app.iterate` working copies (`ObjectCopiedEvent`) get the same treatment. Ordinary `manage_pasteObjects` needs nothing — it goes through `exportFile`/`importFile`, which does copy blob files — but it duplicates the derivative and re-fires `IObjectAddedEvent`, which is one of the reasons the subscriber must be idempotent.

### 2. The loader is not involved

The Thumbor URL is `/<signing>/<transforms>/<zoid_hex>/<tid_hex>`. If it names the derivative's blob, the loader fetches the derivative — it does not need to know that a derivative exists. No mapping lookup per request, no loader release, no new Thumbor image tag.

The entire change is contained in `plone-pgthumbor`.

### 3. Trigger and generation

A derivative is created when **either** condition holds:

1. The longest edge exceeds the configured cap.
2. The source is not a clean sRGB raster: `CMYK`, `LAB`, `I;16`, or a palette image carrying transparency.

Condition 2 is not an afterthought. Tying normalisation to size alone would let a 3 MP CMYK press image through unconverted, and CMYK is the *normal* case for material pulled from print layouts. As an independent trigger, every image ends up in the sRGB path regardless of size.

**Excluded:** SVG (already handled by `_SKIP_THUMBOR_TYPES`) and animated GIFs, which a derivative would flatten.

**Decoding.** `Image.draft()` before `load()`, with `mode=None` so it leaves the colour space alone — the CMYK conversion is done deliberately afterwards, through the embedded ICC profile when one is present.

The naive call `draft(None, (cap, cap))` underdelivers, and by enough to matter. `JpegImageFile.draft` computes `scale = min(w // target_w, h // target_h)` and then picks the largest power of two not exceeding it. At a 4000 px cap:

| Original | `scale` | DCT divisor | Decoded | Peak RGB buffer |
|---|---|---|---|---|
| 11811 × 8858 (104 MP) | 2 | 1/2 | 5906 × 4430 (26 MP) | ~79 MB |
| 7000 × 5000 (35 MP) | 1 | **none** | 7000 × 5000 | ~105 MB |

So the derivative code computes the divisor itself: pick the largest power of two that keeps the result at or above the cap, and pass explicit target dimensions rather than `(cap, cap)`. For TIFF and PNG `draft` is a no-op and a full decode is the price.

**Encoding.** JPEG at quality 92 with `subsampling=0`. 4:4:4 matters here because the derivative is an intermediate that Thumbor scales again, and chroma subsampling applied twice produces colour fringing on edges. Images with an alpha channel are written as PNG instead. EXIF and IPTC are dropped, matching Thumbor's own `PRESERVE_EXIF_INFO = False` default.

**Deliberately excluded: EXIF orientation.** Applying it would make images above and below the cap render differently, because Thumbor's `RESPECT_ORIENTATION` defaults to off. Correcting orientation means changing both, with its own visual verification — a separate concern, not a side effect of this change.

**Failure policy.** Derivative generation must never fail an upload. Everything runs inside `try/except`; on failure there is no derivative, a log line, and the original is used exactly as before.

`Image.MAX_IMAGE_PIXELS` is **not** touched. It is a module global that every `Image.open` in the process consults, including `plone.namedfile.utils.getImageInfo` on every upload; mutating it from a worker thread disables bomb protection process-wide for the duration. It is also unnecessary: the error threshold is twice the default, ~179 MP, and the largest image found in the field was 164.6 MP. Instead the ceiling is our own — read `im.size` from the header after `open()` and refuse above `MAX_SOURCE_PIXELS` (a module constant, not a setting) before `load()` is ever called.

**Wiring.** A subscriber on `IObjectAddedEvent` and `IObjectModifiedEvent`, registered for `IDexterityContent`, that walks `iterSchemata` and collects every `INamedBlobImageField`.

Generation is synchronous. The mass-upload path (`@@fileUpload` → `DXFileFactory`) handles one file per request and holds a module-global `upload_lock` across `createContentInContainer`, so within a process that path serialises on its own.

The edit path holds no such lock, so `IObjectModifiedEvent` can fan out across worker threads — at ~79–105 MB per concurrent decode against a 4 GiB limit. A process-wide `BoundedSemaphore(1)` with a short acquisition timeout bounds it; on timeout the subscriber skips and records a retry marker rather than queueing behind another decode.

**Every outcome is recorded.** A silent skip is indistinguishable from "correctly not needed", which would make the backfill's termination criterion unreachable and leave failures unenumerable. Alongside `_pgthumbor_source` (the derivative, or `None` when none is needed) the generator writes `_pgthumbor_source_info` carrying the outcome reason and the source blob's `(zoid, tid)` at generation time. The backfill treats "neither attribute present" as a candidate.

That recorded `(zoid, tid)` also closes an invalidation hole. The claim in §1 that a stale derivative cannot exist assumes every image replacement produces a *new* `NamedBlobImage`. In-place mutation is a real pattern — `NamedBlobImage.data` is a settable property, and migration scripts and transmogrifier blueprints use it. Comparing the recorded ids against `get_blob_ids(original)` at URL-build time turns a mismatch into "no derivative" instead of serving the previous image indefinitely.

### 4. Source selection and crop translation

Both decisions belong to `_build_thumbor_url()`, in one place:

```
source = getattr(data, "_pgthumbor_source", None) or data
```

**No retry fallback to the original.** If a derivative exists but has no committed TID yet, `get_blob_ids()` returns `None` and no Thumbor URL is produced — that is correct and intended. Substituting the original in that window would bake a permanent, direct Thumbor URL pointing at an image that may exceed `MAX_PIXELS`, frozen in catalog metadata with no path to recovery. The desired behaviour is what the naive expression already does; the rule is an *omission*, and needs a comment and a test so nobody later "fixes" it.

**Crop coordinates must be rescaled.** `plone.app.imagecropping` stores boxes in original pixels and `_get_crop()` passes them through untouched. Against a 4000 px derivative of an 11811 px original, an unscaled box cuts into empty space:

The box arrives in the nested form `((left, top), (right, bottom))`, so it cannot be mapped element-wise. Both axes need their own factor, taken from the derivative's *actual* size rather than from the cap — height rounding means `4000 × 2999`, not `4000 × 3000`, and a single shared factor drifts past the bottom edge. Rounding is direction-aware so the region never grows: floor on left and top, ceil on right and bottom, then clamp to the derivative's bounds.

The translation applies only when the derivative was actually selected — gated on `source is not data`, never on "a crop exists". A skipped or failed derivative carrying a crop must pass the box through untouched.

If the rescaled box degenerates to zero width or height it is dropped entirely, and the mode's normal `fit_in`/`smart` values are restored. That ordering matters: the drop has to happen *before* the `if crop is not None:` block forces `fit_in=True, smart=False`, or a dropped crop leaves the scale rendering under crop semantics without a crop. Note that `libthumbor` silently ignores an all-zero box, which would otherwise yield a full uncropped image rather than an error.

Selection and crop translation must stay in the same function. Split apart, the next caller that picks a source and forgets the translation is one refactor away.

Since imagecropping reactivation is itself a committed deliverable (`bda/aaf/deployment#2`), the plan carries an explicit verification step: set a crop in the editor, on one image above and one below the cap, and compare the rendered region.

**Never request more pixels than the selected source holds.** `ThumborImageScaling.srcset` unconditionally back-fills an entry at the original's dimensions whenever no configured scale covers them — which for an 11811 px print original is always. Those dimensions come from the original via `pre_scale`, independently of source selection. Today that entry fails loudly with a 400. Once the source is a 4000 px derivative it *succeeds*, because Thumbor's `MAX_PIXELS` guards the source and not the output: it upscales to 11811 × 8858, allocating ~314 MB in a pod limited to 1536Mi and offering browsers a multi-megabyte srcset candidate.

Turning a visible failure into an invisible one is worse than leaving it broken, so the funnel clamps requested width and height to the selected source's dimensions, and `srcset` compares its guards against the effective source rather than the original — dropping entries it cannot satisfy instead of emitting an upscale.

**Deliberately excluded: falling back to the original for very small crops.** At a 4000 px cap and a largest scale of 1600, crops down to ~40 % of the edge stay lossless; below that the result softens rather than breaks. A special case would need a second setting for Thumbor's `MAX_PIXELS` and a branch that must *not* fire for the very images this design exists to fix. It earns its place only if the houses turn out to crop that aggressively.

### 5. Commit semantics and `image_scales`

A ZODB blob receives its TID at commit; before that it has no OID at all, since ZODB assigns them during `_store_objects`. `get_blob_ids()` rejects both states. `reindexObject()` runs inside the creating transaction, so it cannot know a TID that does not yet exist.

The consequences, per case:

| Case | Original | Derivative | Indexed URL |
|---|---|---|---|
| New upload | uncommitted | uncommitted | uid fallback |
| Edit, image replaced | uncommitted | uncommitted | uid fallback |
| Edit, image untouched | committed | committed | direct Thumbor URL, correct |
| Backfill, phase 1 | committed | uncommitted | uid fallback |
| Backfill, phase 2 | committed | committed | direct Thumbor URL, correct |

A uid fallback URL is not a dead end: since #17, `ThumborScaleStorage.get_or_generate()` heals a volatile miss by parsing the deterministic `{fieldname}-{width}-{md5hex32}` uid and rebuilding the info through `pre_scale`. Traversal then constructs the Thumbor URL live from the current committed state, and therefore picks the derivative.

That healing path performs **no ZODB write** — `ThumborScaleStorage` keeps its storage in a volatile dict and never calls `generate_scale`. There is no write-on-read anywhere in this design.

**The backfill reindexes in two phases, and this is load-bearing.** Derivatives for chunk N are written and committed; chunk N is reindexed on a later pass, once its TIDs exist.

An earlier draft of this section called the second phase an optimisation. That was wrong. For exactly the images this feature targets, the existing catalog rows do not hold uid URLs — they hold direct, absolute, signed Thumbor URLs, because `_build_thumbor_url` succeeds today (it knows nothing of `MAX_PIXELS`; it is Thumbor that 400s afterwards). A browser fetches those without Plone in the path, so healing can never intervene. Between phase 1 and phase 2 nothing improves for the 263 affected images, and a skipped or failed phase 2 leaves them broken while the run reports success.

Consequently a chunk counts as done only once reindexed, progress records the two phases separately, and the terminal verification asserts that no catalog row still carries a Thumbor URL whose blob zoid belongs to an original that now has a derivative.

**The reindex must run with a request carrying the browser layer.** `plone.pgthumbor` gates itself exclusively on `IPlonePgthumborLayer` via `zope.globalrequest.getRequest()`. Under `zconsole`, `makerequest()` does not call `setRequest()`, so `getRequest()` returns `None` — and then `ImageFieldScales` fails its `getMultiAdapter((context, None), name="images")` lookup, the indexer raises, and ZCatalog stores `Missing.Value`. A reindex run that way does not merely miss the derivative: it **empties `image_scales` for every object it touches**. The entry point must `alsoProvides(request, IPlonePgthumborLayer)` and `setRequest(request)`, and abort loudly if either is absent. The existing `purge_scales.main()` has the same latent defect and is fixed in the same change.

**Dependency on #21.** The healing path currently recovers *a* scale rather than *the* scale: `mode` is hardcoded to `"scale"`, `_allowed_scale_sizes()` collides on width, and `0:H` scales heal to original dimensions. This design does not depend on healing for correctness, but the two-phase backfill is what keeps the site off that path. If #21 is fixed first, the pressure drops; if not, the two-phase requirement becomes load-bearing.

### 6. Settings

| Setting | Default | Notes |
|---|---|---|
| `PGTHUMBOR_SOURCE_MAX_EDGE` | `4000` | Longest edge in pixels; `0` disables derivative generation entirely |

Env-first with registry fallback, matching the existing settings, and exposed in the control panel — but **not** copying their falsiness-as-unset idiom. `get_thumbor_config()` currently treats a falsy env value as "unset" and reaches for the registry, which is harmless for booleans defaulting to `False` and wrong for an integer where `0` is the meaningful value "disabled". Copying it would make `PGTHUMBOR_SOURCE_MAX_EDGE=0` — the documented kill switch, the thing you reach for during a bulk import or an incident — silently read as unset and get overwritten by the registry default of 4000. The env lookup uses a `None` sentinel instead, and all four env/registry combinations are tested explicitly.

**Pillow becomes a declared runtime dependency.** This is the package's first image-byte manipulation, so `Pillow` joins `[project].dependencies` (and the test extra, which currently has neither Pillow nor Plone). The documented claim that Pillow is never imported has to be scoped honestly to the request path, where it remains true: `ThumborScaleStorage` still never calls `IImageScaleFactory`, and no scaling happens while serving a page. Affected: `docs/sources/explanation/architecture.md`, `docs/sources/llms.txt`, `docs/sources/explanation/why-thumbor.md`, `README.md`, and three `storage.py` docstrings. Worth noting the claim is already imprecise today — `plone.namedfile.utils` imports `PIL.Image` at module scope and runs EXIF handling on every upload.

**Why 4000.** The motivating deployment's largest registered scale is `albumfull 1600:1600` with high-pixel-density scales disabled, so 4000 is ~2.5× the largest rendition and leaves crops down to 40 % of the edge lossless. Changing the cap invalidates existing derivatives; the remedy is re-running the (idempotent) backfill.

### 7. Backfill

Existing content needs derivatives, and existing catalog rows hold direct Thumbor URLs pointing at originals, which do not heal.

- **Work list from SQL, not a catalog walk.** A `zconsole` brain walk over the result set OOM-killed a production container during the original scan; the existing `scan_oversized_sql.py` is the working pattern. Phase 1 never loads a content object — it fetches the `NamedBlobImage` by oid directly, which is what keeps it memory-light.
- **Keyset pagination on `zoid`**, not `sort_on="UID"`. The earlier draft asked for the latter because pgcatalog gives no process-stable order; since the work list does not come from the catalog at all, `WHERE zoid > :last ORDER BY zoid` is strictly better — stable, resumable, and free of `OFFSET` drift.
- **Dedicated pod**, chunked and resumable, progress persisted outside ZODB, with the phase recorded per chunk.
- **Reindex passes `idxs=["image_scales"]`.** An empty `idxs` triggers `notifyModified()` and would bump the modification date of every object touched, breaking recently-modified listings and downstream cache keys. `purge_scales.py` already gets this right.
- **Two passes over the population, not one.** The colour-space trigger is invisible to SQL: a 3 MP CMYK image is a candidate but no size predicate finds it. Pass 1 selects by pixel count; pass 2 determines colour space from image headers only, via a ranged read rather than a full blob fetch — `ZODB.blob.loadBlob` would otherwise pull the entire S3 object for each of tens of thousands of images.
- **Re-running after a cap change needs `force`.** Because every examined image records an outcome, a plain re-run skips everything. A recheck mode that ignores the recorded outcome is required, not optional.
- **Terminates with a verification run that finds zero candidates**, the same shape as the legacy-scale purge, plus the catalog assertion from §5.
- **The dry run reports the real numbers first** — candidate count and median encoded derivative size on a sample. The ~2 MB figure used in the consequences below is an estimate; JPEG q92 at 4:4:4 on detailed press imagery may well land at 4–8 MB.

### 8. Tests

Written first, following the existing pure-pytest structure (`test_scaling`, `test_blob`, `test_imagecropping`).

- Trigger matrix: above and below cap; CMYK under the cap; alpha → PNG; animated GIF and SVG untouched; corrupt bytes do not fail the upload.
- `draft()` path, asserted on decoded dimensions rather than memory — including the 7000 px case where no DCT reduction is available.
- Crop translation: direction-aware rounding, clamping, degenerate box, and the untouched-without-derivative case.
- Selection: derivative preferred; **no** substitution of the original while the derivative lacks a TID; recorded source ids mismatched → treated as no derivative.
- Clamping: no Thumbor URL ever requests a box larger than the selected source.
- Integration: replacing an image drops the derivative and regenerates it.

Two of these need capability the suite does not have today, and both are cheap:

**Real image bytes.** The trigger matrix *is* Pillow behaviour — mode sniffing, `is_animated`, palette transparency, DCT reduction — so mocking the PIL boundary would only assert the mock, and the `draft()` assertion could not be written at all. Tiny images generated in-process (`Image.new(...).save(buf, ...)`) fit the established style: `tests/conftest.py` already holds plain module-level helpers that tests import by name, and the largest fixture needed encodes in milliseconds. Binary fixtures in git are the wrong answer here — unreviewable, and `check-added-large-files` is in the pre-commit config. A small file pinning each factory's `mode`/`size`/`format` guards against a Pillow upgrade changing what they produce.

**A minimal ZODB layer.** `ZODB.DB(DemoStorage())` with a blob dir — no Plone, no Zope — covers the two things mocks structurally cannot: `get_blob_ids` before and after commit, which is the whole of §5's table, and the pickle round-trip that proves the clone modifier keeps derivatives out of version snapshots.

One trap: `_mock_image_data()` in `test_scaling.py` and `test_integration.py` returns a `MagicMock`, so `getattr(data, "_pgthumbor_source", None)` auto-creates a child mock and never returns `None`. Both copies need an explicit `_pgthumbor_source = None` **before** source selection lands, or roughly forty currently-green tests fail at once on `u64()` receiving a mock.

### 9. Rollout

1. Stage first. Verify the four IA images from `bda/aaf/deployment#6`, plus the crop check from §4.
2. Confirm the running image ships `plone-pgthumbor >= 0.6.5`. The consuming deployment pins a floor of `>= 0.6.4`, which predates the #17 healing fix, so a build that resolved early may still carry 0.6.4.
3. Production deploy, then the backfill.
4. **Varnish ban.** The 400 responses are very likely cached; a previous error page survived 5.4 hours despite `s-maxage=60`.
5. Close `bda/aaf/deployment#6` only after production verification.

## Consequences

The question of whether press originals may be downscaled — open in `#6` and awaiting consultation with the houses — becomes moot. Originals stay at print resolution and Thumbor stops seeing them.

Two improvements fall out that were not the goal: the loader's 1 GB disk cache holds roughly twenty times as many sources at ~2 MB each, and the Thumbor pod, limited to 1536Mi, never decodes a 100 MP image again.

## Accepted limitations

- Crops below ~40 % of the edge soften rather than break. See §4.
- Storage grows by one derivative per qualifying image. Blobs above 64 KB live in S3, not in the Postgres PVC, so the cost is object storage. The dry run reports the exact count before anything is written.
- Orientation handling is unchanged and remains inconsistent for images carrying non-default EXIF orientation.

## Open questions

None blocking. Cap tuning is a setting, not a decision.
