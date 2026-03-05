<!-- diataxis: how-to -->

# Configure Thumbor for plone.pgthumbor

This guide covers every `thumbor.conf` setting relevant to the
plone.pgthumbor stack.
All settings are standard Thumbor configuration keys
that can also be set via environment variables.

## Minimal configuration

```python
LOADER = "zodb_pgjsonb_thumborblobloader.loader"

HANDLER_LISTS = [
    "thumbor.handler_lists.healthcheck",
    "zodb_pgjsonb_thumborblobloader.auth_handler",
]

SECURITY_KEY = "your-secret-key"
PGTHUMBOR_DSN = "dbname=zodb host=postgres port=5432 user=zodb password=zodb"
```

## Loader and handlers

### `LOADER`

The image loader module.
Must be set to the zodb-pgjsonb blob loader:

```python
LOADER = "zodb_pgjsonb_thumborblobloader.loader"
```

This loader reads image blobs directly from the `blob_state` table in
PostgreSQL using an async connection pool (psycopg 3).

### `HANDLER_LISTS`

Custom Thumbor handler lists.
The `auth_handler` module adds access control
for non-public Plone content:

```python
HANDLER_LISTS = [
    "thumbor.handler_lists.healthcheck",
    "zodb_pgjsonb_thumborblobloader.auth_handler",
]

```

The healthcheck handler must come first so `/healthcheck` is matched before
the image URL regex.

The `auth_handler` intercepts requests with 3-segment blob paths
(`<blob_zoid>/<tid>/<content_zoid>`) and verifies access with Plone via the
`@thumbor-auth` REST endpoint before delivering the image.
Two-segment paths
(`<blob_zoid>/<tid>`) are served without any access check.

## Security

### `SECURITY_KEY`

The shared HMAC-SHA1 key for signing Thumbor URLs.
Plone uses this key to
generate signed URLs; Thumbor uses it to verify them:

```python
SECURITY_KEY = "your-secret-key"
```

Can also be set via the `THUMBOR_SECURITY_KEY` environment variable:

```python
import os
SECURITY_KEY = os.environ.get("THUMBOR_SECURITY_KEY", "")
```

:::{warning}
Use a strong, random key in production (at least 32 characters).
The key
must be identical in Thumbor's `SECURITY_KEY` and Plone's
`PGTHUMBOR_SECURITY_KEY` environment variable.
:::

### `ALLOW_UNSAFE_URL`

Allow unsigned `/unsafe/` URLs.  **Must be `False` in production.**

```python
ALLOW_UNSAFE_URL = False
```

When `True`, Thumbor accepts URLs prefixed with `/unsafe/` without HMAC
verification.
Useful for development only.

## Result storage

### `RESULT_STORAGE`

Thumbor's built-in result cache.
Stores already-transformed images so
repeated requests skip re-processing:

```python
RESULT_STORAGE = "thumbor.result_storages.file_storage"
RESULT_STORAGE_FILE_STORAGE_ROOT_PATH = "/tmp/thumbor/result_storage"
```

The file storage is the simplest option.
For production deployments consider
`thumbor.result_storages.no_storage` if you rely solely on an upstream CDN
cache, or a Redis-based result storage for clustered Thumbor setups.

## PostgreSQL connection

### `PGTHUMBOR_DSN`

PostgreSQL connection string for the `blob_state` table.
Uses libpq
connection string format:

```python
PGTHUMBOR_DSN = "dbname=zodb host=postgres port=5432 user=zodb password=zodb"
```

Can be set via environment variable:

```python
import os
PGTHUMBOR_DSN = os.environ.get("PGTHUMBOR_DSN", "")
```

The loader verifies that the `blob_state` table exists on first connection.

### `PGTHUMBOR_POOL_MIN_SIZE`

Minimum number of connections in the async connection pool.
Default: `1`.

```python
PGTHUMBOR_POOL_MIN_SIZE = 1
```

### `PGTHUMBOR_POOL_MAX_SIZE`

Maximum number of connections in the async connection pool.
Default: `4`.

```python
PGTHUMBOR_POOL_MAX_SIZE = 4
```

Increase this if Thumbor handles many concurrent image requests.
Each
connection holds a PostgreSQL backend slot.

## Plone access control

### `PGTHUMBOR_PLONE_AUTH_URL`

Internal URL of the Plone site, used by the `auth_handler` to verify access
for non-public images.
This should be a direct URL to Plone, bypassing any
reverse proxy to avoid loops and reduce latency:

