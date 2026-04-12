/* ═══════════════════════════════════════════════════════════════════════
   VNC Monitor Dashboard — Frontend Application
   ═══════════════════════════════════════════════════════════════════════ */

import RFB from '/static/novnc/core/rfb.js';

// ═══════════════════════════════════════════════════════════════════════
//  STATE
// ═══════════════════════════════════════════════════════════════════════

const state = {
    devices: [],
    groups: [],
    settings: {},
    rfbInstances: {},      // deviceId -> RFB instance
    reconnectTimers: {},   // deviceId -> timeout id
    reconnectAttempts: {}, // deviceId -> attempt count
    collapsedGroups: new Set(),
    fullControlDeviceId: null,
    fullControlRfb: null,
    remoteClipboard: '',
    dragDeviceId: null,
    sse: null,
    importPreview: null,
    lastSseProxyIds: new Set(),
};

// ═══════════════════════════════════════════════════════════════════════
//  API CLIENT
// ═══════════════════════════════════════════════════════════════════════

const api = {
    async _fetch(url, opts = {}) {
        const res = await fetch(url, {
            headers: { 'Content-Type': 'application/json', ...opts.headers },
            ...opts,
        });
        if (!res.ok) {
            const text = await res.text().catch(() => res.statusText);
            throw new Error(`${res.status}: ${text}`);
        }
        const ct = res.headers.get('content-type') || '';
        if (ct.includes('application/json')) return res.json();
        return res;
    },
    getDevices()            { return this._fetch('/api/devices'); },
    createDevice(data)      { return this._fetch('/api/devices', { method: 'POST', body: JSON.stringify(data) }); },
    updateDevice(id, data)  { return this._fetch(`/api/devices/${id}`, { method: 'PUT', body: JSON.stringify(data) }); },
    deleteDevice(id)        { return this._fetch(`/api/devices/${id}`, { method: 'DELETE' }); },
    testDevice(id)          { return this._fetch(`/api/devices/${id}/test`, { method: 'POST' }); },
    getToken(id)            { return this._fetch(`/api/devices/${id}/token`); },
    reorderDevices(updates) { return this._fetch('/api/devices/reorder', { method: 'PUT', body: JSON.stringify({ updates }) }); },
    getGroups()             { return this._fetch('/api/groups'); },
    createGroup(data)       { return this._fetch('/api/groups', { method: 'POST', body: JSON.stringify(data) }); },
    updateGroup(id, data)   { return this._fetch(`/api/groups/${id}`, { method: 'PUT', body: JSON.stringify(data) }); },
    deleteGroup(id)         { return this._fetch(`/api/groups/${id}`, { method: 'DELETE' }); },
    getSettings()           { return this._fetch('/api/settings'); },
    updateSettings(data)    { return this._fetch('/api/settings', { method: 'PUT', body: JSON.stringify(data) }); },
    importJson(file) {
        const form = new FormData();
        form.append('file', file);
        return this._fetch('/api/import/json', { method: 'POST', body: form, headers: {} });
    },
    confirmImport(data) { return this._fetch('/api/import/json/confirm', { method: 'POST', body: JSON.stringify(data) }); },
    startProxy(id)  { return this._fetch(`/api/proxy/${id}/start`, { method: 'POST' }); },
    stopProxy(id)   { return this._fetch(`/api/proxy/${id}/stop`, { method: 'POST' }); },
};

// ═══════════════════════════════════════════════════════════════════════
//  UTILITIES
// ═══════════════════════════════════════════════════════════════════════

function $(sel) { return document.querySelector(sel); }
function $$(sel) { return document.querySelectorAll(sel); }

function toast(msg, type = 'info') {
    const container = $('#toast-container');
    const el = document.createElement('div');
    el.className = `toast toast--${type}`;
    el.innerHTML = `<span>${msg}</span><button class="toast-close">&times;</button>`;
    container.appendChild(el);
    el.querySelector('.toast-close').onclick = () => el.remove();
    setTimeout(() => { if (el.parentNode) el.remove(); }, 5000);
}

