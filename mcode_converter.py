"""Convert MCODE serial GPS data to NMEA format for gpsd consumption."""

import json
import os
import platform
import re
import serial
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


def gps_time_to_datetime(gps_week: int, tow_seconds: float) -> datetime:
    """Convert GPS week and time of week to UTC datetime.

    GPS epoch: January 6, 1980 00:00:00 UTC
    Each GPS week has 604800 seconds (7 days).
    Does not account for leap seconds (GPS time is continuous, UTC has leap seconds).
    """
    # GPS epoch in Unix time (seconds since Jan 1, 1970)
    GPS_EPOCH_UNIX = 315964800

    # Calculate total seconds since GPS epoch
    total_seconds = (gps_week * 604800) + tow_seconds

    # Convert to Unix timestamp
    unix_timestamp = GPS_EPOCH_UNIX + total_seconds

    # Convert to datetime (using timezone-aware UTC to avoid deprecation warning)
    return datetime.fromtimestamp(unix_timestamp, tz=timezone.utc).replace(tzinfo=None)


class MCodeParser:
    """Parse MCODE GPS sentences."""

    def __init__(self):
        self.buffer = ""
        self.current_mcode = {}

    def feed(self, data: str) -> dict | None:
        """Feed data and return parsed MCODE dict when complete, or None."""
        self.buffer += data

        # Look for complete MCODE sentences (multi-part)
        # Pattern: $MCODE1/2 or $MCODE2/2
        match = re.search(r'\$MCODE(\d+)/(\d+),(.+?)(?=\$|$)', self.buffer)
        if not match:
            return None

        part_num = int(match.group(1))
        total_parts = int(match.group(2))
        part_data = match.group(3).rstrip()

        # Store the part
        self.current_mcode[part_num] = part_data

        # Clean up buffer
        self.buffer = self.buffer[match.end():]

        # If we have all parts, return parsed data
        if len(self.current_mcode) == total_parts:
            return self._parse_complete()

        return None

    def _parse_complete(self) -> dict | None:
        """Parse complete MCODE data from all parts."""
        try:
            # Join all parts and split by comma
            full_data = "".join(self.current_mcode[i] for i in sorted(self.current_mcode.keys()))
            fields = full_data.split(",")

            if len(fields) < 4:
                self.current_mcode.clear()
                return None

            result = {
                "lat": float(fields[0]),
                "lon": float(fields[1]),
                "alt": float(fields[2]),
                "speed_ms": float(fields[3]) if len(fields) > 3 else 0,
            }

            self.current_mcode.clear()
            return result
        except (ValueError, IndexError):
            self.current_mcode.clear()
            return None


