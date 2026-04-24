# Sink Data Handler (Web Server Edition)

The Sink Data Handler (`sink_handler.py`) is a Python application that interfaces with a BLE Mesh Sink Device over a serial connection. It logs incoming sensor data to a local SQLite database (`sensor_data.db`) and allows administrators to configure the mesh network's power-saving cycle.

It runs entirely headlessly as a **Flask Web Server**, designed to run in the background on a Raspberry Pi while you access the Vineyard-themed interface from any browser on the network.

## Prerequisites

- Python 3.x
- `pyserial` (for serial communication)
- `flask` (for the web server)

```bash
pip install pyserial flask
```

## Running the Application

```bash
python sink_handler.py
```

The server starts on `http://0.0.0.0:5000/`. Access it from any device on the same network:
`http://<IP_ADDRESS_OF_PI>:5000`

---

## Interface Overview

### Home Page (Public)

The home page is accessible to anyone on the network and shows:

- **System Status** — Live connection status (connected port, auto-cycle phase, color-coded indicator).
- **Latest Node Readings** — Cards showing the most recent Top and Bottom temperature readings (°F) for each node, with timestamps.
- **Download CSV** — Exports all collected data as a formatted CSV file (see CSV format below).
- **Admin Button** — Opens the admin panel (password protected).

### Admin Panel

Click the **⚙ Admin** button in the top-right corner. You will be prompted for credentials:

- **Username:** `meshadmin`
- **Password:** `SquashyGrapes2026`

Once authenticated, the admin panel provides:

#### Serial Connection
Connect or disconnect from the sink device. Select the serial port and baud rate (default 115200).

#### Mesh Cycle Settings
- **Suspend Duration (s)** — How long the mesh sleeps per cycle.
- **Wake Duration (s)** — How long the mesh stays awake per cycle.
- **Firmware ON Delay (s)** — Time the device remains on after triggering a suspend (matches compiled firmware constant, default 20s).
- **Publish TX Power (dBm)** — Signal strength from −40 to +8 dBm.
- **Suspend Mesh Now** — Immediately triggers the suspend sequence.
- **Enable Auto Cycle** — Toggle to automatically cycle the mesh between Wake and Suspend phases.

#### Data Management
- **Load CDB JSON** — Upload an nRF Mesh CDB `.json` file to map element addresses to human-readable names. Persists across reboots.
- **Prune (s)** — Removes duplicate readings that occurred within the specified window (seconds) for the same sensor.
- **Clear All Data** — Deletes all rows from `sensor_data` while keeping the table structure intact. Useful for clearing test data before a real deployment.

#### System Log
Real-time console showing serial RX, database inserts, auto-cycle transitions, and errors.

---

## Temperature Display

All temperatures are stored internally in **Celsius** as floating-point values for precision. They are converted to **Fahrenheit** for all display and export purposes:

- **Web UI:** Displayed as `XX.X °F` (1 decimal place).
- **CSV export:** `Top (°F)` and `Bottom (°F)` columns, 1 decimal place.

---

## CSV Export Format

Each row in the exported CSV represents one **paired reading event** for a node (one Top + one Bottom reading). Columns:

| Column | Description |
|---|---|
| Timestamp | Date/time of the most recent reading in the pair |
| Node Address | Hex address of the node (e.g. `0x1100`) |
| Node Name | Human-readable name from CDB, or auto-generated |
| Top (°F) | Top sensor reading in Fahrenheit |
| Bottom (°F) | Bottom sensor reading in Fahrenheit |

Rows are sorted chronologically.

---

## Auto-Connect

The application automatically attempts to connect to the serial device on startup. Configure at the top of `sink_handler.py`:

```python
PI_AUTO_CONNECT  = True
PI_SERIAL_PORT   = "/dev/lergrec_gateway"
PI_BAUD_RATE     = 115200
PI_RETRY_DELAY   = 5     # seconds between retries
PI_MAX_RETRIES   = 12    # set 0 to retry indefinitely
```

---

## Data Format

The application expects incoming serial JSON:
```json
{"name": "top_sensor", "addr": 4352, "value": 24.7}
```

- `name` — Sensor identifier. Names containing `"top"` are treated as top readings; `"bot"` as bottom.
- `addr` — 16-bit unicast address of the element.
- `value` — Temperature in Celsius (floating-point for precision).
