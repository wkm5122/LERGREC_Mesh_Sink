# Sink Data Handler (Web Server Edition)

The Sink Data Handler (`sink_handler.py`) is a Python application that interfaces with a BLE Mesh Sink Device over a serial connection. It logs incoming sensor data to a local SQLite database (`sensor_data.db`) and allows the user to configure the mesh network's power-saving cycle. 

It has been upgraded to run entirely headlessly as a **Flask Web Server**. This makes it perfect to run in the background (console/SSH) on a Raspberry Pi, while you access the beautiful Vineyard-themed Graphical User Interface from any web browser on your network.

## Prerequisites

- Python 3.x
- `pyserial` (for serial communication)
- `flask` (for the web server)

You can install the required packages using pip:
```bash
pip install pyserial flask
```

## Running the Application

To start the continuous web server, navigate to the directory containing the file and run:
```bash
python sink_handler.py
```

The console will indicate that the Flask server is running (usually on `http://0.0.0.0:5000/`).

## Accessing the Interface

Open a web browser on any device in the same network and navigate to:
`http://<IP_ADDRESS_OF_PI>:5000`
(If running locally, you can use `http://localhost:5000`)

## Features and Usage

### 1. Connection
The **Connection** bar at the top allows you to connect to the Sink Device.
- **Port**: Select the COM port from the dropdown menu (e.g. `/dev/ttyACM0` on Pi). Click the **Refresh ↻** arrow to update the list.
- **Baud**: Set the baud rate (default is 115200).
- **Connect / Disconnect**: Click to establish or terminate the connection.

### 2. Controls & Config
This panel manages the BLE Mesh sleep behavior and auto-cycling.
- **Suspend/Wake Duration**: The duration the mesh should remain asleep or awake. 
- **Firmware ON Delay**: Matches the compiled ON delay in the firmware (time the device remains on after a suspend transition before actually sleeping, default 20s).
- **Publish TX Power**: Sets the signal strength (`-40 dBm` to `+8 dBm`).
- **Suspend Mesh Now**: Immediately triggers the mesh to begin its suspend sequence.
- **Enable Auto Cycle**: Toggle switch to have the application automatically cycle the Sink device between Wake and Suspend phases.

### 3. Data Explorer
View and manage the sensor data stored in the local SQLite database (`sensor_data.db`).
- **Filter by Address**: Enter a specific node address to isolate data.
- **Prune (s)**: Removes repetitive readings that occurred within the specified timeframe for the same sensor.
- **Load CDB JSON**: Upload an nRF Mesh CDB (`.json`) configuration file to map your device Elements to their expected sub-addresses.
- **Export CSV**: Downloads all currently displayed data to your PC as a `.csv` file.
- **Clear Data**: Deletes all sensor data from the database.

### 4. System Log
A real-time console showing serial feedback, database inserts, and auto-cycle transitions.

## Data Format
The application expects incoming serial data from the Sink device to be formatted as JSON strings:
```json
{"name":"top_sensor","addr":4352,"value":25}
```
When this format is detected on the serial line, it extracts the data and inserts it into the `sensor_data` SQL table along with a timestamp.