function formatTimestamp() {
    const d = new Date();
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}_${pad(d.getHours())}-${pad(d.getMinutes())}-${pad(d.getSeconds())}`;
}

// ═══════════════════════════════════════════════════════════════════════
//  DASHBOARD RENDERING
// ═══════════════════════════════════════════════════════════════════════

function getFilteredDevices() {
    let devices = [...state.devices];
    const search = ($('#search-input')?.value || '').toLowerCase().trim();
    const filter = $('#filter-select')?.value || 'all';

    if (search) {
        devices = devices.filter(d =>
            d.name.toLowerCase().includes(search) ||
            d.host.toLowerCase().includes(search)
        );
    }
    if (filter === 'online') {
        devices = devices.filter(d => d.health_status === 'online');
    } else if (filter === 'offline') {
        devices = devices.filter(d => d.health_status !== 'online');
    }
    return devices;
}

function groupDevices(devices) {
    const grouped = {};
    for (const d of devices) {
        const g = d.group_name || 'Ungrouped';
        if (!grouped[g]) grouped[g] = [];
        grouped[g].push(d);
    }
    // Sort groups by the groups list order, then alphabetically
    const groupOrder = state.groups.map(g => g.name);
    const sortedKeys = Object.keys(grouped).sort((a, b) => {
        const ia = groupOrder.indexOf(a);
        const ib = groupOrder.indexOf(b);
        if (ia >= 0 && ib >= 0) return ia - ib;
        if (ia >= 0) return -1;
        if (ib >= 0) return 1;
        return a.localeCompare(b);
    });
    const result = {};
    for (const k of sortedKeys) result[k] = grouped[k];
    return result;
}

function getGroupColor(groupName) {
    const g = state.groups.find(g => g.name === groupName);
    if (g) return g.color;
    const d = state.devices.find(d => d.group_name === groupName);
    return d?.group_color || '#4589ff';
}

function renderDashboard() {
    const container = $('#device-grid-container');
    const emptyState = $('#empty-state');
    const filteredDevices = getFilteredDevices();

    if (state.devices.length === 0) {
        container.innerHTML = '';
        emptyState.style.display = 'flex';
        return;
    }
    emptyState.style.display = 'none';

    if (filteredDevices.length === 0) {
        container.innerHTML = '<div class="empty-state"><p>No devices match your search or filter.</p></div>';
        return;
    }

    const grouped = groupDevices(filteredDevices);
    let html = '';

    for (const [groupName, devices] of Object.entries(grouped)) {
        const isCollapsed = state.collapsedGroups.has(groupName);
        const color = getGroupColor(groupName);

        html += `<div class="group-section" data-group="${esc(groupName)}">`;
        html += `<div class="group-header ${isCollapsed ? 'collapsed' : ''}" data-group="${esc(groupName)}">`;
        html += `<svg class="group-chevron" width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M4 6l4 4 4-4" stroke="currentColor" stroke-width="2"/></svg>`;
        html += `<div class="group-color-bar" style="background:${esc(color)}"></div>`;
        html += `<span class="group-name">${esc(groupName)}</span>`;
        html += `<span class="group-count">(${devices.length})</span>`;
        html += `</div>`;

        html += `<div class="group-body" style="${isCollapsed ? 'max-height:0' : ''}">`;
        html += `<div class="device-grid">`;

        for (const device of devices) {
            const status = device.health_status || 'unknown';
            html += `<div class="device-tile" data-id="${device.id}" data-status="${status}" draggable="true">`;
            html += `<div class="tile-screen" id="tile-screen-${device.id}" style="pointer-events:none">`;
            html += `<div class="tile-overlay" id="tile-overlay-${device.id}">`;
            html += `<div class="tile-spinner"></div>`;
            html += `<span id="tile-status-text-${device.id}">Connecting...</span>`;
            html += `</div></div>`;
            html += `<div class="tile-bottom">`;
            html += `<div class="tile-info">`;
            html += `<span class="status-dot status-dot--${status}"></span>`;
            html += `<span class="tile-name" title="${esc(device.name)}">${esc(device.name)}</span>`;
            html += `<span class="tile-host">${esc(device.host)}</span>`;
            html += `</div>`;
            html += `<div class="tile-actions">`;
            html += `<button class="tile-control-btn" data-id="${device.id}" title="Open Full Screen">▶ Open</button>`;
            html += `<button class="tile-menu-btn" data-id="${device.id}" title="Options">⋮</button>`;
            html += `</div>`;
            html += `</div></div>`;
        }

        html += `</div></div></div>`;
    }

    container.innerHTML = html;
    updateOnlineCount();
    applyGridColumns();
    attachTileEvents();
    connectAllTiles();
}

function esc(str) {
    const el = document.createElement('span');
    el.textContent = str;
    return el.innerHTML;
}

function updateOnlineCount() {
    const total = state.devices.length;
    const online = state.devices.filter(d => d.health_status === 'online').length;
    const el = $('#online-count-text');
    if (el) el.textContent = `${online}/${total} Online`;
}

function applyGridColumns() {
    const cols = state.settings.grid_columns || 'auto';
    const grids = $$('.device-grid');
    const tileMin = $('#zoom-slider')?.value || 280;

    grids.forEach(grid => {
        if (cols === 'auto') {
            grid.style.gridTemplateColumns = `repeat(auto-fill, minmax(${tileMin}px, 1fr))`;
        } else {
            grid.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
        }
    });
}

// ═══════════════════════════════════════════════════════════════════════
//  TILE EVENTS
// ═══════════════════════════════════════════════════════════════════════

function attachTileEvents() {
    // Group header toggle
    $$('.group-header').forEach(header => {
        header.addEventListener('click', () => {
            const group = header.dataset.group;
            if (state.collapsedGroups.has(group)) {
                state.collapsedGroups.delete(group);
                header.classList.remove('collapsed');
                header.nextElementSibling.style.maxHeight = '';
            } else {
                state.collapsedGroups.add(group);
                header.classList.add('collapsed');
                header.nextElementSibling.style.maxHeight = '0';
            }
        });
    });

    // Full Control button on each tile
    $$('.tile-control-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            openFullControl(parseInt(btn.dataset.id));
        });
    });

    $$('.device-tile').forEach(tile => {

        // Context menu on three-dot button
        const menuBtn = tile.querySelector('.tile-menu-btn');
        if (menuBtn) {
            menuBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                showContextMenu(e, parseInt(menuBtn.dataset.id));
            });
        }

        // Right-click context menu
        tile.addEventListener('contextmenu', (e) => {
            e.preventDefault();
            showContextMenu(e, parseInt(tile.dataset.id));
        });

        // Double click to open
        tile.addEventListener('dblclick', () => {
            openFullControl(parseInt(tile.dataset.id));
        });

        // Drag & Drop
        tile.addEventListener('dragstart', (e) => {
            state.dragDeviceId = parseInt(tile.dataset.id);
            tile.classList.add('dragging');
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', tile.dataset.id);
        });
        tile.addEventListener('dragend', () => {
            tile.classList.remove('dragging');
            state.dragDeviceId = null;
            $$('.drag-over').forEach(el => el.classList.remove('drag-over'));
        });
        tile.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
            tile.classList.add('drag-over');
        });
        tile.addEventListener('dragleave', () => {
            tile.classList.remove('drag-over');
        });
        tile.addEventListener('drop', (e) => {
            e.preventDefault();
            tile.classList.remove('drag-over');
            const fromId = state.dragDeviceId;
            const toId = parseInt(tile.dataset.id);
            if (fromId && fromId !== toId) {
                handleDrop(fromId, toId);
            }
        });
    });
}

