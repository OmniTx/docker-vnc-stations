FROM python:3.11-slim

RUN pip install --no-cache-dir \
    fastapi \
    "uvicorn[standard]" \
    websockify \
    pycryptodome \
    aiofiles \
    python-multipart \
    apscheduler

WORKDIR /app

# Download noVNC using Python (handles GitHub redirects properly)
RUN python3 -c "import urllib.request; urllib.request.urlretrieve('https://github.com/novnc/noVNC/archive/refs/tags/v1.5.0.tar.gz', '/tmp/novnc.tar.gz')" && \
    tar xzf /tmp/novnc.tar.gz -C /tmp && \
    mv /tmp/noVNC-1.5.0 /app/static/novnc && \
    rm /tmp/novnc.tar.gz

COPY app/ /app/app/
COPY static/index.html static/app.js static/style.css /app/static/

RUN mkdir -p /app/data

EXPOSE 8080
EXPOSE 6100-6199

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
