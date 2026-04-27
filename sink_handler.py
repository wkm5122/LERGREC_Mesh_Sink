import os
import time
import json
import sqlite3
import threading
from datetime import datetime
import serial
import serial.tools.list_ports
from flask import Flask, render_template, request, jsonify, Response, session
from functools import wraps

app = Flask(__name__)
app.secret_key = "LERGREC-SinkHandler-2026-SecretKey"  # Stable key for session persistence

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

# ---------------------------------------------------------------------------
# Admin Credentials
# ---------------------------------------------------------------------------
ADMIN_USERNAME = "meshadmin"
ADMIN_PASSWORD = "SquashyGrapes2026"


def celsius_to_fahrenheit(c):
    """Convert a Celsius float to Fahrenheit, returning a float."""
    return (float(c) * 9.0 / 5.0) + 32.0


def require_admin(f):
    """Decorator: returns 401 if admin is not authenticated."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return jsonify({"success": False, "msg": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


class SinkHandlerCore:
    def __init__(self):
        self.serial_port = None
        self.is_connected = False
        self.stop_threads = False
        self.last_sent_duration = None
        
        self.db_file = "sensor_data.db"
        self.settings_file = "settings.json"
        
        # State
        self.interval_minutes = 15  # User-facing capture interval in minutes
        self.wake_dur = 30
        self.on_delay = 20
        self.auto_cycle_enabled = True  # Always enabled — continuous cycling is required
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
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS managed_nodes (
                    base_addr INTEGER PRIMARY KEY,
                    node_name TEXT NOT NULL
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

    @property
    def suspend_dur(self):
        """Suspend duration in seconds, derived from the user-set capture interval."""
        return max(1, self.interval_minutes * 60 - self.wake_dur - self.on_delay)

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

    def _group_addrs_into_nodes(self, addrs):
        """
        Groups a sorted list of unicast addresses into node pairs.
        BLE mesh assigns each element a consecutive address (base, base+1, ...).
        A physical node with a top + bottom sensor therefore occupies two
        consecutive addresses.  We pair them up: (15,16), (17,18), etc.
        Any address whose partner is missing still gets its own node entry
        so readings are never silently dropped.
        """
        pairs = []
        used = set()
        for addr in sorted(addrs):
            if addr in used:
                continue
            partner = addr + 1
            if partner in addrs and partner not in used:
                pairs.append((addr, partner))
                used.add(addr)
                used.add(partner)
            else:
                pairs.append((addr, None))
                used.add(addr)
        return pairs

    def _resolve_node_name(self, cursor, top_addr):
        """
        Returns the display name for a node whose top-sensor element address is top_addr.
        Priority:
          1. managed_nodes table  (base_addr = top_addr - 1, because top element is base+1)
          2. managed_nodes table  (base_addr = top_addr, for single-element nodes)
          3. node_elements table  (CDB-imported name)
          4. Fallback hex string
        """
        # The sensor node's base unicast is one below the top element (000E -> 000F top)
        for candidate_base in (top_addr - 1, top_addr):
            cursor.execute(
                "SELECT node_name FROM managed_nodes WHERE base_addr = ?", (candidate_base,)
            )
            row = cursor.fetchone()
            if row:
                return row[0]
        # CDB fallback
        cursor.execute(
            "SELECT name FROM node_elements WHERE unicast_addr = ?", (top_addr,)
        )
        row = cursor.fetchone()
        if row:
            return row[0]
        return f"Node 0x{top_addr:04X}"

    # ------------------------------------------------------------------
    # Managed Nodes CRUD
    # ------------------------------------------------------------------
    def get_managed_nodes(self):
        with sqlite3.connect(self.db_file, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT base_addr, node_name FROM managed_nodes ORDER BY base_addr")
            return [{"base_addr": r[0], "node_name": r[1]} for r in cursor.fetchall()]

    def add_managed_node(self, base_addr_hex, node_name):
        """Add or update a managed node. base_addr_hex is a hex string like '000E'."""
        try:
            base_addr = int(base_addr_hex.strip(), 16)
        except ValueError:
            return False, "Invalid hex address."
        if not node_name.strip():
            return False, "Node name cannot be empty."
        with sqlite3.connect(self.db_file, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO managed_nodes (base_addr, node_name) VALUES (?, ?) "
                "ON CONFLICT(base_addr) DO UPDATE SET node_name = excluded.node_name",
                (base_addr, node_name.strip())
            )
            conn.commit()
        self.log(f"Managed node added/updated: 0x{base_addr:04X} = '{node_name.strip()}'")
        return True, "Node saved."

    def remove_managed_node(self, base_addr_hex):
        try:
            base_addr = int(base_addr_hex.strip(), 16)
        except ValueError:
            return False, "Invalid hex address."
        with sqlite3.connect(self.db_file, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM managed_nodes WHERE base_addr = ?", (base_addr,))
            conn.commit()
        self.log(f"Managed node removed: 0x{base_addr:04X}")
        return True, "Node removed."

    def get_latest_readings(self):
        """
        Returns the most recent top and bottom readings for each physical node.
        Always includes every entry in managed_nodes, even if no data has arrived yet.
        Also includes any unmanaged nodes that have data in sensor_data.
        """
        def fmt_f(val):
            if val is None:
                return None
            return round(celsius_to_fahrenheit(val), 1)

        with sqlite3.connect(self.db_file, check_same_thread=False) as conn:
            cursor = conn.cursor()

            # --- Build the full set of (top_addr, bot_addr) pairs to show ---

            # 1. Managed nodes: base_addr is the node unicast; top element = base+1, bot = base+2
            cursor.execute("SELECT base_addr FROM managed_nodes ORDER BY base_addr")
            managed_bases = [row[0] for row in cursor.fetchall()]
            # top element addr = base+1, bottom = base+2 (per JSON: 000E base -> 000F top, 0010 bot)
            managed_pairs = {(b + 1, b + 2): b for b in managed_bases}

            # 2. Unmanaged nodes from sensor_data (exclude addrs already covered by managed pairs)
            cursor.execute("SELECT DISTINCT addr FROM sensor_data ORDER BY addr")
            all_data_addrs = set(row[0] for row in cursor.fetchall())
            managed_element_addrs = set()
            for (ta, ba) in managed_pairs:
                managed_element_addrs.add(ta)
                managed_element_addrs.add(ba)
            unmanaged_addrs = sorted(all_data_addrs - managed_element_addrs)
            unmanaged_pairs = self._group_addrs_into_nodes(unmanaged_addrs)

            nodes = []

            # --- Managed nodes first (always shown) ---
            for (top_addr, bot_addr), base_addr in sorted(managed_pairs.items()):
                cursor.execute(
                    "SELECT node_name FROM managed_nodes WHERE base_addr = ?", (base_addr,)
                )
                row = cursor.fetchone()
                node_label = row[0] if row else f"Node 0x{base_addr:04X}"

                cursor.execute(
                    """SELECT value, timestamp FROM sensor_data
                       WHERE addr = ? ORDER BY id DESC LIMIT 1""",
                    (top_addr,)
                )
                top_row = cursor.fetchone()

                cursor.execute(
                    """SELECT value, timestamp FROM sensor_data
                       WHERE addr = ? ORDER BY id DESC LIMIT 1""",
                    (bot_addr,)
                )
                bot_row = cursor.fetchone()

                nodes.append({
                    "addr": base_addr,
                    "label": node_label,
                    "top_f":  fmt_f(top_row[0]) if top_row else None,
                    "top_ts": top_row[1][:19].replace("T", " ") if top_row else None,
                    "bot_f":  fmt_f(bot_row[0]) if bot_row else None,
                    "bot_ts": bot_row[1][:19].replace("T", " ") if bot_row else None,
                })

            # --- Unmanaged nodes (only shown if they have data) ---
            for top_addr, bot_addr in unmanaged_pairs:
                node_label = self._resolve_node_name(cursor, top_addr)

                cursor.execute(
                    """SELECT value, timestamp FROM sensor_data
                       WHERE addr = ? AND name LIKE '%top%'
                       ORDER BY id DESC LIMIT 1""",
                    (top_addr,)
                )
                top_row = cursor.fetchone()
                if top_row is None:
                    cursor.execute(
                        """SELECT value, timestamp FROM sensor_data
                           WHERE addr = ? ORDER BY id DESC LIMIT 1""",
                        (top_addr,)
                    )
                    top_row = cursor.fetchone()

                bot_row = None
                if bot_addr is not None:
                    cursor.execute(
                        """SELECT value, timestamp FROM sensor_data
                           WHERE addr = ? AND name LIKE '%bot%'
                           ORDER BY id DESC LIMIT 1""",
                        (bot_addr,)
                    )
                    bot_row = cursor.fetchone()
                    if bot_row is None:
                        cursor.execute(
                            """SELECT value, timestamp FROM sensor_data
                               WHERE addr = ? ORDER BY id DESC LIMIT 1""",
                            (bot_addr,)
                        )
                        bot_row = cursor.fetchone()

                nodes.append({
                    "addr":   top_addr,
                    "label":  node_label,
                    "top_f":  fmt_f(top_row[0]) if top_row else None,
                    "top_ts": top_row[1][:19].replace("T", " ") if top_row else None,
                    "bot_f":  fmt_f(bot_row[0]) if bot_row else None,
                    "bot_ts": bot_row[1][:19].replace("T", " ") if bot_row else None,
                })

            return nodes


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

    def export_pivoted_csv(self):
        """
        Returns a CSV string where each row is one reading event per physical node.
        Columns: Timestamp, Node Address, Node Name, Top (°F), Bottom (°F)

        Physical nodes are identified by consecutive address pairs (top_addr, top_addr+1).
        Readings are paired chronologically by timestamp proximity. If one sensor
        missed a reading, the row is still emitted with that column left blank.
        """
        import csv
        from io import StringIO
        from collections import defaultdict

        with sqlite3.connect(self.db_file, check_same_thread=False) as conn:
            cursor = conn.cursor()

            # Get all distinct addresses and group into node pairs
            cursor.execute("SELECT DISTINCT addr FROM sensor_data ORDER BY addr")
            addrs = [row[0] for row in cursor.fetchall()]
            pairs = self._group_addrs_into_nodes(addrs)

            output_rows = []

            for top_addr, bot_addr in pairs:
                # Node label — managed_nodes > CDB > fallback
                node_label = self._resolve_node_name(cursor, top_addr)
                # For the CSV Node Address column, use the node's base unicast addr
                # (i.e. top_addr - 1 if a managed entry exists at base, else top_addr)
                base_for_csv = top_addr
                with sqlite3.connect(self.db_file, check_same_thread=False) as _c2:
                    _cur2 = _c2.cursor()
                    _cur2.execute("SELECT base_addr FROM managed_nodes WHERE base_addr = ?", (top_addr - 1,))
                    if _cur2.fetchone():
                        base_for_csv = top_addr - 1
                node_addr_str = f"0x{base_for_csv:04X}"

                # Fetch all top readings chronologically
                cursor.execute(
                    """SELECT timestamp, value FROM sensor_data
                       WHERE addr = ? ORDER BY id ASC""",
                    (top_addr,)
                )
                top_readings = cursor.fetchall()

                # Fetch all bottom readings chronologically
                bot_readings = []
                if bot_addr is not None:
                    cursor.execute(
                        """SELECT timestamp, value FROM sensor_data
                           WHERE addr = ? ORDER BY id ASC""",
                        (bot_addr,)
                    )
                    bot_readings = cursor.fetchall()

                # Pair them up by index — each cycle should produce one top + one bottom.
                # If counts differ, the shorter side gets None for missing readings.
                max_len = max(len(top_readings), len(bot_readings))
                for i in range(max_len):
                    top_ts, top_val = top_readings[i] if i < len(top_readings) else (None, None)
                    bot_ts, bot_val = bot_readings[i] if i < len(bot_readings) else (None, None)

                    # Use whichever timestamp is available; prefer the later one
                    if top_ts and bot_ts:
                        emit_ts = top_ts if top_ts >= bot_ts else bot_ts
                    else:
                        emit_ts = top_ts or bot_ts

                    output_rows.append({
                        "timestamp": emit_ts[:19].replace("T", " ") if emit_ts else "",
                        "addr": node_addr_str,
                        "name": node_label,
                        "top_f": round(celsius_to_fahrenheit(top_val), 1) if top_val is not None else "",
                        "bot_f": round(celsius_to_fahrenheit(bot_val), 1) if bot_val is not None else "",
                    })

        # Sort by timestamp then address
        output_rows.sort(key=lambda r: (r["timestamp"], r["addr"]))

        si = StringIO()
        cw = csv.writer(si)
        cw.writerow(["Timestamp", "Node Address", "Node Name", "Top (°F)", "Bottom (°F)"])
        for r in output_rows:
            cw.writerow([r["timestamp"], r["addr"], r["name"], r["top_f"], r["bot_f"]])

        return si.getvalue()

    def clear_db_data(self):
        with sqlite3.connect(self.db_file, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM sensor_data")
            conn.commit()
        self.log("Database cleared by admin.")

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
                filepath = "uploaded_cdb.json"
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
    """Background thread: try to connect to PI_SERIAL_PORT at startup."""
    if not PI_AUTO_CONNECT:
        return

    core_instance.log(f"Auto-connect: looking for {PI_SERIAL_PORT} @ {PI_BAUD_RATE} baud ...")
    attempt = 0
    while True:
        attempt += 1
        time.sleep(PI_RETRY_DELAY)

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

# ---------------------------------------------------------------------------
# Flask Routes — Public
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/status', methods=['GET'])
def get_status():
    cycle_secs = core.interval_minutes * 60
    return jsonify({
        "is_connected": core.is_connected,
        "port": core.serial_port.port if core.serial_port else None,
        "status_text": core.current_status,
        "status_color": core.current_status_color,
        "cycle_time": cycle_secs,
        "interval_minutes": core.interval_minutes,
        "suspend_dur": core.suspend_dur,
        "on_delay": core.on_delay,
        "wake_dur": core.wake_dur,
    })

@app.route('/api/readings', methods=['GET'])
def get_readings():
    """Returns the latest top/bottom Fahrenheit reading for each node."""
    return jsonify(core.get_latest_readings())

@app.route('/api/data/export', methods=['GET'])
def export_data():
    """Export pivoted CSV: one row per node, Top (°F) and Bottom (°F) columns."""
    csv_content = core.export_pivoted_csv()
    return Response(
        b'\xef\xbb\xbf' + csv_content.encode("utf-8"),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-disposition": "attachment; filename=sensor_data.csv"}
    )

# ---------------------------------------------------------------------------
# Flask Routes — Admin Auth
# ---------------------------------------------------------------------------

@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    data = request.json or {}
    if data.get('username') == ADMIN_USERNAME and data.get('password') == ADMIN_PASSWORD:
        session['admin_logged_in'] = True
        return jsonify({"success": True})
    return jsonify({"success": False, "msg": "Invalid credentials"}), 401

@app.route('/api/admin/logout', methods=['POST'])
def admin_logout():
    session.pop('admin_logged_in', None)
    return jsonify({"success": True})

@app.route('/api/admin/check', methods=['GET'])
def admin_check():
    return jsonify({"authenticated": bool(session.get('admin_logged_in'))})

# ---------------------------------------------------------------------------
# Flask Routes — Admin Protected
# ---------------------------------------------------------------------------

@app.route('/api/admin/status', methods=['GET'])
@require_admin
def admin_get_status():
    """Full status including cycle config — admin only."""
    return jsonify({
        "is_connected": core.is_connected,
        "port": core.serial_port.port if core.serial_port else None,
        "status_text": core.current_status,
        "status_color": core.current_status_color,
        "auto_cycle": core.auto_cycle_enabled,
        "interval_minutes": core.interval_minutes,
        "suspend_dur": core.suspend_dur,
        "wake_dur": core.wake_dur,
        "on_delay": core.on_delay
    })

@app.route('/api/ports', methods=['GET'])
@require_admin
def get_ports():
    return jsonify(core.get_ports())

@app.route('/api/connect', methods=['POST'])
@require_admin
def connect():
    data = request.json
    if core.is_connected:
        success, msg = core.disconnect()
        return jsonify({"success": success, "msg": msg})
    else:
        success, msg = core.connect(data.get('port'), data.get('baud', 115200))
        return jsonify({"success": success, "msg": msg})

@app.route('/api/config', methods=['POST'])
@require_admin
def config():
    data = request.json
    MAX_TOTAL_SECS = 12 * 3600  # 12 hours

    # Validate timing fields
    timing_keys = ('interval_minutes', 'wake_dur', 'on_delay')
    for key in timing_keys:
        if key in data:
            try:
                val = float(data[key]) if key == 'interval_minutes' else int(data[key])
            except (TypeError, ValueError):
                return jsonify({"success": False, "msg": f"Invalid value for {key}."}), 400
            if val <= 0:
                return jsonify({"success": False, "msg": f"{key} must be a positive number greater than 0."}), 400

    new_interval = float(data['interval_minutes']) if 'interval_minutes' in data else core.interval_minutes
    new_wake     = int(data['wake_dur'])            if 'wake_dur'    in data else core.wake_dur
    new_delay    = int(data['on_delay'])            if 'on_delay'    in data else core.on_delay

    total = new_interval * 60
    if total > MAX_TOTAL_SECS:
        hours = total / 3600
        return jsonify({
            "success": False,
            "msg": f"Capture interval of {hours:.1f} hours exceeds the 12-hour maximum."
        }), 400

    # Ensure suspend would be at least 1 second
    computed_suspend = new_interval * 60 - new_wake - new_delay
    if computed_suspend < 1:
        return jsonify({
            "success": False,
            "msg": f"Wake duration ({new_wake}s) + firmware delay ({new_delay}s) exceeds the capture interval ({new_interval} min). Please increase the interval or reduce wake/delay."
        }), 400

    if 'interval_minutes' in data:
        core.interval_minutes = new_interval
    if 'wake_dur' in data:
        core.wake_dur = new_wake
    if 'on_delay' in data:
        core.on_delay = new_delay
    if 'tx_power' in data:
        core.set_tx_power(data['tx_power'])
    return jsonify({"success": True})

@app.route('/api/command', methods=['POST'])
@require_admin
def command():
    data = request.json
    if 'cmd' in data:
        core.send_command(data['cmd'])
    elif 'suspend_now' in data:
        core.suspend_mesh()
    return jsonify({"success": True})

@app.route('/api/data/prune', methods=['POST'])
@require_admin
def prune_data():
    prune_time = request.json.get('prune_time', 4)
    res = core.prune_duplicates(prune_time)
    return jsonify({"success": True, "pruned": res})

@app.route('/api/data/clear', methods=['POST'])
@require_admin
def clear_data():
    core.clear_db_data()
    return jsonify({"success": True})

@app.route('/api/cdb', methods=['POST'])
@require_admin
def upload_cdb():
    if 'file' not in request.files:
        return jsonify({"success": False, "msg": "No file part"})
    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "msg": "No selected file"})

    if file:
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
@require_admin
def get_logs():
    since = request.args.get('since', 0, type=int)
    new_logs = core.logs[since:]
    return jsonify({
        "logs": new_logs,
        "next_index": len(core.logs)
    })

@app.route('/api/logs/clear', methods=['POST'])
@require_admin
def clear_logs():
    core.logs = []
    return jsonify({"success": True})

# ---------------------------------------------------------------------------
# Flask Routes — Managed Nodes
# ---------------------------------------------------------------------------

@app.route('/api/admin/nodes', methods=['GET'])
@require_admin
def get_nodes():
    return jsonify(core.get_managed_nodes())

@app.route('/api/admin/nodes', methods=['POST'])
@require_admin
def add_node():
    data = request.json or {}
    base_addr_hex = data.get('base_addr', '').strip()
    node_name     = data.get('node_name', '').strip()
    success, msg  = core.add_managed_node(base_addr_hex, node_name)
    return jsonify({"success": success, "msg": msg}), (200 if success else 400)

@app.route('/api/admin/nodes/<base_addr_hex>', methods=['DELETE'])
@require_admin
def remove_node(base_addr_hex):
    success, msg = core.remove_managed_node(base_addr_hex)
    return jsonify({"success": success, "msg": msg}), (200 if success else 400)

# ---------------------------------------------------------------------------
# Background: midnight log-clear
# ---------------------------------------------------------------------------

def _midnight_log_clear():
    """Clears the in-memory log buffer once per day at midnight."""
    import time as _time
    while True:
        now = datetime.now()
        # Seconds until next midnight
        seconds_until_midnight = (
            (23 - now.hour) * 3600 +
            (59 - now.minute) * 60 +
            (59 - now.second) + 1
        )
        _time.sleep(seconds_until_midnight)
        core.logs = []
        core.log("System: In-memory log cleared by daily maintenance.")


if __name__ == "__main__":
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static/css', exist_ok=True)
    os.makedirs('static/js', exist_ok=True)

    # Daily midnight log clear
    midnight_thread = threading.Thread(target=_midnight_log_clear, daemon=True)
    midnight_thread.start()

    if PI_AUTO_CONNECT:
        ac_thread = threading.Thread(
            target=auto_connect_on_startup, args=(core,), daemon=True
        )
        ac_thread.start()

    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