async function handleDrop(fromId, toId) {
    const fromDevice = state.devices.find(d => d.id === fromId);
    const toDevice = state.devices.find(d => d.id === toId);
    if (!fromDevice || !toDevice) return;

    // Move fromDevice to toDevice's group and position
    const updates = [];
    const targetGroup = toDevice.group_name;
    const devicesInGroup = state.devices
        .filter(d => d.group_name === targetGroup)
        .sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0));

    // Insert fromDevice at toDevice's position
    const targetIdx = devicesInGroup.findIndex(d => d.id === toId);
    const filtered = devicesInGroup.filter(d => d.id !== fromId);
    filtered.splice(targetIdx, 0, fromDevice);

    for (let i = 0; i < filtered.length; i++) {
        updates.push({
            device_id: filtered[i].id,
            sort_order: i,
            group_name: targetGroup,
        });
    }

    try {
        await api.reorderDevices(updates);
        await refreshDevices();
        toast('Device reordered', 'success');
    } catch (err) {
        toast('Failed to reorder: ' + err.message, 'error');
    }
}

// ═══════════════════════════════════════════════════════════════════════
//  noVNC TILE CONNECTIONS
// ═══════════════════════════════════════════════════════════════════════

function getQualitySettings(mode) {
    // mode: 'tile' | 'full-low' | 'full-medium' | 'full-high' | 'full-auto'
    switch (mode) {
        case 'tile':        return { quality: 2, compression: 6 };
        case 'full-low':    return { quality: 2, compression: 8 };
        case 'full-medium': return { quality: 5, compression: 4 };
        case 'full-high':   return { quality: 9, compression: 0 };
        case 'full-auto':
        default:            return { quality: 6, compression: 2 };
    }
}

async function connectTile(device) {
    const screenEl = $(`#tile-screen-${device.id}`);
    const overlayEl = $(`#tile-overlay-${device.id}`);
    const statusTextEl = $(`#tile-status-text-${device.id}`);

    if (!screenEl) return;

    // Disconnect previous instance
    disconnectTile(device.id);

    if (!device.ws_port) {
        if (statusTextEl) statusTextEl.textContent = 'No proxy';
        return;
    }

    if (statusTextEl) statusTextEl.textContent = 'Connecting...';
    if (overlayEl) overlayEl.classList.remove('connected');

    const wsUrl = `ws://${location.hostname}:${device.ws_port}`;
    let password = '';

    try {
        if (device.has_password) {
            const tokenRes = await api.getToken(device.id);
            password = tokenRes.password || '';
        }
    } catch (err) {
        console.warn('Token fetch failed:', err);
    }

    const opts = { shared: true };
    if (password) opts.credentials = { password };

    try {
        const rfb = new RFB(screenEl, wsUrl, opts);
        rfb.viewOnly = true;
        rfb.scaleViewport = true;
        rfb.background = '#0a0a0a';

        const qs = getQualitySettings('tile');
        rfb.qualityLevel = qs.quality;
        rfb.compressionLevel = qs.compression;

        rfb.addEventListener('connect', () => {
            if (overlayEl) overlayEl.classList.add('connected');
            state.reconnectAttempts[device.id] = 0;
        });

        rfb.addEventListener('disconnect', (e) => {
            if (overlayEl) overlayEl.classList.remove('connected');
            if (statusTextEl) statusTextEl.textContent = e.detail.clean ? 'Disconnected' : 'Connection lost';
            delete state.rfbInstances[device.id];
            maybeReconnect(device);
        });

        rfb.addEventListener('credentialsrequired', () => {
            if (password) {
                rfb.sendCredentials({ password });
            } else {
                if (statusTextEl) statusTextEl.textContent = 'Password required';
            }
        });

        rfb.addEventListener('clipboard', (e) => {
            state.remoteClipboard = e.detail.text;
        });

        state.rfbInstances[device.id] = rfb;
    } catch (err) {
        console.error('noVNC error for', device.name, err);
        if (statusTextEl) statusTextEl.textContent = 'Connection error';
        maybeReconnect(device);
    }
}

function disconnectTile(deviceId) {
    const rfb = state.rfbInstances[deviceId];
    if (rfb) {
        try { rfb.disconnect(); } catch (e) { /* ignore */ }
        delete state.rfbInstances[deviceId];
    }
    clearReconnect(deviceId);
}

function connectAllTiles() {
    for (const device of state.devices) {
        if (device.enabled && device.ws_port) {
            connectTile(device);
        }
    }
}

function disconnectAllTiles() {
    for (const id of Object.keys(state.rfbInstances)) {
        disconnectTile(parseInt(id));
    }
}

// ═══════════════════════════════════════════════════════════════════════
//  AUTO-RECONNECT
// ═══════════════════════════════════════════════════════════════════════

function maybeReconnect(device) {
    if (state.settings.auto_reconnect === 'false') return;
    if (!device.enabled) return;

    const attempts = (state.reconnectAttempts[device.id] || 0);
    const delays = [5000, 10000, 30000];
    const delay = delays[Math.min(attempts, delays.length - 1)];

    state.reconnectAttempts[device.id] = attempts + 1;

    const statusText = $(`#tile-status-text-${device.id}`);
    if (statusText) statusText.textContent = `Reconnecting in ${delay/1000}s...`;

    state.reconnectTimers[device.id] = setTimeout(() => {
        const current = state.devices.find(d => d.id === device.id);
        if (current && current.enabled) {
            connectTile(current);
        }
    }, delay);
}

