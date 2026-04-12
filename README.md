# GPSD Manager

A web-based management interface for a local [gpsd](https://gpsd.io/) instance, built with FastAPI.

## Features

- **Service Control** - Start, stop, and restart gpsd via systemctl
- **Live GPS Data** - Real-time position, altitude, speed, heading, satellite count, SNR, and DOP values (updates every second)
- **Device Management** - Scan for available serial/USB GPS devices and configure them as sources
- **Options** - Toggle gpsd flags (`-n`, `-N`, `-G`, `-b`) with support for combined flags (e.g. `-Gn`)
- **Config Persistence** - Save options and devices to `/etc/default/gpsd`
- **Log Viewer** - View recent gpsd logs from journald
- **Startup Checks** - Verifies gpsd is installed and checks permissions on launch

## Requirements

- Linux with systemd
- Python 3.10+
- gpsd installed (`sudo apt install gpsd gpsd-clients`)

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

## Project Structure

```
gpsd_manager.py    - Application (FastAPI server + gpsd management logic)
web/index.html     - Web interface (single-page, self-contained)
requirements.txt   - Python dependencies
```
