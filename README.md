# GPSD Manager

A web-based management interface for a local [gpsd](https://gpsd.io/) instance, built with FastAPI.

## Features

- **Service Control** - Start, stop, and restart gpsd via systemctl
- **Live GPS Data** - Real-time position, altitude, speed, heading, satellite count, SNR, and DOP values (updates every second)
- **Device Management** - Scan for available serial/USB GPS devices and configure them as sources
- **Options** - Toggle gpsd flags (`-n`, `-N`, `-G`, `-b`) with support for combined flags (e.g. `-Gn`)
- **Config Persistence** - Save options and devices to `/etc/default/gpsd`
- **MCODE to NMEA Converter** - Support for proprietary GPS receivers that output MCODE format; automatically converts to NMEA for gpsd consumption
- **Log Viewer** - View recent gpsd logs from journald
- **Startup Checks** - Verifies gpsd is installed and checks permissions on launch

## Requirements

### Linux
- Python 3.10+
- gpsd installed (`sudo apt install gpsd gpsd-clients`)
- systemd (for service control)

### Windows
- Python 3.10+
- pyserial library (installed via requirements.txt)
- USB-to-Serial driver for your GPS device

## Installation

```bash
git clone https://github.com/Into69/gpsd_Manager.git
cd gpsd_Manager
pip install -r requirements.txt
```

## Usage

```bash
python gpsd_manager.py
```

Open the displayed URL (default: `http://0.0.0.0:8000`) in a browser.

## Configuration

The app reads and writes gpsd configuration from `/etc/default/gpsd`. Writing to this file requires appropriate permissions (root or passwordless sudo).

## MCODE to NMEA Converter

For GPS receivers that output proprietary MCODE format (not standard NMEA):

### How it works
- Reads raw MCODE sentences from a serial GPS device
- Converts them to standard NMEA sentences (GGA for position, RMC for speed/time)
- On Linux: Outputs to a FIFO that gpsd can read from
- On Windows: Outputs to a file in the temp directory
- Seamless integration with the rest of gpsd_manager
- Automatically sends `mcode atak enable` command on startup

### Usage (Linux)
1. Connect your MCODE GPS device to a USB port
2. Identify the serial port (e.g., `/dev/ttyUSB0`)
3. Use the web UI or API:
   ```bash
   POST /api/converter/start
   {"port": "/dev/ttyUSB0", "baudrate": 9600}
   ```
4. The FIFO path will be `/tmp/gpsd-manager/mcode.fifo`
5. Add this to gpsd's devices and restart gpsd

### Usage (Windows)
1. Connect your MCODE GPS device to a USB port
2. Identify the COM port (e.g., `COM3`) — Windows will assign it automatically
3. Open the web UI at `http://localhost:8000`
4. In the "Custom GPS Receiver (MCODE)" section:
   - Click **Rescan** to populate COM ports
   - Select your device from the dropdown
   - Select the correct baud rate
   - Click **Enable**
5. NMEA output will be written to `%TEMP%\mcode_output.txt` (typically `C:\Users\YourUser\AppData\Local\Temp\mcode_output.txt`)
6. You can use this file with other GPS applications or analysis tools

### NMEA Integration with GPSD (Linux)
The converter can automatically feed data into the running gpsd instance:

1. **Enable the converter** via the web UI
2. Click **Add to GPSD** button
3. The converter device is added to `/etc/default/gpsd`
4. GPSD service is restarted
5. Real-time GPS data appears in the main "GPS Information" panel

### MCODE Format
Currently supports:
- Field 1: Latitude (decimal degrees)
- Field 2: Longitude (decimal degrees)
- Field 3: Altitude (meters)
- Field 4: Speed (m/s) — converted to knots for NMEA

More fields can be added as needed.

### On Windows
- Converter outputs NMEA to file at `%TEMP%\mcode_output.txt`
- Can be read by other GPS applications
- For gpsd integration on Windows, run gpsd on WSL or a Linux system and point it to a network socket or shared file

## Project Structure

```
gpsd_manager.py      - Application (FastAPI server + gpsd management logic)
mcode_converter.py   - MCODE to NMEA converter (runs as subprocess)
web/index.html       - Web interface (single-page, self-contained)
requirements.txt     - Python dependencies
```
