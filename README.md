# plone-pgthumbor

Thumbor image scaling for Plone — replaces in-ZODB scales with Thumbor URLs.

Instead of generating and storing scaled images in ZODB annotations (via Pillow),
this package generates Thumbor URLs that point to a Thumbor server which handles
all image scaling, caching, and format conversion.

## How it works

1. `@@images/image/preview` returns a **302 redirect** to a signed Thumbor URL
2. Thumbor fetches the original blob from `blob_state` (via `zodb-pgjsonb-thumborblobloader`)
3. Thumbor scales, caches, and returns the image
4. Cache busting is natural: TID changes when image changes, URL changes

## Requirements

- [zodb-pgjsonb](https://github.com/bluedynamics/zodb-pgjsonb) — ZODB storage with PostgreSQL JSONB
- [zodb-pgjsonb-thumborblobloader](https://github.com/bluedynamics/zodb-pgjsonb-thumborblobloader) — Thumbor loader for blob_state
- [Thumbor](https://thumbor.readthedocs.io/) >= 7.0

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `PGTHUMBOR_SERVER_URL` | (required) | Public Thumbor URL |
| `PGTHUMBOR_SECURITY_KEY` | (required) | HMAC-SHA1 signing key |
| `PGTHUMBOR_UNSAFE` | `false` | Use `/unsafe/` URLs (dev only) |

Environment variables take precedence over Plone registry settings.