function clearReconnect(deviceId) {
    if (state.reconnectTimers[deviceId]) {
        clearTimeout(state.reconnectTimers[deviceId]);
        delete state.reconnectTimers[deviceId];
    }
}

// ═══════════════════════════════════════════════════════════════════════
//  FULL CONTROL MODE
// ═══════════════════════════════════════════════════════════════════════

async function openFullControl(deviceId) {
    const device = state.devices.find(d => d.id === deviceId);
    if (!device) return;

    // Ensure proxy is running
    if (!device.ws_port) {
        try {
            const res = await api.startProxy(deviceId);
            device.ws_port = res.ws_port;
        } catch (err) {
            toast('Cannot start proxy: ' + err.message, 'error');
            return;
        }
    }

    const overlay = $('#modal-fullcontrol');
    const screen = $('#fullcontrol-screen');
    const nameEl = $('#fc-device-name');
    const statusEl = $('#fc-status');

    nameEl.textContent = device.name;
    statusEl.textContent = 'Connecting...';
    screen.innerHTML = '';
    overlay.style.display = 'flex';
    state.fullControlDeviceId = deviceId;

    const wsUrl = `ws://${location.hostname}:${device.ws_port}`;
    let password = '';

    try {
        if (device.has_password) {
            const tokenRes = await api.getToken(deviceId);
            password = tokenRes.password || '';
        }
    } catch (err) {
        console.warn('Token fetch failed:', err);
    }

    const opts = { shared: true };
    if (password) opts.credentials = { password };

    try {
        const rfb = new RFB(screen, wsUrl, opts);
        rfb.showDotCursor = true; // Enables remote/dot cursor
        rfb.viewOnly = true; // Default to view-only for Full Screen
        
        const toggleText = $('#fc-toggle-control-text');
        if (toggleText) toggleText.textContent = 'Enable Control';

        rfb.scaleViewport = true;
        rfb.resizeSession = false;
        rfb.background = '#000';

        const qs = getQualitySettings('full-auto');
        rfb.qualityLevel = qs.quality;
        rfb.compressionLevel = qs.compression;

        rfb.addEventListener('connect', () => {
            statusEl.textContent = 'Connected';
            statusEl.style.color = 'var(--success)';
        });

        rfb.addEventListener('disconnect', (e) => {
            statusEl.textContent = e.detail.clean ? 'Disconnected' : 'Connection lost';
            statusEl.style.color = 'var(--danger)';
        });

        rfb.addEventListener('credentialsrequired', () => {
            if (password) rfb.sendCredentials({ password });
            else statusEl.textContent = 'Password required';
        });

        rfb.addEventListener('clipboard', (e) => {
            state.remoteClipboard = e.detail.text;
        });

        // FPS Tracking
        let frames = 0;
        let lastTime = performance.now();
        const metricsEl = $('#fc-metrics');
        if (metricsEl) metricsEl.textContent = '-- FPS';

        rfb.addEventListener('FBUComplete', () => {
            frames++;
            const now = performance.now();
            if (now - lastTime >= 1000) {
                const fps = Math.round((frames * 1000) / (now - lastTime));
                if (metricsEl) metricsEl.textContent = `${fps} FPS`;
                frames = 0;
                lastTime = now;
            }
        });

        state.fullControlRfb = rfb;

        // Focus the remote session
        setTimeout(() => rfb.focus(), 200);
    } catch (err) {
        statusEl.textContent = 'Connection failed';
        toast('Full control connection error: ' + err.message, 'error');
    }
}

function closeFullControl() {
    if (state.fullControlRfb) {
        try { state.fullControlRfb.disconnect(); } catch (e) { /* ignore */ }
        state.fullControlRfb = null;
    }
    state.fullControlDeviceId = null;
    $('#modal-fullcontrol').style.display = 'none';
    $('#fullcontrol-screen').innerHTML = '';
    $('#fc-status').style.color = '';
}

function setFullControlQuality(mode) {
    if (!state.fullControlRfb) return;
    const qs = getQualitySettings('full-' + mode);
    state.fullControlRfb.qualityLevel = qs.quality;
    state.fullControlRfb.compressionLevel = qs.compression;
}

// ═══════════════════════════════════════════════════════════════════════
//  SCREENSHOT
// ═══════════════════════════════════════════════════════════════════════

function takeScreenshot() {
    if (!state.fullControlRfb) return;
    const device = state.devices.find(d => d.id === state.fullControlDeviceId);
    const name = device?.name || 'Screenshot';
    const filename = `${name.replace(/[^a-zA-Z0-9]/g, '_')}_${formatTimestamp()}.png`;

    state.fullControlRfb.toBlob((blob) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        a.click();
        URL.revokeObjectURL(url);
        toast('Screenshot saved: ' + filename, 'success');
    }, 'image/png');
}

// ═══════════════════════════════════════════════════════════════════════
//  CLIPBOARD EXCHANGE
// ═══════════════════════════════════════════════════════════════════════

function openClipboardModal() {
    $('#modal-clipboard').style.display = 'flex';
    $('#clipboard-text').value = '';
    $('#clipboard-remote-text').style.display = 'none';
}

function sendClipboardToRemote() {
    if (!state.fullControlRfb) return;
    const text = $('#clipboard-text').value;
    if (!text) return;
    state.fullControlRfb.clipboardPasteFrom(text);
    toast('Text sent to remote clipboard', 'success');
}

