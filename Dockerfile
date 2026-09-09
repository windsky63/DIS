# syntax=docker/dockerfile:1.7

ARG NODE_IMAGE=node:24-bookworm-slim
ARG PYTHON_IMAGE=python:3.14-slim-bookworm

FROM ${NODE_IMAGE} AS frontend-builder

ARG NPM_REGISTRY=https://registry.npmjs.org

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm config set registry "${NPM_REGISTRY}" \
    && npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build


FROM ${PYTHON_IMAGE} AS runtime

ARG APP_VERSION=2.0.4
ARG BUILD_ID=local
ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    DRAWING_MARK_RECOGNITION_VERSION=${APP_VERSION} \
    DRAWING_MARK_RECOGNITION_BUILD_ID=${BUILD_ID}

LABEL org.opencontainers.image.title="图纸标识识别系统" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${BUILD_ID}"

WORKDIR /app

COPY backend/requirements.txt ./backend/requirements.txt
RUN python -m pip install --index-url "${PIP_INDEX_URL}" --requirement backend/requirements.txt

COPY backend/ ./backend/
COPY --from=frontend-builder /build/frontend/dist/ ./frontend/dist/

RUN groupadd --gid 10001 drawingmarker \
    && useradd --uid 10001 --gid drawingmarker --no-create-home --shell /usr/sbin/nologin drawingmarker \
    && mkdir -p /app/data/jobs /app/data/audit /app/backend/PCF \
    && chown -R drawingmarker:drawingmarker /app/data

USER drawingmarker

EXPOSE 8768
VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8768/api/health', timeout=3).read()"]

ENTRYPOINT ["python", "-u", "backend/server.py"]
CMD ["--host", "0.0.0.0", "--port", "8768"]
