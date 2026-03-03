<!-- diataxis: how-to -->

# Production Deployment Checklist

This guide covers the key considerations for deploying plone.pgthumbor and
Thumbor in a production environment.

## HMAC Key Management

Generate a strong random key (at least 32 characters):

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

This key must be shared between Plone and Thumbor:

| Service | Setting                                         |
|---------|-------------------------------------------------|
| Plone   | `PGTHUMBOR_SECURITY_KEY` environment variable   |
| Thumbor | `SECURITY_KEY` in `thumbor.conf` (or `THUMBOR_SECURITY_KEY` env var) |

Store the key in a secrets manager (Vault, AWS Secrets Manager, Docker
secrets).  Never commit it to version control.

## Disable Unsafe Mode

In `thumbor.conf`:

```python
ALLOW_UNSAFE_URL = False
```

On the Plone side, do **not** set `PGTHUMBOR_UNSAFE=true`.

## Reverse Proxy Configuration

Thumbor should not be directly exposed to the internet.  Place it behind a
reverse proxy (nginx, Traefik, Caddy).

### nginx Example

```nginx
server {
    listen 443 ssl http2;
    server_name example.com;

    # Thumbor image serving
    location /thumbor/ {
        rewrite ^/thumbor/(.*)$ /$1 break;
        proxy_pass http://thumbor:8888;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Cache scaled images at the proxy level
        proxy_cache_valid 200 30d;
        proxy_cache_valid 404 1m;
    }

    # Plone backend with VirtualHostMonster
    location / {
        rewrite ^(.*)$ /VirtualHostBase/https/$host:443/Plone/VirtualHostRoot$1 break;
        proxy_pass http://plone:8080;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Host  $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Key points:

- The `/thumbor/` prefix is stripped before forwarding to Thumbor.
- Set `PGTHUMBOR_SERVER_URL` on the Plone side to the public-facing URL, for
  example `https://example.com/thumbor`.
- Plone generates Thumbor URLs using this base URL, so it must be reachable
  from the end user's browser.

### Traefik Example (Docker Labels)

```yaml
thumbor:
  labels:
    - "traefik.enable=true"
    - "traefik.http.routers.thumbor.rule=Host(`example.com`) && PathPrefix(`/thumbor/`)"
    - "traefik.http.routers.thumbor.middlewares=thumbor-strip"
    - "traefik.http.middlewares.thumbor-strip.stripprefix.prefixes=/thumbor"
    - "traefik.http.services.thumbor.loadbalancer.server.port=8888"
```

## Internal Network for Auth Requests

The `auth_handler` in Thumbor calls Plone's `@thumbor-auth` endpoint to verify
access for non-public images.  This must be an internal, direct URL that
bypasses the reverse proxy:

```python
# thumbor.conf
PGTHUMBOR_PLONE_AUTH_URL = "http://plone:8080/Plone"
```

Reasons:

- Avoids routing loops (nginx forwards `/thumbor/` to Thumbor, which calls
  back to Plone through nginx).
- Reduces latency -- no TLS termination or proxy overhead.
- Allows network-level isolation (Thumbor and Plone on the same Docker network
  or internal subnet).

In Docker Compose, both services share a network by default.  In Kubernetes,
use the service DNS name (for example `http://plone-service:8080/Plone`).

## HTTPS

All traffic between the browser and the reverse proxy must use HTTPS.

- Configure TLS on the reverse proxy (Let's Encrypt, cert-manager, or your
  own certificates).
- Set `PGTHUMBOR_SERVER_URL` to `https://...` so Plone generates HTTPS image
  URLs.
- Thumbor itself does not need TLS -- it runs behind the reverse proxy on an
  internal network.

## Thumbor Result Storage

Thumbor caches already-scaled images in its result storage.  For production:

**File storage** (single-node deployments):

```python
RESULT_STORAGE = "thumbor.result_storages.file_storage"
RESULT_STORAGE_FILE_STORAGE_ROOT_PATH = "/var/cache/thumbor/results"
```

Mount this path as a persistent volume so the cache survives container
restarts.

**No storage** (CDN-fronted deployments):

```python
RESULT_STORAGE = "thumbor.result_storages.no_storage"
```

If a CDN (CloudFront, Fastly, Cloudflare) caches responses, Thumbor's own
result cache is unnecessary.

## Blob Disk Cache Sizing

The loader-side disk cache (`PGTHUMBOR_CACHE_DIR` / `PGTHUMBOR_CACHE_MAX_SIZE`)
caches raw blob bytes before Thumbor processes them.  This is especially
useful when the same source image is requested at multiple sizes.

Sizing guidelines:

- Estimate the total size of your most frequently accessed images.
- A 1--5 GB cache is a good starting point for most sites.
- The cache uses LRU eviction based on access time.  It evicts down to 90% of
  `PGTHUMBOR_CACHE_MAX_SIZE` when the limit is hit.

```python
PGTHUMBOR_CACHE_DIR = "/var/cache/thumbor/blobs"
PGTHUMBOR_CACHE_MAX_SIZE = 5368709120  # 5 GB
```

## Connection Pool Sizing

The default pool settings (`PGTHUMBOR_POOL_MIN_SIZE=1`,
`PGTHUMBOR_POOL_MAX_SIZE=4`) work for low-traffic sites.  For higher
concurrency:

```python
PGTHUMBOR_POOL_MIN_SIZE = 2
PGTHUMBOR_POOL_MAX_SIZE = 16
```

Each connection uses one PostgreSQL backend slot.  Make sure
`max_connections` in `postgresql.conf` has enough headroom for all services.

## Auth Cache TTL

The default `PGTHUMBOR_AUTH_CACHE_TTL=60` means that permission changes take
up to 60 seconds to take effect for cached images.  Adjust based on your
security requirements:

- **Strict:** `10` -- near-real-time permission enforcement, more Plone
  round-trips.
- **Relaxed:** `300` -- fewer round-trips, permissions lag up to 5 minutes.

## Monitoring

- **Health check:** Thumbor exposes `/healthcheck` when
  `thumbor.handler_lists.healthcheck` is in `HANDLER_LISTS`.  Use this for
  load balancer probes.
- **Logs:** Monitor Thumbor logs for `SchemaError` (missing `blob_state`
  table), pool connection failures, and S3 download errors.
- **Metrics:** Thumbor supports Statsd metrics out of the box.  Configure
  `STATSD_HOST` and `STATSD_PORT` in `thumbor.conf`.

## Summary Checklist

- [ ] Strong random HMAC key, shared between Plone and Thumbor
- [ ] `ALLOW_UNSAFE_URL = False` in Thumbor
- [ ] `PGTHUMBOR_UNSAFE` not set (or `false`) in Plone
- [ ] Reverse proxy (nginx/Traefik) in front of both Plone and Thumbor
- [ ] `PGTHUMBOR_SERVER_URL` points to the public HTTPS URL
- [ ] `PGTHUMBOR_PLONE_AUTH_URL` uses internal direct URL
- [ ] HTTPS on the reverse proxy
- [ ] Persistent volume for Thumbor result storage
- [ ] Blob disk cache sized appropriately
- [ ] Connection pool sized for expected concurrency
- [ ] Health check endpoint monitored
