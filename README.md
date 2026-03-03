# plone-pgthumbor

Thumbor image scaling for Plone — replaces in-ZODB scales with Thumbor redirect URLs.

Instead of generating and storing scaled images in ZODB annotations (via Pillow), this package intercepts Plone's image scaling and returns signed [Thumbor](https://www.thumbor.org/) URLs. Thumbor fetches the original blob directly from PostgreSQL, scales on demand, and caches the result — no image data ever enters ZODB.

## How it works

Plone generates signed Thumbor URLs and either embeds them directly or redirects to them — it never processes image data itself.

**Classic Plone / direct `@@images` traversal**

```
Browser             Plone                        Thumbor              PostgreSQL
  |                   |                             |                      |
  | GET @@images/     |                             |                      |
  |   image/preview   |                             |                      |
  |------------------>|                             |                      |
  |  302 to Thumbor   |                             |                      |
  |<------------------|                             |                      |
  |                                                 |                      |
  | GET /sig/fit-in/400x0/<zoid>/<tid>              |                      |
  |------------------------------------------------>|                      |
  |                                                 | SELECT data          |
  |                                                 | FROM blob_state      |
  |                                                 |--------------------> |
  |                                                 |<---------------------|
  |             200 scaled JPEG (Thumbor cache hit) |                      |
  |<------------------------------------------------|                      |
```

**Volto / REST API (`image_scales` metadata)**

At catalog index time, Plone pre-computes absolute Thumbor URLs for every scale and stores them in the `image_scales` catalog metadata. Volto reads these directly from the REST API response and renders `<img src="https://thumbor/...">` — no redirect hop at all.

In both cases Thumbor retrieves blobs via [zodb-pgjsonb-thumborblobloader](https://github.com/bluedynamics/zodb-pgjsonb-thumborblobloader): local disk cache first, then PostgreSQL bytea, with optional S3 fallback. Cache busting is automatic — the blob TID in the URL changes whenever the image is updated.

## Requirements

- [zodb-pgjsonb](https://github.com/bluedynamics/zodb-pgjsonb) — ZODB storage with PostgreSQL JSONB + blob_state table
- [zodb-pgjsonb-thumborblobloader](https://github.com/bluedynamics/zodb-pgjsonb-thumborblobloader) — Thumbor loader reading from blob_state
- [Thumbor](https://thumbor.readthedocs.io/) >= 7.0
- Plone 6.2+

## Installation

```bash
pip install plone.pgthumbor
```

Add to your Plone site's GenericSetup profile dependencies or install via the Add-ons control panel.

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `PGTHUMBOR_SERVER_URL` | (required) | Public Thumbor base URL, e.g. `http://thumbor:8888` |
| `PGTHUMBOR_SECURITY_KEY` | (required) | Shared HMAC-SHA1 signing key |
| `PGTHUMBOR_UNSAFE` | `false` | Use `/unsafe/` URLs instead of signed — dev only |

Environment variables take precedence over Plone registry settings (`IThumborSettings`).

## Scale modes

| Plone mode | Thumbor behaviour |
|------------|-------------------|
| `scale` (default) | `fit-in` + smart crop — fits within box, no upscale |
| `cover` | Smart crop to exact dimensions |
| `contain` | `fit-in` only — fits within box, no crop |

## Try It Out

A self-contained Docker Compose stack (Plone 6.2 + Thumbor + PostgreSQL + nginx)
using PyPI releases is in [tryout/](tryout/).

```bash
cd tryout
docker compose up -d --build
# Plone at http://localhost:8080  (admin/admin)
```

For development with local source installs, use [development/](development/) instead.

## Documentation

Rendered documentation: **https://bluedynamics.github.io/plone-pgthumbor/**

- [Architecture](https://github.com/bluedynamics/plone-pgthumbor/blob/main/docs/sources/explanation/architecture.md) -- request flow, Thumbor integration design
- [Security](https://github.com/bluedynamics/plone-pgthumbor/blob/main/docs/sources/explanation/security.md) -- three-layer access control model
- [Configuration Reference](https://github.com/bluedynamics/plone-pgthumbor/blob/main/docs/sources/reference/configuration.md) -- all settings for Plone and Thumbor
- [CHANGES.md](https://github.com/bluedynamics/plone-pgthumbor/blob/main/CHANGES.md) -- changelog

## Source Code and Contributions

The source code is managed in a Git repository, with its main branches hosted on GitHub.
Issues can be reported there too.

We'd be happy to see many forks and pull requests to make this package even better.
We welcome AI-assisted contributions, but expect every contributor to fully understand and be able to explain the code they submit.
Please don't send bulk auto-generated pull requests.

Maintainers are Jens Klein and the BlueDynamics Alliance developer team.
We appreciate any contribution and if a release on PyPI is needed, please just contact one of us.
We also offer commercial support if any training, coaching, integration or adaptations are needed.

## License

GPL-2.0
