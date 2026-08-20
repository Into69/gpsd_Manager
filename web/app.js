// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
async function api(method, path, body) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch('/api/' + path, opts);
    return res.json();
}

function toast(msg, type = 'success') {
    const el = document.createElement('div');
    el.className = 'toast toast-' + type;
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 3500);
}

function fmt(val, unit, decimals = 6) {
    if (val === null || val === undefined) return '--';
    return Number(val).toFixed(decimals) + (unit ? ' ' + unit : '');
}

function fmtSpeed(mps) {
    if (mps === null || mps === undefined) return '--';
    const kmh = mps * 3.6;
    const kt = mps * 1.94384;
    return `${mps.toFixed(2)} m/s (${kmh.toFixed(1)} km/h, ${kt.toFixed(1)} kt)`;
}

function fmtDopPair(a, b) {
    if ((a === null || a === undefined) && (b === null || b === undefined)) return '--';
    return `${fmt(a, '', 1)} / ${fmt(b, '', 1)}`;
}

function setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
}

function snrColor(ss) {
    if (ss === null || ss === undefined || ss <= 0) return 'var(--snr-none)';
    if (ss >= 40) return 'var(--snr-good)';
    if (ss >= 35) return 'var(--snr-ok)';
    if (ss >= 30) return 'var(--snr-mid)';
    if (ss >= 25) return 'var(--snr-warn)';
    if (ss >= 20) return 'var(--snr-low)';
    return 'var(--snr-bad)';
}

// Serial data buffer (max 1000 lines)
const serialDataBuffer = [];
const MAX_SERIAL_LINES = 1000;

function addSerialData(data) {
    if (!data) return;
    const lines = data.split('\n').filter(l => l.trim());
    serialDataBuffer.push(...lines);
    if (serialDataBuffer.length > MAX_SERIAL_LINES) {
        serialDataBuffer.splice(0, serialDataBuffer.length - MAX_SERIAL_LINES);
    }
    updateSerialDisplay();
}

function updateSerialDisplay() {
    const textarea = document.getElementById('mcode-serial-data');
    if (textarea) {
        textarea.value = serialDataBuffer.join('\n');
        textarea.scrollTop = textarea.scrollHeight;
    }
}

// ---------------------------------------------------------------------------
// Startup
// ---------------------------------------------------------------------------
async function checkStartup() {
    const data = await api('GET', 'startup');
    const banner = document.getElementById('startup-banner');
    if (data.issues && data.issues.length > 0) {
        banner.style.display = 'block';
        banner.textContent = data.issues.join(' | ');
        if (!data.installed) banner.classList.add('error');
    }
}

// ---------------------------------------------------------------------------
// Service status
// ---------------------------------------------------------------------------
async function refreshStatus() {
    const data = await api('GET', 'status');

    document.getElementById('st-state').innerHTML = data.running
        ? '<span class="badge badge-green">Running</span>'
        : '<span class="badge badge-red">Stopped</span>';
    setText('st-pid', data.pid || '--');
    setText('st-version', data.version || '--');
    setText('st-path', data.gpsd_path || '--');
    setText('st-devices',
        data.devices && data.devices.length ? data.devices.join(', ') : 'None');
    document.getElementById('st-perms').innerHTML = data.has_permissions
        ? '<span class="badge badge-green">OK</span>'
        : '<span class="badge badge-yellow">Limited</span>';

    document.getElementById('header-service').innerHTML = data.running
        ? '<span class="badge badge-green">Running</span>'
        : '<span class="badge badge-red">Stopped</span>';

    const activeEl = document.getElementById('active-devices');
    const activeSet = new Set(data.devices || []);
    const allDevices = [...new Set([...configuredDevices, ...activeSet])].sort();
    if (allDevices.length) {
        activeEl.innerHTML = allDevices.map(d => {
            const active = activeSet.has(d);
            const badge = active
                ? '<span class="badge badge-green">active</span>'
                : '<span class="badge badge-red">inactive</span>';
            return `<li class="device-item"><span class="device-path">${d}</span> ${badge}</li>`;
        }).join('');
    } else {
        activeEl.innerHTML = '<li class="empty-state">No devices</li>';
    }

    const errCard = document.getElementById('errors-card');
    const errList = document.getElementById('errors-list');
    if (data.errors && data.errors.length) {
        errCard.style.display = 'block';
        errList.innerHTML = data.errors.map(e => `<li>${e}</li>`).join('');
    } else {
        errCard.style.display = 'none';
    }

    // Also update devices list
    try {
        const devData = await api('POST', 'devices/rescan');
        configuredDevices = devData.active || [];
    } catch (e) {
        // Silently ignore if rescan fails
    }
}

