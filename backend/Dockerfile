# RhoGuard backend. Works on any container host (Hugging Face Spaces, Render, Fly, Railway).
#   docker build -t rhoguard-api . && docker run -p 8000:8000 -e PORT=8000 -e RHO_API_KEY=sandbox-any-token rhoguard-api
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1 \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dependencies first so the layer caches across code changes ([tool.uv] package = false,
# so the lockfile alone is enough to build the venv).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app ./app
COPY fixtures ./fixtures

# Hugging Face Spaces run as uid 1000 and need a writable cwd for STATE_DIR.
RUN useradd -m -u 1000 app && chown -R app:app /app
USER app

# Hugging Face Spaces default to 7860; Render/Railway inject PORT.
ENV PORT=7860
EXPOSE 7860
CMD ["sh", "-c", "uv run --no-sync uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
