# Thumbor Source Derivative — Design

**Date:** 2026-08-21, revised 2026-08-24
**Status:** approved
**Driver:** `bda/aaf/deployment#6` — Thumbor returns HTTP 400 for source images above its `MAX_PIXELS` limit

> **Revision 2026-08-24.** A pre-implementation review verified several claims of the first draft against the installed sources and found four of them wrong. Corrected here: the `draft()` reduction factor and the memory figures that followed from it (§3); the crop-rescale pseudocode, which could not run as written (§4); the characterisation of the two-phase backfill as an optimisation (§5); and the assertion that a stale derivative is structurally impossible, which versioning defeats (§1). Three adjacent defects that this feature would turn from loud failures into silent ones are now in scope: the srcset original-width upscale (§4), the `zconsole` request/layer gap that empties `image_scales` (§5), and the falsiness-as-unset config idiom that would break the `0` kill switch (§6).

> **Revision 2026-08-24, second pass.** A second review ran the falsifiable claims against the installed sources and against Pillow itself. Four more corrections: the `draft()` decoded height was off by one and would have made the first TDD assertion unsatisfiable (§3); `MAX_SOURCE_PIXELS` at 500 MP is unreachable, because Pillow raises inside `Image.open` at ~179 MP (§3); the mechanism by which a request-less reindex empties `image_scales` is not the one described — under `plone.pgcatalog` the column is overwritten with `null` rather than left untouched (§5); and the semaphore-timeout retry marker excluded its own image from the backfill forever (§3, §7). Newly in scope: an upper bound on the cap (§6), the persistent registration and upgrade step the clone modifier needs — plus the fact that CMFEditions may be absent entirely (§1); and orphaned derivative blobs (Accepted limitations). Issue **#21 moves from adjacent concern to prerequisite** (§5): this design would turn its loudest defect into a silent one, and the backfill would move uids out from under its proposed fix. Issues #14, #15 and #16 join the context; #16 already describes the `zconsole` defect this design proposes to fix.

> **Revision 2026-08-24, implementation notes.** Implemented on `feat/25-source-derivative`. Five things the build changed, all with their reasoning in the commit that made them. The "skip the derivative when it is not smaller than the source" rule is **dropped**: byte size and pixel count are independent, so a well-compressed 11811 px original can be smaller than its own 4000 px derivative while still being the image Thumbor refuses — the rule had no safe domain, and a larger derivative is now logged and kept. A **truncated source yields a truncated derivative** rather than none, because `plone.scale` enables `LOAD_TRUNCATED_IMAGES` process-wide and this package should not judge the same bytes more harshly than the scaling it replaces (§3, Accepted limitations). The clone modifier needs **no `zcml:condition`** after all — it is registered programmatically into `portal_modifier`, so no ZCML references CMFEditions and the `ImportError` is caught in `setuphandlers`. Derivatives are excluded from the backfill's candidate set by a **marker attribute** the generator writes, not by matching their filename, which would have silently skipped an editorial upload that happened to be named that way. And §5's terminal verification **does not** implement the URL-level assertion: it needs per-row hex matching inside JSONB, cannot be expressed as one indexed query, and detects exactly the failure the request-context guard refuses to allow — three counts replace it, of which "an owner whose `image_scales` is null" catches both "phase 2 never reached it" and "something nulled the column".

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
- The remaining `image_scales` metadata defects: issue #14 (indexing outside a request yields `@@images` URLs) and issue #15 (the metadata `download` entry is a Thumbor URL rather than a path to the original). Both are adjacent to §5 and neither is fixed here. Issue #16 — `purge_scales` missing the pgthumbor layer in the `zconsole` path — *is* fixed here, as part of §5, because this design would otherwise add a second script with the same defect.
- Fixing `_heal_legacy_uid`'s geometry defects — issue #21 — remains separate work in a separate file, but it is **not deferrable**: it ships first. See §5 and step 1 of §9.
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

**A second cost, on legacy field values.** Writing the attribute marks the `NamedBlobImage` changed, which gives it a new `_p_mtime`. That matters because scale uids are hashed over a modification time: `plone.scale`'s `hash_key` folds in `self.modified_time`, `ImageScaling.modified(fieldname)` takes it from `field.modified`, and `ModifiedPropertyMixin.modified` returns `self._modified / 1000` when that attribute exists but falls back to `self._p_mtime` when it does not. `_setData` sets `_modified`, so anything uploaded through the normal path is stable — its uids do not move when a derivative lands beside it. A field value whose stored state predates `_modified` has only `_p_mtime`, and there **every scale uid changes the moment the derivative is written**: catalog metadata and cached HTML go stale in one step, and every affected render falls through to `_heal_legacy_uid`. Phase 2 of the backfill repairs the catalog; it cannot repair what Varnish is holding. The size of that population is unknown — the dry run counts it before anything is written (§7), and it is a direct input to the #21 ordering argument in §5.

