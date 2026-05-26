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
    if (checked.length === 0) {
        toast('No devices selected', 'error');
        return;
    }
    const data = await api('POST', 'devices', { devices: checked });
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
        return `<tr><td>${name}</td><td class="num">${c.used}</td><td class="num">${c.visible}</td></tr>`;
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
    connectGpsWs();
}
init();

setInterval(refreshStatus, 15000);
setInterval(tickStaleness, 1000);
