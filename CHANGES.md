# Changelog

## 0.6.6 (unreleased)

- Bump `hynek/build-and-inspect-python-package` from v2 to v3.0.1. Hatchling now
  emits `Metadata-Version: 2.5`, which the Twine bundled in v2 rejects with
  `InvalidDistribution: '2.5' is not a valid metadata version` — the release
  build failed before uploading anything. v3 ships Twine 7, which supports it.

- Add the `LICENSE` file with the full GNU General Public License v2.0 text.
  The packaging metadata already declared `GPL-2.0-only`, but the license text
  itself was missing from the repository, so GitHub reported "No license" and
  the terms could not be verified from the source tree alone. Fixes #24.

- Support the `scale` mode semantics bug from plone.scale < version 6.
  The scale mode names are opposite to the corresponding CSS `object-fit`
  property names. We expect that to be fixed in version 6.
  [thet]

- Add `cloud-vinyl` and `plone.observability` to the ecosystem navigation
  dropdown in the docs.

## 0.6.5 (2026-08-04)

- Fix: SVG (skip-Thumbor) images no longer emit uid-based scale URLs that
  permanently 404. Root cause was not `purge_scales` but the volatile
  `ThumborScaleStorage` introduced in 0.6.x: `get_or_generate` reads a
  fresh empty per-instance dict on every traversal, so *no* uid scale URL
  could ever resolve — the `plone.scale` annotation is never consulted.
  Fixed on both ends: skip-types now emit the original field URL with a
  modification-time cache buster (both plone.namedfile code paths, the
  legacy `__init__` for 7.x and `_scale_url` for >= 8.0.0a2), the HiDPI
  `srcset` attribute and the `srcset()` method emit Thumbor URLs, and
  `get_or_generate` heals legacy uid URLs (cached HTML, stale
  `image_scales` catalog metadata) by parsing the deterministic
  `{fieldname}-{width}-{md5}` uid and regenerating the info on the fly —
  restricted to widths registered in `plone.allowed_sizes`. Review
  hardening on top: srcset() mirrors the parent's edge-case guards
  (zero-size original, original-size back-fill, unresolvable src scale),
  and the HiDPI srcset path threads crop info through for scale infos
  that carry a scale name.
  Closes [#17](https://github.com/bluedynamics/plone-pgthumbor/issues/17).

- Add `cdk8s-plone` to the ecosystem navigation dropdown in the docs.

- Chore: apply ruff 0.16 markdown code-fence formatting to four docs files
  (pre-existing drift; the QA workflow runs the latest ruff via uvx over
  the whole repo). Mark up `zope2.Public` as inline code in a security-doc
  heading so vale's Microsoft.Spacing rule no longer trips on it.

## 0.6.4 (2026-04-20)

- Fix: `_needs_auth_url()` no longer issues a PostgreSQL query per image.
  The old implementation looked up `allowed_roles` in `object_state` via
  a request-scoped pool connection, which saturated the per-pod psycopg
  pool under cold-cache production load (30 thumbnails per listing page
  × concurrent anonymous requests = 30 s `PoolTimeout` stacks).
  Replaced with an in-memory `rolesForPermissionOn("View", context)`
  lookup — the plone-pgcatalog `allowed_roles` column is a cache of
  exactly this computation, so the SQL was re-asking a question Zope
  already knew the answer to. Zero DB round-trips, zero pool pressure,
  no catalog-lag skew vs. live workflow state.
  Closes [#8](https://github.com/bluedynamics/plone-pgthumbor/issues/8).

- Fix: `@thumbor-auth` REST service now prefers the ZODB storage
  connection (already held for the request) over the psycopg pool, so
  per-image auth verification doesn't contend on `pool.getconn()`.
  The SQL query is unchanged — this is strictly a connection-acquisition
  change, matching the pattern plone-pgcatalog uses in
  `_get_pg_read_connection`.  Falls back to the pool when no ZODB
  storage is in scope (tests, scripts).
  Related to [#8](https://github.com/bluedynamics/plone-pgthumbor/issues/8).

## 0.6.3 (2026-04-13)

- Move `@@images` out of overrides, it is on a layer.

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
