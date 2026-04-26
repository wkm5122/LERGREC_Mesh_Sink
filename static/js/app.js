// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let lastLogIndex = 0;
let isAdminOpen = false;
let isAdminAuthed = false;
let adminLogInterval = null;
let isConnectedState = false;
let adminTimeoutTimer = null;

const ADMIN_TIMEOUT_MS = 20 * 60 * 1000; // 20 minutes

function resetAdminTimeout() {
    if (!isAdminAuthed) return;
    clearTimeout(adminTimeoutTimer);
    adminTimeoutTimer = setTimeout(() => {
        doLogout();
        // Show a message in the login card after it appears
        setTimeout(() => {
            const err = document.getElementById('loginError');
            if (err) {
                err.textContent = 'Session expired after 20 minutes of inactivity.';
                err.classList.remove('hidden');
            }
        }, 50);
    }, ADMIN_TIMEOUT_MS);
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
async function init() {
    await fetchStatus();
    await fetchReadings();
    // Check if we're already logged in (session cookie may persist)
    const authRes = await fetch('/api/admin/check');
    const authData = await authRes.json();
    if (authData.authenticated) {
        isAdminAuthed = true;
    }

    setInterval(fetchStatus, 3000);
    setInterval(fetchReadings, 5000);

    // Reset admin timeout on any interaction within the admin panel
    const overlay = document.getElementById('adminOverlay');
    if (overlay) {
        ['click', 'keydown', 'input'].forEach(evt =>
            overlay.addEventListener(evt, () => { if (isAdminAuthed) resetAdminTimeout(); })
        );
    }
}

// ---------------------------------------------------------------------------
// Public: Status
// ---------------------------------------------------------------------------
async function fetchStatus() {
    try {
        const res = await fetch('/api/status');
        const status = await res.json();

        const dot = document.getElementById('statusDot');
        const text = document.getElementById('statusText');
        const meta = document.getElementById('statusMeta');

        dot.className = 'dot ' + (status.status_color || 'blue');
        text.textContent = status.status_text || 'Ready';
        meta.textContent = status.is_connected && status.port
            ? `Connected on ${status.port}`
            : 'Not connected';

        isConnectedState = status.is_connected;

        // Show cycle time on home page
        if (status.cycle_time != null) {
            const ctDisplay = document.getElementById('cycleTimeDisplay');
            const ctValue   = document.getElementById('cycleTimeValue');
            const secs = status.cycle_time;
            ctValue.textContent = secs >= 60
                ? `${(secs / 60).toFixed(1)} min (${secs}s)`
                : `${secs}s`;
            ctDisplay.style.display = 'flex';
        }

        // If admin is open and authed, keep connect button in sync
        if (isAdminAuthed) {
            const connectBtn = document.getElementById('connectBtn');
            if (connectBtn) {
                connectBtn.textContent = isConnectedState ? 'Disconnect' : 'Connect';
                connectBtn.className = isConnectedState ? 'btn-danger' : 'btn-primary';
            }
        }
    } catch (e) {
        console.error('Status fetch failed', e);
    }
}

// ---------------------------------------------------------------------------
// Public: Latest Readings
// ---------------------------------------------------------------------------
async function fetchReadings() {
    try {
        const res = await fetch('/api/readings');
        const nodes = await res.json();
        renderNodeCards(nodes);
        document.getElementById('readingsTimestamp').textContent =
            'Updated ' + new Date().toLocaleTimeString();
    } catch (e) {
        console.error('Readings fetch failed', e);
    }
}

function renderNodeCards(nodes) {
    const container = document.getElementById('nodeCards');
    if (!nodes || nodes.length === 0) {
        container.innerHTML = '<div class="loading-text">No readings yet. Waiting for data...</div>';
        return;
    }

    container.innerHTML = nodes.map(n => {
        const topVal  = n.top_f  !== null ? `${n.top_f} °F`  : '—';
        const botVal  = n.bot_f  !== null ? `${n.bot_f} °F`  : '—';
        const topTime = n.top_ts ? `<span class="reading-ts">${n.top_ts}</span>` : '';
        const botTime = n.bot_ts ? `<span class="reading-ts">${n.bot_ts}</span>` : '';
        return `
        <div class="node-card glass-panel">
            <div class="node-title">
                <span class="node-label">${escHtml(n.label)}</span>
            </div>
            <div class="reading-row">
                <div class="reading-item">
                    <span class="reading-label">Top</span>
                    <span class="reading-value">${topVal}</span>
                    ${topTime}
                </div>
                <div class="reading-divider"></div>
                <div class="reading-item">
                    <span class="reading-label">Bottom</span>
                    <span class="reading-value">${botVal}</span>
                    ${botTime}
                </div>
            </div>
        </div>`;
    }).join('');
}

function escHtml(str) {
    return String(str)
        .replace(/&/g,'&amp;')
        .replace(/</g,'&lt;')
        .replace(/>/g,'&gt;')
        .replace(/"/g,'&quot;');
}

// ---------------------------------------------------------------------------
// Admin Overlay
// ---------------------------------------------------------------------------
function openAdmin() {
    document.getElementById('adminOverlay').classList.remove('hidden');
    if (isAdminAuthed) {
        showAdminDash();
    } else {
        showAdminLogin();
    }
    isAdminOpen = true;
}

function closeAdmin() {
    document.getElementById('adminOverlay').classList.add('hidden');
    isAdminOpen = false;
    if (adminLogInterval) {
        clearInterval(adminLogInterval);
        adminLogInterval = null;
    }
}

function showAdminLogin() {
    document.getElementById('adminLogin').classList.remove('hidden');
    document.getElementById('adminDash').classList.add('hidden');
    document.getElementById('loginError').classList.add('hidden');
    document.getElementById('loginUser').value = '';
    document.getElementById('loginPass').value = '';
}

async function showAdminDash() {
    document.getElementById('adminLogin').classList.add('hidden');
    document.getElementById('adminDash').classList.remove('hidden');
    await fetchAdminStatus();
    await fetchPorts();
    await fetchNodes();
    lastLogIndex = 0;
    if (!adminLogInterval) {
        adminLogInterval = setInterval(fetchLogs, 1000);
    }
    resetAdminTimeout();
}

async function doLogin() {
    const user = document.getElementById('loginUser').value.trim();
    const pass = document.getElementById('loginPass').value;
    try {
        const res = await fetch('/api/admin/login', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({username: user, password: pass})
        });
        const data = await res.json();
        if (data.success) {
            isAdminAuthed = true;
            resetAdminTimeout();
            await showAdminDash();
        } else {
            document.getElementById('loginError').classList.remove('hidden');
        }
    } catch (e) {
        document.getElementById('loginError').classList.remove('hidden');
    }
}

async function doLogout() {
    await fetch('/api/admin/logout', {method: 'POST'});
    isAdminAuthed = false;
    clearTimeout(adminTimeoutTimer);
    adminTimeoutTimer = null;
    if (adminLogInterval) {
        clearInterval(adminLogInterval);
        adminLogInterval = null;
    }
    // Reset error message to default before showing login
    const err = document.getElementById('loginError');
    if (err) err.textContent = 'Invalid username or password.';
    showAdminLogin();
}

// ---------------------------------------------------------------------------
// Admin: Status & Config
// ---------------------------------------------------------------------------
async function fetchAdminStatus() {
    try {
        const res = await fetch('/api/admin/status');
        if (!res.ok) return;
        const status = await res.json();

        isConnectedState = status.is_connected;
        const connectBtn = document.getElementById('connectBtn');
        connectBtn.textContent = isConnectedState ? 'Disconnect' : 'Connect';
        connectBtn.className = isConnectedState ? 'btn-danger' : 'btn-primary';

        const suspendDur = document.getElementById('suspendDur');
        const wakeDur    = document.getElementById('wakeDur');
        const onDelay    = document.getElementById('onDelay');

        if (document.activeElement !== suspendDur) suspendDur.value = status.suspend_dur;
        if (document.activeElement !== wakeDur)    wakeDur.value    = status.wake_dur;
        if (document.activeElement !== onDelay)    onDelay.value    = status.on_delay;

        updateCyclePreview();
    } catch (e) {}
}

async function fetchPorts() {
    try {
        const res = await fetch('/api/ports');
        if (!res.ok) return;
        const ports = await res.json();
        const sel = document.getElementById('portSelect');
        sel.innerHTML = ports.length
            ? ports.map(p => `<option value="${p}">${p}</option>`).join('')
            : '<option value="">No ports found</option>';
    } catch (e) {}
}

async function toggleConnect() {
    const port = document.getElementById('portSelect').value;
    const baud = document.getElementById('baudSelect').value;
    if (!isConnectedState && !port) { alert('Please select a port'); return; }

    await fetch('/api/connect', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({port, baud})
    });
    await fetchAdminStatus();
    await fetchStatus();
}

