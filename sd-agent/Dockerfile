FROM python:3.11.13-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN groupadd --system --gid 10001 sdplatform \
    && useradd --system --uid 10001 --gid sdplatform --home-dir /app sdplatform

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.11.31 /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY sd_agent ./sd_agent
COPY alembic.ini ./
COPY migrations ./migrations
RUN uv sync --frozen --no-dev

USER 10001:10001
EXPOSE 8000

CMD ["uv", "run", "uvicorn", "sd_agent.api_main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
