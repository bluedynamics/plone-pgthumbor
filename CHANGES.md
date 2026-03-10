# Changelog

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
