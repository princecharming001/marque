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
#
# CRITICAL: use the LOCKFILE (npm ci), not `npm install`. The deployed Remotion
# Lambda function is version-pinned in its name (remotion-render-4-0-484-…), and
# @remotion/lambda's client MUST match that version exactly. package.json carries
# caret ranges (^4.0.0), so a fresh `npm install` here pulled whatever 4.0.x was
# newest at build time (e.g. 4.0.486) → client/function skew → renderMediaOnLambda
# hung and every clip failed with "bridge timed out". The lockfile pins 4.0.484,
# so `npm ci` reproduces exactly the version the function was deployed with.
COPY render/package.json render/package-lock.json render/tsconfig.bridge.json ./render/
RUN cd render && npm ci
COPY render/src ./render/src
RUN cd render && npm run build:bridge

COPY backend/ ./backend/

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

WORKDIR /app/backend
CMD sh -c 'uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}'