function receiveClipboardFromRemote() {
    if (state.remoteClipboard) {
        $('#clipboard-remote-content').textContent = state.remoteClipboard;
        $('#clipboard-remote-text').style.display = 'block';
        navigator.clipboard.writeText(state.remoteClipboard).then(() => {
            toast('Remote clipboard copied to local', 'success');
        }).catch(() => {
            toast('Clipboard shown below — copy manually', 'warning');
        });
    } else {
        toast('No clipboard data from remote', 'warning');
    }
}

// ═══════════════════════════════════════════════════════════════════════
//  CONTEXT MENU
// ═══════════════════════════════════════════════════════════════════════

let contextMenuDeviceId = null;

function showContextMenu(e, deviceId) {
    contextMenuDeviceId = deviceId;
    const menu = $('#context-menu');
    menu.style.display = 'block';

    // Position
    let x = e.clientX || e.pageX;
    let y = e.clientY || e.pageY;
    menu.style.left = `${x}px`;
    menu.style.top = `${y}px`;

    // Adjust if off-screen
    requestAnimationFrame(() => {
        const rect = menu.getBoundingClientRect();
        if (rect.right > window.innerWidth) menu.style.left = `${x - rect.width}px`;
        if (rect.bottom > window.innerHeight) menu.style.top = `${y - rect.height}px`;
    });

    // Update "Enable Control" label based on view_only status
    const device = state.devices.find(d => d.id === deviceId);
    const enableCtrlItem = menu.querySelector('[data-action="enable-control"]');
    if (enableCtrlItem && device) {
        const rfb = state.rfbInstances[deviceId];
        const isViewOnly = rfb?.viewOnly ?? true;
        enableCtrlItem.innerHTML = isViewOnly
            ? `<svg width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M3 8l3 3 7-7" stroke="currentColor" stroke-width="2" stroke-linecap="square"/></svg> Enable Control`
            : `<svg width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M3 8l3 3 7-7" stroke="currentColor" stroke-width="2" stroke-linecap="square"/></svg> Disable Control`;
    }
}

function hideContextMenu() {
    $('#context-menu').style.display = 'none';
    contextMenuDeviceId = null;
}

async function handleContextAction(action) {
    const deviceId = contextMenuDeviceId;
    hideContextMenu();
    if (!deviceId) return;

    const device = state.devices.find(d => d.id === deviceId);
    if (!device && action !== 'remove') return;

    switch (action) {
        case 'edit':
            showEditDeviceModal(device);
            break;
        case 'fullcontrol':
            openFullControl(deviceId);
            break;
        case 'test':
            try {
                const res = await api.testDevice(deviceId);
                toast(`${device.name}: ${res.status}`, res.status === 'online' ? 'success' : 'warning');
                await refreshDevices();
            } catch (err) {
                toast('Test failed: ' + err.message, 'error');
            }
            break;
        case 'enable-control': {
            const rfb = state.rfbInstances[deviceId];
            if (rfb) {
                rfb.viewOnly = !rfb.viewOnly;
                const screenEl = document.getElementById(`tile-screen-${deviceId}`);
                if (screenEl) {
                    screenEl.style.pointerEvents = rfb.viewOnly ? 'none' : 'auto';
                }
                toast(rfb.viewOnly ? 'Tile Control Disabled' : 'Tile Control Enabled', 'info');
            }
            break;
        }
        case 'remove':
            if (confirm(`Remove device "${device?.name || deviceId}"?`)) {
                try {
                    disconnectTile(deviceId);
                    await api.deleteDevice(deviceId);
                    await refreshDevices();
                    toast('Device removed', 'success');
                } catch (err) {
                    toast('Failed to remove: ' + err.message, 'error');
                }
            }
            break;
    }
}

// ═══════════════════════════════════════════════════════════════════════
//  DEVICE MODALS (ADD / EDIT)
// ═══════════════════════════════════════════════════════════════════════

function showAddDeviceModal() {
    $('#modal-device-title').textContent = 'Add Device';
    $('#device-edit-id').value = '';
    $('#device-name').value = '';
    $('#device-host').value = '';
    $('#device-port').value = state.settings.vnc_default_port || '5900';
    $('#device-password').value = '';
    $('#device-group').value = 'Ungrouped';
    $('#device-color').value = '#4589ff';
    $('#device-viewonly').checked = false;
    $('#device-enabled').checked = true;
    updateGroupDatalist();
    $('#modal-device').style.display = 'flex';
}

function showEditDeviceModal(device) {
    $('#modal-device-title').textContent = 'Edit Device';
    $('#device-edit-id').value = device.id;
    $('#device-name').value = device.name;
    $('#device-host').value = device.host;
    $('#device-port').value = device.port;
    $('#device-password').value = '';
    $('#device-group').value = device.group_name;
    $('#device-color').value = device.group_color || '#4589ff';
    $('#device-viewonly').checked = Boolean(device.view_only);
    $('#device-enabled').checked = Boolean(device.enabled);
    updateGroupDatalist();
    $('#modal-device').style.display = 'flex';
}

function updateGroupDatalist() {
    const dl = $('#group-list');
    dl.innerHTML = '';
    const names = new Set(state.devices.map(d => d.group_name).filter(Boolean));
    state.groups.forEach(g => names.add(g.name));
    names.forEach(n => {
        const opt = document.createElement('option');
        opt.value = n;
        dl.appendChild(opt);
    });
}

