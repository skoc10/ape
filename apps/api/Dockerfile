# Two images from one file (the Python package is shared):
#   docker build --target api    -t ape-api    apps/api
#   docker build --target worker -t ape-worker apps/api
FROM python:3.12-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY ape ./ape
RUN uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm AS base
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/ape /app/ape
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYDANTIC_AI_NO_BANNER=1
USER 10001:10001

# REST + agent WebSocket gateway. No PDF libraries here on purpose.
FROM base AS api
EXPOSE 8000
CMD ["uvicorn", "ape.server:app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

# Queue consumer: rules, LLM, reports. Pango and the embedded fonts are only needed here.
FROM base AS worker
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
 && apt-get clean
USER 10001:10001
EXPOSE 9091
CMD ["python", "-m", "ape.worker"]
