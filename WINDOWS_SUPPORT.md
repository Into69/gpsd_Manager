# Windows Support for GPSD Manager

The GPSD Manager now has full Windows support for the MCODE GPS converter.

## What Changed

### Platform Detection
- Automatic detection of Windows vs. Linux
- OS-specific code paths for device discovery, file paths, and output handling

### Device Discovery (Windows)
- Scans Windows Registry for COM ports (`HARDWARE\DEVICEMAP\SERIALCOMM`)
- Also probes common COM ports (`COM1` through `COM8`)
- Dropdown in web UI auto-populates with available ports
- "Rescan" button to refresh the list

### Output Methods
- **Windows**: Writes NMEA sentences to `%TEMP%\mcode_output.txt`
- **Linux**: Writes to FIFO at `/tmp/gpsd-manager/mcode.fifo` (for gpsd integration)

### Web UI Improvements
- Dynamic port selection dropdown (no hardcoded ports)
- Platform indicator showing current OS
- Rescan button for COM port discovery

## Using on Windows

### Setup
```bash
# Install dependencies
pip install -r requirements.txt
```

### Running
```bash
# Start the application
python gpsd_manager.py
```

Open browser to `http://localhost:8000`

### Connecting Your GPS Device
1. Plug in USB GPS device
2. Windows will assign a COM port (check Device Manager)
3. In the web UI:
   - Click **Rescan** in the MCODE section
   - Select your COM port from dropdown
   - Select baud rate
   - Click **Enable**

### Using the Output
The converter outputs NMEA to: `C:\Users\YourUser\AppData\Local\Temp\mcode_output.txt`

You can:
- Monitor the file in real-time (use `tail -f` in WSL, or tail.exe)
- Use with other GPS/mapping software that accepts NMEA input
- Parse programmatically for your own applications

## Features Working on Both Platforms

- MCODE serial reading and parsing
- Multi-part MCODE sentence handling
- NMEA conversion (GGA, RMC)
- Automatic `mcode atak enable` command on startup
- Live status in web UI
- Enable/Disable controls
- Error reporting

## Features Platform-Specific

| Feature | Windows | Linux |
|---------|---------|-------|
| Device Discovery | Windows Registry + COM probing | udevadm |
| Output Method | Text file | FIFO |
| Service Control | Manual start/stop | systemctl |
| Config File | Not used | /etc/default/gpsd |

## Limitations on Windows

- No systemd service control (gpsd integration not available natively)
- Output is to a regular file instead of FIFO
- Device discovery is simpler (just COM port enumeration)

## Advantages on Windows

- No gpsd dependency required
- Can run standalone
- Simple text file output easy to integrate
- USB driver support via Windows native drivers
- Works in WSL2 as well (set port to `/dev/ttyUSB0` etc)
