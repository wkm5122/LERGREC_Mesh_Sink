import os
import time
import json
import sqlite3
import threading
from datetime import datetime
import serial
import serial.tools.list_ports
from flask import Flask, render_template, request, jsonify, Response

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Raspberry Pi Auto-Connect Configuration
# ---------------------------------------------------------------------------
# Set PI_AUTO_CONNECT = True to have the program automatically connect to the
# sink device on startup without any manual action in the web UI.
#
# PI_SERIAL_PORT : the device path for the UART-to-USB adapter.
#   Common values on Raspberry Pi:
#     /dev/ttyUSB0  — CP210x / CH340 USB-UART adapters (most common)
#     /dev/ttyACM0  — CDC-ACM class devices
# PI_BAUD_RATE    : must match the baud rate configured on the sink device.
# PI_RETRY_DELAY  : seconds to wait between each connection attempt.
# PI_MAX_RETRIES  : total attempts before giving up (0 = try forever).
# ---------------------------------------------------------------------------
PI_AUTO_CONNECT  = True
PI_SERIAL_PORT   = "/dev/lergrec_gateway"
PI_BAUD_RATE     = 115200
PI_RETRY_DELAY   = 5    # seconds between retries
PI_MAX_RETRIES   = 12   # give up after ~60 s; set 0 to retry indefinitely