**Nesting has one consequence that must be handled explicitly: versioning.** `Products.CMFEditions` snapshots content by deep-pickling it, and `plone.app.versioningbehavior`'s `CloneNamedFileBlobs.getReferencedAttributes` protects blobs from that pickle by collecting them — but it walks **top-level field values only**, returning `field_value._blob` per `INamedBlobImageField`. A nested `_pgthumbor_source._blob` is not in that mapping, so it goes through the pickle, and `ZODB.blob.Blob.__getstate__` returns `None`: the file does not travel.

Every version of an image-bearing versioned type would therefore store a derivative whose blob is empty, and a **revert** would produce a field value carrying a derivative that `get_blob_ids()` resolves to a perfectly valid `(zoid, tid)` pointing at nothing. Thumbor 400s, and the structural-invalidation argument above does not save us, because the attribute *is* present. This affects any versioned type with a lead image, which is the common case.

The fix is to keep derivatives out of the repository entirely: register an `ICloneModifier` that maps `_pgthumbor_source` to `None` on clone, so a reverted object arrives with a bare field value and regenerates on the next modification. `plone.app.iterate` working copies (`ObjectCopiedEvent`) get the same treatment. Ordinary `manage_pasteObjects` needs nothing — it goes through `exportFile`/`importFile`, which does copy blob files — but it duplicates the derivative and re-fires `IObjectAddedEvent`, which is one of the reasons the subscriber must be idempotent.

**Registering that modifier is not a ZCML line.** CMFEditions clone modifiers live in the persistent `portal_modifier` tool: `plone.app.versioningbehavior`'s `install_modifiers` builds each one, wraps it in a `ConditionalTalesModifier` and calls `portal_modifier.register(id, wrapper)` from a GenericSetup import step. Three consequences follow. The registration needs a **GenericSetup upgrade step**, not merely a profile post-handler — `post_install` runs on install only, so an already-installed site would take the registry upgrade and never receive the modifier, and an already-installed site is precisely the one this design exists to repair. The modifier class needs the Zope 2 scaffolding its peers carry (`InitializeClass`, an add form and a factory), because the wrapper expects a registerable object. And the step must be idempotent, skipping when the id is already present, because upgrade steps get re-run.

**CMFEditions may not be there at all.** `Products.CMFEditions` is a hard dependency of `Products.CMFPlone`, so it always imports — but `portal_modifier` exists only once the CMFEditions GenericSetup profile has been applied, and `plone.app.versioningbehavior` reaches a site through `plone.app.contenttypes`, not through Plone core. A deployment with custom types and no `plone.app.contenttypes` has neither. The install step therefore looks the tool up as `getToolByName(site, "portal_modifier", None)` and returns quietly when it is absent, and the ZCML pulling in CMFEditions interfaces is guarded by `zcml:condition="installed Products.CMFEditions"`. Nothing is lost by that: with no repository there are no snapshots, and the modifier has nothing to protect.

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
| 11811 × 8858 (104 MP) | 2 | 1/2 | 5906 × 4429 (26 MP) | ~79 MB |
| 7000 × 5000 (35 MP) | 1 | **none** | 7000 × 5000 | ~105 MB |

So the derivative code computes the divisor itself: pick the largest power of two that keeps the result at or above the cap, and pass explicit target dimensions rather than `(cap, cap)`. For TIFF and PNG `draft` is a no-op and a full decode is the price.

**Encoding.** JPEG at quality 92 with `subsampling=0`. 4:4:4 matters here because the derivative is an intermediate that Thumbor scales again, and chroma subsampling applied twice produces colour fringing on edges. Images with an alpha channel are written as PNG instead. EXIF and IPTC are dropped, matching Thumbor's own `PRESERVE_EXIF_INFO = False` default.

**Deliberately excluded: EXIF orientation.** Applying it would make images above and below the cap render differently, because Thumbor's `RESPECT_ORIENTATION` defaults to off. Correcting orientation means changing both, with its own visual verification — a separate concern, not a side effect of this change.

**Failure policy.** Derivative generation must never fail an upload. Everything runs inside `try/except`; on failure there is no derivative, a log line, an `"error"` outcome record — non-terminal, so the backfill picks it up again — and the original is used exactly as before.

**A truncated source is not a failure, by decision.** `plone/scale/scale.py:53` sets `PIL.ImageFile.LOAD_TRUNCATED_IMAGES = True` at module import, process-wide, so it is already `True` in every Plone process that has loaded `plone.namedfile` — which is every process running this package. A truncated blob therefore decodes, with the missing rows filled grey, and produces a grey-padded derivative rather than an error. This design does not override that. A package that replaces Plone's scaling should not judge the same bytes more harshly than the scaling it replaces, and turning the flag off for our decode would mean mutating a process global for other threads — the objection this design already raises against `MAX_IMAGE_PIXELS`. See Accepted limitations.

