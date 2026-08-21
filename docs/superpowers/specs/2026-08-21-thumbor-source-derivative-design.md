# Thumbor Source Derivative — Design

**Date:** 2026-08-21
**Status:** approved, not yet planned
**Driver:** `bda/aaf/deployment#6` — Thumbor returns HTTP 400 for source images above its `MAX_PIXELS` limit

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

### 2. The loader is not involved

The Thumbor URL is `/<signing>/<transforms>/<zoid_hex>/<tid_hex>`. If it names the derivative's blob, the loader fetches the derivative — it does not need to know that a derivative exists. No mapping lookup per request, no loader release, no new Thumbor image tag.

The entire change is contained in `plone-pgthumbor`.

### 3. Trigger and generation

A derivative is created when **either** condition holds:

1. The longest edge exceeds the configured cap.
2. The source is not a clean sRGB raster: `CMYK`, `LAB`, `I;16`, or a palette image carrying transparency.

Condition 2 is not an afterthought. Tying normalisation to size alone would let a 3 MP CMYK press image through unconverted, and CMYK is the *normal* case for material pulled from print layouts. As an independent trigger, every image ends up in the sRGB path regardless of size.

**Excluded:** SVG (already handled by `_SKIP_THUMBOR_TYPES`) and animated GIFs, which a derivative would flatten.

**Decoding.** `Image.draft(None, (cap, cap))` before `load()`. For JPEG this scales in the DCT step by 1/2, 1/4 or 1/8: a 104 MP JPEG decodes at roughly 1.6 MP — milliseconds and a few MB instead of seconds and ~300 MB. `mode=None` keeps `draft` away from the colour space; the CMYK conversion is done deliberately afterwards, through the embedded ICC profile when one is present. For TIFF and PNG `draft` is a no-op and a full decode is the price.

**Encoding.** JPEG at quality 92 with `subsampling=0`. 4:4:4 matters here because the derivative is an intermediate that Thumbor scales again, and chroma subsampling applied twice produces colour fringing on edges. Images with an alpha channel are written as PNG instead. EXIF and IPTC are dropped, matching Thumbor's own `PRESERVE_EXIF_INFO = False` default.

**Deliberately excluded: EXIF orientation.** Applying it would make images above and below the cap render differently, because Thumbor's `RESPECT_ORIENTATION` defaults to off. Correcting orientation means changing both, with its own visual verification — a separate concern, not a side effect of this change.

**Failure policy.** Derivative generation must never fail an upload. Everything runs inside `try/except`; on failure there is no derivative, a log line, and the original is used exactly as before.

This includes suspending `Image.MAX_IMAGE_PIXELS` locally and enforcing an explicit ceiling of our own instead. Pillow raises `DecompressionBombError` above twice its default, i.e. ~179 MP, and the largest image found in the field was 164.6 MP.

**Wiring.** A subscriber on `IObjectAddedEvent` and `IObjectModifiedEvent` that walks `iterSchemata` and collects every `INamedBlobImageField`.

Generation is synchronous. The mass-upload path (`@@fileUpload` → `DXFileFactory`) handles one file per request and holds a module-global `upload_lock` across `createContentInContainer`, so within a process the work serialises on its own. Combined with `draft()`, the memory profile does not justify a worker.

### 4. Source selection and crop translation

Both decisions belong to `_build_thumbor_url()`, in one place:

```
source = getattr(data, "_pgthumbor_source", None) or data
```

**No retry fallback to the original.** If a derivative exists but has no committed TID yet, `get_blob_ids()` returns `None` and no Thumbor URL is produced — that is correct and intended. Substituting the original in that window would bake a permanent, direct Thumbor URL pointing at an image that may exceed `MAX_PIXELS`, frozen in catalog metadata with no path to recovery. The desired behaviour is what the naive expression already does; the rule is an *omission*, and needs a comment and a test so nobody later "fixes" it.

**Crop coordinates must be rescaled.** `plone.app.imagecropping` stores boxes in original pixels and `_get_crop()` passes them through untouched. Against a 4000 px derivative of an 11811 px original, an unscaled box cuts into empty space:

```
factor = derivative_width / original_width
box    = tuple(round(v * factor) for v in box)
```

clamped to the derivative's bounds, and dropped entirely if rounding degenerates it to zero width or height.

Selection and crop translation must stay in the same function. Split apart, the next caller that picks a source and forgets the translation is one refactor away.

Since imagecropping reactivation is itself a committed deliverable (`bda/aaf/deployment#2`), the plan carries an explicit verification step: set a crop in the editor, on one image above and one below the cap, and compare the rendered region.

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

**The backfill nevertheless reindexes in two phases:** derivatives for chunk N are written and committed, and chunk N is reindexed on a later pass, once its TIDs exist. Not for correctness — healing covers that — but so the catalog holds direct Thumbor URLs instead of trading a 302 through Plone for every image on a site of ~139k objects, which today's metadata does not do.

**Dependency on #21.** The healing path currently recovers *a* scale rather than *the* scale: `mode` is hardcoded to `"scale"`, `_allowed_scale_sizes()` collides on width, and `0:H` scales heal to original dimensions. This design does not depend on healing for correctness, but the two-phase backfill is what keeps the site off that path. If #21 is fixed first, the pressure drops; if not, the two-phase requirement becomes load-bearing.

### 6. Settings

| Setting | Default | Notes |
|---|---|---|
| `PGTHUMBOR_SOURCE_MAX_EDGE` | `4000` | Longest edge in pixels; `0` disables derivative generation entirely |

Env-first with registry fallback, matching the existing settings, and exposed in the control panel.

**Why 4000.** The motivating deployment's largest registered scale is `albumfull 1600:1600` with high-pixel-density scales disabled, so 4000 is ~2.5× the largest rendition and leaves crops down to 40 % of the edge lossless. Changing the cap invalidates existing derivatives; the remedy is re-running the (idempotent) backfill.

### 7. Backfill

Existing content needs derivatives, and existing catalog rows hold direct Thumbor URLs pointing at originals, which do not heal.

- **Work list from SQL, not a catalog walk.** A `zconsole` brain walk over the result set OOM-killed a production container during the original scan; the existing `scan_oversized_sql.py` is the working pattern.
- **Dedicated pod**, chunked and resumable, progress persisted.
- **`sort_on="UID"` wherever results are ordered** — pgcatalog gives no process-stable order otherwise.
- **Two phases per chunk**, as above.
- **Terminates with a verification run that finds zero candidates**, the same shape as the legacy-scale purge.

### 8. Tests

Written first, following the existing pure-pytest structure (`test_scaling`, `test_blob`, `test_imagecropping`).

- Trigger matrix: above and below cap; CMYK under the cap; alpha → PNG; animated GIF and SVG untouched; corrupt bytes do not fail the upload.
- `draft()` path, asserted on decoded dimensions rather than memory.
- Crop translation: rounding, clamping, degenerate box.
- Selection: derivative preferred; **no** substitution of the original while the derivative lacks a TID.
- Integration: replacing an image drops the derivative and regenerates it.

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
