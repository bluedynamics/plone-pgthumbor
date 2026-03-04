<!-- diataxis: how-to -->

# Configure Plone settings

plone.pgthumbor reads its configuration from environment variables.
A Plone registry schema (`IThumborSettings`) is also available for settings
that do not change per deployment.

**Environment variables always take precedence over registry values.**

## Environment variables

Set these before starting Zope.

### `PGTHUMBOR_SERVER_URL` (required)

The public, browser-facing URL of the Thumbor server.  Plone uses this as the
base URL when generating image `src` attributes.  It must be reachable from
the end user's browser:

```bash
export PGTHUMBOR_SERVER_URL="https://example.com/thumbor"
```

In the Docker example stack, nginx proxies `/thumbor/` to the Thumbor
container, so the value is:

```bash
export PGTHUMBOR_SERVER_URL="http://localhost:8080/thumbor"
```

A trailing slash is stripped automatically.

If this variable is empty, plone.pgthumbor is effectively disabled -- the
`@@images` view falls back to default Plone scaling behavior.

### `PGTHUMBOR_SECURITY_KEY` (required unless unsafe mode)

The HMAC-SHA1 key used to sign Thumbor URLs.  Must match the `SECURITY_KEY`
in `thumbor.conf`:

```bash
export PGTHUMBOR_SECURITY_KEY="your-strong-random-key"
```

If neither `PGTHUMBOR_SECURITY_KEY` nor `PGTHUMBOR_UNSAFE` is set,
plone.pgthumbor logs a warning and disables itself.

### `PGTHUMBOR_UNSAFE`

Enable unsigned `/unsafe/` URLs.  **Development only.**

```bash
export PGTHUMBOR_UNSAFE="true"
```

Accepted values: `true`, `1`, `yes` (case-insensitive).  All other values are
treated as `false`.

When enabled, Plone generates unsigned URLs prefixed with `/unsafe/` instead
of an HMAC signature.  Thumbor must also have `ALLOW_UNSAFE_URL = True` for
these to work.

:::{warning}
Never enable unsafe mode in production.  It allows anyone to request arbitrary
image transformations, consuming server resources and potentially exposing
private images.
:::

## Plone registry settings

The `IThumborSettings` registry schema provides the following fields.  They
serve as fallback values when the corresponding environment variable is not
set.  Configure them in **Site Setup > Thumbor Settings** or programmatically
via the Plone registry.

### `server_url`

Thumbor server URL.  Same role as `PGTHUMBOR_SERVER_URL`.  The environment
variable takes precedence.

- Type: `TextLine`
- Default: `""` (empty)

### `security_key`

Shared HMAC key.  Same role as `PGTHUMBOR_SECURITY_KEY`.  The environment
variable takes precedence.

- Type: `TextLine`
- Default: `""` (empty)

### `unsafe`

Unsafe mode.  Same role as `PGTHUMBOR_UNSAFE`.  The environment variable takes
precedence.

- Type: `Bool`
- Default: `False`

### `smart_cropping`

Enable Thumbor smart cropping.  When enabled, Plone appends the `/smart/`
filter to Thumbor URLs.  Thumbor uses OpenCV-based face and feature detection
to choose a focal point for cropping.

- Type: `Bool`
- Default: `False`

### `paranoid_mode`

When enabled, every image request includes the content object's ZOID in the
Thumbor URL path (3-segment format: `<blob_zoid>/<tid>/<content_zoid>`).
Thumbor's `auth_handler` then verifies with Plone that the requesting user has
`View` permission on the content object.

When disabled (default), only images on non-public content use the 3-segment
format.  Public images use the 2-segment format and skip the auth check for
better performance.

- Type: `Bool`
- Default: `False`

## Configuration precedence

The configuration is resolved in `plone.pgthumbor.config.get_thumbor_config()`:

1. Environment variables are checked first.
2. If `PGTHUMBOR_SERVER_URL` is empty, the function returns `None`
   (Thumbor integration disabled).
3. If neither `PGTHUMBOR_SECURITY_KEY` nor `PGTHUMBOR_UNSAFE` is set,
   a warning is logged and the function returns `None`.

## Example: Docker Compose

In a Docker Compose file, set the environment on the Plone service:

```yaml
plone:
  environment:
    PGTHUMBOR_SERVER_URL: "http://localhost:8080/thumbor"
    PGTHUMBOR_SECURITY_KEY: "your-strong-random-key"
```

## Example: systemd unit

In a systemd service file:

```ini
[Service]
Environment="PGTHUMBOR_SERVER_URL=https://example.com/thumbor"
Environment="PGTHUMBOR_SECURITY_KEY=your-strong-random-key"
```