`Image.MAX_IMAGE_PIXELS` is **not** touched. It is a module global that every `Image.open` in the process consults, including `plone.namedfile.utils.getImageInfo` on every upload; mutating it from a worker thread disables bomb protection process-wide for the duration.

That leaves Pillow's own ceiling in force, and it is lower than the first draft assumed. `_decompression_bomb_check` runs **inside `Image.open`**, before any code of ours can read `im.size`: above `2 × MAX_IMAGE_PIXELS` it raises `DecompressionBombError`, and between `MAX_IMAGE_PIXELS` and that it emits `DecompressionBombWarning`. With the installed default of 89,478,485 px the hard limit is 178,956,970 px — ~179 MP. A `MAX_SOURCE_PIXELS` of 500 MP would therefore never be reached, and a test for it could not be written without mutating the very global this paragraph forbids mutating.

So the constant sits **below** Pillow's limit and **above** everything that has to succeed: `MAX_SOURCE_PIXELS = 175_000_000`, a module constant and not a setting, checked against `im.size` after `open()` and before `load()`. The band is narrow on purpose. The largest image found in the field is 164.6 MP and it must still get a derivative — it is one of the images this design exists to fix — so any constant below that would reject exactly the wrong population. Pillow's hard limit is 178,956,970 px. 175 MP is the only sensible thing between the two, and if the field ever holds something larger, the answer is that Pillow refuses first, not that this number moves.

Beyond ~179 MP Pillow raises before the generator can look; the generator catches `DecompressionBombError` like any other failure and records it as an outcome, so those images stay enumerable rather than silently missing. They keep returning Thumbor's 400 — a known, bounded gap, and the reason this is a constant rather than a knob nobody would tune correctly.

Between 89.5 MP and 179 MP `Image.open` emits `DecompressionBombWarning`. Under default filters that is a log line, which is fine. Under `-W error` it becomes an exception that the blanket `try/except` would swallow into "no derivative", silently — the worst outcome available. The generator therefore suppresses that one warning class around its own decode with `warnings.catch_warnings()`. The filter state it manipulates is process-global for the duration, which is the same objection raised above against `MAX_IMAGE_PIXELS`; it is accepted here because the decode semaphore bounds the window to one thread and because the effect of the suppression is a missing log line, not disabled protection.

**Wiring.** A subscriber on `IObjectAddedEvent` and `IObjectModifiedEvent`, registered for `IDexterityContent`, that walks `iterSchemata` and collects every `INamedBlobImageField`.

Generation is synchronous. The mass-upload path (`@@fileUpload` → `DXFileFactory`) handles one file per request and holds a module-global `upload_lock` across `createContentInContainer`, so within a process that path serialises on its own.

The edit path holds no such lock, so `IObjectModifiedEvent` can fan out across worker threads — at ~79–105 MB per concurrent decode against a 4 GiB limit. A process-wide `BoundedSemaphore(1)` with a short acquisition timeout bounds it; on timeout the subscriber skips rather than queueing behind another decode, and records a `"retry"` outcome. That outcome is deliberately **not** terminal — see the candidate rule below, which has to re-select it.

**Every outcome is recorded.** A silent skip is indistinguishable from "correctly not needed", which would make the backfill's termination criterion unreachable and leave failures unenumerable. Alongside `_pgthumbor_source` (the derivative, or `None` when none is needed) the generator writes `_pgthumbor_source_info` carrying the outcome reason, the cap in force at the time, and the source blob's `(zoid, tid)`.

**Not every recorded outcome is terminal.** The backfill's candidate rule is "neither attribute present, **or** the recorded reason is non-terminal, **or** the recorded cap differs from the configured one". Terminal reasons are the ones where re-running changes nothing: a derivative was written, none was needed, or the source is a type we never process (SVG, animated GIF). Non-terminal reasons are `"retry"` from a semaphore timeout and `"error"` from a failed decode — transient conditions whose entire purpose is that a later run tries again. The first draft treated every record as terminal, which meant a single contended upload excluded its image from the backfill permanently while the terminal verification still reported success.

That recorded `(zoid, tid)` also closes an invalidation hole. The claim in §1 that a stale derivative cannot exist assumes every image replacement produces a *new* `NamedBlobImage`. In-place mutation is a real pattern — `NamedBlobImage.data` is a settable property, and migration scripts and transmogrifier blueprints use it. Comparing the recorded ids against `get_blob_ids(original)` at URL-build time turns a mismatch into "no derivative" instead of serving the previous image indefinitely.

### 4. Source selection and crop translation

Both decisions belong to `_build_thumbor_url()`, in one place:

```
source = getattr(data, "_pgthumbor_source", None) or data
```

