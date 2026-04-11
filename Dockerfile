FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    fastapi \
    "uvicorn[standard]" \
    websockify \
    pycryptodome \
    aiofiles \
    python-multipart \
    apscheduler

WORKDIR /app
RUN mkdir -p /app/static

# Download noVNC v1.6.0 — step by step for reliability
RUN curl -fSL -o /tmp/novnc.tar.gz https://github.com/novnc/noVNC/archive/refs/tags/v1.6.0.tar.gz
RUN tar xzf /tmp/novnc.tar.gz -C /tmp
RUN mv /tmp/noVNC-1.6.0 /app/static/novnc && rm -f /tmp/novnc.tar.gz

COPY app/ /app/app/
COPY static/index.html static/app.js static/style.css /app/static/

RUN mkdir -p /app/data

EXPOSE 8080
EXPOSE 6100-6199

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