async function saveDevice() {
    const editId = $('#device-edit-id').value;
    const data = {
        name: $('#device-name').value.trim(),
        host: $('#device-host').value.trim(),
        port: parseInt($('#device-port').value) || 5900,
        password: $('#device-password').value,
        group_name: $('#device-group').value.trim() || 'Ungrouped',
        group_color: $('#device-color').value,
        view_only: $('#device-viewonly').checked,
        enabled: $('#device-enabled').checked,
    };

    if (!data.name || !data.host) {
        toast('Name and Host are required', 'error');
        return;
    }

    try {
        if (editId) {
            // Don't send empty password = don't change it
            if (!data.password) delete data.password;
            await api.updateDevice(editId, data);
            toast('Device updated', 'success');
        } else {
            await api.createDevice(data);
            toast('Device added', 'success');
        }
        $('#modal-device').style.display = 'none';
        await refreshDevices();
    } catch (err) {
        toast('Error: ' + err.message, 'error');
    }
}

// ═══════════════════════════════════════════════════════════════════════
//  SETTINGS PANEL
// ═══════════════════════════════════════════════════════════════════════

function openSettings() {
    const s = state.settings;
    $('#setting-columns').value = s.grid_columns || 'auto';
    $('#setting-quality').value = s.thumbnail_quality || 'low';
    $('#setting-darkmode').checked = s.dark_mode !== 'false';
    $('#setting-autoreconnect').checked = s.auto_reconnect !== 'false';
    $('#setting-health-interval').value = s.health_check_interval || 30;
    $('#setting-default-port').value = s.vnc_default_port || 5900;
    $('#setting-username').value = s.app_username || '';
    $('#setting-password').value = '';
    $('#settings-panel').style.display = 'flex';
    $('#settings-backdrop').style.display = 'block';
}

function closeSettings() {
    $('#settings-panel').style.display = 'none';
    $('#settings-backdrop').style.display = 'none';
}

async function saveSettings() {
    const data = {
        grid_columns: $('#setting-columns').value,
        thumbnail_quality: $('#setting-quality').value,
        dark_mode: $('#setting-darkmode').checked,
        auto_reconnect: $('#setting-autoreconnect').checked,
        health_check_interval: parseInt($('#setting-health-interval').value) || 30,
        vnc_default_port: parseInt($('#setting-default-port').value) || 5900,
        app_username: $('#setting-username').value.trim() || null,
    };

    const pw = $('#setting-password').value;
    if (pw) data.app_password = pw;

    try {
        state.settings = await api.updateSettings(data);
        applySettings();
        toast('Settings saved', 'success');
        closeSettings();
    } catch (err) {
        toast('Error saving settings: ' + err.message, 'error');
    }
}

function applySettings() {
    const s = state.settings;
    if (s.dark_mode === 'false') {
        document.body.classList.add('light-mode');
    } else {
        document.body.classList.remove('light-mode');
    }
    applyGridColumns();
}

// ═══════════════════════════════════════════════════════════════════════
//  IMPORT / EXPORT
// ═══════════════════════════════════════════════════════════════════════

async function handleImportFile(file) {
    if (!file || !file.name.endsWith('.json')) {
        toast('Please select a .json file', 'error');
        return;
    }

    try {
        const preview = await api.importJson(file);
        state.importPreview = preview;
        showImportModal(preview);
    } catch (err) {
        toast('Import error: ' + err.message, 'error');
    }
}

function showImportModal(preview) {
    const modal = $('#modal-import');
    const title = $('#import-title');
    const summary = $('#import-summary');
    const tableWrap = $('#import-table-wrap');

    title.textContent = `Import Preview — ${preview.total_found} stations found across ${preview.groups_found.length} groups`;

    summary.innerHTML = `
        <span class="import-badge import-badge--new">✅ ${preview.new.length} New</span>
        <span class="import-badge import-badge--dup">⚠️ ${preview.duplicates.length} Duplicates</span>
        <span class="import-badge import-badge--invalid">❌ ${preview.invalid.length} Invalid</span>
        <span class="import-badge import-badge--groups">📁 Groups: ${preview.groups_found.join(', ')}</span>
    `;

    // Build table
    const allDevices = [
        ...preview.new.map(d => ({ ...d, _type: 'new' })),
        ...preview.duplicates.map(d => ({ ...d, _type: 'duplicate' })),
    ];

    // Group by group_name
    const grouped = {};
    for (const d of allDevices) {
        const g = d.group_name || 'Ungrouped';
        if (!grouped[g]) grouped[g] = [];
        grouped[g].push(d);
    }

    let tableHtml = `<table class="import-table">
        <thead><tr>
            <th style="width:30px"><input type="checkbox" id="import-select-all" checked></th>
            <th>Display Name</th>
            <th>Host</th>
            <th>Port</th>
            <th>Group</th>
            <th>Mode</th>
            <th>Status</th>
        </tr></thead><tbody>`;

    for (const [groupName, devices] of Object.entries(grouped)) {
        tableHtml += `<tr class="import-group-row"><td colspan="7">${esc(groupName)} (${devices.length})</td></tr>`;
        for (const d of devices) {
            const disabled = d.enabled === false;
            const nameClass = disabled ? 'import-disabled' : '';
            const viewBadge = d.view_only
                ? '<span class="import-badge-viewonly import-badge-viewonly--view">👁 View Only</span>'
                : '<span class="import-badge-viewonly import-badge-viewonly--ctrl">🖱 Control</span>';
            const statusLabel = d._type === 'duplicate'
                ? `<span style="color:var(--warning)">Duplicate (${esc(d.conflict_with || '')})</span>`
                : '<span style="color:var(--success)">New</span>';
            const note = disabled ? ' <small style="color:var(--text-disabled)">(Disabled in MightyViewer)</small>' : '';

            tableHtml += `<tr>
                <td><input type="checkbox" class="import-device-check" data-host="${esc(d.host)}" data-port="${d.port}" checked></td>
                <td class="${nameClass}">${esc(d.name)}${note}</td>
                <td style="font-family:var(--font-mono)">${esc(d.host)}</td>
                <td>${d.port}</td>
                <td>${esc(d.group_name)}</td>
                <td>${viewBadge}</td>
                <td>${statusLabel}</td>
            </tr>`;
        }
    }

    if (preview.invalid.length > 0) {
        tableHtml += `<tr class="import-group-row"><td colspan="7">Invalid (${preview.invalid.length})</td></tr>`;
        for (const inv of preview.invalid) {
            tableHtml += `<tr><td></td><td colspan="5" style="color:var(--danger)">${esc(inv.reason)}</td><td></td></tr>`;
        }
    }

    tableHtml += '</tbody></table>';
    tableWrap.innerHTML = tableHtml;

    // Select all toggle
    const selectAll = tableWrap.querySelector('#import-select-all');
    if (selectAll) {
        selectAll.addEventListener('change', () => {
            tableWrap.querySelectorAll('.import-device-check').forEach(cb => {
                cb.checked = selectAll.checked;
            });
        });
    }

    modal.style.display = 'flex';
}