**No retry fallback to the original.** If a derivative exists but has no committed TID yet, `get_blob_ids()` returns `None` and no Thumbor URL is produced — that is correct and intended. Substituting the original in that window would bake a permanent, direct Thumbor URL pointing at an image that may exceed `MAX_PIXELS`, frozen in catalog metadata with no path to recovery. The desired behaviour is what the naive expression already does; the rule is an *omission*, and needs a comment and a test so nobody later "fixes" it.

**Crop coordinates must be rescaled.** `plone.app.imagecropping` stores boxes in original pixels and `_get_crop()` passes them through untouched. Against a 4000 px derivative of an 11811 px original, an unscaled box cuts into empty space:

The box arrives in the nested form `((left, top), (right, bottom))`, so it cannot be mapped element-wise. Both axes need their own factor, taken from the derivative's *actual* size rather than from the cap — height rounding means `4000 × 2999`, not `4000 × 3000`, and a single shared factor drifts past the bottom edge. Rounding is direction-aware so the region never *loses* content at its edges: floor on left and top, ceil on right and bottom, then clamp to the derivative's bounds. (An earlier draft justified the same rule as "so the region never grows", which is backwards — floor on the near edges and ceil on the far ones make the mapped box cover the original selection rather than cut into it. The rule was right; the reason given for it was not.)

The translation applies only when the derivative was actually selected — gated on `source is not data`, never on "a crop exists". A skipped or failed derivative carrying a crop must pass the box through untouched.

If the rescaled box degenerates to zero width or height it is dropped entirely, and the mode's normal `fit_in`/`smart` values are restored. That ordering matters: the drop has to happen *before* the `if crop is not None:` block forces `fit_in=True, smart=False`, or a dropped crop leaves the scale rendering under crop semantics without a crop. Note that `libthumbor` silently ignores an all-zero box, which would otherwise yield a full uncropped image rather than an error.

Selection and crop translation must stay in the same function. Split apart, the next caller that picks a source and forgets the translation is one refactor away.

Since imagecropping reactivation is itself a committed deliverable (`bda/aaf/deployment#2`), the plan carries an explicit verification step: set a crop in the editor, on one image above and one below the cap, and compare the rendered region.

**Never request more pixels than the selected source holds.** `ThumborImageScaling.srcset` unconditionally back-fills an entry at the original's dimensions whenever no configured scale covers them — which for an 11811 px print original is always. Those dimensions come from the original via `pre_scale`, independently of source selection. Today that entry fails loudly with a 400. Once the source is a 4000 px derivative it *succeeds*, because Thumbor's `MAX_PIXELS` guards the source and not the output: it upscales to 11811 × 8858, allocating ~314 MB in a pod limited to 1536Mi and offering browsers a multi-megabyte srcset candidate.

Turning a visible failure into an invisible one is worse than leaving it broken, so the funnel clamps requested width and height to the selected source's dimensions, and `srcset` compares its guards against the effective source rather than the original — dropping entries it cannot satisfy instead of emitting an upscale.

**Deliberately excluded: falling back to the original for very small crops.** A crop covering fraction *X* of the derivative's edge feeds `cap × X` source pixels into a rendition of width *S*, so it stays lossless while `X ≥ S / cap`. The binding *S* is **not** the largest registered scale — it is the largest scale that actually carries a crop, because `plone.app.imagecropping` stores boxes per scale name. A site that only crops `preview 400:0` has a threshold of 10 % at a 4000 px cap, not 40 %.

| Largest cropped scale | cap 4000 | cap 5000 |
|---|---|---|
| 1600 | 40 % | 32 % |
| 460 | 11.5 % | 9.2 % |
| 400 | 10 % | 8 % |
| 175 | 4.4 % | 3.5 % |

Below the threshold the result softens rather than breaks. A special case would need a second setting for Thumbor's `MAX_PIXELS` and a branch that must *not* fire for the very images this design exists to fix. It earns its place only once a deployment has measured its own crop distribution and found the threshold binding — which is what the dry run reports (§7).

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

A uid fallback URL is not a dead end: since #17, `ThumborScaleStorage.get_or_generate()` heals a volatile miss by parsing the deterministic `{fieldname}-{width}-{md5hex32}` uid and rebuilding the info through `pre_scale`. Traversal then constructs the Thumbor URL live from the current committed state, and therefore picks the derivative. What it does *not* reliably recover is the geometry the uid was minted for — that is #21, and it is why the prerequisite at the end of this section exists. The rows in the table above say the right blob is served; they do not say it is served at the right size until #21 is fixed.

That healing path performs **no ZODB write** — `ThumborScaleStorage` keeps its storage in a volatile dict and never calls `generate_scale`. There is no write-on-read anywhere in this design.