async function serviceAction(action) {
    const data = await api('POST', action);
    toast(data.message, data.success ? 'success' : 'error');
    setTimeout(() => {
        refreshStatus();
        if (action === 'restart') {
            loadOptions();
            configuredDevices = [];
            scanDevices();
        }
    }, 1000);
}

// ---------------------------------------------------------------------------
// Options
// ---------------------------------------------------------------------------
async function loadOptions() {
    const data = await api('GET', 'options');
    const container = document.getElementById('options-list');
    container.innerHTML = '';
    for (const [flag, info] of Object.entries(data.options)) {
        const row = document.createElement('div');
        row.className = 'option-row';
        row.innerHTML = `
            <div class="option-info">
                <div class="option-flag">${flag}</div>
                <div class="option-desc">${info.description}</div>
            </div>
            <label class="toggle">
                <input type="checkbox" ${info.enabled ? 'checked' : ''}
                       onchange="toggleOption('${flag}', this.checked)">
                <span class="slider"></span>
            </label>
        `;
        container.appendChild(row);
    }
}

async function toggleOption(flag, enabled) {
    const data = await api('POST', 'options', { flag, enabled });
    toast(data.message, data.success ? 'success' : 'error');
}

async function saveOptions() {
    const data = await api('POST', 'options/save');
    toast(data.message, data.success ? 'success' : 'error');
}

// ---------------------------------------------------------------------------
// Devices
// ---------------------------------------------------------------------------
let configuredDevices = [];

async function scanDevices() {
    const el = document.getElementById('available-devices');
    el.innerHTML = '<li class="empty-state"><span class="spinner"></span>Scanning...</li>';

    if (configuredDevices.length === 0) {
        const startup = await api('GET', 'startup');
        configuredDevices = startup.configured_devices || [];
    }

    const data = await api('GET', 'devices');
    if (!data.devices || data.devices.length === 0) {
        el.innerHTML = '<li class="empty-state">No GPS devices found</li>';
        return;
    }
    el.innerHTML = data.devices.map(d => `
        <li class="device-item">
            <input type="checkbox" value="${d.path}" class="device-checkbox"
                   ${configuredDevices.includes(d.path) ? 'checked' : ''}>
            <div>
                <span class="device-path">${d.path}</span>
                ${d.description ? `<div class="device-desc">${d.description}</div>` : ''}
                <div class="device-desc">${d.type}</div>
            </div>
        </li>
    `).join('');
}

async function applyDevices() {
    const checked = [...document.querySelectorAll('.device-checkbox:checked')].map(c => c.value);

    // Allow empty selection only if custom GPS is enabled
    const isMCodeEnabled = document.getElementById('mcode-status')?.textContent?.includes('Enabled');

    if (checked.length === 0 && !isMCodeEnabled) {
        toast('No devices selected. Enable custom GPS to use no devices.', 'error');
        return;
    }
    const data = await api('POST', 'devices', { devices: checked });
    toast(data.message, data.success ? 'success' : 'error');
}

async function clearDevices() {
    if (!confirm('Clear all GPS devices? (only if using custom GPS receiver)')) {
        return;
    }
    const data = await api('POST', 'devices', { devices: [] });
    toast(data.message, data.success ? 'success' : 'error');
}