async function confirmImport() {
    if (!state.importPreview) return;

    const checkedHosts = new Set();
    $$('.import-device-check:checked').forEach(cb => {
        checkedHosts.add(`${cb.dataset.host}:${cb.dataset.port}`);
    });

    const allDevices = [...state.importPreview.new, ...state.importPreview.duplicates];
    const selectedDevices = allDevices.filter(d => checkedHosts.has(`${d.host}:${d.port}`));

    if (selectedDevices.length === 0) {
        toast('No devices selected', 'warning');
        return;
    }

    try {
        const res = await api.confirmImport({
            devices: selectedDevices,
            overwrite_duplicates: $('#import-overwrite').checked,
            preserve_view_only: $('#import-preserve-viewonly').checked,
        });
        $('#modal-import').style.display = 'none';
        state.importPreview = null;
        toast(`✅ Imported ${res.imported} stations, skipped ${res.skipped}`, 'success');
        await refreshDevices();
    } catch (err) {
        toast('Import failed: ' + err.message, 'error');
    }
}

function exportMightyViewer() {
    window.location.href = '/api/export/json';
}

function exportPlainJson() {
    window.location.href = '/api/export/devices';
}

// ═══════════════════════════════════════════════════════════════════════
//  SSE — Real-time updates
// ═══════════════════════════════════════════════════════════════════════

function connectSSE() {
    if (state.sse) {
        state.sse.close();
    }

    const sse = new EventSource('/api/events');

    sse.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.type === 'status_update') {
                // Update health statuses
                for (const [idStr, status] of Object.entries(data.health || {})) {
                    const id = parseInt(idStr);
                    const device = state.devices.find(d => d.id === id);
                    if (device) {
                        device.health_status = status;
                        // Update tile status indicator
                        const tile = $(`.device-tile[data-id="${id}"]`);
                        if (tile) {
                            tile.dataset.status = status;
                            const dot = tile.querySelector('.status-dot');
                            if (dot) dot.className = `status-dot status-dot--${status}`;
                        }
                    }
                }

                // Update proxy info
                const proxies = data.proxies || {};
                for (const [idStr, info] of Object.entries(proxies)) {
                    const id = parseInt(idStr, 10);
                    const device = state.devices.find(d => d.id === id);
                    if (device) {
                        device.ws_port = info.port;
                        device.proxy_status = info.status;
                    }
                }
                const proxyIds = new Set(Object.keys(proxies).map(s => parseInt(s, 10)));
                for (const id of state.lastSseProxyIds) {
                    if (!proxyIds.has(id)) {
                        const device = state.devices.find(d => d.id === id);
                        if (device) {
                            device.ws_port = null;
                            device.proxy_status = 'stopped';
                        }
                    }
                }
                state.lastSseProxyIds = proxyIds;

                updateOnlineCount();
            }
        } catch (e) { /* ignore non-JSON */ }
    };

    sse.onerror = () => {
        sse.close();
        setTimeout(connectSSE, 5000);
    };

    state.sse = sse;
}

// ═══════════════════════════════════════════════════════════════════════
//  DATA REFRESH
// ═══════════════════════════════════════════════════════════════════════

async function refreshDevices() {
    try {
        const [devices, groups] = await Promise.all([
            api.getDevices(),
            api.getGroups(),
        ]);
        state.devices = devices;
        state.groups = groups;
        renderDashboard();
    } catch (err) {
        console.error('Failed to refresh:', err);
    }
}

// ═══════════════════════════════════════════════════════════════════════
//  MODAL CLOSE HANDLERS
// ═══════════════════════════════════════════════════════════════════════

function setupModalCloseHandlers() {
    // Generic close buttons
    $$('[data-close]').forEach(btn => {
        btn.addEventListener('click', () => {
            const modalId = btn.dataset.close;
            const modal = $(`#${modalId}`);
            if (modal) modal.style.display = 'none';
        });
    });

    // Close modals on backdrop click
    $$('.modal-overlay').forEach(overlay => {
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) overlay.style.display = 'none';
        });
    });

    // Close context menu on click anywhere
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.context-menu') && !e.target.closest('.tile-menu-btn')) {
            hideContextMenu();
        }
    });

    // Escape key
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            hideContextMenu();
            const fc = $('#modal-fullcontrol');
            if (fc.style.display !== 'none') {
                closeFullControl();
                return;
            }
            $$('.modal-overlay').forEach(m => { if (m.style.display !== 'none') m.style.display = 'none'; });
            closeSettings();
        }
    });
}

