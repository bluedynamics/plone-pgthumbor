FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN uv pip install --system --no-cache \
    "thumbor>=7.0" \
    "zodb-pgjsonb-thumborblobloader"

COPY thumbor.conf /etc/thumbor.conf

EXPOSE 8888

CMD ["thumbor", "--conf=/etc/thumbor.conf"]