```python
PGTHUMBOR_PLONE_AUTH_URL = "http://plone:8080/Plone"
```

Can be set via environment variable:

```python
import os
PGTHUMBOR_PLONE_AUTH_URL = os.environ.get("PGTHUMBOR_PLONE_AUTH_URL", "")
```

The auth handler calls `<url>/@thumbor-auth?zoid=<content_zoid_hex>` with the
browser's Cookie and Authorization headers forwarded.
Plone returns 200 if
the user may view the content, or 403/401 otherwise.

If this setting is empty and a 3-segment (authenticated) URL is requested, the
handler denies the request.

### `PGTHUMBOR_AUTH_CACHE_TTL`

How long (in seconds) to cache auth results.
Default: `60`.

```python
PGTHUMBOR_AUTH_CACHE_TTL = 60
```

Auth results are cached per `(content_zoid, cookie_header)` tuple.
A shorter
TTL means more frequent Plone round-trips but faster permission revocation.

## Disk cache (loader-side)

The loader has its own disk cache, separate from Thumbor's result storage.
This caches raw blob bytes to avoid repeated PostgreSQL or S3 fetches.

### `PGTHUMBOR_CACHE_DIR`

Directory for the local disk cache.
Empty string (default) disables caching:

```python
PGTHUMBOR_CACHE_DIR = "/tmp/thumbor/blob_cache"
```

### `PGTHUMBOR_CACHE_MAX_SIZE`

Maximum cache size in bytes.
Default: `0` (disabled).
LRU eviction removes
the least-recently-accessed files when the cache exceeds this limit:

```python
# 1 GB cache
PGTHUMBOR_CACHE_MAX_SIZE = 1073741824
```

The cache uses deterministic filenames (`{zoid:016x}-{tid:016x}.blob`).
Since blobs are addressed by immutable `(zoid, tid)` pairs, there is no
cache invalidation concern -- only LRU eviction for space.

## S3 fallback

For tiered blob storage where large blobs are offloaded to S3.
See
{doc}`enable-s3-fallback` for a detailed setup guide.

### `PGTHUMBOR_S3_BUCKET`

S3 bucket name.
Empty string (default) disables S3 fallback:

```python
PGTHUMBOR_S3_BUCKET = "my-blobs"
```

### `PGTHUMBOR_S3_REGION`

AWS region.
Default: `us-east-1`:

```python
PGTHUMBOR_S3_REGION = "eu-central-1"
```

### `PGTHUMBOR_S3_ENDPOINT`

Custom S3 endpoint URL.
Empty string (default) uses AWS.
Set this for
S3-compatible services like MinIO:

```python
PGTHUMBOR_S3_ENDPOINT = "http://minio:9000"
```

## Full example

```python
import os

# Loader
LOADER = "zodb_pgjsonb_thumborblobloader.loader"

# Handlers
HANDLER_LISTS = [
    "thumbor.handler_lists.healthcheck",
    "zodb_pgjsonb_thumborblobloader.auth_handler",
]

# Security
SECURITY_KEY = os.environ.get("THUMBOR_SECURITY_KEY", "change-me")
ALLOW_UNSAFE_URL = False

# Result storage
RESULT_STORAGE = "thumbor.result_storages.file_storage"
RESULT_STORAGE_FILE_STORAGE_ROOT_PATH = "/tmp/thumbor/result_storage"

# PostgreSQL
PGTHUMBOR_DSN = os.environ.get("PGTHUMBOR_DSN", "")
PGTHUMBOR_POOL_MIN_SIZE = 2
PGTHUMBOR_POOL_MAX_SIZE = 8

# Plone access control
PGTHUMBOR_PLONE_AUTH_URL = os.environ.get("PGTHUMBOR_PLONE_AUTH_URL", "")
PGTHUMBOR_AUTH_CACHE_TTL = int(os.environ.get("PGTHUMBOR_AUTH_CACHE_TTL", "60"))

# Disk cache
PGTHUMBOR_CACHE_DIR = "/var/cache/thumbor/blobs"
PGTHUMBOR_CACHE_MAX_SIZE = 1073741824  # 1 GB

# S3 fallback (optional)
PGTHUMBOR_S3_BUCKET = os.environ.get("PGTHUMBOR_S3_BUCKET", "")
PGTHUMBOR_S3_REGION = os.environ.get("PGTHUMBOR_S3_REGION", "us-east-1")
PGTHUMBOR_S3_ENDPOINT = os.environ.get("PGTHUMBOR_S3_ENDPOINT", "")
```
