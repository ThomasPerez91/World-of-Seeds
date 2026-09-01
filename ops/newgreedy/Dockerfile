# syntax=docker/dockerfile:1.7

ARG PYTHON_BASE_IMAGE
FROM ${PYTHON_BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock ./requirements.lock
RUN python -m pip install \
    --no-cache-dir \
    --no-compile \
    --require-hashes \
    --requirement requirements.lock

COPY . .

RUN test -f newgreedy.py \
    && test -f newgreedy_addon.py \
    && test -f newgreedy_web.py \
    && test -f static/index.html \
    && test ! -e config.ini \
    && test ! -e stats.json \
    && test ! -e torrent_registry.json \
    && test ! -e newgreedy.log \
    && test ! -e .git \
    && test ! -e /root/.mitmproxy

EXPOSE 3456 8080

HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=6 \
    CMD curl -fsS http://localhost:8080/api/health >/dev/null

CMD ["python3", "newgreedy.py"]
