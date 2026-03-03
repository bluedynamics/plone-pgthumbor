# plone.pgthumbor

<!-- diataxis: landing -->

Pillow-free image scaling for Plone via [Thumbor](https://www.thumbor.org/).

Replaces Plone's `@@images` view with signed Thumbor URLs -- no more in-ZODB
scale storage, no more write-on-read, no more Pillow in the Plone process.
Includes [zodb-pgjsonb-thumborblobloader](https://github.com/bluedynamics/zodb-pgjsonb-thumborblobloader),
a Thumbor 7 loader that reads blobs directly from PostgreSQL.

**Key capabilities:**

- Drop-in replacement for Plone's image scaling (overrides `@@images`)
- 302 redirect to Thumbor -- Plone never touches image bytes
- HMAC-signed URLs prevent arbitrary transformation requests
- Access control for non-public images via `@thumbor-auth` REST service
- Smart focal point detection and cropping
- Async blob loading from PostgreSQL `blob_state` table
- S3 fallback for tiered blob storage
- Local disk LRU cache on the Thumbor side
- Plone control panel for settings

**Requirements:** Python 3.12+, Plone 6, [zodb-pgjsonb](https://github.com/bluedynamics/zodb-pgjsonb) >= 1.1, Thumbor 7+, PostgreSQL 14+

## Documentation

::::{grid} 2
:gutter: 3

:::{grid-item-card} Tutorials
:link: tutorials/index
:link-type: doc

**Learning-oriented** -- Step-by-step lessons to build skills.

*Start here if you are new to plone.pgthumbor.*
:::

:::{grid-item-card} How-To Guides
:link: how-to/index
:link-type: doc

**Goal-oriented** -- Solutions to specific problems.

*Use these when you need to accomplish something.*
:::

:::{grid-item-card} Reference
:link: reference/index
:link-type: doc

**Information-oriented** -- Configuration tables and API details.

*Consult when you need detailed information.*
:::

:::{grid-item-card} Explanation
:link: explanation/index
:link-type: doc

**Understanding-oriented** -- Architecture, security, and design decisions.

*Read to deepen your understanding of how it works.*
:::

::::

## Quick Start

1. {doc}`Install plone.pgthumbor and thumborblobloader <how-to/install>`
2. {doc}`Run the Docker quickstart <tutorials/quickstart-docker>` (Plone + Thumbor + PostgreSQL in 5 minutes)
3. {doc}`Deploy to production <how-to/deploy-production>`

```{toctree}
---
maxdepth: 3
caption: Documentation
titlesonly: true
hidden: true
---
tutorials/index
how-to/index
reference/index
explanation/index
```
