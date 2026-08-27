# Changelog

## 0.7.1 (unreleased)

- Fix: `purge_scales` reindexes only the objects it actually changed, and can
  be walked in bounded slices. Both halves of
  [#16](https://github.com/bluedynamics/plone-pgthumbor/issues/16) that 0.7.0
  left open.

  `_has_image_scales_metadata()` asks the catalog *schema*, which is constant
  for a whole run, so deciding on it alone reindexed every catalogued object
  rather than the handful whose annotation was deleted — O(site) catalog
  writes for O(objects-with-legacy-scales) of work. It is now asked once per
  run instead of once per object, and the reindex happens only where an
  annotation was actually removed.

  `purge_scales()` takes `limit` and `start` and returns `next_start` and
  `done`, so a site too large to finish in one request or one process can be
  walked in slices. `@@thumbor-purge-scales?limit=1000&start=0` reports where
  to resume; the `zconsole` entry point reads `PURGE_LIMIT` and `PURGE_START`.
  A bounded walk sorts on `path` — the default order is whatever PostgreSQL
  returns and is not stable across queries, and an offset into an unstable
  order silently skips objects on resume. Paths do not change during a purge,
  which is what makes ordering on them safe here.

  `purge_scales()` now returns a dict rather than a 4-tuple.

- Lower ruff's C901 max-complexity threshold from 15 to 13 as part of the
  ecosystem-wide complexity ratchet. The code base passes as-is after the
  `srcset` refactor (#33).
- Refactor `ThumborImageScaling.srcset` (complexity 21 → 8) below the C901
  threshold and drop its `# noqa: C901` marker (#33). Primary-field lookup,
  srcset candidate collection, tag-attribute assembly, and the
  `scale_in_src` fallback pick are now dedicated helper methods; behavior
  is unchanged.
- Enable ruff's cyclomatic-complexity check (`C901`, mccabe) with
  `max-complexity = 15`. One existing hotspot is marked with a targeted
  `# noqa: C901` as a visible refactor candidate (`ThumborImageScaling.srcset`,
  complexity 21); `scripts/` is exempted via per-file-ignores (CLI code).

- Tests: CI now runs the suite against `plone.namedfile` 7.x **and** 8.x.
  The two put a different `ImageScale` method on the live path —
  `scaling._HAS_SCALE_URL` forks on it — so a run that only ever saw one of
  them exercised half the package. There is no lockfile here, so an
  unconstrained resolve gets 8.x while production runs 7.x, which was
  exactly the half CI never covered. The test step uses `uv run --no-sync`,
  because a plain `uv run` re-resolves from `pyproject.toml` and would
  silently undo the pin, leaving four jobs that all claim to cover both.

## 0.7.0 (2026-08-24)

- Add Thumbor source derivatives. Thumbor refuses images above its `MAX_PIXELS`
  limit (75 MP by default) and answers HTTP 400 after several seconds of work, so
  print-resolution originals never rendered at all; it also fetched the *whole*
  original on every cache miss, a 40 MB blob crossing the network and the decoder
  to produce a 3 KB listing thumbnail. Plone now stores a capped,
  sRGB-normalised second `NamedBlobImage` on the original field value, as
  `_pgthumbor_source`, and every Thumbor URL addresses that blob instead. The
  original is never modified and `@@download` still serves it byte for byte.
  Pillow becomes a direct dependency: it runs once per image on write, never on
  the request path, where `ThumborScaleStorage` still looks up no
  `IImageScaleFactory` and `tests/test_storage.py::test_no_pillow_invoked` still
  holds.
  Closes [#25](https://github.com/bluedynamics/plone-pgthumbor/issues/25).

  New setting `PGTHUMBOR_SOURCE_MAX_EDGE`, registry field `source_max_edge`,
  default 4000 pixels. `0` disables generation entirely, and values above `8000`
  are clamped on read rather than trusted, because a registry record written
  before the bound existed never revalidates and an env var bypasses validation
  outright. The ceiling is arithmetic rather than taste: a longest edge of *E*
  bounds the derivative at *E²* pixels, so above `sqrt(75e6)`, roughly 8660, a
  derivative could reproduce the very HTTP 400 this removes, and it would do so
  silently. The env lookup uses a `None` sentinel instead of the existing
  falsiness-as-unset idiom, or the documented `0` kill switch would read as unset
  and get overwritten by the registry default. The cap in force is recorded with
  each derivative, so changing it later is an ordinary backfill run rather than a
  migration. Profile version 4, with `upgrade_to_4` registering the record on
  sites that already have the add-on.

  A subscriber on `IObjectAddedEvent` and `IObjectModifiedEvent`, registered for
  `IDexterityContent` and never for `*`, walks every schema and behaviour and
  gives each `NamedBlobImage` field a derivative. Generation triggers on size or
  on colour space (`CMYK`, `LAB`, the 16-bit integer modes, palette with
  transparency), independently, because tying normalisation to size alone would
  let a 3 MP CMYK press image through unconverted. SVG and animated GIFs are
  skipped. One decode at a time per process, behind a bounded semaphore with a
  short timeout: a print-resolution decode costs 79 to 105 MB of pixel buffer and
  `IObjectModifiedEvent` can fan out across every worker thread. Every outcome is
  recorded, including the ones that produced nothing, so failures stay
  enumerable; a semaphore timeout and a failed decode are explicitly non-terminal
  and get picked up again by an ordinary backfill run, with no `force` flag for
  anyone to forget.

  Source selection, crop translation and dimension clamping all land in
  `_build_thumbor_url`, the package's single URL funnel, so all four call sites
  get them at once. Crop boxes from `plone.app.imagecropping` are stored in the
  original's pixels and are now rescaled onto the derivative with a factor per
  axis and direction-aware rounding, and dropped when they degenerate. Requested
  dimensions are clamped to the selected source, and `srcset` no longer offers a
  candidate the source cannot satisfy: its original-width back-fill entry used to
  fail loudly with a Thumbor 400, and against a 4000 px derivative it would have
  started succeeding by scaling an 11811 px image up instead, which is a worse
  outcome than the failure.

  Keep source derivatives out of `Products.CMFEditions` version snapshots.
  `CloneNamedFileBlobs` collects top-level field blobs only, so a nested
  derivative went through the pickle and `ZODB.blob.Blob.__getstate__` returned
  `None`: the snapshot held a `NamedBlobImage` that looked entirely valid and
  read back zero bytes, and a revert produced a field value whose `(zoid, tid)`
  resolved to an empty blob, which Thumbor answers with 400. A new
  `ICloneModifier` drops both attributes on clone, not only the derivative, since
  a terminal outcome record with no derivative would never regenerate. It is
  registered into the persistent `portal_modifier` tool by a GenericSetup step
  rather than by ZCML, so the profile goes to version 5 with `upgrade_to_5`: an
  install-only handler would leave every existing site without it, and an
  existing site is exactly the one this repairs. Both `Products.CMFEditions` and
  its `portal_modifier` tool may be absent; both absences are logged and ignored,
  because with no version repository there is nothing to protect.

  New script `scripts/backfill_thumbor_sources.py` gives existing content its
  derivatives. A keyset walk over `object_state` rather than a catalog walk (a
  brain walk over the same population OOM-killed a production container during
  the original scan), chunked, resumable, with a dry run that reports the numbers
  a cap is chosen from: candidate count, median encoded derivative size, how many
  field values will have their scale uids move, and which scale names actually
  carry crops. Phase 2 re-indexes `image_scales` once the new blobs have
  transaction ids, and it is not optional: the affected catalog rows hold direct,
  signed Thumbor URLs that a browser fetches without Plone in the path, so uid
  healing can never reach them and nothing improves until phase 2 has run.

  Fix: `plone.pgthumbor.purge_scales` no longer blanks `image_scales`
  site-wide. It called `makerequest`, which sets `app.REQUEST` but leaves
  `zope.globalrequest.getRequest()` at `None`, and then re-indexed
  `image_scales` for every object in the catalog. With no request the
  `image_scales` indexer raises `AttributeError`, which is plone.indexer's
  deliberate "do not index" signal; `plone.pgcatalog`'s `extract_idx` reads every
  metadata column as `getattr(wrapper, name, None)` and the default swallows that
  signal; the value becomes a plain `None` and is merged into the JSONB column as
  an explicit `null`. The column was overwritten, not skipped. The new
  `plone.pgthumbor.zconsole` module establishes a request carrying the browser
  layer and refuses to let a script write unless a request exists, provides the
  layer, and resolves `@@images` to `ThumborImageScaling`; the backfill uses the
  same gate before its reindex phase.
  Closes [#16](https://github.com/bluedynamics/plone-pgthumbor/issues/16).

  Two limitations are accepted rather than fixed. A truncated source blob yields
  a truncated derivative, grey where the scan data ran out, rather than no
  derivative: `plone.scale` sets `PIL.ImageFile.LOAD_TRUNCATED_IMAGES = True`
  process-wide at import, so Plone already renders those bytes that way, and a
  package that replaces Plone's scaling should not judge the same bytes more
  harshly than the scaling it replaces. And images above roughly 179 MP get no
  derivative at all, because Pillow raises `DecompressionBombError` inside
  `Image.open` before this package's own 175 MP ceiling can be consulted, and
  raising `Image.MAX_IMAGE_PIXELS` from a worker thread would disable bomb
  protection process-wide for every other decode in the process. Those images are
  recorded as failures, so they stay enumerable, and they keep returning
  Thumbor's 400.

  Chore: `uv.lock` is now in `.gitignore`. The absent lockfile is deliberate, but
  `uv run` writes one on every invocation and `check-added-large-files` caught it
  at 527 KB. Note that a plain `uv run` also re-syncs the environment against
  `pyproject.toml`, silently undoing a local `plone.namedfile < 8` pin; use
  `UV_NO_SYNC=1 uv run pytest` when the pin matters.

  Docs: new how-to guides for choosing the cap and for running the backfill, a
  source derivatives section in the architecture explanation, and the "Pillow is
  never imported, never invoked" claim scoped to the request path, where it stays
  true.

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

- Fix: `_heal_legacy_uid` now recovers the scale a uid was minted for instead
  of guessing one from its width. The uid's md5 covers the whole parameter set
  plus the field's modification time, so the candidates are enumerated and
  re-hashed with `plone.scale`'s own `hash_key` until one matches. That
  identifies the mode rather than assuming `"scale"`, tells two registered
  scales sharing a width apart (`Haeuser 400:200` used to heal as
  `preview 400:0`), and resolves height-driven `0:H` scales, which used to
  heal into a request at the original's dimensions and could push Thumbor past
  `MAX_PIXELS`.

  Matching only works once the modification time is reconstructed:
  `publishTraverse` adapts `(context, None)`, so the storage's `modified_time`
  is `None` at healing time while the uid was hashed against the field's
  modification time. A uid older than the image's last modification cannot be
  identified at all and falls back to the first registered scale of that width.
  The original's dimensions are requested only for the one case where that
  fallback has no other reading: a uid with no width at all, and no `0:H`
  scale registered.

  Closes [#21](https://github.com/bluedynamics/plone-pgthumbor/issues/21).

- Fix: a healed uid for a scale with both dimensions set now recovers the
  scale's name instead of `scale=None`, so `_get_crop` still finds the
  configured crop. `hash_key` drops the `scale` key whenever width and
  height are both truthy, so a named call (`tag(scale="Haeuser")`) and the
  `image_scales` indexer's `scale=None` call mint the identical uid;
  healing could not tell them apart and previously assumed the uncropped
  one.

- Fix: healing no longer reads the field's image size before any candidate
  is hashed. `NamedBlobImage.getImageSize()` lazily assigns
  `_width`/`_height` on first call, which registers a ZODB write on a
  `Persistent` object — reachable by an unauthenticated GET with an
  attacker-chosen uid. `_original_size` is now consulted only once every
  registry-derived candidate has already failed to match, which is also
  the common case, so the successful healing path no longer computes the
  image size twice either.

- Fix: the scale mode now reaches the generated Thumbor URL. `plone.scale`
  keeps `mode` in `info["key"]` and never copies it into the info dict, so
  `info.get("mode", "scale")` always read `"scale"` and every URL was built
  with `fit_in` — a `contain` scale got an `<img>` tag claiming the cropped
  box and an image fitted inside it instead. Both `plone.namedfile` code paths
  and the HiDPI `srcset` attribute are fixed. Forward-compatible with
  [plone/plone.scale#156](https://github.com/plone/plone.scale/pull/156),
  which adds `mode` to the info dict upstream.

- Tests: pin `scale_mode_to_thumbor` against real `scalePILImage` output. The
  mapping compensates for `plone.scale`'s inverted mode names behind a
  `plone.scale < 6` gate, and until the fix above it was only ever reached
  with `"scale"`, so the other two branches were unobservable.

- Tests: cover the mode-threading fix on the two call sites that had none:
  `srcset_attribute`, whose argument is a `calculate_srcset` entry rather
  than a full info dict, and `ThumborImageScaling._scale_url`.

- Docs: fix the "Scale modes" tables in `README.md`,
  `docs/sources/explanation/why-thumbor.md`, and
  `docs/sources/reference/url-format.md`, which described the inverse of
  live behaviour. `plone.scale`'s own mode names are the reverse of what
  they describe; `scale_mode_to_thumbor` compensates for that, and once
  `mode` started reaching the Thumbor URL the tables' error stopped being
  harmless.

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
