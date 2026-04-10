<!-- diataxis: reference -->

# REST API reference

This page documents the `@thumbor-auth` REST API service provided by
`plone.pgthumbor` and consumed by the Thumbor auth handler in
`zodb-pgjsonb-thumborblobloader`.

## @thumbor-auth

Verifies whether the current user may view a content object identified by
its ZODB OID.
Used by the Thumbor `AuthImagingHandler` to check access
before delivering images for 3-segment (authenticated) URLs.

### Endpoint

```
GET /@thumbor-auth?zoid=<hex_oid>
```

### Registration

| Property | Value |
|---|---|
| Method | `GET` |
| Name | `@thumbor-auth` |
| Context | `INavigationRoot` |
| Permission | `zope2.Public` |
| Factory | `plone.pgthumbor.restapi.ThumborAuthService` |

The `zope2.Public` permission allows the endpoint to be called without
Plone authentication.
Access control is determined by inspecting the
caller's effective principals against the object's
`allowedRolesAndUsers` catalog index.

### Parameters

| Parameter | Location | Type | Required | Description |
|---|---|---|---|---|
| `zoid` | Query string | Hexadecimal string | Yes | ZODB OID of the content object to check, encoded as a hex integer (for example, `1a`). |

### Response codes

| Status | Body | Condition |
|---|---|---|
| `200 OK` | `{}` | User is allowed to view the object. |
| `400 Bad Request` | `{"error:" "Missing zoid parameter"}` | The `zoid` query parameter is absent. |
| `400 Bad Request` | `{"error:" "Invalid zoid parameter"}` | The `zoid` value is not valid hexadecimal. |
| `401 Unauthorized` | `{"error:" "Unauthorized"}` | The user's principals do not overlap with the object's `allowedRolesAndUsers`. |
| `404 Not Found` | `{"error:" "Not found"}` | No object with the given ZOID exists in `object_state`. |
| `503 Service Unavailable` | `{"error:" "Service unavailable"}` | The PostgreSQL query failed. |

All responses have `Content-Type: application/json`.

### SQL query

The service executes a single PostgreSQL query against the `object_state`
table (managed by `zodb-pgjsonb` + `plone-pgcatalog`).
It checks whether the object's `allowed_roles` TEXT[] column overlaps
with the current user's effective principals:

```sql
SELECT (allowed_roles && %s::text[]) AS allowed
FROM object_state WHERE zoid = %s
```

`plone-pgcatalog` extracts `allowedRolesAndUsers` from the indexed
data into a dedicated `TEXT[]` column (with a GIN index).
The `&&` array-overlap operator replaces the old JSONB `?|` lookup.

The first parameter is the list of user principals (obtained from
`catalog._listAllowedRolesAndUsers(user)`).
The second parameter is the
integer ZOID parsed from the query string.

No ZODB object loading or security manager switching is required.

## Thumbor auth handler (AuthImagingHandler)

The `AuthImagingHandler` in `zodb-pgjsonb-thumborblobloader` is the
consumer of the `@thumbor-auth` endpoint.
It runs inside Thumbor as a
custom handler registered via `HANDLER_LISTS`.

### URL detection

The handler inspects the request path to determine the URL format:

- **3-segment URL**: The last three path segments are all valid
  hexadecimal strings (`blob_zoid/tid/content_zoid`).
  The handler
  extracts `content_zoid` and performs an auth check.
- **2-segment URL**: Only the last two path segments are valid hex
  (`blob_zoid/tid`). No auth check is performed; the image is served
  directly.

### Auth check flow

For 3-segment URLs, the handler:

1.
Extracts the `content_zoid` hex string from the last path segment.
2.
Checks the in-memory auth cache for a cached result.
3.
If no cached result, sends an HTTP GET to:
   ```
   {PGTHUMBOR_PLONE_AUTH_URL}/@thumbor-auth?zoid={content_zoid_hex}
   ```
4.
Forwards the browser's `Cookie` and `Authorization` headers so Plone
   can authenticate the user from the shared reverse-proxy session.
5.
Interprets an HTTP 200 response as "allowed;" any other status as
   "denied."
6.
Caches the result keyed by `(content_zoid_hex, cookie_header)` for
   `PGTHUMBOR_AUTH_CACHE_TTL` seconds.
7.
Returns HTTP 403 to the client if access is denied.

### Auth Cache

| Property | Value |
|---|---|
| Cache key | `(content_zoid_hex, Cookie header value)` |
| Default TTL | 60 seconds (configurable via `PGTHUMBOR_AUTH_CACHE_TTL`) |
| Scope | Module-level dictionary, per Thumbor process |

The cache prevents repeated Plone round-trips for the same user viewing
multiple images on a single page.
Different users (identified by their
cookie) have separate cache entries.

### Headers Forwarded

| Header | Purpose |
|---|---|
| `Cookie` | Session cookie for Plone authentication. |
| `Authorization` | HTTP Basic or Bearer token authentication. |

Both headers are forwarded from the original browser request to the
`@thumbor-auth` call.
This requires that the browser, Thumbor, and Plone
share a common reverse proxy so that authentication cookies are available
in the Thumbor request.
