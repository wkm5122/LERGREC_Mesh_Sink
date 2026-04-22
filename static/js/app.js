// DOM Elements
const portSelect = document.getElementById('portSelect');
const baudSelect = document.getElementById('baudSelect');
const connectBtn = document.getElementById('connectBtn');
const refreshPortsBtn = document.getElementById('refreshPortsBtn');

const suspendDur = document.getElementById('suspendDur');
const wakeDur = document.getElementById('wakeDur');
const onDelay = document.getElementById('onDelay');
const txPower = document.getElementById('txPower');
const autoCycleToggle = document.getElementById('autoCycleToggle');

const statusDot = document.getElementById('statusDot');
const statusText = document.getElementById('statusText');

const logArea = document.getElementById('logArea');
const autoScroll = document.getElementById('autoScroll');

const searchAddr = document.getElementById('searchAddr');
const pruneTime = document.getElementById('pruneTime');
const dataTableBody = document.getElementById('dataTableBody');

let lastLogIndex = 0;
let isConnectedState = false;

// Initialization
async function init() {
    await fetchPorts();
    await fetchStatus();
    fetchData(true);
    
    // Start Pollers
    setInterval(fetchStatus, 2000);
    setInterval(fetchLogs, 1000);
}

// API Functions
async function fetchPorts() {
    const res = await fetch('/api/ports');
    const ports = await res.json();
    portSelect.innerHTML = ports.map(p => `<option value="${p}">${p}</option>`).join('');
    if (ports.length === 0) portSelect.innerHTML = `<option value="">No ports found</option>`;
}

async function fetchStatus() {
    try {
        const res = await fetch('/api/status');
        const status = await res.json();
        
        isConnectedState = status.is_connected;
        if (isConnectedState) {
            connectBtn.textContent = 'Disconnect';
            connectBtn.classList.add('btn-danger');
            connectBtn.classList.remove('btn-primary');
            if(status.port && portSelect.value !== status.port) portSelect.value = status.port;
        } else {
            connectBtn.textContent = 'Connect';
            connectBtn.classList.add('btn-primary');
            connectBtn.classList.remove('btn-danger');
        }

        // Update dot
        statusDot.className = 'dot ' + (status.status_color || 'blue');
        statusText.textContent = status.status_text || 'Ready';

        // Set inputs if we haven't touched them
        if (document.activeElement !== suspendDur) suspendDur.value = status.suspend_dur;
        if (document.activeElement !== wakeDur) wakeDur.value = status.wake_dur;
        if (document.activeElement !== onDelay) onDelay.value = status.on_delay;
        
        autoCycleToggle.checked = status.auto_cycle;

    } catch(e) {
        console.error("Status fetch failed", e);
    }
}

async function fetchLogs() {
    try {
        const res = await fetch(`/api/logs?since=${lastLogIndex}`);
        const data = await res.json();
        
        if (data.logs.length > 0) {
            data.logs.forEach(log => {
                // simple split on ' - ' to style timestamp
                let parts = log.split(' - ');
                let html = `<div><span class="ts">${parts[0]}</span> - ${parts.slice(1).join(' - ')}</div>`;
                logArea.innerHTML += html;
            });
            lastLogIndex = data.next_index;
            if (autoScroll.checked) logArea.scrollTop = logArea.scrollHeight;
        }
    } catch(e) {}
}

async function fetchData(showAll = false) {
    if (showAll) searchAddr.value = '';
    const addr = searchAddr.value.trim();
    let url = '/api/data';
    if (addr && !showAll) url += `?addr=${encodeURIComponent(addr)}`;

    try {
        const res = await fetch(url);
        const data = await res.json();
        renderTable(data);
    } catch(e) {}
}

// Actions
refreshPortsBtn.addEventListener('click', fetchPorts);

connectBtn.addEventListener('click', async () => {
    const port = portSelect.value;
    const baud = baudSelect.value;
    if (!isConnectedState && !port) { alert("Please select a port"); return; }
    
    await fetch('/api/connect', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ port, baud })
    });
    fetchStatus();
});

async function updateConfig() {
    const payload = {
        suspend_dur: parseInt(suspendDur.value),
        wake_dur: parseInt(wakeDur.value),
        on_delay: parseInt(onDelay.value),
        auto_cycle: autoCycleToggle.checked
    };
    await fetch('/api/config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
    });
}

async function setTxPower() {
    await fetch('/api/config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ tx_power: txPower.value })
    });
}

function suspendNow() {
    fetch('/api/command', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ suspend_now: true })
    });
}

function clearLogs() {
    fetch('/api/logs/clear', { method: 'POST' }).then(() => {
        logArea.innerHTML = '';
        lastLogIndex = 0;
    });
}

function pruneData() {
    const pTime = pruneTime.value;
    fetch('/api/data/prune', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ prune_time: parseFloat(pTime) })
    }).then(() => fetchData());
}

function clearData() {
    if(confirm("Are you sure you want to delete all database entries?")) {
        fetch('/api/data/clear', { method: 'POST' }).then(() => fetchData());
    }
}

function uploadCDB(input) {
    if (!input.files || input.files.length === 0) return;
    const formData = new FormData();
    formData.append("file", input.files[0]);
    
    fetch('/api/cdb', {
        method: 'POST',
        body: formData
    }).then(res => res.json()).then(data => {
        if(data.success) {
            alert(data.msg);
            fetchData();
        } else {
            alert("Error: " + data.msg);
        }
    });
    input.value = ''; // reset
}

function renderTable(data) {
    dataTableBody.innerHTML = data.map(r => `
        <tr>
            <td>${r.id}</td>
            <td>${r.timestamp.substring(0, 19).replace('T', ' ')}</td>
            <td>${r.name}</td>
            <td>${r.addr}</td>
            <td>${r.location}</td>
            <td>${r.uuid}</td>
            <td>${r.value}</td>
        </tr>
    `).join('');
    if(data.length === 0) {
        dataTableBody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: #888;">No data records found.</td></tr>`;
    }
}

// Run
window.addEventListener('load', init);
