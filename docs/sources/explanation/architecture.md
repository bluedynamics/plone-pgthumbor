<!-- diataxis: explanation -->

# Architecture

plone.pgthumbor replaces Plone's built-in image scaling pipeline with Thumbor, an
open-source image processing server.
Instead of loading blob data into Python,
resizing with Pillow, and storing the result back in ZODB, Plone generates a signed
Thumbor URL and sends the browser a 302 redirect.
Thumbor fetches the original blob
directly from PostgreSQL (via zodb-pgjsonb's `blob_state` table), scales it, and
serves the result -- all without Plone touching a single pixel.

This page explains how data flows through the system, how the components fit
together, and the reasoning behind the key design choices.

## Key files

### plone.pgthumbor (Plone side)

| File | Purpose |
|---|---|
| `scaling.py` | `ThumborImageScale` + `ThumborImageScaling` -- `@@images` view override, 302 redirect, crop lookup |
| `storage.py` | `ThumborScaleStorage` -- `IImageScaleStorage` adapter, no Pillow invocation |
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
    P->>PG: SELECT (idx->'allowedRolesAndUsers' ?| user_principals) FROM object_state
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
   PostgreSQL directly: does the object's `allowedRolesAndUsers` JSONB array
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

This means Pillow is never imported, never invoked, and no annotation objects
are created in ZODB.
The only data stored is dimension metadata (uid, width,
height) -- enough for Plone to generate `<img>` tags with correct `width` and
`height` attributes, and enough for the catalog to index scale information.

### ZCML overrides

plone.pgthumbor replaces two Plone components via `overrides.zcml`:

1. **`@@images` browser page** -- `ThumborImageScaling` replaces
   `plone.namedfile`'s `ImageScaling` for all `IImageScaleTraversable` objects.
   This intercepts every image scale request site-wide.

2. **`IImageScaleStorage` adapter** -- `ThumborScaleStorage` replaces
   `AnnotationStorage` for all `IImageScaleTraversable` objects.
   This prevents
   Pillow from being invoked during scale generation.

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
