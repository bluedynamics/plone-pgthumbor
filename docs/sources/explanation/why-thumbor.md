<!-- diataxis: explanation -->

# Why Thumbor

Plone ships with a capable image scaling pipeline built on Pillow. It works. So why
replace it with an external image server? This page explains the problems with
Plone's built-in approach, why a CDN alone does not solve them, and what Thumbor
brings to the table.

## The problem: write-on-read scaling

Plone's default image scaling model is *write-on-read*. When a browser requests a
scaled version of an image for the first time, this is what happens:

1. **Load the full blob.** The original image (potentially megabytes of JPEG or PNG)
   is loaded from ZODB blob storage into Python memory.
2. **Resize with Pillow.** `plone.scale` invokes Pillow to decode the image, resize
   it to the target dimensions, and re-encode it.
3. **Store the result.** The scaled image is written back to ZODB as an annotation
   on the content object -- a new persistent object with its own OID.
4. **Serve the response.** The scaled bytes are streamed to the browser through the
   WSGI stack.

Each unique scale (thumbnail, preview, large, mini, etc.) goes through this process
independently. A single content object with 6 defined scales produces 6 additional
persistent objects in ZODB, each containing the full scaled image bytes.

### Why this is expensive

- **Memory pressure.** Loading a 5 MB JPEG into Pillow allocates a decoded pixel
  buffer (width x height x channels) that can easily reach 50-100 MB for a
  high-resolution photo. This happens inside the Plone WSGI worker process, which
  typically has a limited number of threads.

- **Database churn.** Each scale write creates a new ZODB transaction. With
  RelStorage or zodb-pgjsonb, that means new rows in PostgreSQL for objects that
  exist solely as cached derivatives. On a site with thousands of images and
  multiple scales per image, the annotation objects can outnumber the actual content
  objects.

- **Blob storage bloat.** In classic ZODB blob storage, each annotation is a file
  on disk. In zodb-pgjsonb, each scale is a row in `blob_state`. The original 5 MB
  image becomes 5 MB + N scales, all stored persistently in the database.

- **Thread starvation.** While Pillow is processing an image, the Plone worker
  thread is blocked. A burst of first-time scale requests (e.g., after a cache
  flush or new deployment) can saturate all worker threads, making the site
  unresponsive for regular page requests.

- **Serialization bottleneck.** ZODB serializes writes. While one thread is writing
  a scale annotation, other threads that want to modify the same content object
  must wait. This can cause `ConflictError` retries on busy sites.

### The cumulative effect

On a Plone site with 10,000 images and 6 scales each, the write-on-read model
creates 60,000 additional persistent objects in ZODB. When the site is upgraded
and scale definitions change (new dimensions, new format), all 60,000 cached
scales are stale and must be regenerated -- again through write-on-read, again
through Pillow, again with the associated memory and database overhead.

## Why not just a CDN?

A CDN (CloudFront, Fastly, Cloudflare) caches responses at edge locations,
reducing the load on the origin server. This helps with the *serving* problem but
not the *generation* problem:

- **First request still hits Plone.** The CDN caches the response, but someone has
  to generate it first. That first request still loads the blob, resizes with
  Pillow, writes the annotation, and streams the result. The CDN just ensures it
  only happens once per edge location.

- **Cache invalidation.** When an image changes, the CDN cache must be invalidated.
  Plone does not natively integrate with CDN purge APIs. Without explicit
  invalidation, stale images are served until the CDN TTL expires.

- **Access control.** A CDN serves content publicly by default. For sites with
  private content (intranet, member areas, workflow-controlled publications), a
  CDN cannot enforce Plone's per-object security model. CDN-level access control
  (signed URLs, token auth) requires custom integration that duplicates security
  logic outside Plone.

- **No transformation offload.** The CDN caches the *result* but does not perform
  the *transformation*. Plone still needs Pillow and still pays the memory and CPU
  cost for every new scale.

A CDN is complementary to Thumbor -- not a replacement. In production, a CDN in
front of Thumbor is an excellent combination: Thumbor handles transformation and
origin caching, the CDN handles edge caching and bandwidth offload.

## Why Thumbor

