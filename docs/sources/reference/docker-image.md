<!-- diataxis: reference -->

# Docker image

A pre-built OCI image for the Thumbor service with
`zodb-pgjsonb-thumborblobloader` is published to the GitHub Container
Registry.

## Image location

```
ghcr.io/bluedynamics/zodb-pgjsonb-thumborblobloader
```

## Platforms

The image is built for two architectures:

- `linux/amd64`
- `linux/arm64`

## Base image

`python:3.13-trixie` (Debian Trixie)

## Tags

| Tag pattern | Example | Description |
|---|---|---|
| `thumbor-<THUMBOR>_loader-<LOADER>` | `thumbor-7.7.7_loader-0.3.0` | Pinned to specific Thumbor and loader versions. |
| `latest` | | Always points to the most recent build. |

On tagged releases the loader version is clean (for example `0.3.0`).
On development builds from `main` it includes the git describe suffix
(for example `0.3.0-2-gabcdef0`).

## Automatic rebuilds

| Trigger | When |
|---------|------|
| Push to `main` | Every merge or push to main |
| GitHub Release published | When a `v*` tag is created |
| Manual dispatch | Via GitHub Actions UI or `gh workflow run docker.yaml` |
| Weekly Thumbor check | Every Monday at 06:00 UTC -- rebuilds if a new Thumbor version is on PyPI |

## Environment variables

The image is configured entirely via environment variables.
No `thumbor.conf` editing is required.

### Security

| Variable | Default | Description |
|----------|---------|-------------|
| `THUMBOR_SECURITY_KEY` | `"CHANGE-ME"` | HMAC-SHA1 signing key. Must match `PGTHUMBOR_SECURITY_KEY` on the Plone side. |
| `ALLOW_UNSAFE_URL` | `"False"` | Accept unsigned `/unsafe/` URLs. Must be `"False"` in production. |

### PostgreSQL connection

| Variable | Default | Description |
|----------|---------|-------------|
| `PGTHUMBOR_DSN` | `""` | PostgreSQL connection string (required). For example `dbname=zodb host=postgres user=zodb password=zodb`. |
| `PGTHUMBOR_POOL_MIN_SIZE` | `1` | Minimum connections in the async pool. |
| `PGTHUMBOR_POOL_MAX_SIZE` | `4` | Maximum connections in the async pool. |

### Disk cache

| Variable | Default | Description |
|----------|---------|-------------|
| `PGTHUMBOR_CACHE_DIR` | `""` | Directory for the local blob cache. Empty disables caching. |
| `PGTHUMBOR_CACHE_MAX_SIZE` | `0` | Maximum cache size in bytes. `0` disables caching. |

### S3 fallback

| Variable | Default | Description |
|----------|---------|-------------|
| `PGTHUMBOR_S3_BUCKET` | `""` | S3 bucket name. Empty disables S3 fallback. |
| `PGTHUMBOR_S3_REGION` | `us-east-1` | AWS region. |
| `PGTHUMBOR_S3_ENDPOINT` | `""` | Custom S3 endpoint for MinIO/Ceph. Empty uses AWS. |

### Plone access control

| Variable | Default | Description |
|----------|---------|-------------|
| `PGTHUMBOR_PLONE_AUTH_URL` | `""` | Internal Plone URL for auth verification. Empty disables the auth handler entirely. |
| `PGTHUMBOR_AUTH_CACHE_TTL` | `60` | Auth cache lifetime in seconds. |

### Automatic image format conversion

| Variable | Default | Description |
|----------|---------|-------------|
| `THUMBOR_AUTO_WEBP` | `"true"` | Automatically convert to WebP when the browser supports it. |
| `THUMBOR_AUTO_AVIF` | `"false"` | Automatically convert to AVIF when the browser supports it (more CPU-intensive). |

### Cache-Control headers

These settings only take effect when the auth handler is loaded
(`PGTHUMBOR_PLONE_AUTH_URL` is set).

| Variable | Default | Description |
|----------|---------|-------------|
| `PGTHUMBOR_CACHE_CONTROL_AUTHENTICATED` | `private, max-age=86400` | Cache-Control header for authenticated (3-segment) image URLs. `private` prevents proxy caching while allowing the browser to cache. |
| `PGTHUMBOR_CACHE_CONTROL_PUBLIC` | `""` (empty) | Cache-Control header for public (2-segment) image URLs. Empty uses Thumbor's default (`max-age=N,public`). |

### Result storage

| Variable | Default | Description |
|----------|---------|-------------|
| `RESULT_STORAGE_PATH` | `/tmp/thumbor/result_storage` | Directory for Thumbor's result cache (processed images). |

## Health check

The image exposes a health check endpoint at `/healthcheck` on port 8888.

```bash
curl http://localhost:8888/healthcheck
# Returns: WORKING
```

## Exposed port

The image listens on port **8888**.
