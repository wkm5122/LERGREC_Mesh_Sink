document.addEventListener('DOMContentLoaded', () => {
    // --- DOM Elements: Admin Auth ---
    const btnShowLogin = document.getElementById('btn-show-login');
    const loginModal = document.getElementById('login-modal');
    const btnCloseModal = document.getElementById('btn-close-modal');
    const btnSubmitLogin = document.getElementById('btn-submit-login');
    const adminUsername = document.getElementById('admin-username');
    const adminPassword = document.getElementById('admin-password');
    const loginError = document.getElementById('login-error');
    
    const publicView = document.getElementById('public-view');
    const adminView = document.getElementById('admin-view');
    const btnLogout = document.getElementById('btn-logout');

    // --- DOM Elements: Controls & Data ---
    const btnSetSuspend = document.getElementById('btn-set-suspend');
    const btnSetTx = document.getElementById('btn-set-tx');
    const btnSuspendNow = document.getElementById('btn-suspend-now');
    const toggleAutoCycle = document.getElementById('toggle-auto-cycle');
    const btnClearLog = document.getElementById('btn-clear-log');
    const btnDownloadCsv = document.getElementById('btn-download-csv');
    const btnClearDb = document.getElementById('btn-clear-db'); // New DB Wipe Button

    const inputSuspend = document.getElementById('suspend-duration');
    const inputTx = document.getElementById('tx-power');
    const inputWake = document.getElementById('wake-duration');
    const inputDelay = document.getElementById('firmware-delay');

    const cycleStatusDot = document.getElementById('cycle-status-dot');
    const cycleStatusText = document.getElementById('cycle-status-text');
    const latestDataContainer = document.getElementById('latest-data');
    const logWindow = document.getElementById('log-window');

    // --- Admin Authentication Logic ---
    btnShowLogin.addEventListener('click', () => {
        loginModal.classList.remove('hidden');
        adminUsername.focus();
    });

    btnCloseModal.addEventListener('click', () => {
        loginModal.classList.add('hidden');
        loginError.classList.add('hidden');
    });

    btnSubmitLogin.addEventListener('click', () => {
        const user = adminUsername.value;
        const pass = adminPassword.value;

        if (user === 'meshadmin' && pass === 'SquashyGrapes2026') {
            loginModal.classList.add('hidden');
            adminView.classList.remove('hidden');
            btnShowLogin.classList.add('hidden');
            adminUsername.value = '';
            adminPassword.value = '';
            loginError.classList.add('hidden');
        } else {
            loginError.classList.remove('hidden');
            adminPassword.value = '';
        }
    });

    btnLogout.addEventListener('click', () => {
        adminView.classList.add('hidden');
        btnShowLogin.classList.remove('hidden');
    });

    adminPassword.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') btnSubmitLogin.click();
    });

    // --- Mesh Logic & Controls ---
    btnDownloadCsv.addEventListener('click', () => {
        window.location.href = '/api/export_csv';
    });

    fetch('/api/config')
        .then(res => res.json())
        .then(data => {
            inputSuspend.value = data.suspend_duration;
            inputWake.value = data.wake_duration;
            inputDelay.value = data.firmware_delay;
            inputTx.value = data.tx_power;
            toggleAutoCycle.checked = data.auto_cycle;
        });

    function sendConfig(payload) {
        fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        }).then(res => res.json())
          .then(data => console.log('Config updated:', data))
          .catch(err => console.error('Error updating config:', err));
    }

    btnSetSuspend.addEventListener('click', () => sendConfig({ suspend_duration: parseInt(inputSuspend.value) }));
    btnSetTx.addEventListener('click', () => sendConfig({ tx_power: parseInt(inputTx.value) }));
    inputWake.addEventListener('change', () => sendConfig({ wake_duration: parseInt(inputWake.value) }));
    inputDelay.addEventListener('change', () => sendConfig({ firmware_delay: parseInt(inputDelay.value) }));
    toggleAutoCycle.addEventListener('change', (e) => sendConfig({ auto_cycle: e.target.checked }));

    btnSuspendNow.addEventListener('click', () => {
        fetch('/api/command', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cmd: "mesh_app set_onoff_temp" }) 
        }).then(res => res.json())
          .then(data => alert('Suspend command sent to gateway.'));
    });

    btnClearLog.addEventListener('click', () => logWindow.innerHTML = '');

    // Database Wipe Logic
    btnClearDb.addEventListener('click', () => {
        if(confirm("WARNING: This will permanently delete all sensor data from the database. The CSV will be empty. Proceed?")) {
            fetch('/api/admin/clear', { method: 'POST' })
            .then(res => res.json())
            .then(data => alert(data.message));
        }
    });

    // --- Polling Data, Status, and Logs ---
    setInterval(() => {
        fetch('/api/status')
            .then(res => res.json())
            .then(data => {
                if(data.cycle_state === "Suspend") {
                    cycleStatusDot.className = "dot red";
                    cycleStatusText.innerText = "Suspended";
                } else if(data.cycle_state === "Wake") {
                    cycleStatusDot.className = "dot green";
                    cycleStatusText.innerText = "Wake Phase (Listening)";
                } else {
                    cycleStatusDot.className = "dot blue";
                    cycleStatusText.innerText = "Ready (Manual Mode)";
                }
            });

        fetch('/api/data/latest')
            .then(res => res.json())
            .then(data => {
                latestDataContainer.innerHTML = '';
                if(data.length === 0) {
                    latestDataContainer.innerHTML = '<p style="color:#aaa;">No data received yet.</p>';
                } else {
                    data.forEach(node => {
                        const div = document.createElement('div');
                        div.className = 'node-card';
                        // Ensure formatting to 1 decimal place with °F
                        div.innerHTML = `
                            <h3>${node.name} <span style="font-size:12px;color:#888;">(0x${node.addr.toString(16)})</span></h3>
                            <div class="val">${parseFloat(node.value).toFixed(1)}°F</div>
                            <div class="time">${node.timestamp}</div>
                        `;
                        latestDataContainer.appendChild(div);
                    });
                }
            });

        if (!adminView.classList.contains('hidden')) {
            fetch('/api/logs')
                .then(res => res.json())
                .then(data => {
                    logWindow.innerHTML = data.logs.join('<br>');
                    logWindow.scrollTop = logWindow.scrollHeight;
                });
        }
    }, 2000);
});