async function updateConfig() {
    resetAdminTimeout();
    const payload = {
        suspend_dur: parseInt(document.getElementById('suspendDur').value),
        wake_dur:    parseInt(document.getElementById('wakeDur').value),
        on_delay:    parseInt(document.getElementById('onDelay').value),
    };
    const res = await fetch('/api/config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!data.success) {
        alert('Invalid setting: ' + data.msg);
        // Refresh fields back to current valid values
        await fetchAdminStatus();
    }
    updateCyclePreview();
}

async function setTxPower() {
    await fetch('/api/config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({tx_power: document.getElementById('txPower').value})
    });
}

function suspendNow() {
    fetch('/api/command', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({suspend_now: true})
    });
}

// ---------------------------------------------------------------------------
// Admin: Logs
// ---------------------------------------------------------------------------
async function fetchLogs() {
    if (!isAdminOpen || !isAdminAuthed) return;
    try {
        const res = await fetch(`/api/logs?since=${lastLogIndex}`);
        if (!res.ok) return;
        const data = await res.json();
        if (data.logs.length > 0) {
            const logArea = document.getElementById('logArea');
            data.logs.forEach(log => {
                const parts = log.split(' - ');
                logArea.innerHTML += `<div><span class="ts">${parts[0]}</span> - ${parts.slice(1).join(' - ')}</div>`;
            });
            lastLogIndex = data.next_index;
            if (document.getElementById('autoScroll').checked) {
                logArea.scrollTop = logArea.scrollHeight;
            }
        }
    } catch (e) {}
}

