FROM ghcr.io/astral-sh/uv:0.11.32 AS uv
FROM python:3.12-slim-bookworm

COPY --from=uv /uv /usr/local/bin/uv

RUN groupadd --gid 10001 inventory \
    && useradd --uid 10001 --gid 10001 --create-home inventory

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY alembic.ini ./
COPY src ./src
RUN uv sync --frozen --no-dev

RUN mkdir -p /var/lib/cloud-inventory/artifacts \
    && chown -R 10001:10001 /var/lib/cloud-inventory /app

ENV PATH="/app/.venv/bin:${PATH}"
USER 10001:10001
