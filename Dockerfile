# ═══════════════════════════════════════════════════════════════
# Stage 1: Builder — install deps with build tools
# ═══════════════════════════════════════════════════════════════
FROM python:3.11-alpine AS builder

RUN apk add --no-cache gcc musl-dev libffi-dev curl

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir \
    fastapi \
    "uvicorn[standard]" \
    websockify \
    pycryptodome \
    aiofiles \
    python-multipart \
    apscheduler

# Download noVNC v1.6.0
RUN curl -fSL -o /tmp/novnc.tar.gz \
    https://github.com/novnc/noVNC/archive/refs/tags/v1.6.0.tar.gz && \
    tar xzf /tmp/novnc.tar.gz -C /tmp && \
    mv /tmp/noVNC-1.6.0 /opt/novnc

# ═══════════════════════════════════════════════════════════════
# Stage 2: Runtime — minimal Alpine image
# ═══════════════════════════════════════════════════════════════
FROM python:3.11-alpine

RUN apk add --no-cache libffi

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/novnc /app/static/novnc

ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY app/ /app/app/
COPY static/index.html static/app.js static/style.css /app/static/
RUN mkdir -p /app/data

EXPOSE 80
EXPOSE 6100-6199

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80"]
