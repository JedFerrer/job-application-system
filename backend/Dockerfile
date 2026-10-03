FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Build tools needed for numpy/pandas wheel compilation on Python 3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ && rm -rf /var/lib/apt/lists/*

# uv for fast, reproducible installs
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml uv.lock ./

# Install the locked graph first to preserve caching when application code changes.
ARG INSTALL_DEV=false
RUN if [ "$INSTALL_DEV" = "true" ]; then \
      uv sync --locked --no-cache --extra scraping --group dev --no-editable --no-install-project; \
    else \
      uv sync --locked --no-cache --extra scraping --no-dev --no-editable --no-install-project; \
    fi

COPY . .

# Install the application non-editably into the same locked environment.
RUN if [ "$INSTALL_DEV" = "true" ]; then \
      uv sync --locked --no-cache --extra scraping --group dev --no-editable; \
    else \
      uv sync --locked --no-cache --extra scraping --no-dev --no-editable; \
    fi

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