**The backfill reindexes in two phases, and this is load-bearing.** Derivatives for chunk N are written and committed; chunk N is reindexed on a later pass, once its TIDs exist.

An earlier draft of this section called the second phase an optimisation. That was wrong. For exactly the images this feature targets, the existing catalog rows do not hold uid URLs — they hold direct, absolute, signed Thumbor URLs, because `_build_thumbor_url` succeeds today (it knows nothing of `MAX_PIXELS`; it is Thumbor that 400s afterwards). A browser fetches those without Plone in the path, so healing can never intervene. Between phase 1 and phase 2 nothing improves for the 263 affected images, and a skipped or failed phase 2 leaves them broken while the run reports success.

Consequently a chunk counts as done only once reindexed, progress records the two phases separately, and the terminal verification asserts that no catalog row still carries a Thumbor URL whose blob zoid belongs to an original that now has a derivative.

**The reindex must run with a request carrying the browser layer.** `plone.pgthumbor` gates itself exclusively on `IPlonePgthumborLayer` via `zope.globalrequest.getRequest()`. Under `zconsole`, `makerequest()` does not call `setRequest()`, so `getRequest()` returns `None`. A reindex run that way does not merely miss the derivative: it **overwrites `image_scales` with null for every object it touches**.

The first draft had the outcome right and the mechanism wrong, which matters, because a test written against the wrong mechanism asserts nothing. Verified against the installed packages, the chain is:

1. `Products.CMFPlone.image_scales.indexer.image_scales` calls `queryMultiAdapter((obj, getRequest()), IImageScalesAdapter)`. With `None` as the request the lookup misses and the indexer raises `AttributeError` — that is plone.indexer's deliberate "do not index" signal. `ImageFieldScales.__call__` never runs, so its own `ComponentLookupError` guard is not what fires.
2. `plone.pgcatalog`'s `extraction.extract_idx` reads every metadata column as `getattr(wrapper, meta_name, None)`. The default **swallows** the `AttributeError`, and with it the "do not index" signal. The value becomes plain `None`.
3. `None` is then written, not skipped: `idx["image_scales"] = None`, handed to `set_partial_pending(zoid, {"image_scales": None})` — a JSONB merge that puts an explicit `null` where the scales used to be.

The full-reindex path arrives in the same place. pgcatalog's `_reindexObject` filters `idxs` down to registered *indexes*, and `image_scales` is a metadata *column*, so the filter empties the list and every column is re-extracted from the same request-less wrapper.

The entry point must therefore `alsoProvides(request, IPlonePgthumborLayer)` and `setRequest(request)`, and abort loudly if either is missing — concretely, assert that the `@@images` lookup resolves to `ThumborImageScaling` before any write happens. The existing `purge_scales.main()` carries the same defect, already filed as **issue #16**, and is fixed in this change. Issues **#14** (metadata indexed outside a request gets `@@images` URLs) and **#15** (the metadata `download` entry is a Thumbor URL) sit on the same surface; neither is addressed here, but #15's URLs will resolve through the derivative once this ships.

**#21 was a prerequisite, not an adjacent concern — and it is now satisfied** (PR #29, merged as `14a0de1`; a release is still owed, see §9). The reasoning is kept because it constrains what follows. The healing path recovered *a* scale rather than *the* scale: `mode` is hardcoded to `"scale"`, `_allowed_scale_sizes()` collides on width, and `0:H` scales heal to original dimensions. An earlier draft called this a dependency whose pressure the two-phase backfill relieves. That was wrong twice over, and the correction reverses the conclusion.

*The backfill relieves less than claimed.* Two phases keep the **backfill population** off the healing path. Healing stays on the ordinary editorial path regardless: a new upload or an image replacement reindexes inside the creating transaction, when neither blob has a TID, so the catalog holds a uid URL until something reindexes again — and every render in that window heals. Nothing in this design changes that.

*This design makes #21's worst defect quieter.* Its `0:H` case heals into a request at the original's dimensions. Today that pushes Thumbor past `MAX_PIXELS` on a large original and returns a loud 400. With a derivative in place and the §4 clamp active, the same request succeeds against a 4000 px source and returns a correctly encoded image of the wrong size. By the standard §4 sets for the srcset upscale — a visible failure is worth more than an invisible one — that is a regression, and this change is what would introduce it.

*And the order was not free.* An earlier draft of this paragraph named the wrong mechanism, which is worth recording because the corrected one still constrains this design. That draft said #21's fix hashes `allowed_sizes × modes` against `self.modified_time`. It cannot: `ImageScaling.publishTraverse` builds the storage for the uid branch as `getMultiAdapter((self.context, None), IImageScaleStorage)` (`plone/namedfile/scaling.py:453`), so `modified_time` is `None` at healing time, while the uid was minted through `functools.partial(self.modified, fieldname)`. Hashing candidates against `None` would have matched nothing. The shipped fix reconstructs the mint time first, via `ImageScaling(self.context, getRequest()).modified(fieldname)` — the same bound method minting uses, so the value is identical by construction, and byte-identical across `plone.namedfile` 7.3.0 and 8.x.

