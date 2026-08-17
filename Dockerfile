FROM node:24-alpine AS frontend-build

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime

ARG WOS_APP_VERSION

LABEL org.opencontainers.image.title="World of Seeds" \
    org.opencontainers.image.version="${WOS_APP_VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/backend \
    WOS_STATIC_ROOT=/app/static

RUN groupadd --gid 10001 worldofseeds \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin worldofseeds

WORKDIR /app
COPY backend/ ./backend/
RUN pip install --no-cache-dir --require-hashes -r ./backend/requirements.lock
COPY --from=frontend-build /build/frontend/dist ./static

USER 10001:10001
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-server-header"]