class GPSPrintParser:
    """Parse GPS print format data."""

    def __init__(self):
        self.buffer = ""
        self.gps_time_data = {}
        self.zed_data = {}

    def feed(self, data: str) -> dict | None:
        """Feed data and return parsed GPS dict when complete, or None."""
        self.buffer += data

        # Normalize line endings: ensure all lines end with \n
        self.buffer = self.buffer.replace('\r\n', '\n').replace('\r', '\n')

        lines = self.buffer.split('\n')
        # Keep the last incomplete line in buffer
        self.buffer = lines[-1]

        parsed = None
        for line in lines[:-1]:
            line = line.strip()
            if not line:
                continue

            # Parse GPS TIME line
            if line.startswith('[GPS TIME]'):
                time_match = re.search(r'week=(\d+)\s+tow=([\d.]+)\s+gps_sec=([^\s]*)', line)
                if time_match:
                    self.gps_time_data = {
                        'week': int(time_match.group(1)),
                        'tow': float(time_match.group(2)),
                        'gps_sec': time_match.group(3),
                    }
                    # Also capture raw line for debugging
                    self.gps_time_data['raw_line'] = line

            # Parse ZED Alt line
            elif line.startswith('ZED Alt:'):
                alt_match = re.search(r'ZED Alt:\s+([-\d.]+)', line)
                lat_match = re.search(r'Lat:\s+([-\d.]+)', line)
                lon_match = re.search(r'Lon:\s+([-\d.]+)', line)
                hdop_match = re.search(r'HDOP:\s+([\d.]+)', line)
                fix_match = re.search(r'Fix:(\d+)', line)
                sv_match = re.search(r'SV:(\d+)', line)

                if alt_match and lat_match and lon_match:
                    self.zed_data = {
                        'alt': float(alt_match.group(1)),
                        'lat': float(lat_match.group(1)),
                        'lon': float(lon_match.group(1)),
                        'hdop': float(hdop_match.group(1)) if hdop_match else 0,
                        'fix': int(fix_match.group(1)) if fix_match else 0,
                        'sv': int(sv_match.group(1)) if sv_match else 0,
                        'raw_line': line,
                    }
                    print(f"GPSPrint: Parsed ZED data, has_gps_time={bool(self.gps_time_data)}", file=sys.stderr)

                    # If we have both GPS time and ZED data, return the parsed result
                    if self.gps_time_data and self.zed_data:
                        print(f"GPSPrint: Returning parsed result", file=sys.stderr)
                        parsed = self._parse_complete()

        return parsed

    def _parse_complete(self) -> dict | None:
        """Combine GPS time and ZED data into standard format."""
        try:
            # Convert GPS time to datetime
            gps_datetime = None
            datetime_str = None
            if self.gps_time_data.get('week') is not None and self.gps_time_data.get('tow') is not None:
                gps_datetime = gps_time_to_datetime(
                    self.gps_time_data['week'],
                    self.gps_time_data['tow']
                )
                # Format for display: YYYY-MM-DD HH:MM:SS UTC
                datetime_str = gps_datetime.strftime("%Y-%m-%d %H:%M:%S") + " UTC"

            # Combine all decoded info
            result = {
                "lat": self.zed_data['lat'],
                "lon": self.zed_data['lon'],
                "alt": self.zed_data['alt'],
                "speed_ms": 0,  # Speed not provided in this format
                "timestamp": gps_datetime,  # Converted GPS time (for NMEA generation)
                "datetime_str": datetime_str,  # Human-readable format for display
                # Additional decoded fields
                "week": self.gps_time_data.get('week'),
                "tow": self.gps_time_data.get('tow'),
                "hdop": self.zed_data.get('hdop'),
                "fix_quality": self.zed_data.get('fix'),  # 0=2d fix, 1=3d fix, etc
                "satellites_used": self.zed_data.get('sv'),
            }
            return result
        except (ValueError, KeyError):
            return None


