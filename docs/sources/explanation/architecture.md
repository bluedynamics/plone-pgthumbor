<!-- diataxis: explanation -->

# Architecture

plone.pgthumbor replaces Plone's built-in image scaling pipeline with Thumbor, an
open-source image processing server.
Instead of loading blob data into Python,
resizing with Pillow, and storing the result back in ZODB, Plone generates a signed
Thumbor URL and sends the browser a 302 redirect.
Thumbor fetches the blob
directly from PostgreSQL (via zodb-pgjsonb's `blob_state` table), scales it, and
serves the result -- all without Plone touching a single pixel while serving the
request.
Plone does decode pixels in one place, and only there: when an image is
uploaded or edited, it builds the capped source derivative Thumbor will read
from.
See [Source derivatives](#source-derivatives) below.

This page explains how data flows through the system, how the components fit
together, and the reasoning behind the key design choices.

## Key files

### plone.pgthumbor (Plone side)

| File | Purpose |
|---|---|
| `scaling.py` | `ThumborImageScale` + `ThumborImageScaling` -- `@@images` view override, 302 redirect, crop lookup |
| `storage.py` | `ThumborScaleStorage` -- `IImageScaleStorage` adapter, no scaling on the request path |
| `derivative.py` | Source derivative pixel work -- decode, colour normalisation, encode; pure bytes in, bytes out |
| `subscribers.py` | `generate_source_derivatives` -- walks Dexterity schemata on add and modify, one decode at a time |
| `modifiers.py` | `SkipThumborSourceDerivatives` -- keeps derivatives out of `CMFEditions` version snapshots |
| `zconsole.py` | Request context for command line entry points, so a re-index cannot blank `image_scales` |
| `url.py` | `thumbor_url()` + `scale_mode_to_thumbor()` -- signed URL generation via libthumbor (supports crop coordinates) |
| `blob.py` | `get_blob_ids()` -- extracts `(zoid, tid)` from `NamedBlobImage._blob` |
| `config.py` | `ThumborConfig` dataclass, reads env vars (`PGTHUMBOR_SERVER_URL`, `PGTHUMBOR_SECURITY_KEY`) |
| `restapi.py` | `ThumborAuthService` -- `@thumbor-auth` REST endpoint for access control |
| `interfaces.py` | `IThumborSettings` + `ICropProvider` -- Plone registry schema and crop adapter interface |
| `controlpanel.py` | `ThumborSettingsForm` -- Plone control panel for Thumbor settings |
| `addons_compat/` | Conditional adapters for third-party addons (for example, `imagecropping.py` for plone.app.imagecropping) |
| `overrides.zcml` | ZCML overrides that wire `ThumborImageScaling` and `ThumborScaleStorage` |
| `configure.zcml` | Service registration, GenericSetup profile, control panel page, conditional addon includes |

### zodb-pgjsonb-thumborblobloader (Thumbor side)

| File | Purpose |
|---|---|
| `loader.py` | Thumbor `LOADER` plugin -- fetches blob bytes from PG / S3 / disk cache |
| `auth_handler.py` | `AuthImagingHandler` -- Thumbor handler that enforces Plone access control |
| `cache.py` | `BlobCache` -- LRU disk cache with deterministic `{zoid:016x}-{tid:016x}.blob` filenames |
| `pool.py` | `AsyncConnectionPool` singleton (psycopg3), schema verification on first use |
| `s3.py` | S3 download via boto3 + `asyncio.to_thread` for large blob offload |

## Overview

The fundamental idea is separation of concerns: Plone decides *what* to show and
*who* may see it. Thumbor handles the *how* -- fetching, resizing, caching, and
serving image bytes.
The two communicate indirectly through signed URLs and a
shared PostgreSQL database.

```{mermaid}
flowchart LR
    A[Plone] -->|302 redirect with signed URL| B[Browser]
    B -->|follow redirect| C[Thumbor]
    C -->|fetch blob| D[(PostgreSQL)]
    C -->|fallback| E[(S3)]
    C -->|serve scaled image| B
```

## Request flow

A complete image request touches multiple services.
Here is the sequence for a
browser rendering a page that contains a scaled image:

```{mermaid}
sequenceDiagram
    participant B as Browser
    participant N as nginx
    participant P as Plone
    participant T as Thumbor
    participant PG as PostgreSQL

    B->>N: GET /page (HTML)
    N->>P: proxy /page
    P->>B: HTML with <img src="/thumbor/{hmac}/.../{zoid}/{tid}">

    B->>N: GET /thumbor/{hmac}/.../{zoid}/{tid}
    N->>T: strip prefix, forward to Thumbor
    T->>T: verify HMAC signature
    T->>PG: SELECT data FROM blob_state WHERE zoid=? AND tid=?
    PG->>T: blob bytes
    T->>T: resize / crop / convert
    T->>N: scaled image bytes
    N->>B: image response (cacheable)
```

### Authenticated content (3-segment URL)

When the content is not publicly accessible, Plone appends the content object's
ZOID as a third URL segment.
Thumbor's `AuthImagingHandler` detects the 3-segment
format and makes a subrequest to Plone before loading the blob:

```{mermaid}
sequenceDiagram
    participant B as Browser
    participant N as nginx
    participant T as Thumbor
    participant P as Plone (internal)
    participant PG as PostgreSQL

    B->>N: GET /thumbor/{hmac}/.../{blob_zoid}/{tid}/{content_zoid}
    N->>T: strip prefix, forward (Cookie header preserved)
    T->>T: detect 3-segment URL → extract content_zoid

    T->>P: GET /@thumbor-auth?zoid={content_zoid} (Cookie + Authorization forwarded)
    P->>PG: SELECT (allowed_roles && user_principals) FROM object_state
    PG->>P: allowed = true/false
    P->>T: 200 OK / 401 Unauthorized

    alt authorized
        T->>PG: SELECT data FROM blob_state
        PG->>T: blob bytes
        T->>B: scaled image
    else unauthorized
        T->>B: 403 Forbidden
    end
```

### Step by step (Plone side)

1. **Browser requests a page.** Plone renders HTML.
When `@@images` is called for
   an image field, `ThumborImageScaling` creates a `ThumborImageScale` instance.

2. **`ThumborImageScale.__init__()` generates the URL.** It extracts the blob's
   ZOID and TID from `NamedBlobImage._blob` (via `get_blob_ids()`), maps the Plone
   scale mode to Thumbor parameters, and calls `thumbor_url()` to produce an
   HMAC-signed URL.

3. **Access check decides 2-segment vs 3-segment URL.** `_needs_auth_url()` queries
   PostgreSQL directly: does the object's `allowed_roles` TEXT[] column
   contain `'Anonymous'`?
   If yes, the content is public and a 2-segment URL
   suffices.
   If no (or if paranoid mode is enabled), the content object's ZOID is
   appended as a third segment.

4. **`index_html()` returns a 302 redirect.** The browser follows the redirect to
   Thumbor.

### Step by step (Thumbor side)

1. **Thumbor receives the request.** If `HANDLER_LISTS` includes
   `zodb_pgjsonb_thumborblobloader.auth_handler`, `AuthImagingHandler` intercepts
   the request before the standard imaging pipeline.

2. **Auth check (3-segment URLs only).** `_extract_content_zoid()` inspects the URL
   path.
   If the last three segments are all valid hex, this is a 3-segment
   authenticated URL.
   The handler forwards the browser's `Cookie` and
   `Authorization` headers to Plone's `@thumbor-auth` endpoint for verification.
   Results are cached per `(content_zoid, cookie)` for `PGTHUMBOR_AUTH_CACHE_TTL`
   seconds.

3. **The loader fetches blob data.** `loader.load()` parses the image path into
   `(zoid, tid)` integers, checks the disk cache, then queries `blob_state` in
   PostgreSQL.
   If the row has a `data` column (PG bytea), the bytes are returned
   directly.
   If only an `s3_key` is present, the loader downloads from S3 via
   `asyncio.to_thread` (since boto3 is synchronous).

4. **Thumbor processes the image.** Standard Thumbor pipeline: decode, apply
   operations (resize, fit-in, smart crop, filters), encode, return.

5. **Result caching.** Thumbor's built-in result storage caches the processed
   image.
   Subsequent requests for the same signed URL skip processing entirely.

## Design choices

### Why 302 redirect (not proxy)

Plone could proxy the request -- fetching the scaled image from Thumbor and
streaming it to the browser.
This would hide Thumbor behind Plone but at a severe
cost:

- **Memory.** Every image response would flow through the Plone WSGI process,
  consuming Python memory for the duration of the transfer.
- **Concurrency.** Each proxied image request occupies a Plone worker thread.
  A page with 20 images would hold 20 threads during image delivery.
- **Cacheability.** A 302 redirect lets the browser (and any CDN or reverse proxy
  in front) cache the Thumbor URL directly.
  Subsequent requests never touch Plone.

The 302 approach means the browser makes two requests for the first load -- one to
Plone (fast, returns only a redirect header) and one to Thumbor (which does the
actual work).
On subsequent loads, the browser cache or CDN handles the image
directly.

### Why ZOID + TID as URL path

Thumbor's image URL is `{blob_zoid:x}/{blob_tid:x}` in hexadecimal.
This design
has several advantages:

- **Immutable cache keys.** A ZODB TID (transaction ID) is assigned once and never
  reused.
  The combination of `(zoid, tid)` uniquely and permanently identifies a
  specific version of a blob.
  When the image changes, it gets a new TID, producing
  a new URL.
  Old cached responses become naturally unreachable -- no explicit cache
  invalidation needed.

- **No path encoding.** ZODB OIDs are 8-byte integers. Their hex representation
  contains only `[0-9a-f]` characters -- no URL encoding issues, no filesystem
  special characters, no ambiguity.
  Compare this with using the Plone content path,
  which would need encoding for spaces, Unicode, slashes, and the many edge cases
  of Plone's virtual hosting.

- **Direct database lookup.** The loader queries `blob_state WHERE zoid = ? AND
  tid = ?` -- a primary key lookup, the fastest possible database operation.

- **No ZODB dependency.** Thumbor does not need ZODB, Plone, or any Zope library.
  It needs only psycopg and the PostgreSQL DSN.
  This keeps the Thumbor container
  small and fast.

### Why ThumborScaleStorage stores no image data

In standard Plone, `AnnotationStorage` stores scaled image data as annotations on
the content object.
Each scale is a persistent object containing the resized bytes.
`ThumborScaleStorage` overrides this entirely:

- `scale()` delegates to `pre_scale()`, which computes target dimensions but
  generates no image data.
- `get_or_generate()` returns stored metadata without calling `generate_scale()`.
- `generate_scale()` also delegates to `pre_scale()`.

This means no scaling happens on the request path and no annotation objects
are created in ZODB.
`ThumborScaleStorage` never looks up `IImageScaleFactory`,
so no image bytes are decoded while a page is being served.
Pillow is imported
in the process, and the package uses it, but only on write: see
[Source derivatives](#source-derivatives).
Dimension metadata (uid, width, height) is held in a
volatile, in-memory dict that lives only for the lifetime of the adapter
instance -- nothing is persisted to ZODB, so no `ScalesDict` write transactions
are created.
This is enough for Plone to generate `<img>` tags with correct
`width` and `height` attributes, and enough for the catalog to index scale
information.

### ZCML overrides

plone.pgthumbor replaces two Plone components via `overrides.zcml`:

1. **`@@images` browser page** -- `ThumborImageScaling` replaces
   `plone.namedfile`'s `ImageScaling` for all `IImageScaleTraversable` objects.
   This intercepts every image scale request site-wide.
   Bound to `IPlonePgthumborLayer`, so it only activates in Plone sites where the
   add-on is installed.

2. **`IImageScaleStorage` adapter** -- A factory function
   (`thumbor_scale_storage_factory`) is registered for
   `(IImageScaleTraversable, *)`.
   The `*` discriminator is necessary because
   `plone.namedfile` calls `getMultiAdapter((context, modified_callable), IImageScaleStorage)` --
   the second argument is a callable or `None`, never a request, so a browser-layer
   discriminator would never match.
   The factory checks `IPlonePgthumborLayer` on the
   current request at runtime: if the layer is active, it returns a
   `ThumborScaleStorage` instance (volatile dict, no Pillow); otherwise it falls
   back to the standard `AnnotationStorage`.
   This makes the override safe for Zope
   instances hosting multiple Plone sites where only some have pgthumbor installed.

These are `overrides.zcml` registrations (not `configure.zcml`), which means they
take precedence over plone.namedfile's own registrations regardless of ZCML loading
order.

### Pluggable crop providers (ICropProvider)

Thumbor supports explicit crop coordinates in its URL format
(`{left}x{top}:{right}x{bottom}`), which crop the source image before
resizing.
plone.pgthumbor exposes this through the `ICropProvider` ZCA adapter
interface, keeping addon-specific logic out of the core scaling code.

The lookup flow in `_get_crop()`:

1. Call `queryAdapter(context, ICropProvider)`.
   If no adapter is registered,
   return `None` (no crop).
2. Extract the scale name from plone.namedfile's `scale_info["key"]` tuple.
3. Call `provider.get_crop(fieldname, scale_name)`.
4. If the provider returns a 4-tuple `(left, top, right, bottom)`, convert it
   to the nested format `((left, top), (right, bottom))` that libthumbor
   expects.

When a crop is active, `_build_thumbor_url()` forces `fit_in=True` and
`smart=False`.
The rationale: if the editor has explicitly chosen a crop
region, automatic smart detection should not override that choice.

**Why an adapter, not a hook or event?**
The ZCA adapter pattern is the right choice because:

- It is conditional by nature -- no adapter registered means zero overhead.
- It composes cleanly with ZCML conditions (`zcml:condition="installed ..."`)
  for automatic activation when a compatible addon is present.
- Multiple crop sources can coexist: a more specific `for` interface wins
  over `for="*"`, following standard ZCA precedence.
- Third-party packages can provide their own `ICropProvider` without modifying
  plone.pgthumbor code.

The built-in `ImageCroppingCropProvider` (in `addons_compat/imagecropping.py`)
reads from `IAnnotations(context)["plone.app.imagecropping"]`, where
plone.app.imagecropping stores its crop boxes.
It is registered via
conditional ZCML and has zero import cost when the addon is not installed.

### SVG passthrough

SVGs are vector images that Thumbor cannot process.
When `ThumborImageScale`
detects `content_type == "image/svg+xml"`, it falls back to the standard Plone
behavior -- serving the SVG directly without redirect.
The set of skipped types
is defined in `_SKIP_THUMBOR_TYPES`.

(source-derivatives)=

## Source derivatives

Thumbor refuses to process images beyond its `MAX_PIXELS` limit, 75 megapixels by
default, and it answers HTTP 400 after several seconds of work when it meets one.
Press originals routinely exceed that: 100 by 75 cm at 300 dpi is 104 megapixels,
and the largest example that motivated this work was 164.6 megapixels.
Shrinking the originals destroys the asset the press area exists to serve, and
raising `MAX_PIXELS` leaves Thumbor decoding 100+ megapixel images on every cache
miss.
A second problem hides behind the first: Thumbor fetches the *whole* original
for every cache miss, including a 60 by 60 listing thumbnail, so a 40 MB blob
crosses the network and the decoder to produce a 3 KB tile.

A **source derivative** solves both.
It is a capped, sRGB-normalised second image that Thumbor reads instead of the
original.
The original is never modified and `@@download` still serves it byte for byte.

### Where it lives

The derivative is a second `NamedBlobImage`, stored as an attribute on the
original field value:

```text
content.image                    -> NamedBlobImage (original, print resolution)
content.image._pgthumbor_source  -> NamedBlobImage (derivative, capped and normalised)
```

Because it is a distinct `NamedBlobImage`, it carries its own blob and therefore
its own `(zoid, tid)` pair, which is exactly what a Thumbor URL addresses.

Two attributes are written on the field value:

| Attribute | Meaning |
|---|---|
| `_pgthumbor_source` | The derivative, or `None` when the image needs none |
| `_pgthumbor_source_info` | `{"reason": ..., "max_edge": ..., "source_ids": ...}`, the outcome record |

A third attribute, `_pgthumbor_is_source`, is set on the derivative itself and
never on an original.
A derivative is a `NamedBlobImage` like any other, so it lands in the database as
a row of its own and would otherwise look like a fresh candidate to the backfill.
A marker says so by structure; matching on the generated filename instead would
silently skip an editorial upload that happened to be named the same way.

The outcome record exists because a silent skip looks exactly like an image that
correctly needs nothing, which would put the termination criterion of the backfill
out of reach and leave failures impossible to enumerate.
Reasons split into terminal ones, where re-running would change nothing, and
non-terminal ones, `retry` from a busy decode slot and `error` from a failed
decode, which an ordinary backfill run has to pick up again.
Recording `max_edge` is what turns tuning the cap into a setting change rather
than a migration, and recording `source_ids` closes an invalidation hole
described below.

### Why the field value and not an annotation

Invalidation becomes structural.
Replacing an image produces a *new* `NamedBlobImage`, which carries no derivative
attribute at all, so a fresh derivative is generated and a stale one cannot
survive.
An annotation keyed by field name would need explicit invalidation, and a missed
invalidation means serving the wrong image indefinitely.

Two lesser benefits follow: no field name plumbing, because the single URL funnel
receives the field value rather than its name, and uniform coverage of every image
field, whether it comes from the content type, a behaviour, or a custom schema.

The cost is an undeclared attribute on a `plone.namedfile` class.
`NamedBlobFile` is a plain `Persistent` subclass without `__slots__`, and this
package is its only writer.

One hole remains, and the outcome record closes it.
`NamedBlobImage.data` is a settable property, and migration scripts and bulk
import pipelines do assign to it, which replaces the bytes without replacing the
object.
The recorded `source_ids` are therefore compared against the original's current
blob ids when a URL is built: a mismatch means the original was mutated in place,
and the derivative is treated as absent rather than served.
Recorded ids of `None` mean that no comparison is possible, not that one failed:
a derivative generated before its original was ever committed has nothing to
record.

### The versioning caveat

Structural invalidation has one exception, and it needed its own module.

`Products.CMFEditions` snapshots content by deep-pickling it, and
`plone.app.versioningbehavior`'s `CloneNamedFileBlobs` protects blobs from that
pickle by collecting them.
It walks **top-level field values only**, returning `field_value._blob` per image
field.
A derivative lives one level deeper, so its blob is not in that mapping: it goes
through the pickle, and `ZODB.blob.Blob.__getstate__` returns `None`.

The result is not an error, which is what makes it dangerous.
The snapshot holds a `NamedBlobImage` that looks entirely valid and reads back
zero bytes.
After a revert it resolves to a real `(zoid, tid)` naming an empty blob, Thumbor
is handed nothing and answers 400, and the argument above does not save us,
because the attribute *is* present.

`SkipThumborSourceDerivatives`, an `ICloneModifier`, drops both attributes on
clone, so a reverted object arrives with a bare field value and regenerates on its
next modification.
Both attributes go, not only the derivative: a reverted object carrying a terminal
outcome record and no derivative would answer "nothing to do" forever.

Registering it is not a ZCML line.
`CMFEditions` clone modifiers live in the persistent `portal_modifier` tool, so
registration is a GenericSetup step, and it is an **upgrade** step rather than an
install-only handler, because an already-installed site is precisely the one this
feature exists to repair.
It degrades quietly twice over: `Products.CMFEditions` is not a dependency of this
package, and its `portal_modifier` tool exists only once the `CMFEditions` profile
has been applied.
Neither absence is a failure, because with no version repository there are no
snapshots to protect.

### The loader is not involved

A Thumbor URL names one blob by `(zoid, tid)`.
Pointing it at the derivative's blob rather than the original's is therefore
entirely a Plone-side decision: no mapping lookup per request, no loader release,
no new Thumbor image tag.
`zodb-pgjsonb-thumborblobloader` does not need to know that derivatives exist.

Selection happens in `_build_thumbor_url`, the package's single URL funnel, which
all four URL-emitting call sites go through.
Three things live there together, deliberately:

1. **Selection.** Prefer the derivative, unless the recorded blob ids say the
   original was mutated underneath it.
2. **Crop translation.** `plone.app.imagecropping` stores boxes in the original's
   pixels, so against a 4000 pixel derivative of an 11811 pixel original an
   untranslated box cuts into empty space. Each axis gets its own factor, taken
   from the derivative's actual size rather than from the cap, and rounding is
   direction-aware so the mapped region covers the original selection instead of
   cutting into the subject.
3. **Clamping.** Requested width and height are capped at the selected source's
   dimensions. Thumbor's `MAX_PIXELS` guards the source and not the output, so
   asking for an 11811 pixel rendition of a 4000 pixel derivative would *succeed*
   by scaling it up, allocating hundreds of megabytes and handing the browser a
   multi-megabyte candidate. A loud failure turning silent is worse than leaving
   it broken.

Splitting selection from translation would mean the next caller that picks a
source and forgets to rescale the crop is one refactor away, so the funnel keeps
them adjacent.

One omission in that funnel is a feature rather than an oversight.
When the derivative exists but has no committed transaction id yet, no Thumbor URL
is produced at all.
Substituting the original there would emit a permanent, signed, direct URL naming
an image that may exceed `MAX_PIXELS` and freeze it into catalog metadata, where a
browser fetches it without Plone in the path and uid healing can never repair it.
Returning nothing yields a uid fallback instead, which heals on the next render
and is corrected for good by the second phase of the backfill.

### The draft decode

Generation opens the source, checks its size against a ceiling, evaluates the
trigger, and only then asks the decoder for a reduced-resolution read through
`Image.draft()` with `mode=None`, leaving the colour space alone for the
deliberate conversion that follows.

The obvious call, `draft(None, (cap, cap))`, delivers less than it looks like it
should, by enough to matter.
`JpegImageFile.draft` computes a single shared divisor as
`min(w // target_w, h // target_h)` and then drops to the largest power of two not
exceeding it, so a square target reduces too little on every image that is not
square.
The package computes the divisor itself and passes explicit per-axis targets: the
largest power of two that keeps the result at or above the cap, never below it,
because the resize afterwards still needs pixels to work with.

| Original | Decoded at a 4000 cap | Peak buffer |
|---|---|---|
| 11811 x 8858 (104 megapixels) | 5906 x 4429 | ~79 MB |
| 7000 x 5000 (35 megapixels) | 7000 x 5000, no reduction available | ~105 MB |

Halving the second one would undershoot the cap, so it decodes in full.
For TIFF and PNG, `draft` is a no-op and a full decode is the price.

The trigger is evaluated against the *true* source size, before drafting, and the
order is load-bearing.
Drafting first lands exactly on the cap for any original whose longest edge is the
cap times two, four or eight, at which point "larger than the cap" stops being
true and precisely the oversized images this exists for would get no derivative.

The decode is bounded on both sides.
`MAX_SOURCE_PIXELS`, a module constant of 175 megapixels, is checked against the
header before any pixels are read.
It sits in a narrow band on purpose: above the 164.6 megapixel largest image found
in the field, which must still be fixed, and below Pillow's own hard limit of
178,956,970 pixels, twice `MAX_IMAGE_PIXELS`, above which `Image.open` raises
before our own check could look at the size.
`Image.MAX_IMAGE_PIXELS` itself is never assigned, because it is a process global
that every `Image.open` consults, including `plone.namedfile`'s own handling on
every upload, and raising it from a worker thread would disable bomb protection
for everyone for the duration.

Generation runs behind a process-wide semaphore that admits one decode at a time,
with a short timeout.
The mass upload path already serialises on its own lock, but the edit path does
not, and `IObjectModifiedEvent` can fan out across every worker thread at 79 to
105 MB of pixel buffer each.
A thread that cannot get in promptly records a `retry` outcome and gives up rather
than queueing, because the work is never urgent: without a derivative the original
is served exactly as it was before.

### Colour normalisation

Size is not the only trigger.
An image also gets a derivative when it is not a clean sRGB raster: `CMYK`, `LAB`,
the 16-bit integer modes, or a palette image carrying transparency.
Tying normalisation to size alone would let a 3 megapixel CMYK press image through
unconverted, and CMYK is the *normal* case for material pulled out of print
layouts.

Conversion goes through the embedded ICC profile when the image has one, using
`PIL.ImageCms`, and falls back to a plain `convert()` when it does not, when the
profile turns out to be unusable, or when the Pillow build has no `littlecms` at
all.
That last case is worth knowing about: stock Pillow wheels bundle `liblcms2` on
every platform, and so does the `plone/plone-backend` image, so the ICC path is
live where it matters.
Where it is not, CMYK falls back to `convert()` and its naive "255 minus ink"
formula, which shifts press colours noticeably.
A silent degradation is exactly what this design spends its effort avoiding, so
the package logs a warning once per process when `ImageCms` is unavailable.

Colour is normalised before geometry.
Resizing a palette image in palette space interpolates palette *indices* and
produces garbage, so a palette image becomes RGB or RGBA before the resize touches
it.
The resize itself never enlarges, which matters for a small CMYK image that
triggered on colour space alone.

Encoding is JPEG at quality 92 with no chroma reduction, or PNG where transparency
has to survive.
Full chroma resolution is not vanity here: the derivative is an intermediate that
Thumbor scales again, and chroma reduction applied twice produces visible colour
fringing on edges.
EXIF and IPTC are dropped, matching Thumbor's own `PRESERVE_EXIF_INFO = False`
default.
EXIF *orientation* is deliberately not applied: doing so would make images above
and below the cap render differently, since Thumbor's `RESPECT_ORIENTATION`
defaults to off, so correcting orientation means correcting both, which is its own
change with its own visual verification.

SVG is skipped, as everywhere else in the package, and animated GIFs are skipped
because a derivative would flatten them to the first frame.

### What softens, and what stays broken

A crop covering fraction *X* of the derivative's edge feeds `cap * X` source
pixels into a rendition of width *S*, so it stays lossless while `X >= S / cap`.
The binding *S* is not the largest registered scale: crops are stored per scale
name, so it is the largest scale that actually carries one.
Below the threshold the result softens rather than breaks.

Falling back to the original for very small crops was considered and rejected.
It would need a second setting mirroring Thumbor's `MAX_PIXELS`, and a branch that
must *not* fire for the very images this feature exists to fix.
It earns its place only once a deployment has measured its own crop distribution
and found the threshold binding, which is what the dry run reports.
{doc}`/how-to/choose-source-max-edge` walks through that measurement.

Two limitations are accepted rather than fixed:

- **A truncated source blob yields a truncated derivative**, grey where the scan
  data ran out, rather than no derivative.
  `plone.scale` sets `LOAD_TRUNCATED_IMAGES` process-wide at import, so those
  bytes already render grey-padded in every Plone process, and a package that
  replaces Plone's scaling should not judge the same bytes more harshly than the
  scaling it replaces.
  Turning the flag off for one decode would mutate a process global for every
  other thread, which is the objection raised against `MAX_IMAGE_PIXELS` above.
- **Images above roughly 179 megapixels get no derivative.**
  Pillow raises inside `Image.open`, before the package's own ceiling can be
  consulted, and raising the global that governs that is refused for the reason
  just given.
  Those images are recorded as failures and keep returning Thumbor's 400, so they
  stay enumerable rather than silently missing.
  Nothing in the field is close to the limit, but an upload above it stays broken.

Replacing an image also orphans its derivative.
The old field value, its derivative and both blobs become garbage the moment a new
`NamedBlobImage` takes their place, and nothing here collects them, so "one
derivative per qualifying image" is the steady-state figure rather than a storage
bound.

## Cache hierarchy

Images pass through multiple cache layers.
Each layer serves a different purpose:

```{mermaid}
flowchart TB
    B[Browser cache] -->|miss| C[CDN / reverse proxy cache]
    C -->|miss| T[Thumbor result storage]
    T -->|miss: need original blob| D[Blob disk cache]
    D -->|miss| PG[(PostgreSQL bytea)]
    PG -->|no data column| S3[(S3 object store)]
```

| Layer | Scope | Invalidation | Purpose |
|---|---|---|---|
| Browser cache | Per-user | URL changes on new TID | Avoid network requests entirely |
| CDN / reverse proxy | Shared | URL changes on new TID | Reduce Thumbor load for popular images |
| Thumbor result storage | Per-Thumbor instance | URL changes on new TID | Avoid re-processing (crop, resize, encode) |
| Blob disk cache | Per-Thumbor instance | LRU eviction by size | Avoid repeated PG queries for the same original |
| PostgreSQL bytea | Authoritative | Never (immutable by TID) | Primary blob storage |
| S3 | Overflow | Never (immutable by TID) | Large blob offload for PG space management |

The critical insight is that ZODB TIDs are immutable: when a blob changes, its TID
changes, which changes the URL, which is a completely different cache key at every
layer.
No cache invalidation problem exists -- only cache eviction for space
management.

The blob disk cache (`BlobCache`) uses deterministic filenames
(`{zoid:016x}-{tid:016x}.blob`) and LRU eviction by access time. When total cache
size exceeds `PGTHUMBOR_CACHE_MAX_SIZE`, the oldest-accessed files are removed
until total size drops to 90% of the maximum.

## Deployment topology

The example `docker-compose.yml` illustrates the reference deployment:

```{mermaid}
flowchart LR
    subgraph External
        B[Browser]
    end
    subgraph Docker Compose
        N[nginx :80]
        P[Plone :8080]
        T[Thumbor :8888]
        PG[(PostgreSQL :5432)]
    end

    B -->|:8080| N
    N -->|/| P
    N -->|/thumbor/| T
    T -->|internal auth| P
    P --> PG
    T --> PG
```

nginx serves as the reverse proxy, routing `/thumbor/` to the Thumbor container
(stripping the prefix) and everything else to Plone (with VirtualHostMonster
rewriting).
The Thumbor container talks to Plone directly via the Docker network
for auth subrequests (`PGTHUMBOR_PLONE_AUTH_URL = http://plone:8080/Plone`) --
this bypasses nginx and avoids routing loops.

Both Plone and Thumbor share the same PostgreSQL instance and the same HMAC
security key.
The security key must be identical on both sides: Plone uses it to
sign URLs, Thumbor uses it to verify signatures.
