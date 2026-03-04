<!-- diataxis: explanation -->

# Cloud-native deployment

The Docker Compose example in the repository demonstrates plone.pgthumbor's
multi-service architecture with nginx as the reverse proxy. In production, this
translates naturally to Kubernetes with Traefik as the ingress controller. This page
discusses the architectural reasoning behind a cloud-native deployment -- service
topology, routing, shared secrets, and scaling considerations.

This is a conceptual guide, not a manifests reference. The goal is to help you
reason about how the pieces fit together in a Kubernetes environment so you can
write manifests that match your infrastructure.

## Service topology

A plone.pgthumbor deployment consists of three services and one ingress:

```{mermaid}
flowchart TB
    subgraph Internet
        B[Browser]
    end
    subgraph Kubernetes Cluster
        I[Traefik IngressRoute]
        subgraph Plone
            PS[plone-svc :8080]
            PP1[Plone Pod]
            PP2[Plone Pod]
        end
        subgraph Thumbor
            TS[thumbor-svc :8888]
            TP1[Thumbor Pod]
            TP2[Thumbor Pod]
            TP3[Thumbor Pod]
        end
        subgraph PostgreSQL
            PGS[postgres-svc :5432]
            PGP[PostgreSQL Pod]
        end
    end

    B --> I
    I -->|/| PS
    I -->|/thumbor/| TS
    PS --> PGS
    TS --> PGS
    TS -.->|internal auth| PS
    PP1 & PP2 --- PS
    TP1 & TP2 & TP3 --- TS
    PGP --- PGS
```

| Resource | Kind | Purpose |
|---|---|---|
| `plone` | Deployment + Service | Plone application server (WSGI) |
| `thumbor` | Deployment + Service | Thumbor image processing server |
| `postgres` | StatefulSet + Service (or managed PG) | Shared PostgreSQL database |
| `ingress` | Traefik IngressRoute | Path-based routing, TLS termination |

## Why Traefik fits

Traefik is a natural choice for this architecture because of its native Kubernetes
integration:

**IngressRoute CRD.** Traefik's custom resource definition allows path-based
routing without the limitations of the standard Kubernetes Ingress object. You
define routes declaratively:

- `/thumbor/` routes to `thumbor-svc:8888`
- `/` routes to `plone-svc:8080`

**StripPrefix middleware.** The browser-facing Thumbor URL includes a `/thumbor/`
prefix for routing purposes, but Thumbor itself expects URLs without it. Traefik's
`StripPrefix` middleware removes the prefix before forwarding -- the same function
that the nginx `rewrite` rule serves in the Docker Compose example.

**Automatic HTTPS.** Traefik's Let's Encrypt integration handles TLS certificate
provisioning and renewal. Since HMAC-signed URLs are not encrypted (they rely on
the signature, not transport secrecy), HTTPS is important for protecting the session
cookie that flows through the auth subrequest chain.

**Header forwarding.** Traefik preserves all request headers by default, including
`Cookie` and `Authorization`. This is critical for the auth flow: the browser's
session cookie must reach Thumbor (for forwarding to Plone) without being stripped.

Other ingress controllers (nginx-ingress, HAProxy, Envoy) work equally well. The
requirements are: path-based routing, prefix stripping, and header forwarding.
Traefik is highlighted here because its CRD model maps cleanly to the
plone.pgthumbor routing needs.

## Internal service DNS

The most important networking detail is the auth subrequest URL. In the Docker
Compose example:

```
PGTHUMBOR_PLONE_AUTH_URL = http://plone:8080/Plone
```

In Kubernetes, this becomes:

```
PGTHUMBOR_PLONE_AUTH_URL = http://plone-svc:8080/Plone
```

This URL is cluster-internal. Thumbor's `AuthImagingHandler` calls Plone's
`@thumbor-auth` endpoint directly via the Kubernetes service DNS name, without
passing through the ingress controller. This has three benefits:

1. **No routing loop.** If the auth URL went through Traefik, the `/` prefix match
   would route it to Plone, but the request would first hit Traefik's middleware
   stack, potentially triggering rate limits or other middleware that should not
   apply to internal service-to-service communication.

2. **Lower latency.** Cluster-internal traffic stays within the pod network. There
   is no TLS termination overhead, no ingress controller hop, and no external DNS
   resolution.

3. **Security boundary.** The `@thumbor-auth` endpoint is registered with
   `permission="zope2.Public"` because Thumbor is not a Zope user. In a
   cluster-internal network, this is acceptable -- the endpoint is only reachable
   from within the cluster. If it were exposed through the ingress, anyone on the
   internet could probe it (though the endpoint only returns boolean yes/no
   responses, the reduced attack surface is still preferable).

## Shared secrets

Plone and Thumbor must share two secrets:

| Secret | Used by Plone | Used by Thumbor |
|---|---|---|
| HMAC security key | Sign URLs (`PGTHUMBOR_SECURITY_KEY`) | Verify signatures (`THUMBOR_SECURITY_KEY`) |
| PostgreSQL DSN | Store/load objects (`PGJSONB_DSN`) | Load blobs (`PGTHUMBOR_DSN`) |

In Kubernetes, these should live in a single `Secret` resource, mounted as
environment variables in both the Plone and Thumbor Deployments. This ensures the
keys stay in sync -- a mismatch between the HMAC keys would cause every image
request to fail with a signature verification error.

The PostgreSQL DSN may differ between services (different connection pool sizes,
different database users with different permissions), but the host, port, and
database name must be the same since both services read from the same tables.

