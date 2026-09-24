# Engine plus the production UI, one process on port 8811.
# Profile and state are mounts, not part of the image.

FROM node:22-bookworm-slim AS ui
WORKDIR /src/ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
# Empty origin: the browser calls /agui, /profile, … on this same server.
ENV VITE_API_URL=
RUN npm run build

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    HARNESS_UI_DIR=/app/ui \
    PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
COPY bizharness ./bizharness
RUN uv sync --frozen --no-dev

COPY --from=ui /src/ui/dist /app/ui
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh \
    && useradd --create-home --uid 1000 harness
USER harness

EXPOSE 8811
ENTRYPOINT ["/entrypoint.sh"]
