# Render Blueprint — see render.yaml. Repo-root Dockerfile, context `.` (Render
# looks for ./Dockerfile). Mirrors the maxapp deploy pattern (python:3.12-slim,
# same web-service shape) — Marque additionally needs a Node runtime because
# backend/main.py shells out to render/dist/lambda-render.js (the Remotion
# Lambda submit/poll bridge; Remotion's render API is Node-only).
FROM python:3.12-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r backend/requirements.txt

# Node deps + build the Lambda submit/poll bridge (dist/lambda-render.js) —
# main.py invokes this compiled file, not the TS source.
COPY render/package.json render/tsconfig.bridge.json ./render/
RUN cd render && npm install
COPY render/src ./render/src
RUN cd render && npm run build:bridge

COPY backend/ ./backend/

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

WORKDIR /app/backend
CMD sh -c 'uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}'