class NMEAGenerator:
    """Generate standard NMEA sentences from MCODE data."""

    @staticmethod
    def _checksum(sentence: str) -> str:
        """Calculate NMEA checksum."""
        checksum = 0
        for char in sentence:
            checksum ^= ord(char)
        return f"{checksum:02X}"

    @staticmethod
    def _dms_from_decimal(decimal_degrees: float, is_lat: bool) -> tuple[str, str]:
        """Convert decimal degrees to DDmm.mmmm format and return (dms, dir)."""
        is_negative = decimal_degrees < 0
        abs_val = abs(decimal_degrees)

        degrees = int(abs_val)
        minutes = (abs_val - degrees) * 60

        if is_lat:
            direction = "S" if is_negative else "N"
            dms = f"{degrees:02d}{minutes:07.4f}"
        else:
            direction = "W" if is_negative else "E"
            dms = f"{degrees:03d}{minutes:07.4f}"

        return dms, direction

    @classmethod
    def gga(cls, data: dict, timestamp: datetime | None = None) -> str:
        """Generate $GPGGA sentence (position fix)."""
        if timestamp is None:
            timestamp = datetime.utcnow()

        time_str = timestamp.strftime("%H%M%S")
        lat_dms, lat_dir = cls._dms_from_decimal(data["lat"], is_lat=True)
        lon_dms, lon_dir = cls._dms_from_decimal(data["lon"], is_lat=False)

        # Fix quality: 1=GPS fix, 0=no fix
        fix_quality = 1 if data.get("lat") and data.get("lon") else 0

        # Build sentence (without $ and checksum)
        sentence = f"GPGGA,{time_str},{lat_dms},{lat_dir},{lon_dms},{lon_dir},{fix_quality},00,99.99,{data['alt']:.1f},M,0,M,,"

        checksum = cls._checksum(sentence)
        return f"${sentence}*{checksum}\r\n"

    @classmethod
    def rmc(cls, data: dict, timestamp: datetime | None = None) -> str:
        """Generate $GPRMC sentence (position, speed, course)."""
        if timestamp is None:
            timestamp = datetime.utcnow()

        time_str = timestamp.strftime("%H%M%S")
        date_str = timestamp.strftime("%d%m%y")

        # Status: A=active, V=void
        status = "A" if data.get("lat") and data.get("lon") else "V"

        lat_dms, lat_dir = cls._dms_from_decimal(data["lat"], is_lat=True)
        lon_dms, lon_dir = cls._dms_from_decimal(data["lon"], is_lat=False)

        # Convert speed from m/s to knots (1 m/s = 1.94384 knots)
        speed_knots = data.get("speed_ms", 0) * 1.94384

        # Build sentence
        sentence = f"GPRMC,{time_str},{status},{lat_dms},{lat_dir},{lon_dms},{lon_dir},{speed_knots:.2f},0,{date_str},,"

        checksum = cls._checksum(sentence)
        return f"${sentence}*{checksum}\r\n"


