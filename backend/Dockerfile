FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Build tools needed for numpy/pandas wheel compilation on Python 3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ && rm -rf /var/lib/apt/lists/*

# uv for fast, reproducible installs
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml ./
RUN uv pip install --system --no-cache ".[scraping]"

# Install dev deps in a separate layer so production builds can skip them
ARG INSTALL_DEV=false
RUN if [ "$INSTALL_DEV" = "true" ]; then \
      uv pip install --system --no-cache pytest pytest-asyncio pytest-cov ruff mypy; \
    fi

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