That reconstruction still resolves to `field.modified`, so §1's consequence is untouched: on a field value lacking `_modified`, writing a derivative moves `_p_mtime`, moves the reconstructed mint time, and makes every uid minted before that point unmatchable. The ordering conclusion held; the work happened in the right order.

What is new is a defined safety net. Where no candidate matches, healing now falls back to the first registered scale of that width and **never** requests the original's dimensions speculatively — those are used only when the uid carries no width at all and no `0:H` scale is registered. The `MAX_PIXELS` regression this section warned the derivative would introduce is therefore closed at its source, not merely masked by §4's clamp. The dry run should still count the population that lands in the fallback: it renders at approximately-right geometry rather than at the geometry its uid was minted for.

### 6. Settings

| Setting | Default | Notes |
|---|---|---|
| `PGTHUMBOR_SOURCE_MAX_EDGE` | `4000` | Longest edge in pixels; `0` disables derivative generation entirely; values above `8000` are clamped |

Env-first with registry fallback, matching the existing settings, and exposed in the control panel — but **not** copying their falsiness-as-unset idiom. `get_thumbor_config()` currently treats a falsy env value as "unset" and reaches for the registry, which is harmless for booleans defaulting to `False` and wrong for an integer where `0` is the meaningful value "disabled". Copying it would make `PGTHUMBOR_SOURCE_MAX_EDGE=0` — the documented kill switch, the thing you reach for during a bulk import or an incident — silently read as unset and get overwritten by the registry default of 4000. The env lookup uses a `None` sentinel instead, and all four env/registry combinations are tested explicitly.

**Pillow becomes a declared runtime dependency.** This is the package's first image-byte manipulation, so `Pillow` joins `[project].dependencies` (and the test extra, which currently has neither Pillow nor Plone). The documented claim that Pillow is never imported has to be scoped honestly to the request path, where it remains true: `ThumborScaleStorage` still never calls `IImageScaleFactory`, and no scaling happens while serving a page. Affected: `docs/sources/explanation/architecture.md`, `docs/sources/llms.txt`, `docs/sources/explanation/why-thumbor.md`, `README.md`, and three `storage.py` docstrings. Worth noting the claim is already imprecise today — `plone.namedfile.utils` imports `PIL.Image` at module scope and runs EXIF handling on every upload.

**Why 4000 is the default, and why a default is not an answer.** `plone.pgthumbor` is a generic package. 4000 is chosen to be safe and unsurprising on a site nobody has measured — roughly 2.5× a typical largest rendition of 1600, comfortably inside every bound in this section. It is not a recommendation for any particular deployment.

The right value for a given site follows from that site's own numbers: the largest scale that actually carries crops (§4), the largest registered rendition, and whether high-pixel-density scales are enabled. The package's job is to make those measurable and to make changing the answer cheap. Picking the number is the deployment's job, and the documentation says so rather than presenting 4000 as settled. On the motivating deployment the largest registered scale is `albumfull 1600:1600` with high-pixel-density scales disabled; the dry run reports which scales actually carry crops before anyone commits. 5000 is the obvious next step if the measurement asks for it — 25 MP, still far inside Thumbor's 75 MP, at ~1.56× the storage and resize cost of 4000.

**Changing the cap is a setting change, not a migration.** The cap in force is recorded in `_pgthumbor_source_info` alongside the outcome, and a recorded cap that no longer matches the configured one makes the image a backfill candidate on an ordinary run — no `force`, no recheck mode to remember. That is deliberate. On a generic package every deployment tunes this number at least once, and a tuning step that silently does nothing unless you pass the right flag is a trap. The old derivatives are replaced in place; §Accepted limitations covers what becomes of the ones they displace.

**Why the cap itself needs a ceiling.** The safety argument of this entire design is arithmetic: a longest edge of 4000 bounds the derivative at 16 MP, far below Thumbor's 75 MP `MAX_PIXELS`, so a derivative structurally cannot reproduce the failure it exists to prevent. That argument holds only while the cap stays below √75e6 ≈ 8660. A cap of 10000 yields derivatives up to 100 MP and brings the 400 straight back — silently, because generation would succeed and only Thumbor would object, which is exactly the shape of the bug being fixed. The schema is therefore `min=0, max=8000`, and the config reader clamps rather than trusting its input, since a registry value written before the bound existed would otherwise pass straight through.

### 7. Backfill

Existing content needs derivatives, and existing catalog rows hold direct Thumbor URLs pointing at originals, which do not heal.

