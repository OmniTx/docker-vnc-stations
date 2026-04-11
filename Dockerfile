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

RUN curl -sL https://github.com/novnc/noVNC/archive/refs/tags/v1.6.0.tar.gz | tar xz -C /tmp && \
    mv /tmp/noVNC-1.6.0 /app/static/novnc

COPY app/ /app/app/
COPY static/index.html static/app.js static/style.css /app/static/

RUN mkdir -p /app/data

EXPOSE 8080
EXPOSE 6100-6199

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
