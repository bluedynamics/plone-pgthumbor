<!-- diataxis: how-to -->

# Install plone.pgthumbor

This guide covers installing both the Plone-side package (`plone.pgthumbor`)
and the Thumbor-side loader (`zodb-pgjsonb-thumborblobloader`), then verifying
that everything works together.

## Requirements

- Python 3.12+
- Plone 6.2+ (Classic or Volto)
- PostgreSQL 14+ (tested with 17)
- [zodb-pgjsonb](https://github.com/bluedynamics/zodb-pgjsonb) >= 1.1 as the
  ZODB storage backend
- Thumbor 7+

## Install the Plone Package

Install `plone.pgthumbor` into your Plone environment:

```bash
uv pip install plone.pgthumbor
# or
pip install plone.pgthumbor
```

This also installs `libthumbor` (for HMAC URL signing) and `plone.pgcatalog`
as dependencies.

## Install the Thumbor Loader

On the machine or container running Thumbor, install the blob loader:

```bash
uv pip install zodb-pgjsonb-thumborblobloader
# or
pip install zodb-pgjsonb-thumborblobloader
```

This installs `psycopg[binary]` (async PostgreSQL driver) and `boto3`
(optional, for S3 fallback).

## Configure ZODB Storage

Your `zope.conf` must use zodb-pgjsonb as the ZODB storage backend.
plone.pgthumbor discovers its PostgreSQL connection from the storage layer.

```
%import zodb_pgjsonb

<zodb_db main>
  <pgjsonb>
    dsn dbname=zodb host=localhost port=5432 user=zodb password=zodb
    blob-temp-dir /app/var/blobstorage
  </pgjsonb>
  mount-point /
</zodb_db>
```

## Configure Thumbor

Create a `thumbor.conf` that uses the zodb-pgjsonb blob loader:

```python
LOADER = "zodb_pgjsonb_thumborblobloader.loader"

HANDLER_LISTS = [
    "thumbor.handler_lists.healthcheck",
    "zodb_pgjsonb_thumborblobloader.auth_handler",
]

SECURITY_KEY = "your-secret-key"
ALLOW_UNSAFE_URL = False

RESULT_STORAGE = "thumbor.result_storages.file_storage"
RESULT_STORAGE_FILE_STORAGE_ROOT_PATH = "/tmp/thumbor/result_storage"

PGTHUMBOR_DSN = "dbname=zodb host=localhost port=5432 user=zodb password=zodb"
```

See {doc}`configure-thumbor` for the full list of settings.

## Set Plone Environment Variables

Plone needs to know where to point image URLs.  Set these environment variables
before starting Zope:

```bash
export PGTHUMBOR_SERVER_URL="http://localhost:8080/thumbor"
export PGTHUMBOR_SECURITY_KEY="your-secret-key"
```

The security key must match the `SECURITY_KEY` in `thumbor.conf`.
See {doc}`configure-plone` for all available settings.

## Apply the GenericSetup Profile

Install via Plone's Add-on installer (Site Setup > Add-ons > plone.pgthumbor),
or programmatically:

```python
setup_tool = portal.portal_setup
setup_tool.runAllImportStepsFromProfile("profile-plone.pgthumbor:default")
```

The profile registers the `@@images` override and the `@thumbor-auth` REST
service.

plone.pgthumbor is auto-discovered via `z3c.autoinclude` -- no `%import`
is needed in `zope.conf`.

## Verify Installation

1. Start Thumbor:

   ```bash
   thumbor --conf=/etc/thumbor.conf
   ```

2. Start Zope:

   ```bash
   runwsgi instance/etc/zope.ini
   ```

3. Upload an image in Plone.  Right-click the displayed image and inspect the
   URL.  It should point to your Thumbor server (the value of
   `PGTHUMBOR_SERVER_URL`) with an HMAC signature and blob coordinates:

   ```
   http://localhost:8080/thumbor/<hmac>/400x300/<zoid_hex>/<tid_hex>
   ```

4. Open the URL in a new tab.  You should see the scaled image.

If the image does not load, check:

- Thumbor logs for connection errors to PostgreSQL
- That the `PGTHUMBOR_DSN` in `thumbor.conf` is correct
- That the `PGTHUMBOR_SECURITY_KEY` matches between Plone and Thumbor
- That the `blob_state` table exists in PostgreSQL (created by zodb-pgjsonb)