- **Work list from SQL, not a catalog walk.** A `zconsole` brain walk over the result set OOM-killed a production container during the original scan; the existing `scan_oversized_sql.py` is the working pattern (it lives in the `bda/aaf/deployment` repository, not in this package). Phase 1 never loads a content object — it fetches the `NamedBlobImage` by oid directly, which is what keeps it memory-light.
- **Keyset pagination on `zoid`**, not `sort_on="UID"`. The earlier draft asked for the latter because pgcatalog gives no process-stable order; since the work list does not come from the catalog at all, `WHERE zoid > :last ORDER BY zoid` is strictly better — stable, resumable, and free of `OFFSET` drift.
- **Dedicated pod**, chunked and resumable, progress persisted outside ZODB, with the phase recorded per chunk.
- **Reindex passes `idxs=["image_scales"]`.** An empty `idxs` triggers `notifyModified()` and would bump the modification date of every object touched, breaking recently-modified listings and downstream cache keys. `purge_scales.py` already gets this right.
- **Two passes over the population, not one.** The colour-space trigger is invisible to SQL: a 3 MP CMYK image is a candidate but no size predicate finds it. Pass 1 selects by pixel count; pass 2 determines colour space from image headers only, via a ranged read rather than a full blob fetch — `ZODB.blob.loadBlob` would otherwise pull the entire S3 object for each of tens of thousands of images.
- **A cap change does not need `force`.** The cap is part of the outcome record, so a mismatch against the configured value makes the image a candidate on an ordinary run (§6). Non-terminal outcomes (`"retry"`, `"error"`) are re-selected the same way. `force` remains for the residue only: re-examining images whose outcome is terminal *and* whose cap still matches, after a change this design cannot observe — a Pillow upgrade, corrected colour-space detection, a new skip type.
- **Terminates with a verification run that finds zero candidates**, the same shape as the legacy-scale purge, plus the catalog assertion from §5.
- **The dry run reports the real numbers first**, and they are what a deployment tunes the cap against: candidate count; median encoded derivative size on a sample; the count of candidate field values carrying no `_modified` attribute, whose scale uids will move when the derivative is written (§1); and a histogram of the scale names that actually carry crops, read from the `plone.app.imagecropping` annotation's `{fieldname}_{scalename}` keys. The last one gives the binding *S* for §4's threshold, which is the whole input to choosing a cap. The ~2 MB figure used in the consequences below is an estimate; JPEG q92 at 4:4:4 on detailed press imagery may well land at 4–8 MB.

### 8. Tests

Written first, following the existing pure-pytest structure (`test_scaling`, `test_blob`, `test_imagecropping`).

- Trigger matrix: above and below cap; CMYK under the cap; alpha → PNG; animated GIF and SVG untouched; corrupt bytes do not fail the upload.
- `draft()` path, asserted on decoded dimensions rather than memory — including the 7000 px case where no DCT reduction is available.
- Crop translation: direction-aware rounding, clamping, degenerate box, and the untouched-without-derivative case.
- Selection: derivative preferred; **no** substitution of the original while the derivative lacks a TID; recorded source ids mismatched → treated as no derivative.
- Clamping: no Thumbor URL ever requests a box larger than the selected source.
- Integration: replacing an image drops the derivative and regenerates it.
- Outcome records: a terminal reason is skipped on re-run, a non-terminal one is reprocessed without `force`, and the backfill's candidate predicate agrees with the generator's own skip logic.
- Clone modifier: composes with `CloneNamedFileBlobs` rather than displacing it, and its install step is a no-op — not an error — when `portal_modifier` is absent.

Two of these need capability the suite does not have today, and both are cheap:

**Real image bytes.** The trigger matrix *is* Pillow behaviour — mode sniffing, `is_animated`, palette transparency, DCT reduction — so mocking the PIL boundary would only assert the mock, and the `draft()` assertion could not be written at all. Tiny images generated in-process (`Image.new(...).save(buf, ...)`) fit the established style: `tests/conftest.py` already holds plain module-level helpers that tests import by name, and the largest fixture needed encodes in milliseconds. Binary fixtures in git are the wrong answer here — unreviewable, and `check-added-large-files` is in the pre-commit config. A small file pinning each factory's `mode`/`size`/`format` guards against a Pillow upgrade changing what they produce.

**A minimal ZODB layer.** `ZODB.DB(DemoStorage())` with a blob dir — no Plone, no Zope — covers the two things mocks structurally cannot: `get_blob_ids` before and after commit, which is the whole of §5's table, and the pickle round-trip that proves the clone modifier keeps derivatives out of version snapshots.

One trap: `_mock_image_data()` in `test_scaling.py` and `test_integration.py` returns a `MagicMock`, so `getattr(data, "_pgthumbor_source", None)` auto-creates a child mock and never returns `None`. Both copies need an explicit `_pgthumbor_source = None` **before** source selection lands, or roughly forty currently-green tests fail at once on `u64()` receiving a mock.