For managed PostgreSQL services (AWS RDS, Google Cloud SQL, Azure Database for
PostgreSQL), the StatefulSet is replaced by a connection string pointing to the
managed instance. The architecture is otherwise identical.

## Scaling considerations

The three services have fundamentally different scaling characteristics:

### Thumbor: scale freely

Thumbor is stateless. Each pod processes requests independently using its own
in-memory auth cache and optional local disk cache. Adding more Thumbor pods
linearly increases image processing throughput.

The disk cache (`BlobCache`) is local to each pod. This means different pods may
cache different blobs, and a cache miss on one pod does not benefit from a cache
hit on another. For high-traffic deployments, a shared result storage (Redis,
S3) can be configured in Thumbor to share the processed-image cache across pods.

Thumbor pods tolerate ungraceful termination. There is no state to drain -- a
killed pod simply stops processing. In-flight requests fail and the browser retries
(or the CDN retries, if one is in place).

### Plone: scale cautiously

Each Plone pod holds a ZODB connection pool. Adding more Plone pods increases the
total number of PostgreSQL connections proportionally. PostgreSQL has a hard
connection limit (`max_connections`), and each connection consumes server memory.

Plone scaling is bounded by:

- **PostgreSQL connections.** Each Plone pod opens N ZODB connections (configurable
  in zodb-pgjsonb's `pool_size`). With 4 pods and `pool_size=7`, that is 28
  PostgreSQL connections just for ZODB -- before counting plone-pgcatalog's pool
  connections.

- **ZODB conflict resolution.** Concurrent writes to the same object produce
  `ConflictError`, which triggers retry. More Plone pods means more concurrent
  writers, which can increase conflict rates on hot objects (like portal_catalog
  in classic Plone, though plone-pgcatalog avoids this for catalog writes).

- **Memory.** Each Plone pod loads the full Zope component architecture, ZCML
  registrations, and a ZODB object cache. The baseline memory per pod is
  significant (200-500 MB depending on add-ons).

A common pattern is to run 2-4 Plone pods behind the service, with horizontal
pod autoscaling based on CPU or request latency. The image scaling offload to
Thumbor significantly reduces Plone's per-request resource consumption, which
means each Plone pod can handle more concurrent requests than before.

### PostgreSQL: the shared bottleneck

PostgreSQL is the convergence point for all services. Both Plone (ZODB reads/writes,
catalog queries) and Thumbor (blob reads, auth queries) hit the same database.

For production deployments:

- **Connection pooling.** PgBouncer in front of PostgreSQL multiplexes application
  connections onto a smaller number of server connections, reducing PostgreSQL's
  per-connection memory overhead.

- **Read replicas.** Thumbor's blob reads are pure SELECT queries on immutable
  data (identified by ZOID+TID). These can be directed to a read replica without
  consistency concerns. The auth check's PG query (on `object_state.idx`) could
  also use a replica, accepting a brief window of stale security data after
  permission changes.

- **Managed PostgreSQL.** Cloud providers handle replication, backups, connection
  limits, and storage scaling. This is the recommended approach for production.

## Network diagram with auth flow

The complete request flow in a Kubernetes deployment, showing both the external
image request and the internal auth subrequest:

```{mermaid}
sequenceDiagram
    participant B as Browser
    participant T as Traefik Ingress
    participant Th as Thumbor Pod
    participant P as Plone Pod
    participant PG as PostgreSQL

    B->>T: GET /thumbor/{hmac}/.../{zoid}/{tid}/{content_zoid}
    T->>T: StripPrefix /thumbor/
    T->>Th: GET /{hmac}/.../{zoid}/{tid}/{content_zoid} (Cookie preserved)

    Th->>Th: verify HMAC signature
    Th->>Th: detect 3-segment URL

    Note over Th,P: Internal cluster network (no ingress hop)
    Th->>P: GET /@thumbor-auth?zoid={content_zoid} (Cookie forwarded)
    P->>PG: SELECT (idx->'allowedRolesAndUsers' ?| principals)
    PG->>P: allowed = true
    P->>Th: 200 OK

    Th->>PG: SELECT data FROM blob_state WHERE zoid=? AND tid=?
    PG->>Th: blob bytes
    Th->>Th: resize + encode
    Th->>T: scaled image
    T->>B: image response
```

The key observation is that the auth subrequest bypasses Traefik entirely. It goes
directly from the Thumbor pod to the Plone service (`plone-svc:8080`), staying
within the cluster network. The only external-facing traffic is the initial image
request and the final image response.

## Comparison with Docker Compose

| Concern | Docker Compose (example) | Kubernetes (production) |
|---|---|---|
| Reverse proxy | nginx container | Traefik IngressRoute |
| Prefix stripping | nginx `rewrite` | Traefik `StripPrefix` middleware |
| TLS | Not configured | Traefik + Let's Encrypt |
| Service discovery | Docker DNS (`plone`, `thumbor`) | Kubernetes DNS (`plone-svc`, `thumbor-svc`) |
| Secrets | Environment variables in YAML | Kubernetes Secret resource |
| Scaling | Manual `docker compose up --scale` | HPA (Horizontal Pod Autoscaler) |
| PostgreSQL | Container with volume | StatefulSet or managed PG |
| Blob disk cache | Container-local | emptyDir or PVC per pod |
| Health checks | Docker HEALTHCHECK | Kubernetes liveness/readiness probes |

The architectural pattern is identical in both environments. The Docker Compose
example is a faithful miniature of the production topology -- the same service
boundaries, the same routing rules, the same internal auth flow. Moving to
Kubernetes is a matter of translating container definitions to pod specs and nginx
rules to IngressRoute CRDs.
