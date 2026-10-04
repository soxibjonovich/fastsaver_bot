FROM python:3.13-slim

LABEL authors="maxa27"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# uv is not part of the base image. Pinned, and published as a manylinux wheel,
# so this needs no compiler.
RUN pip install --no-cache-dir uv==0.12.2

# Dependencies before source: source edits must not invalidate the install layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Explicit paths, never `COPY . .` — a blanket copy bakes .env into the image.
COPY run.py config.py ./
COPY bot ./bot

# Run the venv interpreter directly so startup needs neither uv nor network.
CMD ["/app/.venv/bin/python", "run.py"]
