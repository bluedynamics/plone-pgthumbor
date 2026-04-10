# Changelog

## 0.6.2 (2026-04-10)

- Fix: access-check queries now use the dedicated `allowed_roles` TEXT[]
  column instead of `idx->'allowedRolesAndUsers'`. `plone-pgcatalog`
  extracts `allowedRolesAndUsers` into its own column, so the old JSONB
  lookup returned `NULL` for every migrated object — making
  `_needs_auth_url()` always return `True` (broken anonymous images) and
  `@thumbor-auth` always return `401` for 3-segment URLs.
  Affects both `_needs_auth_url` in `scaling.py` and `ThumborAuthService`
  in `restapi.py`.
  Closes [#5](https://github.com/bluedynamics/plone-pgthumbor/issues/5).
- Docs: the Sphinx reference changelog is now a MyST include of the root
  `CHANGES.md`, removing the stale hand-maintained copy.

## 0.6.1 (2026-04-03)

- Fix: `IImageScaleStorage` adapter registration now uses `*` as second
  discriminator instead of `IPlonePgthumborLayer`. The adapter lookup in
  `plone.namedfile` passes a `modified` callable (not a request), so the
  layer-based registration never matched — all scales still used the
  default `AnnotationStorage`.
  Closes [#4](https://github.com/bluedynamics/plone-pgthumbor/issues/4).

## 0.6.0 (2026-04-03)

- Fix: `ThumborScaleStorage` no longer writes `ScalesDict` to ZODB.
  The `storage` property now returns a volatile (non-persistent) dict,
  eliminating constant write transactions from `pre_scale()`.
  Closes [#3](https://github.com/bluedynamics/plone-pgthumbor/issues/3).

## 0.5.0 (2026-04-02)

- Remove `server_url`, `security_key`, and `unsafe` from controlpanel and
  registry. These settings are configured exclusively via environment variables
  (`PGTHUMBOR_SERVER_URL`, `PGTHUMBOR_SECURITY_KEY`, `PGTHUMBOR_UNSAFE`).
- Controlpanel now shows env-var configuration hint in the description.
- Upgrade step (v2 -> v3) deletes orphaned registry records from existing sites.
- Purge button uses alert styling.
- Closes [#2](https://github.com/bluedynamics/plone-pgthumbor/issues/2).

## 0.4.0 (2026-04-02)

- Add browser layer `IPlonePgthumborLayer` and bind all views, services,
  and adapter overrides to it.  This enables clean uninstall via
  GenericSetup: removing the layer deactivates all registrations.
- Add uninstall profile (removes browser layer and control panel configlet).

## 0.3.0 (2026-03-10)

- Wire `smart_cropping` and `paranoid_mode` from env vars / Plone registry into
  Thumbor URL generation.
- Add `_scale_url` override for upcoming plone.namedfile `scale_info` support,
  with backward compatibility for current releases.
- Simplify dev setup: run Plone locally, Docker only for postgres/thumbor/nginx.

## 0.2.0 (2026-03-07)

- Add `@@thumbor-purge-scales` view and `zconsole run -m` script to remove
  legacy ZODB image scales and reindex `image_scales` metadata after installation.

## 0.1.0

- Initial implementation: Thumbor URL generation for Plone image scales.
