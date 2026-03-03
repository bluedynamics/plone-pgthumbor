FROM python:3.13-slim

# Install uv for fast package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Released packages from PyPI
RUN uv pip install --system --no-cache "thumbor>=7.0"

# Local: thumborblobloader (not yet on PyPI)
COPY sources/zodb-pgjsonb-thumborblobloader /tmp/thumborblobloader
RUN cd /tmp/thumborblobloader && \
    SETUPTOOLS_SCM_PRETEND_VERSION=0.1.0 uv pip install --system --no-cache . && \
    rm -rf /tmp/thumborblobloader

COPY sources/plone-pgthumbor/development/thumbor/thumbor.conf /etc/thumbor.conf

EXPOSE 8888

CMD ["thumbor", "--conf=/etc/thumbor.conf"]