[Thumbor](https://www.thumbor.org/) is an open-source image processing server
written in Python (with C extensions for performance-critical operations). It is
purpose-built for exactly the problem Plone's scaling pipeline was never designed
for: on-demand image transformation at scale.

### Relevant capabilities

**Smart cropping.** Thumbor uses OpenCV for face and feature detection. When smart
cropping is enabled, it identifies the most important region of the image (faces,
high-contrast areas) and crops around it. Plone's Pillow pipeline only does
center-crop or simple ratio-based scaling.

**Fit-in mode.** Thumbor's `fit_in` resizes the image to fit within the target
dimensions without cropping -- equivalent to CSS `object-fit: contain`. This maps
directly to Plone's "scale" and "contain" scale modes.

**Format conversion.** Thumbor can convert between image formats (JPEG, PNG, WebP,
AVIF) on the fly based on the client's `Accept` header. This enables serving modern
formats to capable browsers without storing multiple format versions.

**Filter pipeline.** Brightness, contrast, blur, watermark, quality adjustment --
Thumbor supports a composable filter pipeline via URL parameters. While
plone.pgthumbor does not currently expose filters through the Plone UI, the URL
generation supports them for programmatic use.

**Built-in caching.** Thumbor caches processed results in its result storage. The
second request for a given URL is served from cache without re-processing. The
caching is keyed by the full URL (including all transformation parameters), and
since plone.pgthumbor uses immutable ZOID+TID URLs, there is no cache invalidation
problem.

**Async architecture.** Thumbor runs on Tornado (async I/O). Image loading,
processing, and serving are non-blocking. A single Thumbor instance can handle
many concurrent requests without the thread starvation that plagues synchronous
WSGI stacks.

**HMAC signing.** Thumbor has built-in URL signing with HMAC-SHA1. This prevents
clients from constructing arbitrary transformation URLs -- only URLs signed by the
server (Plone, in our case) are accepted. This is essential for preventing
transformation abuse (a malicious client requesting 10000x10000 resizes).

**Custom loaders.** Thumbor's loader plugin architecture allows custom image
sources. `zodb-pgjsonb-thumborblobloader` implements a loader that reads blob
bytes directly from PostgreSQL's `blob_state` table (or S3 as a fallback), with
an LRU disk cache in between.

**Handler lists.** Thumbor's handler architecture allows inserting custom request
handlers before the image processing pipeline. `AuthImagingHandler` uses this to
intercept 3-segment URLs and verify Plone access control before loading any image
data.

### What plone.pgthumbor does NOT use

Thumbor has many features that plone.pgthumbor does not currently expose:

- **Remote image loading** (HTTP/HTTPS loaders) -- all images come from PostgreSQL.
- **Image upload** -- images are stored through Plone's standard content workflow.
- **Face detection storage** -- smart cropping uses Thumbor's built-in per-request
  detection without storing detection results.
- **Custom engines** (Pillow/ImageMagick/GraphicsMagick) -- the default Pillow
  engine is used within Thumbor itself.

## What changes for Plone

With plone.pgthumbor installed, the image scaling pipeline changes fundamentally:

| Aspect | Standard Plone | With plone.pgthumbor |
|---|---|---|
| Scale generation | Pillow in Plone worker | Thumbor server |
| Scale storage | ZODB annotations | Thumbor result cache |
| Image serving | WSGI response streaming | 302 redirect to Thumbor |
| Memory usage | Full image in Python heap | Only URL string in Python heap |
| Thread usage | Blocked during resize | Returns immediately (redirect) |
| ZODB writes per scale | 1 annotation object | 0 |
| Cache invalidation | Annotation deletion | Automatic (TID changes URL) |
| Smart cropping | Not available | OpenCV face/feature detection |
| Format negotiation | Not available | Accept-header based |

The `ThumborScaleStorage` ensures that Plone's `@@images` traversal still works
-- it stores dimension metadata (width, height, uid) for `<img>` tag generation
and catalog indexing, but never invokes Pillow or creates annotation objects.
The `ThumborImageScale` view generates a signed Thumbor URL and returns a 302
redirect instead of streaming image bytes.

From the content editor's perspective, nothing changes. Images are uploaded through
the standard Plone UI, scales are defined in the registry as usual, and the
`@@images` API returns URLs that work in `<img>` tags. The difference is invisible:
faster responses, less memory, less database bloat.
