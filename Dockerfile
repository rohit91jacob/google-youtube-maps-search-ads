# syntax=docker/dockerfile:1.7
# One image for every Cloud Run job; the args pick the stage (`geco extract`, `geco load`, ...).
FROM python:3.15-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH

COPY --from=ghcr.io/astral-sh/uv:0.10.2 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --group dbt --extra gcp --no-install-project
COPY src ./src
COPY dbt ./dbt
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --group dbt --extra gcp --no-editable \
 && useradd --create-home --uid 10001 geco \
 && mkdir -p /tmp/geco && chown -R geco:geco /tmp/geco /app/dbt

ENV GECO_DATA_DIR=/tmp/geco \
    GECO_DBT_PROJECT_DIR=/app/dbt \
    GECO_LOG_FORMAT=json
USER geco
ENTRYPOINT ["geco"]
CMD ["--help"]
