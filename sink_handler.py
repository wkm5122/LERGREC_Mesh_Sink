import serial
import json
import sqlite3
import time
import threading
import io
import csv
from flask import Flask, jsonify, request, Response, send_from_directory

app = Flask(__name__, static_folder='static')

DB_FILE = 'sensor_data.db'
SERIAL_PORT = '/dev/lergrec_gateway'
BAUD_RATE = 115200

# Global state
mesh_config = {
    "suspend_duration": 60,
    "wake_duration": 30,
    "firmware_delay": 20,
    "tx_power": 0,
    "auto_cycle": False
}
cycle_state = "Ready" 
latest_logs = []

def log_msg(msg):
    timestamp = time.strftime("%H:%M:%S")
    formatted = f"{timestamp} - {msg}"
    print(formatted, flush=True)
    latest_logs.append(formatted)
    if len(latest_logs) > 50:
        latest_logs.pop(0)

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS sensor_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            name TEXT,
            addr INTEGER,
            value REAL
        )
    ''')
    conn.commit()
    conn.close()

def process_line(line):
    try:
        json_str = line[line.find("{"):line.rfind("}")+1]
        if not json_str: return

        payload = json.loads(json_str)
        name = payload.get('name', 'unknown')
        addr = payload.get('addr', 0)
        
        # Parse Celsius value as float for maximum precision
        val_c = float(payload.get('value', 0.0))
        
        # Convert to Fahrenheit and round to 1 decimal place
        val_f = round((val_c * 9/5) + 32, 1)

        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('INSERT INTO sensor_data (name, addr, value) VALUES (?, ?, ?)', (name, addr, val_f))
        conn.commit()
        conn.close()

        log_msg(f"DATA: {name} (Addr: {addr}) = {val_f}°F")

    except json.JSONDecodeError:
        pass
    except Exception as e:
        log_msg(f"Error processing data: {e}")

def serial_worker():
    global cycle_state
    ser = None
    
    while True:
        try:
            if ser is None:
                log_msg(f"Auto-connect: looking for {SERIAL_PORT} @ {BAUD_RATE} ...")
                ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
                log_msg(f"Connected to {SERIAL_PORT} at {BAUD_RATE}")
                log_msg("Auto-connect: connected successfully.")

            if ser.in_waiting > 0:
                raw_line = ser.readline()
                try:
                    line = raw_line.decode('utf-8').strip()
                    if line:
                        if not line.startswith("uart:~$"):
                            log_msg(f"RX: {line}")
                        process_line(line)
                except UnicodeDecodeError:
                    pass

            if mesh_config["auto_cycle"]:
                cycle_state = "Wake"
                time.sleep(mesh_config["wake_duration"])
                
                cmd = f"mesh_app set_level {mesh_config['suspend_duration']}\n"
                ser.write(cmd.encode('utf-8'))
                log_msg(f"Sent Level Set ({mesh_config['suspend_duration']}) to All Nodes")
                
                time.sleep(1)
                
                cmd2 = "mesh_app set_onoff_temp\n"
                ser.write(cmd2.encode('utf-8'))
                log_msg("Sent OnOff Set (On) to All Nodes. Turning off in 3s...")
                
                cycle_state = "Suspend"
                time.sleep(mesh_config["suspend_duration"])
            else:
                cycle_state = "Ready"
                time.sleep(0.1)

        except serial.SerialException:
            if ser:
                ser.close()
                ser = None
            time.sleep(5)
        except Exception as e:
            log_msg(f"Worker error: {e}")
            time.sleep(1)

# --- Flask Routes ---

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/static/<path:path>')
def serve_static(path):
    return send_from_directory('static', path)

@app.route('/api/status')
def get_status():
    return jsonify({"cycle_state": cycle_state})

@app.route('/api/config', methods=['GET', 'POST'])
def handle_config():
    if request.method == 'POST':
        data = request.json
        for k, v in data.items():
            if k in mesh_config:
                mesh_config[k] = v
        log_msg(f"Config updated: {data}")
        return jsonify({"status": "success", "config": mesh_config})
    return jsonify(mesh_config)

@app.route('/api/logs')
def get_logs():
    return jsonify({"logs": latest_logs})

@app.route('/api/data/latest')
def get_latest_data():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        SELECT name, addr, value, timestamp 
        FROM sensor_data 
        WHERE id IN (
            SELECT MAX(id) FROM sensor_data GROUP BY addr
        )
    ''')
    rows = c.fetchall()
    conn.close()
    
    data = [{"name": r[0], "addr": r[1], "value": r[2], "timestamp": r[3]} for r in rows]
    return jsonify(data)

@app.route('/api/admin/clear', methods=['POST'])
def clear_database():
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('DELETE FROM sensor_data')
        # Reset the auto-increment counter
        c.execute('DELETE FROM sqlite_sequence WHERE name="sensor_data"') 
        conn.commit()
        conn.close()
        log_msg("Admin action: Database wiped clean.")
        return jsonify({"status": "success", "message": "Database cleared."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/export_csv')
def export_csv():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Pivot query: Groups top and bottom sensors by the minute they were received
    c.execute('''
        SELECT 
            strftime('%Y-%m-%d %H:%M', timestamp) as time_minute,
            MAX(CASE WHEN name LIKE '%top%' THEN value END) as top_temp_F,
            MAX(CASE WHEN name LIKE '%bottom%' THEN value END) as bottom_temp_F
        FROM sensor_data
        GROUP BY time_minute
        ORDER BY time_minute DESC
    ''')
    rows = c.fetchall()
    conn.close()

    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(['Timestamp (Minute)', 'Top_Temp (°F)', 'Bottom_Temp (°F)'])
    cw.writerows(rows)
    
    output = si.getvalue()
    return Response(output, mimetype='text/csv', headers={"Content-Disposition": "attachment;filename=lergrec_sensor_data.csv"})

if __name__ == '__main__':
    init_db()
    t = threading.Thread(target=serial_worker, daemon=True)
    t.start()
    app.run(host='0.0.0.0', port=80, debug=False)