function clearLogs() {
    fetch('/api/logs/clear', {method: 'POST'}).then(() => {
        document.getElementById('logArea').innerHTML = '';
        lastLogIndex = 0;
    });
}

// ---------------------------------------------------------------------------
// Admin: Data Management
// ---------------------------------------------------------------------------
async function pruneData() {
    const pTime = document.getElementById('pruneTime').value;
    await fetch('/api/data/prune', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({prune_time: parseFloat(pTime)})
    });
    fetchReadings();
}

function clearData() {
    if (confirm('Are you sure you want to delete ALL sensor data from the database?\n\nThis cannot be undone. The table structure will be preserved.')) {
        fetch('/api/data/clear', {method: 'POST'}).then(() => fetchReadings());
    }
}

function uploadCDB(input) {
    if (!input.files || input.files.length === 0) return;
    const formData = new FormData();
    formData.append('file', input.files[0]);
    fetch('/api/cdb', {method: 'POST', body: formData})
        .then(res => res.json())
        .then(data => {
            alert(data.success ? data.msg : 'Error: ' + data.msg);
            if (data.success) fetchReadings();
        });
    input.value = '';
}

// ---------------------------------------------------------------------------
// Admin: Node Management
// ---------------------------------------------------------------------------
async function fetchNodes() {
    try {
        const res = await fetch('/api/admin/nodes');
        if (!res.ok) return;
        const nodes = await res.json();
        renderNodeList(nodes);
    } catch (e) {}
}

function renderNodeList(nodes) {
    const list = document.getElementById('nodeList');
    if (!list) return;
    if (nodes.length === 0) {
        list.innerHTML = '<div class="node-list-empty">No nodes configured. Add one below.</div>';
        return;
    }
    list.innerHTML = nodes.map(n => {
        const addrHex = '0x' + n.base_addr.toString(16).toUpperCase().padStart(4, '0');
        return `
        <div class="node-list-row">
            <span class="node-list-name">${escHtml(n.node_name)}</span>
            <span class="node-list-addr">${addrHex}</span>
            <button class="btn-icon btn-remove-node" title="Remove node"
                onclick="removeNode('${n.base_addr.toString(16).toUpperCase().padStart(4,'0')}', '${escHtml(n.node_name)}')">✕</button>
        </div>`;
    }).join('');
}

async function addNode() {
    resetAdminTimeout();
    const nameEl = document.getElementById('newNodeName');
    const addrEl = document.getElementById('newNodeAddr');
    const name = nameEl.value.trim();
    const addr = addrEl.value.trim();

    if (!name || !addr) {
        alert('Please enter both a node name and a unicast address.');
        return;
    }

    const res = await fetch('/api/admin/nodes', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({base_addr: addr, node_name: name})
    });
    const data = await res.json();
    if (data.success) {
        nameEl.value = '';
        addrEl.value = '';
        await fetchNodes();
        fetchReadings();
    } else {
        alert('Error: ' + data.msg);
    }
}

async function removeNode(addrHex, name) {
    resetAdminTimeout();
    if (!confirm(`Remove node "${name}" (0x${addrHex})?\n\nThis will not delete any recorded data — only the name mapping.`)) return;
    const res = await fetch(`/api/admin/nodes/${addrHex}`, {method: 'DELETE'});
    const data = await res.json();
    if (data.success) {
        await fetchNodes();
        fetchReadings();
    } else {
        alert('Error: ' + data.msg);
    }
}

// ---------------------------------------------------------------------------
// Cycle Time Preview (Admin)
// ---------------------------------------------------------------------------
function updateCyclePreview() {
    const suspendVal = parseInt(document.getElementById('suspendDur')?.value) || 0;
    const wakeVal    = parseInt(document.getElementById('wakeDur')?.value)    || 0;
    const delayVal   = parseInt(document.getElementById('onDelay')?.value)    || 0;
    const total      = suspendVal + wakeVal + delayVal;

    const previewEl = document.getElementById('cyclePreviewVal');
    if (!previewEl) return;

    const mins = (total / 60).toFixed(1);
    previewEl.textContent = total >= 60
        ? `${mins} minutes (${total}s)`
        : `${total} seconds`;
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------
window.addEventListener('load', init);
