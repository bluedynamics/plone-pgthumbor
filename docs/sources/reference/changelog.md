<!-- diataxis: reference -->

# Changelog

## plone.pgthumbor

### 0.1.0 (unreleased)

- Initial implementation: Thumbor URL generation for Plone image scales.

- Add 3-segment authenticated Thumbor URLs for access-controlled content.
  Public images use the existing 2-segment format (`zoid/tid`); restricted
  images append a `content_zoid` segment so the Thumbor auth handler can
  verify Plone permissions. Paranoid mode forces auth URLs for all images.

- Add `@thumbor-auth` REST API endpoint for Thumbor auth handler
  subrequests. Checks `allowedRolesAndUsers` via a direct PG query
  against the pgcatalog index -- no ZODB object loading required.

- Add Thumbor settings control panel (`@@thumbor-settings`) with
  auto-registering `getContent()` fallback for fresh installs.
  GenericSetup profile registers `controlpanel.xml` and `registry.xml`.

- Add nginx reverse proxy and auth handler integration to Docker example.
  Thumbor runs behind nginx at `/thumbor/`; Plone generates public-facing
  URLs through the nginx endpoint.

## zodb-pgjsonb-thumborblobloader

### 0.2.0

- Add `AuthImagingHandler` for Plone access control via `@thumbor-auth`
  REST service. 3-segment URLs (`blob_zoid/tid/content_zoid`) verify
  access before delivery; 2-segment URLs are served directly.
- Extend `_parse_path` to accept 3-segment authenticated URL format.

### 0.1.0

- Initial release: Thumbor loader for zodb-pgjsonb blob_state.
