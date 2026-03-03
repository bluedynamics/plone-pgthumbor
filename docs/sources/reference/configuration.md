<!-- diataxis: reference -->

# Configuration Reference

This page documents all configuration options for the Thumbor image scaling
integration. Settings are split between the Plone side (`plone.pgthumbor`)
and the Thumbor side (`zodb-pgjsonb-thumborblobloader`).

## Plone-Side Settings (plone.pgthumbor)

### Environment Variables

Environment variables take precedence over Plone registry settings.
When an environment variable is set, the corresponding registry value is
ignored.

| Variable | Type | Default | Description |
|---|---|---|---|
| `PGTHUMBOR_SERVER_URL` | string | (none) | Public URL of the Thumbor server (e.g., `http://thumbor:8888`). Required for Thumbor URL generation. Trailing slashes are stripped automatically. |
| `PGTHUMBOR_SECURITY_KEY` | string | `""` | Shared HMAC-SHA1 key for signing Thumbor URLs. Must match the `SECURITY_KEY` in `thumbor.conf`. Required unless `PGTHUMBOR_UNSAFE` is enabled. |
| `PGTHUMBOR_UNSAFE` | boolean | `false` | Generate unsigned `/unsafe/` URLs. Accepts `true`, `1`, or `yes` (case-insensitive). For development only. |

If neither `PGTHUMBOR_SECURITY_KEY` nor `PGTHUMBOR_UNSAFE` is set,
Thumbor URL generation is disabled and Plone falls back to standard
ZODB-based image scaling.

### Plone Registry (IThumborSettings)

These settings are editable through the Plone control panel
(`@@thumbor-settings`) and stored in `plone.app.registry`. Environment
variables override these values when set.

| Field | Type | Default | Description |
|---|---|---|---|
| `server_url` | TextLine | `""` | Public URL of the Thumbor server. |
| `security_key` | TextLine | `""` | Shared HMAC-SHA1 key for signing Thumbor URLs. |
| `unsafe` | Bool | `False` | Generate unsigned URLs (development only). |
| `smart_cropping` | Bool | `False` | Enable Thumbor smart cropping (OpenCV face/feature detection). Applied to `scale` and `cover` modes. |
| `paranoid_mode` | Bool | `False` | Always verify image access with Plone for every request, even for publicly accessible content. When disabled, only non-public images use the authenticated 3-segment URL format. |

## Thumbor-Side Settings (zodb-pgjsonb-thumborblobloader)

All Thumbor-side settings are configured in `thumbor.conf`.

### Loader and Handler Registration

| Key | Value | Description |
|---|---|---|
| `LOADER` | `'zodb_pgjsonb_thumborblobloader.loader'` | Registers the blob loader that reads images from the `blob_state` PostgreSQL table. |
| `HANDLER_LISTS` | `['zodb_pgjsonb_thumborblobloader.auth_handler']` | Registers the auth handler that enforces Plone access control for 3-segment URLs. |

### Security

| Key | Type | Default | Description |
|---|---|---|---|
| `SECURITY_KEY` | string | (none) | Thumbor's HMAC-SHA1 signing key. Must match `PGTHUMBOR_SECURITY_KEY` on the Plone side. |
| `ALLOW_UNSAFE_URL` | boolean | `False` | Accept unsigned `/unsafe/` URLs. Must match `PGTHUMBOR_UNSAFE` on the Plone side. |

### PostgreSQL Connection

| Key | Type | Default | Description |
|---|---|---|---|
| `PGTHUMBOR_DSN` | string | (required) | PostgreSQL connection string (e.g., `dbname=zodb host=localhost port=5432 user=zodb password=zodb`). |
| `PGTHUMBOR_POOL_MIN_SIZE` | integer | `1` | Minimum number of connections in the async connection pool. |
| `PGTHUMBOR_POOL_MAX_SIZE` | integer | `4` | Maximum number of connections in the async connection pool. |

The loader uses `psycopg` with `AsyncConnectionPool`. On first use, it
verifies that the `blob_state` table exists (created by `zodb-pgjsonb`).

### Disk Cache

| Key | Type | Default | Description |
|---|---|---|---|
| `PGTHUMBOR_CACHE_DIR` | string | `""` | Directory for the local blob cache. Empty string disables caching. |
| `PGTHUMBOR_CACHE_MAX_SIZE` | integer | `0` | Maximum cache size in bytes. `0` disables caching. When the cache exceeds this size, the oldest files (by access time) are evicted until the cache reaches 90% of the limit. |

Cache filenames are deterministic: `{zoid:016x}-{tid:016x}.blob`. Since
blobs are addressed by immutable `(zoid, tid)` pairs, there is no cache
invalidation concern -- only LRU eviction for space management.

### S3 Storage (Optional)

When blob data is stored in S3 (instead of or in addition to PG `bytea`),
the loader falls back to S3 if the `data` column is `NULL` and `s3_key`
is present.

| Key | Type | Default | Description |
|---|---|---|---|
| `PGTHUMBOR_S3_BUCKET` | string | `""` | S3 bucket name. Required when blobs use S3 tiering. |
| `PGTHUMBOR_S3_REGION` | string | `us-east-1` | AWS region for the S3 bucket. |
| `PGTHUMBOR_S3_ENDPOINT` | string | `""` | Custom S3 endpoint URL (for MinIO or compatible services). Empty uses the default AWS endpoint. |

S3 downloads use `boto3` synchronously, wrapped in `asyncio.to_thread`
for compatibility with Thumbor's async event loop.

### Plone Auth Handler

| Key | Type | Default | Description |
|---|---|---|---|
| `PGTHUMBOR_PLONE_AUTH_URL` | string | `""` | Internal URL of the Plone site (e.g., `http://plone-internal:8080/Plone`). Used by the auth handler to call `@thumbor-auth`. Required for 3-segment authenticated URLs. |
| `PGTHUMBOR_AUTH_CACHE_TTL` | integer | `60` | Auth result cache lifetime in seconds. Cached per `(content_zoid, cookie)` pair to avoid a Plone round-trip on every image request. |

### Result Storage (Optional)

Thumbor's built-in result storage caches the final processed images.
This is separate from the blob disk cache (which caches raw originals).

| Key | Type | Default | Description |
|---|---|---|---|
| `RESULT_STORAGE` | string | (none) | Result storage backend, e.g., `'thumbor.result_storages.file_storage'`. |
| `RESULT_STORAGE_FILE_STORAGE_ROOT_PATH` | string | (none) | Directory for file-based result storage. |