async function enableSatReporting(btn) {
    btn.disabled = true;
    const original = btn.textContent;
    btn.innerHTML = '<span class="spinner"></span>Enabling...';
    try {
        const data = await api('POST', 'gps/enable-satellite-reporting');
        toast(data.message, data.success ? 'success' : 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = original;
    }
}

async function restartReceiver(btn, mode) {
    if (mode === 'cold' && !confirm('Cold restart will wipe all assistance data. Re-acquiring a fix will take 30+ seconds. Continue?')) {
        return;
    }
    btn.disabled = true;
    const original = btn.textContent;
    btn.innerHTML = '<span class="spinner"></span>Sending...';
    try {
        const data = await api('POST', 'gps/restart-receiver', { mode });
        toast(data.message, data.success ? 'success' : 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = original;
    }
}

// ---------------------------------------------------------------------------
// GPS info via WebSocket
// ---------------------------------------------------------------------------
let lastGpsData = null;
let lastFixTime = null;   // ms epoch from data.time
let lastUpdateLocal = 0;  // local performance.now() when we got the last message
let ws = null;
let wsReconnectDelay = 1000;

function connectGpsWs() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${location.host}/ws/gps`;
    setConnState('connecting');
    try {
        ws = new WebSocket(url);
    } catch (e) {
        setConnState('disconnected', e.message);
        scheduleReconnect();
        return;
    }

    ws.onopen = () => {
        wsReconnectDelay = 1000;
    };
    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            handleGpsUpdate(data);
        } catch (e) { /* ignore parse errors */ }
    };
    ws.onerror = () => { /* handled in onclose */ };
    ws.onclose = () => {
        setConnState('disconnected');
        scheduleReconnect();
    };
}

function scheduleReconnect() {
    setTimeout(connectGpsWs, wsReconnectDelay);
    wsReconnectDelay = Math.min(wsReconnectDelay * 1.5, 10000);
}

function setConnState(state, detail) {
    const pill = document.getElementById('conn-pill');
    if (!pill) return;
    pill.className = `conn-pill ${state}`;
    let label = 'Disconnected';
    if (state === 'connecting') label = 'Connecting…';
    else if (state === 'connected') label = 'Live';
    else if (state === 'stale') label = 'Stale';
    else if (state === 'disconnected') label = 'Disconnected';
    pill.innerHTML = `<span class="dot"></span>${label}`;
    if (detail) pill.title = detail; else pill.removeAttribute('title');
}

function handleGpsUpdate(data) {
    lastGpsData = data;
    lastUpdateLocal = performance.now();

    if (!data.connected) {
        setConnState('disconnected', data.error || 'gpsd unreachable');
        showGpsError(data.error || 'gpsd unreachable');
        return;
    }
    setConnState('connected');

    if (data.time) {
        const t = Date.parse(data.time);
        if (!Number.isNaN(t)) lastFixTime = t;
    }
    renderGps(data);
}

function showGpsError(msg) {
    const el = document.getElementById('gps-fix');
    if (el) el.innerHTML = `<span class="badge badge-red">Unavailable</span>`;
    setText('gps-error-detail', msg);
    const detail = document.getElementById('gps-error-row');
    if (detail) detail.style.display = msg ? 'flex' : 'none';
}

function renderGps(data) {
    const detail = document.getElementById('gps-error-row');
    if (detail) detail.style.display = 'none';

    const fixColors = { 'No fix': 'badge-red', '2D fix': 'badge-yellow', '3D fix': 'badge-green' };
    const fixClass = fixColors[data.fix] || 'badge-red';
    document.getElementById('gps-fix').innerHTML = `<span class="badge ${fixClass}">${data.fix}</span>`;
    document.getElementById('gps-status').innerHTML = data.status
        ? `<span class="badge badge-yellow">${data.status}</span>`
        : '';

    setText('gps-lat', fmt(data.lat, '°', 7));
    setText('gps-lon', fmt(data.lon, '°', 7));
    setText('gps-alt', fmt(data.alt, 'm', 1));
    setText('gps-alt-msl', fmt(data.alt_msl, 'm', 1));
    setText('gps-geoid', fmt(data.geoid_sep, 'm', 2));

    setText('gps-speed',
        data.speed === null || data.speed === undefined ? '--' : fmtSpeed(data.speed));
    setText('gps-track', fmt(data.track, '°', 1));
    setText('gps-magtrack', fmt(data.magtrack, '°', 1));
    setText('gps-magvar', fmt(data.magvar, '°', 2));
    setText('gps-climb', fmt(data.climb, 'm/s', 2));
    setText('gps-time', data.time || '--');

    setText('gps-epx', fmt(data.epx, 'm', 2));
    setText('gps-epy', fmt(data.epy, 'm', 2));
    setText('gps-epv', fmt(data.epv, 'm', 2));
    setText('gps-eps', fmt(data.eps, 'm/s', 2));
    setText('gps-epd', fmt(data.epd, '°', 2));
    setText('gps-epc', fmt(data.epc, 'm/s', 2));
    setText('gps-ept', fmt(data.ept, 's', 4));

    setText('gps-sats-used', data.satellites_used ?? '--');
    setText('gps-sats-visible', data.satellites_visible ?? '--');
    setText('gps-hvp-dop',
        (data.hdop === null && data.vdop === null && data.pdop === null) ? '--' :
        `${fmt(data.hdop, '', 1)} / ${fmt(data.vdop, '', 1)} / ${fmt(data.pdop, '', 1)}`);
    setText('gps-tg-dop', fmtDopPair(data.tdop, data.gdop));
    if (data.snr_avg !== null && data.snr_avg !== undefined) {
        setText('gps-snr', `${data.snr_min} / ${data.snr_avg} / ${data.snr_max} (min/avg/max)`);
    } else {
        setText('gps-snr', '--');
    }

    renderConstellations(data.constellations || {});
    renderSatTable(data.satellites || []);
    renderSkyPlot(data.satellites || []);
}

function renderConstellations(consts) {
    const body = document.querySelector('#gps-constellations tbody');
    const names = Object.keys(consts).sort();
    if (names.length === 0) {
        body.innerHTML = '<tr><td colspan="3" class="empty-state">--</td></tr>';
        return;
    }
    body.innerHTML = names.map(name => {
        const c = consts[name];
        return `<tr>`
            + `<td><span class="gnss-badge ${gnssClass(name)}">${name}</span></td>`
            + `<td class="num">${c.used}</td>`
            + `<td class="num">${c.visible}</td>`
            + `</tr>`;
    }).join('');
}

function gnssClass(name) {
    switch ((name || '').toLowerCase()) {
        case 'gps':     return 'gnss-gps';
        case 'glonass': return 'gnss-glonass';
        case 'galileo': return 'gnss-galileo';
        case 'beidou':  return 'gnss-beidou';
        case 'qzss':    return 'gnss-qzss';
        case 'sbas':    return 'gnss-sbas';
        default:        return 'gnss-other';
    }
}

function renderSatTable(sats) {
    const body = document.querySelector('#gps-satellites tbody');
    if (sats.length === 0) {
        body.innerHTML = '<tr><td colspan="6" class="empty-state">--</td></tr>';
        return;
    }
    body.innerHTML = sats.map(s => {
        const ss = s.ss ?? null;
        let ssCell;
        if (ss !== null && ss > 0) {
            // Map 0-50 dB-Hz onto the 60px track (clamped, with a 4% floor so faint sats show).
            const fillPct = Math.max(4, Math.min(100, (ss / 50) * 100));
            const color = snrColor(ss);
            ssCell = `<div class="snr-cell">`
                + `<span class="snr-track"><span class="snr-fill" style="width:${fillPct}%; background:${color};"></span></span>`
                + `<span class="snr-num">${ss}</span>`
                + `</div>`;
        } else {
            ssCell = '<span class="snr-empty">--</span>';
        }
        const usedBadge = s.used
            ? '<span class="badge badge-green">yes</span>'
            : '<span class="badge badge-gray">no</span>';
        const gnssName = s.gnss || '--';
        const elev = (s.elev !== null && s.elev !== undefined) ? `${s.elev}°` : '--';
        const az   = (s.az   !== null && s.az   !== undefined) ? `${s.az}°`   : '--';
        return `<tr>
            <td class="num">${s.prn ?? '--'}</td>
            <td><span class="gnss-badge ${gnssClass(gnssName)}">${gnssName}</span></td>
            <td class="num">${elev}</td>
            <td class="num">${az}</td>
            <td class="num">${ssCell}</td>
            <td class="center">${usedBadge}</td>
        </tr>`;
    }).join('');
}

// ---------------------------------------------------------------------------
// Sky plot (SVG polar projection: zenith=center, horizon=outer ring)
// ---------------------------------------------------------------------------
function renderSkyPlot(sats) {
    const svg = document.getElementById('sky-plot');
    if (!svg) return;
    const cx = 100, cy = 100, rOuter = 90;

    let g = '';
    // Elevation rings (0°, 30°, 60°)
    [0, 30, 60].forEach(elev => {
        const r = ((90 - elev) / 90) * rOuter;
        g += `<circle class="ring" cx="${cx}" cy="${cy}" r="${r}"/>`;
        if (elev !== 0) {
            g += `<text class="ring-label" x="${cx + 2}" y="${cy - r}">${elev}°</text>`;
        }
    });
    // Crosshair / cardinal lines
    g += `<line class="axis" x1="${cx - rOuter}" y1="${cy}" x2="${cx + rOuter}" y2="${cy}"/>`;
    g += `<line class="axis" x1="${cx}" y1="${cy - rOuter}" x2="${cx}" y2="${cy + rOuter}"/>`;
    g += `<text class="cardinal" x="${cx}" y="${cy - rOuter - 6}">N</text>`;
    g += `<text class="cardinal" x="${cx}" y="${cy + rOuter + 6}">S</text>`;
    g += `<text class="cardinal" x="${cx + rOuter + 6}" y="${cy}">E</text>`;
    g += `<text class="cardinal" x="${cx - rOuter - 6}" y="${cy}">W</text>`;

    sats.forEach(s => {
        if (s.elev === null || s.elev === undefined || s.az === null || s.az === undefined) return;
        const r = ((90 - s.elev) / 90) * rOuter;
        const azRad = (s.az * Math.PI) / 180;
        const x = cx + r * Math.sin(azRad);
        const y = cy - r * Math.cos(azRad);
        const color = snrColor(s.ss);
        const usedClass = s.used ? 'sat' : 'sat sat-unused';
        g += `<circle class="${usedClass}" cx="${x}" cy="${y}" r="5" fill="${color}"/>`;
        if (s.prn !== null && s.prn !== undefined) {
            g += `<text class="sat-label" x="${x}" y="${y}">${s.prn}</text>`;
        }
    });

    svg.innerHTML = g;
}

// ---------------------------------------------------------------------------
// Fix age + staleness tick
// ---------------------------------------------------------------------------
function tickStaleness() {
    const ageEl = document.getElementById('fix-age');

    // Fix age (based on TPV.time)
    if (lastFixTime) {
        const ageSec = Math.max(0, (Date.now() - lastFixTime) / 1000);
        if (ageEl) {
            ageEl.textContent = formatAge(ageSec);
            ageEl.className = 'fix-age' + (ageSec > 30 ? ' very-stale' : ageSec > 5 ? ' stale' : '');
        }
    } else if (ageEl) {
        ageEl.textContent = '';
        ageEl.className = 'fix-age';
    }

    // Staleness of the WS connection
    if (ws && ws.readyState === WebSocket.OPEN && lastUpdateLocal) {
        const sinceLast = (performance.now() - lastUpdateLocal) / 1000;
        if (sinceLast > 5) {
            setConnState('stale', `${sinceLast.toFixed(1)}s since last gpsd update`);
        } else if (lastGpsData && lastGpsData.connected) {
            setConnState('connected');
        }
    }
}

function formatAge(s) {
    if (s < 2) return 'just now';
    if (s < 60) return `${Math.round(s)}s ago`;
    if (s < 3600) return `${Math.round(s / 60)}m ago`;
    return `${Math.round(s / 3600)}h ago`;
}

// ---------------------------------------------------------------------------
// MCODE Converter
// ---------------------------------------------------------------------------
async function scanMCodePorts() {
    const select = document.getElementById('mcode-port');
    select.innerHTML = '<option>Scanning...</option>';

    const data = await api('GET', 'devices');
    if (!data.devices || data.devices.length === 0) {
        select.innerHTML = '<option>No serial ports found</option>';
        return;
    }

    // Filter out devices already in GPS devices
    const usedDevices = new Set(configuredDevices);
    const availableDevices = data.devices.filter(d => !usedDevices.has(d.path));

    if (availableDevices.length === 0) {
        select.innerHTML = '<option>No available ports (all in use)</option>';
        return;
    }

    select.innerHTML = availableDevices.map(d => `
        <option value="${d.path}" title="${d.description}">${d.path} ${d.description ? '(' + d.description + ')' : ''}</option>
    `).join('');
}

async function loadMCodeConfig() {
    try {
        const data = await api('GET', 'converter/config');
        const portSelect = document.getElementById('mcode-port');
        const baudrateSelect = document.getElementById('mcode-baudrate');
        const parserSelect = document.getElementById('mcode-parser-type');

        // Set saved values
        if (data.serial_port) {
            portSelect.value = data.serial_port;
        }
        if (data.baudrate) {
            baudrateSelect.value = data.baudrate;
        }
        if (data.parser_type) {
            parserSelect.value = data.parser_type;
        }
    } catch (e) {
        // Silently fail if config doesn't exist
    }
}

async function saveConverterConfig() {
    const parserSelect = document.getElementById('mcode-parser-type');
    try {
        const result = await api('POST', 'converter/config', {
            parser_type: parserSelect.value
        });
        if (result.success) {
            toast('Parser type saved', 'success');
        }
    } catch (e) {
        toast('Failed to save parser type', 'error');
    }
}

async function refreshMCodeStatus() {
    const data = await api('GET', 'converter/status');
    const statusEl = document.getElementById('mcode-status');
    const platformEl = document.getElementById('mcode-platform');
    const startBtn = document.getElementById('mcode-start-btn');
    const stopBtn = document.getElementById('mcode-stop-btn');
    const addBtn = document.getElementById('mcode-add-btn');
    const gpsdRow = document.getElementById('mcode-gpsd-row');
    const gpsdStatus = document.getElementById('mcode-in-config');
    const debugDiv = document.getElementById('mcode-debug');
    const errorsDiv = document.getElementById('mcode-errors');
    const errorsList = document.getElementById('mcode-errors-list');

    // Show platform
    if (data.platform) {
        platformEl.textContent = data.platform;
    }

    if (data.running) {
        statusEl.className = 'badge badge-green';
        statusEl.textContent = 'Enabled';
        startBtn.disabled = true;
        stopBtn.disabled = false;
        errorsDiv.style.display = 'none';

        // Show "Clear All Devices" button when custom GPS is enabled
        const clearBtn = document.getElementById('clear-devices-btn');
        if (clearBtn) {
            clearBtn.style.display = 'inline-block';
        }

        // Show debug info if available
        if (data.debug) {
            debugDiv.style.display = 'block';

            // Display raw source data
            const rawData = data.debug.raw_mcode || '';
            if (rawData === 'manual position') {
                // For manual position mode, clear buffer and show status
                serialDataBuffer.length = 0;
                updateSerialDisplay();
            } else {
                // For serial mode, use the complete buffer from converter
                if (data.debug.serial_data_buffer && Array.isArray(data.debug.serial_data_buffer)) {
                    serialDataBuffer.length = 0;
                    serialDataBuffer.push(...data.debug.serial_data_buffer);
                } else if (rawData) {
                    // Fallback: add current raw data
                    addSerialData(rawData);
                }
                updateSerialDisplay();
            }

            const parsed = data.debug.parsed_fields || {};

            // Organize fields into categories
            const categories = {
                'Position': ['lat', 'lon', 'alt'],
                'Quality': ['fix_quality', 'hdop', 'satellites_used'],
                'Time': ['week', 'tow', 'gps_sec'],
            };

            const fieldLabels = {
                'lat': 'Latitude',
                'lon': 'Longitude',
                'alt': 'Altitude (m)',
                'fix_quality': 'Fix Quality',
                'hdop': 'HDOP',
                'satellites_used': 'Satellites',
                'week': 'GPS Week',
                'tow': 'Time of Week (s)',
                'gps_sec': 'GPS Seconds',
            };

            let parsedHtml = '';
            for (const [category, fields] of Object.entries(categories)) {
                const hasFields = fields.some(f => parsed[f] !== undefined && parsed[f] !== null);
                if (hasFields) {
                    parsedHtml += `<div style="color:var(--text-dim); font-size:0.75rem; margin-top:0.5rem; margin-bottom:0.25rem;"><strong>${category}</strong></div>`;
                    for (const field of fields) {
                        const val = parsed[field];
                        if (val !== undefined && val !== null) {
                            let displayVal = val;
                            if (typeof val === 'number') {
                                if (field === 'tow') {
                                    displayVal = val.toFixed(3);
                                } else if (field === 'hdop' || field === 'alt' || field === 'speed_ms') {
                                    displayVal = val.toFixed(2);
                                } else if (field === 'lat' || field === 'lon') {
                                    displayVal = val.toFixed(5);
                                } else {
                                    displayVal = val.toFixed(6);
                                }
                            }
                            const label = fieldLabels[field] || field;
                            parsedHtml += `<div style="margin-left:0.5rem;">${label}: <span style="color:var(--blue);">${displayVal}</span></div>`;
                        }
                    }
                }
            }
            document.getElementById('mcode-debug-parsed').innerHTML = parsedHtml || '<div style="color:var(--text-dim);">No data yet</div>';

            document.getElementById('mcode-debug-gga').textContent = data.debug.nmea_gga || '';
            document.getElementById('mcode-debug-rmc').textContent = data.debug.nmea_rmc || '';

            // Update GPS Quality section
            const qualityDiv = document.getElementById('mcode-quality');
            if (qualityDiv) {
                const parsed = data.debug.parsed_fields || {};
                if (Object.keys(parsed).length > 0) {
                    qualityDiv.style.display = 'block';

                    // Date/Time
                    const datetimeVal = parsed.datetime_str || '--';
                    document.getElementById('mcode-datetime').textContent = datetimeVal;

                    // Fix quality
                    const fixMap = { 0: '2D Fix', 1: '3D Fix' };
                    let fixVal = '--';
                    if (parsed.fix_quality !== undefined) {
                        if (parsed.fix_quality in fixMap) {
                            fixVal = fixMap[parsed.fix_quality];
                        } else {
                            fixVal = `${parsed.fix_quality} (Unknown)`;
                        }
                    }
                    document.getElementById('mcode-fix-quality').textContent = fixVal;

                    // Satellites
                    const satsVal = parsed.satellites_used !== undefined ? `${parsed.satellites_used} SV` : '--';
                    document.getElementById('mcode-satellites').textContent = satsVal;

                    // HDOP
                    const hdopVal = parsed.hdop !== undefined ? parsed.hdop.toFixed(2) : '--';
                    document.getElementById('mcode-hdop').textContent = hdopVal;

                    // Position
                    const posVal = (parsed.lat !== undefined && parsed.lon !== undefined)
                        ? `${parsed.lat.toFixed(5)}°, ${parsed.lon.toFixed(5)}°`
                        : '--';
                    document.getElementById('mcode-position').textContent = posVal;
                } else {
                    qualityDiv.style.display = 'none';
                }
            }
        } else {
            debugDiv.style.display = 'none';
            const qualityDiv = document.getElementById('mcode-quality');
            if (qualityDiv) qualityDiv.style.display = 'none';
        }

        // Show GPSD config status (Linux only)
        if (data.platform === 'Linux') {
            gpsdRow.style.display = '';
            if (data.device_in_config) {
                gpsdStatus.className = 'badge badge-green';
                gpsdStatus.textContent = 'Configured';
                addBtn.style.display = 'none';
            } else {
                gpsdStatus.className = 'badge badge-yellow';
                gpsdStatus.textContent = 'Not Configured';
                addBtn.style.display = 'inline-block';
                addBtn.disabled = false;
            }
        } else {
            gpsdRow.style.display = 'none';
            addBtn.style.display = 'none';
        }
    } else {
        statusEl.className = 'badge badge-red';
        statusEl.textContent = 'Disabled';
        startBtn.disabled = false;
        stopBtn.disabled = true;
        addBtn.disabled = true;
        addBtn.style.display = 'none';
        gpsdRow.style.display = 'none';
        debugDiv.style.display = 'none';

        // Clear serial data buffer when converter stops
        serialDataBuffer.length = 0;
        updateSerialDisplay();

        // Hide "Clear All Devices" button when custom GPS is disabled
        const clearBtn = document.getElementById('clear-devices-btn');
        if (clearBtn) {
            clearBtn.style.display = 'none';
        }

        if (data.errors && data.errors.length > 0) {
            errorsDiv.style.display = 'block';
            errorsList.innerHTML = data.errors.map(e => `<li>${e}</li>`).join('');
        } else {
            errorsDiv.style.display = 'none';
        }
    }
}

async function startMCodeConverter() {
    const port = document.getElementById('mcode-port').value;
    const baudrate = parseInt(document.getElementById('mcode-baudrate').value);

    const btn = document.getElementById('mcode-start-btn');
    btn.disabled = true;
    const original = btn.textContent;
    btn.innerHTML = '<span class="spinner"></span>Enabling...';

    try {
        const data = await api('POST', 'converter/start', { port, baudrate });
        toast(data.message, data.success ? 'success' : 'error');
        setTimeout(refreshMCodeStatus, 500);
    } finally {
        btn.disabled = false;
        btn.textContent = original;
    }
}

async function stopMCodeConverter() {
    const btn = document.getElementById('mcode-stop-btn');
    btn.disabled = true;
    const original = btn.textContent;
    btn.innerHTML = '<span class="spinner"></span>Disabling...';

    try {
        const data = await api('POST', 'converter/stop');
        toast(data.message, data.success ? 'success' : 'error');
        setTimeout(refreshMCodeStatus, 500);
    } finally {
        btn.disabled = false;
        btn.textContent = original;
    }
}

async function addConverterToGpsd() {
    const btn = document.getElementById('mcode-add-btn');
    btn.disabled = true;
    const original = btn.textContent;
    btn.innerHTML = '<span class="spinner"></span>Adding...';

    try {
        const data = await api('POST', 'converter/add-to-gpsd');
        toast(data.message, data.success ? 'success' : 'error');
        setTimeout(async () => {
            refreshMCodeStatus();
            refreshStatus();
            // Rescan and refresh devices after GPSD restart
            const devicesData = await api('POST', 'devices/rescan');
            configuredDevices = devicesData.active || [];
            scanDevices();
        }, 2000);
    } finally {
        btn.disabled = false;
        btn.textContent = original;
    }
}

// ---------------------------------------------------------------------------
// Manual GPS Position
// ---------------------------------------------------------------------------
async function setManualPosition() {
    const lat = parseFloat(document.getElementById('manual-lat').value);
    const lon = parseFloat(document.getElementById('manual-lon').value);
    const alt = parseFloat(document.getElementById('manual-alt').value);
    const speed_ms = 0;

    if (isNaN(lat) || isNaN(lon) || isNaN(alt)) {
        toast('Please enter valid coordinates', 'error');
        return;
    }

    if (lat < -90 || lat > 90) {
        toast('Latitude must be between -90 and 90', 'error');
        return;
    }

    if (lon < -180 || lon > 180) {
        toast('Longitude must be between -180 and 180', 'error');
        return;
    }

    const position = { lat, lon, alt, speed_ms, timestamp: new Date().toISOString() };

    try {
        // Stop serial converter if running
        await api('POST', 'converter/stop', {});

        // Clear all configured devices first
        await api('POST', 'devices', { devices: [] });

        // Save position and start converter (this will add TCP to GPSD)
        await api('POST', 'gps/manual-position', { position });

        document.getElementById('manual-position-status').innerHTML =
            `<span style="color:var(--green);">✓ Position set: ${lat.toFixed(6)}, ${lon.toFixed(6)}, ${alt.toFixed(1)}m (TCP mode)</span>`;

        toast('Manual position active - converter running on TCP port 2948', 'success');

        // Refresh converter status to show it's running
        await refreshMCodeStatus();
    } catch (e) {
        toast('Failed to set position', 'error');
    }
}

async function clearManualPosition() {
    try {
        await api('POST', 'gps/manual-position', { position: null });

        document.getElementById('manual-lat').value = '';
        document.getElementById('manual-lon').value = '';
        document.getElementById('manual-alt').value = '';
        document.getElementById('manual-position-status').innerHTML = '';
        toast('Manual position cleared', 'success');
    } catch (e) {
        toast('Failed to clear position', 'error');
    }
}

async function loadManualPosition() {
    try {
        const data = await api('GET', 'gps/manual-position');
        const position = data.position;
        if (position) {
            document.getElementById('manual-lat').value = position.lat;
            document.getElementById('manual-lon').value = position.lon;
            document.getElementById('manual-alt').value = position.alt;
            document.getElementById('manual-position-status').innerHTML =
                `<span style="color:var(--green);">✓ Position set: ${position.lat.toFixed(6)}, ${position.lon.toFixed(6)}, ${position.alt.toFixed(1)}m</span>`;
        }
    } catch (e) {
        // Ignore errors loading position
    }
}

// ---------------------------------------------------------------------------
// Logs
// ---------------------------------------------------------------------------
async function refreshLogs() {
    const el = document.getElementById('log-output');
    el.textContent = 'Loading...';
    const data = await api('GET', 'logs');
    el.textContent = data.logs || 'No logs available';
    el.scrollTop = el.scrollHeight;
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
async function init() {
    const startup = await api('GET', 'startup');
    configuredDevices = startup.configured_devices || [];
    checkStartup();
    refreshStatus();
    loadOptions();
    scanDevices();
    scanMCodePorts();
    loadMCodeConfig();
    loadManualPosition();
    refreshMCodeStatus();
    connectGpsWs();

    // Add event listener for parser type changes
    const parserSelect = document.getElementById('mcode-parser-type');
    if (parserSelect) {
        parserSelect.addEventListener('change', saveConverterConfig);
    }
}
init();

setInterval(refreshStatus, 15000);
setInterval(refreshMCodeStatus, 15000);
setInterval(tickStaleness, 1000);
