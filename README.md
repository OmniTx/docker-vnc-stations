# VNC Monitor Dashboard

A self-hosted, Dockerized web application for monitoring and controlling multiple VNC-connected remote desktops from a single browser tab. Built as a browser-based alternative to MightyViewer.

## Deployment

### Option 1: Docker Compose (CLI)

```bash
git clone https://github.com/OmniTx/docker-vnc-stations.git
cd docker-vnc-stations
docker compose up --build -d
```

### Option 2: Portainer (Recommended)

1. In Portainer, go to **Stacks** → **Add stack**
2. Select **Repository** as the build method
3. Set the repository URL to your GitHub repo: `https://github.com/OmniTx/docker-vnc-stations`
4. Branch: `main`
5. Compose path: `docker-compose.yml`
6. (Optional) Add environment variables:
   - `VNC_ENCRYPTION_KEY` = your secret key
   - `HEALTH_CHECK_INTERVAL` = `30`
7. Click **Deploy the stack**

### Access

Open **http://YOUR_SERVER_IP:8080** in your browser.

> **Important:** The container exposes ports **8080** (web UI) and **6100–6199** (websockify proxies).
> Make sure these ports are not blocked by your server's firewall.

## Features

- **Multi-device grid monitoring** — Live VNC thumbnails in a responsive grid, organized by groups
- **Full remote control** — Double-click any tile to open full interactive control with keyboard/mouse
- **MightyViewer import/export** — Import your existing `MightyViewerStations.json` backup directly
- **Group management** — Collapsible group sections with color labels, drag-and-drop reordering
- **Health monitoring** — Automatic TCP ping checks with real-time status indicators (SSE)
- **Auto-reconnect** — Exponential backoff reconnection (5s → 10s → 30s)
- **Clipboard exchange** — Two-way clipboard sync between local and remote
- **Screenshot capture** — Download current remote screen as PNG
- **Quality controls** — Adjustable compression/quality for both thumbnails and full control
- **Search & filter** — Real-time search by name/IP, filter by online/offline status
- **Settings panel** — Grid columns, quality, health check interval, basic auth
- **Import/Export** — Full MightyViewer-compatible round-trip import/export

## How to Add Your First Device

1. Click the **+ Add** button in the header toolbar
2. Enter the device name, host/IP address, and VNC port (default: 5900)
3. Optionally set a VNC password, group name, and group color
4. Click **Save Device**

The dashboard will automatically start a websockify proxy and attempt to connect.

## MightyViewer Import

1. Click the **⚙ Settings** gear icon in the header
2. Scroll to **Import / Export**
3. Drag your `MightyViewerStations.json` file onto the drop zone (or click to browse)
4. Review the import preview — see new devices, duplicates, and groups
5. Click **Import Selected**

## How websockify Port Range Works

Each VNC device gets its own websockify proxy process:

| Component | Port Range | Description |
|-----------|-----------|-------------|
| Web UI | 8080 | FastAPI + static frontend |
| websockify | 6100–6199 | One port per VNC device (max 100 devices) |

The backend dynamically assigns ports from this pool. When a device is added, the next available port (starting from 6100) is allocated. When removed, the port is freed.

## Exposing on LAN

The dashboard is accessible from any device on your LAN. Just use your server's LAN IP:

```
http://192.168.1.100:8080
```

Make sure ports **8080** and **6100-6199** are accessible on your firewall.

## Basic Auth

To protect the web UI with a password:

1. Open **Settings** → **Security**
2. Set a username and password
3. Click **Save Settings**

The browser will prompt for credentials on the next page load. To disable auth, clear the password field.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VNC_ENCRYPTION_KEY` | `vnc-monitor-default-key-change-me` | AES-256 encryption key for stored VNC passwords |
| `HEALTH_CHECK_INTERVAL` | `30` | Seconds between health check pings |

Set these in `docker-compose.yml` or `.env` file.

## Architecture

```
Browser ──→ :8080 (FastAPI) ──→ REST API + Static Files
   │
   └──→ :61XX (websockify) ──→ VNC Server :5900
         (one per device)
```

- **Backend**: Python FastAPI + SQLite + APScheduler
- **Frontend**: Vanilla JS + noVNC library (ES modules)
- **Proxy**: websockify (spawned as subprocess per device)
- **Database**: SQLite (`./data/devices.db`, persisted via Docker volume)

## File Structure

```
├── docker-compose.yml
├── Dockerfile
├── app/
│   ├── main.py              # FastAPI app + SSE + middleware
│   ├── models.py            # Pydantic models
│   ├── database.py          # SQLite CRUD
│   ├── routes/
│   │   ├── devices.py       # CRUD + import/export + settings
│   │   └── proxy.py         # websockify management
│   └── services/
│       ├── crypto.py        # AES-256 password encryption
│       ├── health.py        # Background health checker
│       └── proxy_manager.py # websockify subprocess manager
├── static/
│   ├── index.html           # SPA shell
│   ├── app.js               # Frontend application
│   ├── style.css            # IBM Carbon-inspired dark theme
│   └── novnc/               # noVNC library (cloned at build)
└── data/
    └── devices.db            # SQLite database (created at startup)
```

## License

MIT