**A second trap, in the environment rather than the code.** The package declares `plone.namedfile` without an upper bound and the repository carries no lockfile, so a fresh checkout resolves whatever PyPI currently offers — 8.1.1 at the time of writing, against 7.3.0 in the existing development environment and 7.x on the target deployment. That version decides `_HAS_SCALE_URL`, and with it whether `ThumborImageScale.__init__` or `ThumborImageScale._scale_url` is the live path. `_build_thumbor_url` is common to both, so the implementation needs no fork — but tests for source selection, crop translation and clamping would silently exercise only whichever half the resolver happened to pick. The work is done against `plone.namedfile < 8` to match production, and every §4 behaviour is asserted through both entry points regardless of which one is live.

### 9. Rollout

1. **Issue #21 is fixed, merged and released first** — see §5. Fixed and merged: PR #29, `14a0de1`. **Not yet released** — the newest tag is `v0.6.5` and `CHANGES.md` still reads `0.6.6 (unreleased)`. Cut that release before anything below, per `RELEASE.md`: the version comes from the git tag via `hatch-vcs`, so it is finalize `CHANGES.md`, bump `docs/sources/conf.py`, push, tag, publish.
2. Stage next. Verify the four IA images from `bda/aaf/deployment#6`, plus the crop check from §4.
3. Confirm the running image ships `plone-pgthumbor >= 0.6.5`. The consuming deployment pins a floor of `>= 0.6.4`, which predates the #17 healing fix, so a build that resolved early may still carry 0.6.4.
4. Production deploy, then **run the GenericSetup upgrade steps** (profile 3 → 4 → 5) and verify both landed: the `source_max_edge` registry record exists, and `portal_modifier` lists the clone modifier. Neither arrives on its own — `post_install` does not run on an existing site, and without the modifier every new version snapshot stores a derivative whose blob is empty.
5. **Dry run first, then pick the cap.** The default of 4000 is the package's generic starting point, not a decision for this site. Read the crop histogram and the candidate counts, choose the value, set it, and only then run the backfill. Changing it later is an ordinary re-run (§6), so this is a cheap decision to revisit — but not a free one at 263+ images.
6. Then the backfill.
7. **Varnish ban.** The 400 responses are very likely cached; a previous error page survived 5.4 hours despite `s-maxage=60`.
8. Close `bda/aaf/deployment#6` only after production verification.

## Consequences

The question of whether press originals may be downscaled — open in `#6` and awaiting consultation with the houses — becomes moot. Originals stay at print resolution and Thumbor stops seeing them.

Two improvements fall out that were not the goal: the loader's 1 GB disk cache holds roughly twenty times as many sources at ~2 MB each, and the Thumbor pod, limited to 1536Mi, never decodes a 100 MP image again.

## Accepted limitations

- Crops below ~40 % of the edge soften rather than break. See §4.
- Storage grows by one derivative per qualifying image. Blobs above 64 KB live in S3, not in the Postgres PVC, so the cost is object storage. The dry run reports the exact count before anything is written.
- Orientation handling is unchanged and remains inconsistent for images carrying non-default EXIF orientation.
- Images above ~179 MP get no derivative: Pillow raises `DecompressionBombError` inside `Image.open` before the generator can act, and raising the global that governs it is refused in §3. They are recorded as failures and keep returning Thumbor's 400. Nothing in the field is close — the largest found is 164.6 MP — but an upload above that limit stays broken and enumerable rather than fixed.
- A truncated source blob yields a truncated derivative — grey where the scan data ran out — rather than no derivative. `plone.scale` enables `LOAD_TRUNCATED_IMAGES` process-wide, so this is what Plone already renders for such a blob today; the difference is only that the derivative freezes the grey padding into the Thumbor source instead of reproducing it per scale. The original is untouched and `@@download` still serves the real bytes. A cheap follow-up, if it ever matters, is a diagnostic on JPEGs that end without an `FFD9` marker: that detects the case without decoding and without touching anyone's globals.
- A uid minted for a scale with **both** dimensions set cannot be told apart from one minted with `scale=None`: `hash_key` deletes the `scale` key whenever width and height are both truthy, so both produce the identical uid. Healing assumes the named call. Where a crop is configured for that scale, a healed `image_scales`-minted uid renders cropped while the live render does not. Inherited from #21, not introduced here, but it is a crop discrepancy and the §9 crop verification must expect it.
- Replacing an image orphans its derivative. The old field value, its derivative and both blobs become garbage the moment a new `NamedBlobImage` takes their place, and nothing in this design collects them. "One derivative per qualifying image" is therefore the steady-state figure, not the storage bound, on a site where images are re-uploaded often.

## Open questions

None blocking. Cap tuning is a setting, not a decision.