class SinkHandlerCore:
    def __init__(self):
        self.serial_port = None
        self.is_connected = False
        self.stop_threads = False
        self.last_sent_duration = None
        
        self.db_file = "sensor_data.db"
        self.settings_file = "settings.json"
        
        # State
        self.suspend_dur = 60
        self.wake_dur = 30
        self.on_delay = 20
        self.auto_cycle_enabled = False
        self.current_status = "Ready"
        self.current_status_color = "blue"

        # Logs
        self.logs = []
        
        self._init_db()
        self._load_settings()

    def _init_db(self):
        with sqlite3.connect(self.db_file, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sensor_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    name TEXT,
                    addr INTEGER,
                    value REAL
                )
            ''')
            # Clean up previously logged 32-bit addresses
            cursor.execute("UPDATE sensor_data SET addr = addr & 65535 WHERE addr < 0 OR addr > 65535")
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS node_elements (
                    unicast_addr INTEGER PRIMARY KEY,
                    parent_uuid TEXT,
                    location TEXT,
                    name TEXT
                )
            ''')
            conn.commit()

    def _load_settings(self):
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r') as f:
                    settings = json.load(f)
                    cdb_file = settings.get("cdb_file")
                    if cdb_file and os.path.exists(cdb_file):
                        self.log(f"Auto-loading CDB JSON from settings...")
                        self.load_cdb_json(filepath=cdb_file)
        except Exception as e:
            self.log(f"Error loading settings: {e}")

    def _save_settings(self, key, value):
        settings = {}
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, 'r') as f:
                    settings = json.load(f)
            except:
                pass
        settings[key] = value
        try:
            with open(self.settings_file, 'w') as f:
                json.dump(settings, f, indent=4)
        except Exception as e:
            self.log(f"Error saving settings: {e}")

    def log(self, message):
        timestamp = datetime.now().strftime('%H:%M:%S')
        entry = f"{timestamp} - {message}"
        self.logs.append(entry)
        # Keep logs manageable
        if len(self.logs) > 1000:
            self.logs = self.logs[-500:]
        print(entry)

    def update_status(self, text, color):
        self.current_status = text
        self.current_status_color = color

    def get_ports(self):
        return [p.device for p in serial.tools.list_ports.comports()]

    def connect(self, port, baud):
        if self.is_connected:
            return False, "Already connected"
        try:
            baud = int(baud)
            self.serial_port = serial.Serial(port, baud, timeout=1)
            self.is_connected = True
            self.stop_threads = False
            self.read_thread = threading.Thread(target=self.read_serial, daemon=True)
            self.read_thread.start()
            
            self.auto_cycle_thread = threading.Thread(target=self.auto_cycle_loop, daemon=True)
            self.auto_cycle_thread.start()
            
            self.log(f"Connected to {port} at {baud} baud")
            return True, "Connected"
        except Exception as e:
            self.log(f"Connection Failed: {e}")
            return False, str(e)

    def disconnect(self):
        if not self.is_connected:
            return False, "Not connected"
        
        self.is_connected = False
        self.stop_threads = True
        if self.serial_port:
            self.serial_port.close()
            self.serial_port = None
        self.log("Disconnected")
        self.update_status("Status: Ready", "blue")
        return True, "Disconnected"

    def send_command(self, cmd):
        if self.serial_port and self.is_connected:
            full_cmd = f"{cmd}\r\n"
            try:
                for char in full_cmd:
                    self.serial_port.write(char.encode())
                    self.serial_port.flush() 
                    time.sleep(0.05)
                self.log(f"Sent: {cmd}")
                return True
            except Exception as e:
                self.log(f"Send Error: {e}")
                return False
        else:
            self.log("Error: Not Connected")
            return False

    def set_duration(self, dur):
        try:
            dur = int(dur)
            self.suspend_dur = dur
            self.send_command(f"mesh_app set_level {dur}")
            self.last_sent_duration = dur
            return True
        except ValueError:
            self.log("Invalid Duration")
            return False

    def set_tx_power(self, pwr_lvl):
        if pwr_lvl is not None and str(pwr_lvl).strip():
            self.send_command(f"publish_tx_power {pwr_lvl}")
            return True
        else:
            self.log("Invalid TX Power Level")
            return False

    def suspend_mesh(self):
        self.send_command("mesh_app set_onoff_temp")

    def toggle_auto_cycle(self, enable):
        self.auto_cycle_enabled = enable is True or str(enable).lower() == 'true'
        if self.auto_cycle_enabled:
            self.log("Auto Cycle ENABLED")
        else:
            self.log("Auto Cycle DISABLED")
            self.update_status("Status: Ready", "blue")

    def read_serial(self):
        while not self.stop_threads and self.is_connected:
            try:
                if self.serial_port and self.serial_port.in_waiting:
                    line = self.serial_port.readline().decode('utf-8', errors='ignore').strip()
                    if line:
                        self.process_line(line)
                else:
                    time.sleep(0.1)
            except Exception as e:
                self.log(f"Read Error: {e}")
                break
        self.log("Read Thread Stopped")

    def process_line(self, line):
        self.log(f"RX: {line}") 
        
        if "{" in line and "}" in line:
            try:
                start = line.find('{')
                end = line.rfind('}') + 1
                json_str = line[start:end]
                
                data = json.loads(json_str)
                
                if "name" in data and "value" in data:
                    name = data["name"]
                    value = data["value"]
                    try:
                        addr = int(data.get("addr", 0)) & 0xFFFF
                    except ValueError:
                        addr = 0
                    
                    self.log(f"DATA: {name} (Addr: {addr}) = {value}")
                    
                    try:
                        with sqlite3.connect(self.db_file, check_same_thread=False) as conn:
                            cursor = conn.cursor()
                            cursor.execute('''
                                INSERT INTO sensor_data (timestamp, name, addr, value)
                                VALUES (?, ?, ?, ?)
                            ''', (datetime.now().isoformat(), name, addr, value))
                            conn.commit()
                    except Exception as e:
                        self.log(f"DB Error: {e}")
            except json.JSONDecodeError:
                pass
            except Exception as e:
                self.log(f"Parse Error: {e}")

    def auto_cycle_loop(self):
        state = "WAKE"
        timer = 0
        
        while not self.stop_threads and self.is_connected:
            if not self.auto_cycle_enabled:
                state = "WAKE"
                timer = 0
                time.sleep(0.5)
                continue

            if state == "WAKE":
                if timer == 0:
                    self.update_status(f"Auto Status: WAKE Phase ({self.wake_dur}s)", "green")
                
                time.sleep(1)
                timer += 1
                
                if timer >= self.wake_dur:
                    state = "SUSPEND_INIT"
                    timer = 0
                    
            elif state == "SUSPEND_INIT":
                if self.last_sent_duration != self.suspend_dur:
                    self.log(f"Updating Suspend Duration to {self.suspend_dur}s")
                    self.send_command(f"mesh_app set_level {self.suspend_dur}")
                    self.last_sent_duration = self.suspend_dur
                else:
                    self.log(f"Suspend Duration ({self.suspend_dur}s) unchanged, skipping set_level")
                
                time.sleep(0.5)
                self.send_command("mesh_app set_onoff_temp")
                self.update_status(f"Auto Status: SUSPEND Phase ({self.on_delay}s ON, {self.suspend_dur}s OFF)", "red")
                
                state = "SUSPEND_WAIT"
                timer = 0
            
            elif state == "SUSPEND_WAIT":
                # Wait for both the ON delay + Suspend
                total_wait = self.suspend_dur + self.on_delay
                time.sleep(1)
                timer += 1
                
                if timer >= total_wait:
                    state = "WAKE"
                    timer = 0

    def query_data(self, addr=None):
        with sqlite3.connect(self.db_file, check_same_thread=False) as conn:
            cursor = conn.cursor()
            if addr:
                try:
                    addr_int = int(addr)
                    query = '''
                        SELECT s.id, s.timestamp, s.name, s.addr, 
                               IFNULL(n.location, 'Unknown'), IFNULL(n.parent_uuid, 'Unknown'), 
                               s.value 
                        FROM sensor_data s
                        LEFT JOIN node_elements n ON s.addr = n.unicast_addr
                        WHERE s.addr = ? 
                        ORDER BY s.id DESC
                    '''
                    cursor.execute(query, (addr_int,))
                except ValueError:
                    return []
            else:
                query = '''
                    SELECT s.id, s.timestamp, s.name, s.addr, 
                           IFNULL(n.location, 'Unknown'), IFNULL(n.parent_uuid, 'Unknown'), 
                           s.value 
                    FROM sensor_data s
                    LEFT JOIN node_elements n ON s.addr = n.unicast_addr
                    ORDER BY s.id DESC
                '''
                cursor.execute(query)
            
            rows = cursor.fetchall()
            return rows

    def clear_db_data(self):
        with sqlite3.connect(self.db_file, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM sensor_data")
            conn.commit()

    def prune_duplicates(self, prune_seconds):
        try:
            prune_seconds = float(prune_seconds)
            with sqlite3.connect(self.db_file, check_same_thread=False) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id, timestamp, name, addr, value FROM sensor_data ORDER BY timestamp ASC")
                rows = cursor.fetchall()
                
                if not rows:
                    self.log("No data to prune.")
                    return 0
                
                ids_to_delete = []
                last_seen = {} 
                
                for r in rows:
                    r_id, ts_str, name, addr, val = r
                    try:
                        ts = datetime.fromisoformat(ts_str)
                    except ValueError:
                        continue 
                    
                    key = (name, addr, val)
                    
                    if key in last_seen:
                        last_ts, _ = last_seen[key]
                        time_diff = (ts - last_ts).total_seconds()
                        if 0 <= time_diff <= prune_seconds:
                            ids_to_delete.append(r_id)
                            continue 
                        
                    last_seen[key] = (ts, r_id)

                if ids_to_delete:
                    cursor.executemany("DELETE FROM sensor_data WHERE id = ?", [(i,) for i in ids_to_delete])
                    conn.commit()
                    self.log(f"Pruned {len(ids_to_delete)} duplicate readings.")
                    return len(ids_to_delete)
                else:
                    self.log(f"No duplicates found within {prune_seconds} seconds.")
                    return 0
        except Exception as e:
            self.log(f"Prune Error: {e}")
            return -1

    def load_cdb_json(self, filepath=None, json_content=None):
        try:
            if filepath:
                with open(filepath, 'r', encoding='utf-8') as f:
                    cdb = json.load(f)
            elif json_content:
                cdb = json.loads(json_content)
                filepath = "uploaded_cdb.json" # Just for logs
            else:
                return False, "No data provided"
                
            nodes = cdb.get("nodes", [])
            mapped_count = 0
            
            with sqlite3.connect(self.db_file, check_same_thread=False) as conn:
                cursor = conn.cursor()
                
                for node in nodes:
                    uuid = node.get("UUID")
                    if not uuid: continue
                    
                    node_unicast_str = node.get("unicastAddress", "0000")
                    try:
                        node_base_addr = int(node_unicast_str, 16)
                    except ValueError:
                        continue
                        
                    for element in node.get("elements", []):
                        models = element.get("models", [])
                        
                        is_sensor = False
                        for model in models:
                            mid = model.get("modelId")
                            if mid in ("1100", "1102"):
                                is_sensor = True
                                break
                                
                        if not is_sensor:
                            continue
                            
                        element_index = element.get("index", 0)
                        element_unicast_addr = node_base_addr + element_index
                        
                        location = element.get("location", "0000")
                        name = element.get("name", f"Element: 0x{element_unicast_addr:04X}")
                        
                        cursor.execute('''
                            INSERT INTO node_elements (unicast_addr, parent_uuid, location, name)
                            VALUES (?, ?, ?, ?)
                            ON CONFLICT(unicast_addr) DO UPDATE SET
                                parent_uuid=excluded.parent_uuid,
                                location=excluded.location,
                                name=excluded.name
                        ''', (element_unicast_addr, uuid, location, name))
                        
                        mapped_count += 1
                        
                conn.commit()
            
            self.log(f"Successfully mapped {mapped_count} sensor elements from CDB.")
            if filepath and not json_content:
                self._save_settings("cdb_file", filepath)
            return True, f"Mapped {mapped_count} elements."
            
        except Exception as e:
            self.log(f"Error parsing CDB JSON: {e}")
            return False, str(e)


def auto_connect_on_startup(core_instance):
    """Background thread: try to connect to PI_SERIAL_PORT at startup.

    Waits up to PI_MAX_RETRIES * PI_RETRY_DELAY seconds for the USB-UART
    adapter to enumerate (it may not be ready the instant the OS boots).
    Set PI_MAX_RETRIES = 0 to retry indefinitely.
    """
    if not PI_AUTO_CONNECT:
        return

    core_instance.log(f"Auto-connect: looking for {PI_SERIAL_PORT} @ {PI_BAUD_RATE} baud ...")
    attempt = 0
    while True:
        attempt += 1
        time.sleep(PI_RETRY_DELAY)

        available_ports = core_instance.get_ports()
        if os.path.exists(PI_SERIAL_PORT):
            core_instance.log(f"Auto-connect: {PI_SERIAL_PORT} found (attempt {attempt}), connecting ...")
            success, msg = core_instance.connect(PI_SERIAL_PORT, PI_BAUD_RATE)
            if success:
                core_instance.log("Auto-connect: connected successfully.")
            else:
                core_instance.log(f"Auto-connect: connection failed — {msg}")
            return
        else:
            core_instance.log(
                f"Auto-connect: {PI_SERIAL_PORT} not available yet "
                f"(attempt {attempt}/{PI_MAX_RETRIES if PI_MAX_RETRIES else '∞'}) "
                f"— retrying in {PI_RETRY_DELAY}s ..."
            )

        if PI_MAX_RETRIES and attempt >= PI_MAX_RETRIES:
            core_instance.log(
                f"Auto-connect: gave up after {attempt} attempts. "
                "Connect manually via the web UI."
            )
            return


core = SinkHandlerCore()

# --- Flask Routes ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/status', methods=['GET'])
def get_status():
    return jsonify({
        "is_connected": core.is_connected,
        "port": core.serial_port.port if core.serial_port else None,
        "status_text": core.current_status,
        "status_color": core.current_status_color,
        "auto_cycle": core.auto_cycle_enabled,
        "suspend_dur": core.suspend_dur,
        "wake_dur": core.wake_dur,
        "on_delay": core.on_delay
    })

@app.route('/api/ports', methods=['GET'])
def get_ports():
    return jsonify(core.get_ports())

@app.route('/api/connect', methods=['POST'])
def connect():
    data = request.json
    if core.is_connected:
        success, msg = core.disconnect()
        return jsonify({"success": success, "msg": msg})
    else:
        success, msg = core.connect(data.get('port'), data.get('baud', 115200))
        return jsonify({"success": success, "msg": msg})

@app.route('/api/config', methods=['POST'])
def config():
    data = request.json
    if 'suspend_dur' in data:
        core.set_duration(data['suspend_dur'])
    if 'wake_dur' in data:
        try:
            core.wake_dur = int(data['wake_dur'])
        except: pass
    if 'on_delay' in data:
        try:
            core.on_delay = int(data['on_delay'])
        except: pass
    if 'auto_cycle' in data:
        core.toggle_auto_cycle(data['auto_cycle'])
    if 'tx_power' in data:
        core.set_tx_power(data['tx_power'])
        
    return jsonify({"success": True})

@app.route('/api/command', methods=['POST'])
def command():
    data = request.json
    if 'cmd' in data:
        core.send_command(data['cmd'])
    elif 'suspend_now' in data:
        core.suspend_mesh()
    return jsonify({"success": True})

@app.route('/api/data', methods=['GET'])
def get_data():
    addr = request.args.get('addr')
    rows = core.query_data(addr)
    # Convert tuples to list of dicts
    data = [
        {"id": r[0], "timestamp": r[1], "name": r[2], "addr": r[3], "location": r[4], "uuid": r[5], "value": r[6]}
        for r in rows
    ]
    return jsonify(data)

@app.route('/api/data/prune', methods=['POST'])
def prune_data():
    prune_time = request.json.get('prune_time', 4)
    res = core.prune_duplicates(prune_time)
    return jsonify({"success": True, "pruned": res})

@app.route('/api/data/clear', methods=['POST'])
def clear_data():
    core.clear_db_data()
    return jsonify({"success": True})

@app.route('/api/data/export', methods=['GET'])
def export_data():
    import csv
    from io import StringIO
    rows = core.query_data()
    
    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(["ID", "Timestamp", "Name", "Address", "Location", "UUID", "Value"])
    cw.writerows(rows)
    
    return Response(
        si.getvalue(),
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=sensor_data.csv"}
    )

@app.route('/api/cdb', methods=['POST'])
def upload_cdb():
    if 'file' not in request.files:
        return jsonify({"success": False, "msg": "No file part"})
    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "msg": "No selected file"})

    if file:
        # Save the file next to sink_handler.py so it persists across restarts.
        # _load_settings() will re-load it automatically on the next boot.
        save_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploaded_cdb.json")
        content = file.read().decode('utf-8')
        try:
            with open(save_path, 'w', encoding='utf-8') as f:
                f.write(content)
        except Exception as e:
            return jsonify({"success": False, "msg": f"Failed to save CDB file: {e}"})
        success, msg = core.load_cdb_json(filepath=save_path)
        return jsonify({"success": success, "msg": msg})

@app.route('/api/logs', methods=['GET'])
def get_logs():
    since = request.args.get('since', 0, type=int)
    new_logs = core.logs[since:]
    return jsonify({
        "logs": new_logs,
        "next_index": len(core.logs)
    })

@app.route('/api/logs/clear', methods=['POST'])
def clear_logs():
    core.logs = []
    return jsonify({"success": True})


if __name__ == "__main__":
    # Ensure templates/static directories exist for initial run
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static/css', exist_ok=True)
    os.makedirs('static/js', exist_ok=True)

    # Kick off auto-connect in the background so Flask can start immediately.
    # The web UI will show "Connecting..." in the log while retries happen.
    if PI_AUTO_CONNECT:
        ac_thread = threading.Thread(
            target=auto_connect_on_startup, args=(core,), daemon=True
        )
        ac_thread.start()

    # Run the server accessible on all network interfaces (http://<pi-ip>:5000)
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