// ═══════════════════════════════════════════════════════════════════════
//  EVENT BINDINGS
// ═══════════════════════════════════════════════════════════════════════

function bindEvents() {
    // Header buttons
    $('#btn-add-device').addEventListener('click', showAddDeviceModal);
    $('#btn-settings').addEventListener('click', openSettings);
    $('#btn-settings-close').addEventListener('click', closeSettings);
    $('#settings-backdrop').addEventListener('click', closeSettings);
    $('#btn-save-settings').addEventListener('click', saveSettings);

    // Empty state buttons
    $('#btn-empty-add')?.addEventListener('click', showAddDeviceModal);
    $('#btn-empty-import')?.addEventListener('click', () => {
        openSettings();
        // Scroll to import section after a tick
        setTimeout(() => {
            $('#import-dropzone')?.scrollIntoView({ behavior: 'smooth' });
        }, 300);
    });

    // Save device
    $('#btn-save-device').addEventListener('click', saveDevice);
    $('#form-device').addEventListener('submit', (e) => { e.preventDefault(); saveDevice(); });

    // Zoom slider
    $('#zoom-slider').addEventListener('input', (e) => {
        const val = e.target.value;
        $('#zoom-value').textContent = `${val}px`;
        document.documentElement.style.setProperty('--tile-min', `${val}px`);
        applyGridColumns();
    });

    // Search
    $('#search-input').addEventListener('input', () => {
        renderDashboard();
    });

    // Filter
    $('#filter-select').addEventListener('change', () => {
        renderDashboard();
    });

    // Collapse / Expand all
    $('#btn-collapse-all').addEventListener('click', () => {
        const groups = [...new Set(state.devices.map(d => d.group_name))];
        groups.forEach(g => state.collapsedGroups.add(g));
        renderDashboard();
    });
    $('#btn-expand-all').addEventListener('click', () => {
        state.collapsedGroups.clear();
        renderDashboard();
    });

    // Fullscreen mode
    $('#btn-fullscreen').addEventListener('click', () => {
        if (!document.fullscreenElement) {
            document.documentElement.requestFullscreen?.().catch(() => {
                toast('Fullscreen not supported', 'warning');
            });
        } else {
            document.exitFullscreen?.();
        }
    });

    // Full Control toolbar
    $('#fc-toggle-control').addEventListener('click', () => {
        if (!state.fullControlRfb) return;
        state.fullControlRfb.viewOnly = !state.fullControlRfb.viewOnly;
        
        const text = $('#fc-toggle-control-text');
        if (state.fullControlRfb.viewOnly) {
            text.textContent = 'Enable Control';
            toast('View Only mode (Control Disabled)', 'info');
        } else {
            text.textContent = 'Disable Control';
            toast('Full Control mode enabled', 'success');
        }
    });
    $('#fc-disconnect').addEventListener('click', closeFullControl);
    $('#fc-ctrl-alt-del').addEventListener('click', () => {
        state.fullControlRfb?.sendCtrlAltDel();
        toast('Ctrl+Alt+Del sent', 'info');
    });
    $('#fc-clipboard').addEventListener('click', openClipboardModal);
    $('#fc-screenshot').addEventListener('click', takeScreenshot);
    $('#fc-quality').addEventListener('change', (e) => {
        setFullControlQuality(e.target.value);
    });
    $('#fc-fullscreen').addEventListener('click', () => {
        if (!document.fullscreenElement) {
            $('#modal-fullcontrol').requestFullscreen?.();
        } else {
            document.exitFullscreen?.();
        }
    });

    // Clipboard modal
    $('#btn-clipboard-send').addEventListener('click', sendClipboardToRemote);
    $('#btn-clipboard-receive').addEventListener('click', receiveClipboardFromRemote);

    // Context menu actions
    $$('.context-item').forEach(item => {
        item.addEventListener('click', () => {
            handleContextAction(item.dataset.action);
        });
    });

    // Import dropzone
    const dropzone = $('#import-dropzone');
    const fileInput = $('#import-file-input');

    dropzone.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length) handleImportFile(e.target.files[0]);
    });
    dropzone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropzone.classList.add('dragover');
    });
    dropzone.addEventListener('dragleave', () => {
        dropzone.classList.remove('dragover');
    });
    dropzone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropzone.classList.remove('dragover');
        if (e.dataTransfer.files.length) handleImportFile(e.dataTransfer.files[0]);
    });

    // Import confirm
    $('#btn-import-confirm').addEventListener('click', confirmImport);

    // Export
    $('#btn-export-mv').addEventListener('click', exportMightyViewer);
    $('#btn-export-plain').addEventListener('click', exportPlainJson);
}

// ═══════════════════════════════════════════════════════════════════════
//  INITIALIZATION
// ═══════════════════════════════════════════════════════════════════════

async function init() {
    try {
        // Load settings first
        state.settings = await api.getSettings();
        applySettings();

        // Load devices and groups
        const [devices, groups] = await Promise.all([
            api.getDevices(),
            api.getGroups(),
        ]);
        state.devices = devices;
        state.groups = groups;

        // Render
        renderDashboard();

        // Bind events
        bindEvents();
        setupModalCloseHandlers();

        // Connect SSE
        connectSSE();

    } catch (err) {
        console.error('Initialization error:', err);
        toast('Failed to initialize: ' + err.message, 'error');
    }
}

// Boot
init();