class MCODEConverter:
    """Main converter: reads GPS data from serial, outputs NMEA."""

    TCP_PORT = 2948

    def __init__(self, port: str, baudrate: int = 9600, output_path: str | None = None, output_mode: str = "auto", manual_position: dict | None = None, parser_type: str = "mcode"):
        self.port = port
        self.baudrate = baudrate
        self.output_path = output_path
        self.output_mode = output_mode  # "auto", "file", "pipe", "tcp"
        self.manual_position = manual_position  # If set, output this position instead of reading serial
        self.parser_type = parser_type  # "mcode" or "gps_print"
        self.parser = MCodeParser() if parser_type == "mcode" else GPSPrintParser()
        self.running = False
        self.is_windows = platform.system() == "Windows"
        self.debug_file = self._get_debug_file_path()
        self.tcp_server = None
        self.tcp_clients = []
        self.serial_data_lines = []  # Buffer of all serial data lines (max 1000)

    def run(self):
        """Main loop: read from serial or manual position, convert, output."""
        if self.manual_position:
            self._run_manual_mode()
        else:
            self._run_serial_mode()

    def _run_manual_mode(self):
        """Output NMEA from manually set position via TCP."""
        self._start_tcp_server()
        try:
            self.running = True
            print(f"MCODE converter: manual position mode, TCP server on port {self.TCP_PORT}", file=sys.stderr)
            while self.running:
                try:
                    now = datetime.now(datetime.now().astimezone().tzinfo)
                    gga = NMEAGenerator.gga(self.manual_position, now)
                    rmc = NMEAGenerator.rmc(self.manual_position, now)
                    self._write_debug_info("manual position", self.manual_position, gga, rmc)
                    self._send_to_clients(gga)
                    self._send_to_clients(rmc)
                    time.sleep(1)
                except Exception as e:
                    print(f"Manual mode error: {e}", file=sys.stderr)
                    time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self.running = False
            self._stop_tcp_server()

    def _run_serial_mode(self):
        """Read from serial port and output NMEA via TCP."""
        try:
            ser = serial.Serial(self.port, self.baudrate, timeout=1)
        except serial.SerialException as e:
            print(f"Failed to open {self.port}: {e}", file=sys.stderr)
            return

        # Send enable/disable commands to the device
        try:
            if self.parser_type == "gps_print":
                enable_cmd = "gps print enable\n"
                disable_cmd = "mcode atak disable\n"
            else:
                enable_cmd = "mcode atak enable\n"
                disable_cmd = "gps print disable\n"

            # Send disable command first to ensure clean state
            ser.write(disable_cmd.encode("utf-8"))
            ser.flush()
            print(f"Sent disable command: {disable_cmd.strip()}", file=sys.stderr)
            time.sleep(0.2)

            # Then send enable command
            ser.write(enable_cmd.encode("utf-8"))
            ser.flush()
            print(f"Sent enable command: {enable_cmd.strip()}", file=sys.stderr)
            time.sleep(0.5)
        except Exception as e:
            print(f"Warning: Failed to send setup commands: {e}", file=sys.stderr)

        # Start TCP server
        self._start_tcp_server()

        try:
            self.running = True
            print(f"MCODE converter: reading from {self.port}, streaming NMEA via TCP port {self.TCP_PORT}", file=sys.stderr)

            while self.running:
                try:
                    if ser.in_waiting:
                        data = ser.read(ser.in_waiting).decode("utf-8", errors="replace")
                        print(f"[{self.parser_type}] Read {len(data)} bytes: {data[:100]!r}", file=sys.stderr)

                        # Add all incoming serial data to buffer
                        self._add_serial_data(data)

                        parsed = self.parser.feed(data)
                        print(f"[{self.parser_type}] Parser returned: {parsed is not None}", file=sys.stderr)

                        if parsed:
                            # Use GPS timestamp if available, otherwise use current time
                            if parsed.get('timestamp'):
                                now = parsed['timestamp']
                            else:
                                now = datetime.now(datetime.now().astimezone().tzinfo)
                            gga = NMEAGenerator.gga(parsed, now)
                            rmc = NMEAGenerator.rmc(parsed, now)

                            # Store raw MCODE for debugging
                            self._write_debug_info(data, parsed, gga, rmc)

                            # Send to all connected TCP clients
                            self._send_to_clients(gga)
                            self._send_to_clients(rmc)
                    else:
                        time.sleep(0.01)
                except Exception as e:
                    print(f"Conversion error: {e}", file=sys.stderr)
                    time.sleep(0.1)
        except KeyboardInterrupt:
            pass
        finally:
            self.running = False
            ser.close()
            self._stop_tcp_server()

    def _open_output(self):
        """Open output stream (file-based for simplicity)."""
        if not self.output_path:
            return sys.stdout

        print(f"Opening output file: {self.output_path}", file=sys.stderr)
        try:
            f = open(self.output_path, "w", buffering=1)
            # Set world-readable permissions on Linux
            if not self.is_windows:
                try:
                    os.chmod(self.output_path, 0o666)
                except OSError:
                    pass
            return f
        except Exception as e:
            print(f"Failed to open output: {e}", file=sys.stderr)
            return sys.stdout

    def _get_debug_file_path(self) -> str:
        """Get path for debug file."""
        temp_dir = Path(os.environ.get("TEMP") if self.is_windows else "/tmp")
        return str(temp_dir / "mcode_debug.json")

    def _add_serial_data(self, data: str):
        """Add incoming serial data to buffer (max 1000 lines)."""
        lines = data.split('\n')
        for line in lines:
            if line.strip():  # Only add non-empty lines
                self.serial_data_lines.append(line.strip())
        # Keep only last 1000 lines
        if len(self.serial_data_lines) > 1000:
            self.serial_data_lines = self.serial_data_lines[-1000:]

    def _write_debug_info(self, raw_line: str, parsed: dict, nmea_gga: str, nmea_rmc: str):
        """Write debug info to JSON file."""
        try:
            debug_info = {
                "timestamp": datetime.now().isoformat(),
                "raw_mcode": raw_line,
                "parsed_fields": parsed,
                "nmea_gga": nmea_gga.strip(),
                "nmea_rmc": nmea_rmc.strip(),
                "serial_data_buffer": self.serial_data_lines[-100:],  # Include last 100 lines for performance
            }
            # Write to temp file first, then atomically rename to avoid partial reads
            temp_file = str(self.debug_file) + ".tmp"
            with open(temp_file, "w") as f:
                json.dump(debug_info, f, indent=2)
            os.replace(temp_file, self.debug_file)
        except Exception:
            pass  # Silently ignore debug write errors

    def _start_tcp_server(self):
        """Start TCP server to accept GPSD connections."""
        try:
            self.tcp_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.tcp_server.bind(('127.0.0.1', self.TCP_PORT))
            self.tcp_server.listen(5)
            self.tcp_server.settimeout(0.1)  # Non-blocking with timeout
            print(f"TCP server listening on port {self.TCP_PORT}", file=sys.stderr)
        except Exception as e:
            print(f"Failed to start TCP server: {e}", file=sys.stderr)

    def _stop_tcp_server(self):
        """Stop TCP server and close all connections."""
        for client in self.tcp_clients:
            try:
                client.close()
            except OSError:
                pass
        self.tcp_clients.clear()
        if self.tcp_server:
            try:
                self.tcp_server.close()
            except OSError:
                pass

    def _accept_clients(self):
        """Accept any new client connections."""
        if not self.tcp_server:
            return
        try:
            while True:
                client, addr = self.tcp_server.accept()
                client.settimeout(None)
                self.tcp_clients.append(client)
                print(f"Client connected from {addr}: {len(self.tcp_clients)} total clients", file=sys.stderr)
        except socket.timeout:
            pass
        except Exception as e:
            print(f"Error accepting client: {e}", file=sys.stderr)

    def _send_to_clients(self, data: str):
        """Send data to all connected TCP clients."""
        if not self.tcp_server:
            return

        # Accept any new connections first
        self._accept_clients()

        # Send data to all connected clients
        dead_clients = []
        data_bytes = data.encode('utf-8')
        for i, client in enumerate(self.tcp_clients):
            try:
                client.sendall(data_bytes)
            except (BrokenPipeError, OSError) as e:
                dead_clients.append(i)
                print(f"Client {i} disconnected", file=sys.stderr)

        # Remove dead clients
        for i in reversed(dead_clients):
            try:
                self.tcp_clients[i].close()
            except OSError:
                pass
            self.tcp_clients.pop(i)


if __name__ == "__main__":
    import argparse

    default_port = "COM3" if platform.system() == "Windows" else "/dev/ttyUSB0"

    parser = argparse.ArgumentParser(description="Convert GPS data to NMEA")
    parser.add_argument("--port", default=default_port, help=f"Serial port (default: {default_port})")
    parser.add_argument("--baudrate", type=int, default=9600, help="Baud rate (default: 9600)")
    parser.add_argument("--mode", default="tcp", choices=["tcp"],
                        help="Output mode (TCP only)")
    parser.add_argument("--parser", default="mcode", choices=["mcode", "gps_print"],
                        help="GPS data format parser (default: mcode)")
    parser.add_argument("--manual-position", help="Manual position JSON: {\"lat\": ..., \"lon\": ..., \"alt\": ..., \"speed_ms\": ...}")

    args = parser.parse_args()

    manual_pos = None
    if args.manual_position:
        try:
            manual_pos = json.loads(args.manual_position)
        except json.JSONDecodeError as e:
            print(f"Invalid manual position JSON: {e}", file=sys.stderr)
            sys.exit(1)

    converter = MCODEConverter(args.port, args.baudrate, None, args.mode, manual_pos, args.parser)
    converter.run()
