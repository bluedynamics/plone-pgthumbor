<!-- diataxis: how-to -->

# Set up a development environment

This guide explains how to run plone-pgthumbor and zodb-pgjsonb-thumborblobloader
from source for local development and testing.

## Prerequisites

- Docker and Docker Compose v2+
- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## Clone the monorepo

The development setup lives in the
[z3blobs](https://github.com/bluedynamics/z3blobs) monorepo which contains
all related packages.

```bash
git clone https://github.com/bluedynamics/z3blobs.git
cd z3blobs
```

## Run the development stack

```bash
cd sources/plone-pgthumbor/development
docker compose up -d --build
```

Unlike the `tryout/` setup (which installs from PyPI), the development stack
mounts local source directories. Changes to `sources/plone-pgthumbor/` and
`sources/zodb-pgjsonb-thumborblobloader/` are picked up on container rebuild.

The build context is the monorepo root (`../../..`) so Docker can COPY from
`sources/plone-pgcatalog/`, `sources/plone-pgthumbor/`, and
`sources/zodb-pgjsonb-thumborblobloader/`.

## Run tests

### plone-pgthumbor

```bash
cd sources/plone-pgthumbor
uv venv -p 3.13
uv pip install -e ".[test]"
pytest
```

### zodb-pgjsonb-thumborblobloader

Requires a running PostgreSQL instance (the development stack provides one on
port 5434):

```bash
cd sources/zodb-pgjsonb-thumborblobloader
uv venv -p 3.13
uv pip install -e ".[test,s3]"
ZODB_TEST_DSN="dbname=zodb_test user=zodb password=zodb host=localhost port=5434" pytest
```

## Build documentation

```bash
cd sources/plone-pgthumbor/docs
make docs
```

The HTML output is in `html/`. Use `make docs-live` for live-reload during
writing.
